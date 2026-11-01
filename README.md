# Smart-Print

Smart-Print is a FastAPI and React/Vite application for print-shop orders,
document analysis, printing workflows, and customer uploads.

## Local development

1. Use Python 3.10 or newer and Node.js.
2. Install backend dependencies:

   ```powershell
   python -m pip install -r .\requirements.txt
   ```

3. Configure `SMARTPRINT_ENV=development` and the `SMARTPRINT_DB_*` variables
   for a reachable MySQL database. Local mode uses the `uploads/` and
   `qrcodes/` directories and does not require Supabase credentials.
4. Start the API:

   ```powershell
   python -m uvicorn main:app --reload
   ```

5. In another terminal, install frontend dependencies and start Vite:

   ```powershell
   npm --prefix frontend install
   npm --prefix frontend run dev
   ```

## Supabase Storage setup

Production uses a private Supabase Storage bucket. Create a private bucket
named `smartprint-backend` in the Supabase dashboard. Set its maximum file size
to 50 MiB, and restrict allowed MIME types to `application/pdf` when that
setting is available.

Configure these variables on the **backend only**:

```text
SMARTPRINT_ENV=production
SMARTPRINT_STORAGE_BACKEND=supabase
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<backend-secret>
SUPABASE_BUCKET=smartprint-backend
SMARTPRINT_STORAGE_TIMEOUT_SECONDS=30
SMARTPRINT_STORAGE_DOWNLOAD_URL_TTL_SECONDS=300
```

Also configure the application's existing database, session-secret, and
HTTPS-origin settings as described in
[SECURITY-OPERATIONS.md](./SECURITY-OPERATIONS.md). Never expose the Supabase
service-role key in the frontend or make the bucket public. Configure the
Supabase project's CORS allowlist for the deployed Vercel HTTPS origin so the
browser can upload with `PUT` and `Content-Type`.

The browser uploads to a signed URL for a random staging object.
Smart-Print validates the saved upload intent and actual object size, performs
analysis on temporary files, and uses the private bucket for order documents,
QR images, reprints, and batch exports. Downloads are authorized by the backend
before a short-lived signed URL is returned. Existing objects are not copied:
files that exist only in the old Cloudflare R2 bucket remain there and cannot be
opened from Smart-Print after switching to Supabase.

Schedule cleanup of abandoned objects under the `staging/` prefix. Do not set
bucket-wide expiry for permanent order files. Application settings and
deployment steps, including Render/Vercel configuration and database
migrations, are maintained in [SECURITY-OPERATIONS.md](./SECURITY-OPERATIONS.md).

## Render and Vercel

For a Render backend, create a Web Service with repository root `.`, build
command `python -m pip install -r requirements.txt`, and start command
`python -m uvicorn main:app --host 0.0.0.0 --port $PORT`. Run
`python migrate.py` separately as a pre-deploy step after configuring the
migration database credentials. Supply the production database, session,
origin, and Supabase variables in Render's environment settings. If using the
Aiven CA certificate, make it available as a Render Secret File and set
`SMARTPRINT_DB_SSL_CA` to that file path.

Deploy the Vite frontend from the `frontend` root on Vercel with
`npm run build` and output directory `dist`. The frontend uses same-origin API
paths and secure `SameSite=Lax` owner-session cookies. Vercel hosts the React
frontend and `vercel.json` rewrites HTTP API requests to the Render-hosted
FastAPI backend under the Vercel origin. WebSocket requests are not currently
proxied through Vercel; the frontend does not currently use the backend's
WebSocket endpoint. If WebSocket support is added, connect directly to Render
and review session authentication for that cross-origin connection.
