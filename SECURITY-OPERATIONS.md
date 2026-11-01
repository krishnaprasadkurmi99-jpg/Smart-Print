# SmartPrint security operations

## Required deployment mode

The application refuses to start unless `SMARTPRINT_ENV` is explicitly set to
`development` or `production`. For local development, set
`SMARTPRINT_ENV=development`. For a deployed service, set
`SMARTPRINT_ENV=production`; production startup then requires a stable session
secret, explicit HTTPS origins, and a non-root runtime database account with a
password. For example, configure the production environment variables through
the service manager or secret store before starting Uvicorn:

```text
SMARTPRINT_ENV=production
SMARTPRINT_DB_HOST=<database-host>
SMARTPRINT_DB_USER=<least-privilege-runtime-user>
SMARTPRINT_DB_PASSWORD=<secret-store-value>
SMARTPRINT_DB_NAME=smart_print
SMARTPRINT_SESSION_SECRET=<secret-store-value>
SMARTPRINT_ALLOWED_ORIGINS=https://<public-application-host>
```

Do not use the example placeholders as literal values. The application rejects
an omitted or unsupported mode rather than silently selecting development
defaults.

## Upload limits and reverse proxy

The application applies the request-body cap to `POST /upload/`,
`POST /analyze/`, and `POST /upload/presign`. Per-file, aggregate file-size,
and file-count checks apply to both multipart uploads and presign manifests:

| Setting | Default | Environment variable |
| --- | ---: | --- |
| Maximum request body | 110 MiB | `SMARTPRINT_MAX_UPLOAD_REQUEST_BYTES` |
| Maximum individual file | 50 MiB | `SMARTPRINT_MAX_UPLOAD_FILE_BYTES` |
| Maximum files per request | 10 | `SMARTPRINT_MAX_UPLOAD_FILES` |
| Concurrent PDF analyses per worker | 2 | `SMARTPRINT_PDF_MAX_CONCURRENT` |
| Time waiting for an analysis slot | 5 seconds | `SMARTPRINT_PDF_QUEUE_TIMEOUT_SECONDS` |

Set all limits to positive integers. The request cap includes multipart overhead;
configure the ingress/proxy cap at least as high as the application cap so that
the application can return its JSON `413` response. An Nginx server can use:

