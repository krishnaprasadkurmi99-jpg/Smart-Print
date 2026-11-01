# SmartPrint Independent Security Audit

## Verdict

**No unresolved Critical or High application vulnerabilities were identified in
this audit. The two Medium findings were fixed and covered by tests. SmartPrint
is not yet certified production-ready:** live database migration, trusted
proxy/ingress configuration, and real multi-worker database behavior still
require deployment-specific verification. No production infrastructure was
modified and no deployment was performed.

## Findings

| # | Severity | Status | Finding and resolution |
|---|---|---|---|
| 1 | 🟡 MEDIUM | **Fixed** | Public order submission previously matched customer records by owner and caller-supplied phone number, then overwrote the matched name and email. Public orders now create separate guest customer records and do not read or update existing customer rows. Contact details remain unverified input. |
| 2 | 🟡 MEDIUM | **Fixed** | An omitted `SMARTPRINT_ENV` selected development defaults, including a non-Secure session cookie and default root/empty-password DB settings. Startup now fails unless the mode is explicitly `development` or `production`; existing production checks continue to reject insecure origins, missing secrets, and root/empty-password DB settings. Operations guidance now requires explicit production mode. |

## Controls independently reviewed

- Upload request-size enforcement, per-file and file-count limits, PDF
  concurrency, chunked temporary staging, cleanup, and rate-limited login and
  registration were reviewed. Automated tests cover normal upload/login,
  declared and streamed oversize requests, file limits, concurrency saturation,
  and temporary-file cleanup after success, analysis failure, and cancellation.
- Authentication counters use the shared database table. Tests exercise
  independent connection instances sharing the same store; an actual
  multi-process MySQL/MariaDB concurrency test was not run.
- Client IP resolution uses the ASGI peer address. Forwarded headers must only
  be applied by Uvicorn when configured with exact trusted proxy addresses.
  Tests confirm a raw forwarded header is ignored without that trusted proxy
  layer; a deployed proxy chain was not available to verify.
- Signed owner sessions, owner-scoped APIs, tracking-token checks, private
  document access, and retention protections were reviewed alongside existing
  tests. SQL parameterization, CORS/cross-origin controls, and sensitive error
  handling were also reviewed. No additional finding was reported in these
  areas.
- Migration code runs separately from normal app import. Migration behavior is
  tested with a fake connection; no live schema migration or production grants
  were validated.

## Validation results

- Full Python suite: **37 passed**.
- Python syntax compilation: passed.
- `pip check`: passed; no broken requirements.
- `pip-audit -r requirements.txt`: no known Python dependency vulnerabilities.
- `npm audit --omit=dev` in `frontend/`: **0 vulnerabilities**.
- `git diff --check`: passed.
- The new customer-record, missing-mode, production DB-user, and documentation
  tests passed after remediation.

## Deployment blockers and remaining limitations

1. Set `SMARTPRINT_ENV=production` and all production secrets/origins/database
   settings in the service manager. Missing or unsupported mode now prevents
   startup.
2. Configure and verify the actual proxy/ingress request-body cap, timeouts,
   and Uvicorn `--forwarded-allow-ips` against the real trusted proxy addresses.
   Do not use wildcard trusted proxy addresses.
3. Back up the production DB, run `migrate.py` with the migration-only account,
   and configure a runtime DB account with only required DML privileges.
4. Validate rate-limit counters with the actual MySQL/MariaDB backend and
   multiple deployed workers.
5. Public-order contact fields are not phone/email verified. Each order creates
   a separate customer record to avoid impersonating or modifying an existing
   customer; add a verified contact/merge workflow before associating public
   orders with existing customer history.
6. External payment, messaging, printer dispatch/verification, pickup PIN
   verification, employee roles, and accounting remain unavailable until
   integrations are configured and tested. File encryption at rest is not
   implemented.

Dependency scans report no known vulnerabilities at audit time; continue
scanning as advisories change.
