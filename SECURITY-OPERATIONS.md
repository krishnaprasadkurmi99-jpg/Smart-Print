# SmartPrint security operations

## Upload limits and reverse proxy

The application applies these defaults to both `POST /upload/` and
`POST /analyze/`:

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
location ~ ^/(upload|analyze)/$ {
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