```nginx
location ~ ^/(upload|analyze)(/|$) {
    client_max_body_size 110m;
    client_body_timeout 30s;
    proxy_request_buffering on;
    proxy_pass http://smartprint_app;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Configure an equivalent request-body cap and body-read timeout in other
ingresses. Do not trust client-supplied forwarding headers: configure Uvicorn
proxy-header handling with `--proxy-headers` and set
`--forwarded-allow-ips` to the exact trusted proxy addresses. Without a trusted
proxy configuration the application uses the direct peer address for limits
and security events.

Uploads are copied in 1 MiB chunks to a private OS temporary directory, analyzed
by file path, and removed when request processing exits, including failures.
The PDF concurrency semaphore is per worker: the maximum aggregate concurrency
is `worker_count × SMARTPRINT_PDF_MAX_CONCURRENT`. Size CPU and memory limits
accordingly. `413` indicates request/file/count limits; `503` with `Retry-After`
indicates PDF processing capacity is temporarily exhausted.

### Supabase Storage and direct browser uploads

Production requires `SMARTPRINT_STORAGE_BACKEND=supabase` and a **private**
Supabase Storage bucket. Create the bucket `smartprint-backend` before starting
the application. Set the bucket's maximum file size to 50 MiB and, if MIME
restrictions are enabled, allow only `application/pdf`. Do not make the bucket
public.

Configure these values in the backend's Render environment/secret settings.
Never add `SUPABASE_SERVICE_ROLE_KEY` to Vite variables or frontend code; the
service-role key bypasses Storage RLS and must remain backend-only.

```text
SMARTPRINT_STORAGE_BACKEND=supabase
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<secret-store-value>
SUPABASE_BUCKET=smartprint-backend
SMARTPRINT_STORAGE_TIMEOUT_SECONDS=30
SMARTPRINT_STORAGE_DOWNLOAD_URL_TTL_SECONDS=300
SMARTPRINT_UPLOAD_INTENT_TTL_SECONDS=1800
```

Set the project/API CORS allowlist to the exact public HTTPS frontend origin.
Allow the browser's `PUT` request with the `Content-Type` header. The API issues
signed upload URLs for random `staging/` keys; the browser sends bytes directly
to Supabase, never the service-role key. Supabase controls the signed upload token
expiry; the application independently expires the upload intent. The API stores
only a hash of the upload-intent token, checks Supabase object metadata against
the declared size and application limits, then downloads objects in 1 MiB
chunks to a private temporary directory for analysis. Order creation consumes
the intent once, persists the analyzed files, and deletes staged objects.
Owner/session checks remain in the API before it issues a short-lived signed
download URL.

The existing browser workflow uses a single signed `PUT` per file. Supabase
recommends TUS resumable uploads for files larger than 6 MiB; uploads are not
resumable in this implementation, so interrupted transfers must be retried.

Supabase bucket size controls are required because direct browser transfers do
not pass through the application's request-body middleware. The API also
enforces per-file, aggregate-size, and file-count limits before issuing upload
URLs and rechecks actual object size before PDF processing. Keep reverse-proxy
request-size controls for local/multipart mode and `/upload/presign`.

The application deletes staging objects after successful order creation.
Configure an external scheduled cleanup for abandoned objects under `staging/`;
do not apply an expiry policy to permanent order documents. Application startup
does not create or modify buckets or retention policies.

Existing objects are not copied or deleted during this change. Existing files
that exist only in Cloudflare R2 remain there but are unavailable through the
application after switching to Supabase. A separately approved data migration
would be needed to restore access to those old documents.

## Authentication throttling

Login limits default to 20 attempts per IP and 5 attempts per IP/account pair
within 15 minutes. Registration defaults to 5 attempts per IP per hour. Limits
are temporary, do not permanently lock accounts, and are stored in the shared
database, so workers see the same counters. Account bucket identifiers are
HMAC-derived; usernames and passwords are not stored in the rate-limit table.
Set a stable, secret `SMARTPRINT_SESSION_SECRET` across all workers. The
`security_rate_limits` table is created by the migration command.

Security events are emitted through Python logging for failed/successful
authentication, rate-limit rejections, upload-limit violations, and saturated
PDF processing. Forward application logs to the existing protected log
aggregation system. Do not add request bodies, passwords, session/tracking
tokens, or document content to logs.

Public order contact details are unverified input. Each public order receives
a separate customer record; a submitted phone number is not used to access or
modify a previously stored customer record. Add a verified contact flow before
associating public orders with an existing customer identity.

## Database provisioning and migrations

Provision the database separately (for example, `CREATE DATABASE smart_print`).
Normal application import/startup does not create or alter schema. Before
starting a new or upgraded installation, set `SMARTPRINT_DB_HOST`,
`SMARTPRINT_DB_PORT`, and `SMARTPRINT_DB_NAME`, plus the dedicated
`SMARTPRINT_MIGRATION_DB_USER` and `SMARTPRINT_MIGRATION_DB_PASSWORD`, then run:

```powershell
.\venv\Scripts\python.exe .\migrate.py
```

The migration command creates missing tables, adds missing columns, and
upgrades existing legacy password hashes. It does not drop tables or records.
Back up the database before applying migrations and review the migration output.
The migration account needs `CREATE`, `ALTER`, `INDEX`, `REFERENCES`, `SELECT`,
`INSERT`, and `UPDATE` on the SmartPrint database. Do not use this account for
the application process.

For production, create a non-root runtime user and grant only the required
data-manipulation permissions on the provisioned database:

```sql
GRANT SELECT, INSERT, UPDATE, DELETE
ON smart_print.*
TO 'smartprint_runtime'@'app-host';
```

Use a unique strong password, store it in the runtime secret manager, and set
`SMARTPRINT_DB_USER`, `SMARTPRINT_DB_PASSWORD`, `SMARTPRINT_DB_HOST`, and
`SMARTPRINT_DB_NAME`. Do not grant the runtime account schema-altering,
database-creation, user-management, or global privileges. Grant `EXECUTE` only
if a reviewed deployment later introduces stored procedures.

## Python dependencies and scan

`requirements.txt` pins the installed runtime dependency set. Install it in the
project virtual environment with:

```powershell
.\venv\Scripts\python.exe -m pip install -r .\requirements.txt
.\venv\Scripts\python.exe -m pip install pip-audit
.\venv\Scripts\python.exe -m pip_audit -r .\requirements.txt
```

Review scanner advisories and compatibility before changing pinned versions;
do not apply unattended upgrades to production.

## Render and Vercel deployment prerequisites

For a Render Web Service, use repository root `.`, build command
`python -m pip install -r requirements.txt`, and start command
`python -m uvicorn main:app --host 0.0.0.0 --port $PORT`. Run
`python migrate.py` separately with the migration database credentials before
serving traffic. The backend must not depend on `frontend/dist` or local
persistent disk; customer uploads, QR images, reprints, and batch exports use
Supabase Storage in production.

Set the backend's `SMARTPRINT_DB_HOST`, `SMARTPRINT_DB_PORT`,
`SMARTPRINT_DB_USER`, `SMARTPRINT_DB_PASSWORD`, `SMARTPRINT_DB_NAME`,
`SMARTPRINT_DB_SSL_CA`, `SMARTPRINT_SESSION_SECRET`,
`SMARTPRINT_ALLOWED_ORIGINS`, and Supabase variables in Render's environment
settings. Make the Aiven CA certificate available as a Render Secret File and
set `SMARTPRINT_DB_SSL_CA` to its path. Keep migration credentials separate
from the runtime database account.

Deploy the Vite frontend from Vercel root `frontend`, with `npm run build` and
output directory `dist`. The frontend currently uses relative API paths and
`SameSite=Lax` owner-session cookies. Vercel hosts the React frontend, and
`vercel.json` rewrites HTTP API requests to the Render-hosted FastAPI backend
under the Vercel origin. WebSocket requests are not currently proxied through
Vercel; the frontend does not currently use the backend's WebSocket endpoint.
If WebSocket support is added, connect directly to Render and review session
authentication for that cross-origin connection.

Supabase Storage direct browser uploads require the Supabase project's CORS
configuration to permit the exact Vercel HTTPS origin and the browser `PUT`
request with its `Content-Type` header. Vercel does not make a developer's
`localhost` MySQL instance reachable from a deployed service. Use a
network-accessible MySQL service with TLS and an allowlist that permits the
backend runtime. Never deploy with production settings that point to local
MySQL or local file storage.
