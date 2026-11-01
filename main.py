import os
import random
import shutil
import string
import uuid
import hashlib
import hmac
import secrets
import time
import base64
import json
import re
import logging
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import List
import pymupdf
from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
import mysql.connector
import qrcode
from io import BytesIO
import asyncio
from document_intelligence import analyze_files, analyze_pdf
from upload_security import UploadRequestSizeLimitMiddleware

app = FastAPI(title="Smart Print Shop Management System")
logger = logging.getLogger(__name__)


def _positive_int_setting(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as error:
        raise RuntimeError(f"{name} must be a positive integer.") from error
    if value < 1:
        raise RuntimeError(f"{name} must be a positive integer.")
    return value


MAX_UPLOAD_FILE_BYTES = _positive_int_setting("SMARTPRINT_MAX_UPLOAD_FILE_BYTES", 50 * 1024 * 1024)
MAX_UPLOAD_REQUEST_BYTES = _positive_int_setting(
    "SMARTPRINT_MAX_UPLOAD_REQUEST_BYTES", 110 * 1024 * 1024
)
MAX_UPLOAD_FILES = _positive_int_setting("SMARTPRINT_MAX_UPLOAD_FILES", 10)
PDF_PROCESSING_MAX_CONCURRENT = _positive_int_setting(
    "SMARTPRINT_PDF_MAX_CONCURRENT", 2
)
PDF_PROCESSING_QUEUE_TIMEOUT = _positive_int_setting(
    "SMARTPRINT_PDF_QUEUE_TIMEOUT_SECONDS", 5
)
PDF_PROCESSING_SEMAPHORE = asyncio.Semaphore(PDF_PROCESSING_MAX_CONCURRENT)
AUTH_RATE_WINDOW_SECONDS = _positive_int_setting(
    "SMARTPRINT_AUTH_RATE_WINDOW_SECONDS", 15 * 60
)
AUTH_LOGIN_IP_LIMIT = _positive_int_setting("SMARTPRINT_AUTH_LOGIN_IP_LIMIT", 20)
AUTH_LOGIN_PAIR_LIMIT = _positive_int_setting("SMARTPRINT_AUTH_LOGIN_PAIR_LIMIT", 5)
AUTH_REGISTER_IP_LIMIT = _positive_int_setting("SMARTPRINT_AUTH_REGISTER_IP_LIMIT", 5)
AUTH_REGISTER_WINDOW_SECONDS = _positive_int_setting(
    "SMARTPRINT_AUTH_REGISTER_WINDOW_SECONDS", 60 * 60
)
APP_ENV = os.getenv("SMARTPRINT_ENV", "").strip().lower()
if APP_ENV not in {"development", "production"}:
    raise RuntimeError(
        "SMARTPRINT_ENV must be explicitly set to 'development' or 'production'."
    )
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "SMARTPRINT_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]
if not ALLOWED_ORIGINS or "*" in ALLOWED_ORIGINS:
    raise RuntimeError("SMARTPRINT_ALLOWED_ORIGINS must contain explicit origins and cannot include '*'.")
if APP_ENV == "production" and (
    not os.getenv("SMARTPRINT_ALLOWED_ORIGINS")
    or any(not origin.startswith("https://") for origin in ALLOWED_ORIGINS)
):
    raise RuntimeError("Production allowed origins must be explicitly configured with HTTPS.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)
app.add_middleware(
    UploadRequestSizeLimitMiddleware,
    max_bytes=MAX_UPLOAD_REQUEST_BYTES,
)

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
QRCODE_DIR = os.path.join(os.path.dirname(__file__), "qrcodes")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(QRCODE_DIR, exist_ok=True)

FRONTEND_DIST = os.path.join(os.path.dirname(__file__), "frontend", "dist")
FRONTEND_INDEX = os.path.join(FRONTEND_DIST, "index.html")

DB_HOST = os.getenv("SMARTPRINT_DB_HOST", "localhost")
DB_PORT = int(os.getenv("SMARTPRINT_DB_PORT", "3306"))
DB_USER = os.getenv("SMARTPRINT_DB_USER", "root")
DB_PASSWORD = os.getenv("SMARTPRINT_DB_PASSWORD", "")
DB_NAME = os.getenv("SMARTPRINT_DB_NAME", "smart_print")
if not re.fullmatch(r"[A-Za-z0-9_]+", DB_NAME):
    raise RuntimeError("SMARTPRINT_DB_NAME may contain only letters, numbers, and underscores.")
if APP_ENV == "production" and (
    not DB_PASSWORD
    or not os.getenv("SMARTPRINT_DB_HOST")
    or not os.getenv("SMARTPRINT_DB_USER")
    or DB_USER.lower() == "root"
):
    raise RuntimeError("Configure a non-root production database user, host, and password.")
SESSION_SECRET = os.getenv("SMARTPRINT_SESSION_SECRET")
if APP_ENV == "production" and not SESSION_SECRET:
    raise RuntimeError("SMARTPRINT_SESSION_SECRET must be set in production.")
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(48)
SESSION_SECRET_BYTES = SESSION_SECRET.encode("utf-8")
SESSION_COOKIE_NAME = "smartprint_session"
SESSION_DURATION_SECONDS = 12 * 60 * 60
try:
    FILE_RETENTION_DAYS = int(os.getenv("SMARTPRINT_FILE_RETENTION_DAYS", "1"))
except ValueError as error:
    raise RuntimeError("SMARTPRINT_FILE_RETENTION_DAYS must be a positive integer.") from error
if FILE_RETENTION_DAYS < 1:
    raise RuntimeError("SMARTPRINT_FILE_RETENTION_DAYS must be a positive integer.")


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt${}${}".format(
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def get_db_connection():
    return mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _rate_limit_key(scope: str) -> str:
    return hmac.new(
        SESSION_SECRET_BYTES,
        scope.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _consume_rate_limit(
    request: Request,
    *,
    endpoint: str,
    scopes: list[tuple[str, int, int]],
) -> None:
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    blocked_scope = None
    retry_after = 1
    try:
        for scope_name, limit, window_seconds in scopes:
            bucket_key = _rate_limit_key(scope_name)
            cursor.execute(
                """
                INSERT INTO security_rate_limits (bucket_key, window_started_at, attempts)
                VALUES (%s, UTC_TIMESTAMP(), 1)
                ON DUPLICATE KEY UPDATE
                    attempts = IF(
                        TIMESTAMPDIFF(SECOND, window_started_at, UTC_TIMESTAMP()) >= %s,
                        1,
                        attempts + 1
                    ),
                    window_started_at = IF(
                        TIMESTAMPDIFF(SECOND, window_started_at, UTC_TIMESTAMP()) >= %s,
                        UTC_TIMESTAMP(),
                        window_started_at
                    )
                """,
                (bucket_key, window_seconds, window_seconds),
            )
            cursor.execute(
                """
                SELECT attempts,
                       GREATEST(
                           1,
                           %s - TIMESTAMPDIFF(SECOND, window_started_at, UTC_TIMESTAMP())
                       ) AS retry_after
                FROM security_rate_limits
                WHERE bucket_key = %s
                """,
                (window_seconds, bucket_key),
            )
            result = cursor.fetchone()
            if result["attempts"] > limit and blocked_scope is None:
                blocked_scope = scope_name.rsplit(":", 1)[0]
                retry_after = int(result["retry_after"])
        if secrets.randbelow(100) == 0:
            cursor.execute(
                """
                DELETE FROM security_rate_limits
                WHERE updated_at < UTC_TIMESTAMP() - INTERVAL 1 DAY
                """
            )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("security_event event=auth_rate_limit_storage_error endpoint=%s", endpoint)
        raise HTTPException(
            status_code=503,
            detail="Authentication is temporarily unavailable. Please try again.",
        ) from None
    finally:
        cursor.close()
        db.close()

    if blocked_scope:
        logger.warning(
            "security_event event=auth_rate_limited endpoint=%s client_ip=%s scope=%s",
            endpoint,
            _client_ip(request),
            blocked_scope,
        )
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Please wait before trying again.",
            headers={"Retry-After": str(retry_after)},
        )


def _check_login_rate_limit(request: Request, username: str) -> None:
    ip = _client_ip(request)
    account_tag = hmac.new(
        SESSION_SECRET_BYTES,
        username.strip().casefold().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    _consume_rate_limit(
        request,
        endpoint="login",
        scopes=[
            (f"login_ip:{ip}", AUTH_LOGIN_IP_LIMIT, AUTH_RATE_WINDOW_SECONDS),
            (
                f"login_ip_account:{ip}:{account_tag}",
                AUTH_LOGIN_PAIR_LIMIT,
                AUTH_RATE_WINDOW_SECONDS,
            ),
        ],
    )


def _check_registration_rate_limit(request: Request) -> None:
    _consume_rate_limit(
        request,
        endpoint="register",
        scopes=[
            (
                f"register_ip:{_client_ip(request)}",
                AUTH_REGISTER_IP_LIMIT,
                AUTH_REGISTER_WINDOW_SECONDS,
            )
        ],
    )


def _log_upload_limit(request: Request, reason: str) -> None:
    logger.warning(
        "security_event event=upload_limit_exceeded client_ip=%s reason=%s",
        _client_ip(request),
        reason,
    )


async def _stage_pdf_uploads(
    files: list[UploadFile],
    working_directory: str,
    request: Request,
) -> list[tuple[str, str]]:
    staged_files = []
    try:
        if len(files) > MAX_UPLOAD_FILES:
            _log_upload_limit(request, "file_count")
            raise HTTPException(
                status_code=413,
                detail="The upload contains more files than the configured limit.",
            )
        for upload in files:
            original_name = Path(upload.filename or "document.pdf").name
            if not original_name.lower().endswith(".pdf"):
                raise HTTPException(status_code=400, detail="Only PDF documents can be uploaded.")
            temporary_path = os.path.join(working_directory, f"{uuid.uuid4().hex}.pdf")
            file_size = 0
            with open(temporary_path, "xb") as destination:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    file_size += len(chunk)
                    if file_size > MAX_UPLOAD_FILE_BYTES:
                        _log_upload_limit(request, "per_file_size")
                        raise HTTPException(
                            status_code=413,
                            detail="A PDF exceeds the configured per-file size limit.",
                        )
                    destination.write(chunk)
            staged_files.append((original_name, temporary_path))
        return staged_files
    finally:
        for upload in files:
            await upload.close()


@asynccontextmanager
async def _analyzed_pdf_uploads(files: list[UploadFile], request: Request):
    if len(files) > MAX_UPLOAD_FILES:
        _log_upload_limit(request, "file_count")
        for upload in files:
            await upload.close()
        raise HTTPException(
            status_code=413,
            detail="The upload contains more files than the configured limit.",
        )
    try:
        await asyncio.wait_for(
            PDF_PROCESSING_SEMAPHORE.acquire(),
            timeout=PDF_PROCESSING_QUEUE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "security_event event=pdf_processing_capacity_exceeded client_ip=%s",
            _client_ip(request),
        )
        for upload in files:
            await upload.close()
        raise HTTPException(
            status_code=503,
            detail="Document analysis is busy. Please retry shortly.",
            headers={"Retry-After": str(PDF_PROCESSING_QUEUE_TIMEOUT)},
        ) from None
    except asyncio.CancelledError:
        for upload in files:
            await upload.close()
        raise

    try:
        with tempfile.TemporaryDirectory(prefix="smartprint-pdf-") as working_directory:
            staged_files = await _stage_pdf_uploads(files, working_directory, request)
            try:
                analysis_task = asyncio.create_task(
                    asyncio.to_thread(analyze_files, staged_files)
                )
                try:
                    analyses = await asyncio.shield(analysis_task)
                except asyncio.CancelledError:
                    await analysis_task
                    raise
            except Exception:
                logger.warning(
                    "security_event event=pdf_analysis_rejected client_ip=%s",
                    _client_ip(request),
                )
                raise HTTPException(
                    status_code=400,
                    detail="One or more PDF documents could not be analyzed.",
                ) from None
            yield staged_files, analyses
    finally:
        for upload in files:
            await upload.close()
        PDF_PROCESSING_SEMAPHORE.release()


def _delete_upload_file(filename: str) -> bool:
    if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
        return False
    upload_directory = os.path.abspath(UPLOAD_DIR)
    file_path = os.path.abspath(os.path.join(upload_directory, filename))
    if os.path.commonpath([upload_directory, file_path]) != upload_directory:
        return False
    try:
        os.remove(file_path)
    except FileNotFoundError:
        return True
    except OSError:
        logger.exception("Could not remove an expired PDF.")
        return False
    return True


def cleanup_expired_files() -> dict[str, int]:
    cutoff = datetime.now() - timedelta(days=FILE_RETENTION_DAYS)
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    removed_documents = 0
    removed_batch_exports = 0
    try:
        cursor.execute(
            """
            SELECT d.document_id, d.storage_filename
            FROM order_documents d
            JOIN orders o ON o.order_id = d.order_id
            WHERE o.order_status = 'Completed'
              AND o.completed_at IS NOT NULL
              AND o.completed_at <= %s
              AND d.file_deleted_at IS NULL
            """,
            (cutoff,),
        )
        expired_documents = cursor.fetchall()
        removed_document_ids = [
            (item["document_id"],)
            for item in expired_documents
            if _delete_upload_file(item["storage_filename"])
        ]
        if removed_document_ids:
            cursor.executemany(
                "UPDATE order_documents SET file_deleted_at = CURRENT_TIMESTAMP WHERE document_id = %s",
                removed_document_ids,
            )
            removed_documents = len(removed_document_ids)

        cursor.execute(
            """
            SELECT o.filename
            FROM orders o
            WHERE o.order_status = 'Completed'
              AND o.completed_at IS NOT NULL
              AND o.completed_at <= %s
              AND NOT EXISTS (
                  SELECT 1 FROM order_documents d WHERE d.order_id = o.order_id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM orders other_order
                  WHERE other_order.filename = o.filename
                    AND (
                        other_order.order_status <> 'Completed'
                        OR other_order.completed_at IS NULL
                        OR other_order.completed_at > %s
                    )
              )
            """,
            (cutoff, cutoff),
        )
        for item in cursor.fetchall():
            filename = item.get("filename") or ""
            if filename and os.path.basename(filename) == filename:
                _delete_upload_file(filename)

        cursor.execute(
            """
            SELECT be.export_id
            FROM batch_exports be
            JOIN batch_export_orders beo ON beo.export_id = be.export_id
            JOIN orders o ON o.order_id = beo.order_id
            GROUP BY be.export_id
            HAVING SUM(
                CASE WHEN o.order_status = 'Completed' AND o.completed_at IS NOT NULL
                THEN 1 ELSE 0 END
            ) = COUNT(DISTINCT beo.order_id)
            AND MAX(o.completed_at) <= %s
            """,
            (cutoff,),
        )
        for export in cursor.fetchall():
            cursor.execute(
                "SELECT filename FROM batch_export_files WHERE export_id = %s",
                (export["export_id"],),
            )
            filenames = [item["filename"] for item in cursor.fetchall()]
            if all(_delete_upload_file(filename) for filename in filenames):
                cursor.execute(
                    "DELETE FROM batch_exports WHERE export_id = %s",
                    (export["export_id"],),
                )
                removed_batch_exports += 1

        db.commit()
        return {
            "documents": removed_documents,
            "batch_exports": removed_batch_exports,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        cursor.close()
        db.close()


def _internal_error_response(operation: str) -> JSONResponse:
    logger.exception("Unexpected error while %s", operation)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "An internal error occurred."},
    )


def _encode_session(owner_id: int) -> str:
    payload = json.dumps(
        {"owner_id": owner_id, "exp": int(time.time()) + SESSION_DURATION_SECONDS},
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(SESSION_SECRET_BYTES, encoded, hashlib.sha256).digest()
    return f"{encoded.decode('ascii')}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"


def _decode_session(value: str | None) -> int | None:
    if not value:
        return None
    try:
        encoded, signature = value.split(".", 1)
        expected = hmac.new(SESSION_SECRET_BYTES, encoded.encode("ascii"), hashlib.sha256).digest()
        supplied = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        if not hmac.compare_digest(expected, supplied):
            return None
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        session = json.loads(payload)
        if int(session["exp"]) <= int(time.time()):
            return None
        owner_id = int(session["owner_id"])
        return owner_id if owner_id > 0 else None
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _verify_password(password: str, stored: str | None) -> tuple[bool, bool]:
    if not stored:
        return False, False
    if not stored.startswith("scrypt$"):
        return hmac.compare_digest(password.encode("utf-8"), stored.encode("utf-8")), True
    try:
        _, salt_value, digest_value = stored.split("$", 2)
        salt = base64.urlsafe_b64decode(salt_value.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_value.encode("ascii"))
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=len(expected))
        return hmac.compare_digest(actual, expected), False
    except (ValueError, TypeError):
        return False, False


def _owner_id(request: Request) -> int:
    owner_id = getattr(request.state, "owner_id", None)
    if not owner_id:
        raise HTTPException(status_code=401, detail="Owner authentication is required.")
    return owner_id


def _require_matching_owner(requested_owner_id: int, owner_id: int) -> None:
    if requested_owner_id != owner_id:
        raise HTTPException(status_code=403, detail="This resource belongs to another shop.")


def _tracking_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _customer_tracking_owner(cursor, order_id: str, token: str | None) -> int:
    if not token:
        raise HTTPException(status_code=401, detail="A valid order tracking link is required.")
    cursor.execute(
        "SELECT owner_id, tracking_token_hash FROM orders WHERE order_id = %s",
        (order_id,),
    )
    order = cursor.fetchone()
    stored_hash = order.get("tracking_token_hash") if order else None
    if not stored_hash or not hmac.compare_digest(stored_hash, _tracking_token_hash(token)):
        raise HTTPException(status_code=404, detail="Order not found.")
    return int(order["owner_id"])


@app.middleware("http")
async def protect_owner_apis(request: Request, call_next):
    path = request.url.path
    public_paths = {"/login/", "/register/", "/analyze/", "/upload/"}
    owner_api = path.startswith((
        "/profiles",
        "/reprints",
        "/orders",
        "/merge",
        "/branches",
        "/printers",
        "/inventory",
        "/notifications",
        "/whatsapp",
        "/email",
        "/progress",
        "/analytics",
        "/search/orders",
        "/files",
        "/logout/",
        "/session/",
    ))
    route_parts = path.strip("/").split("/")
    token_tracking = (
        request.method == "GET"
        and len(route_parts) == 2
        and route_parts[0] in {"orders", "progress"}
        and bool(request.query_params.get("tracking_token"))
        and not _decode_session(request.cookies.get(SESSION_COOKIE_NAME))
    )
    if path in public_paths or path.startswith("/track/") or not owner_api or token_tracking:
        response = await call_next(request)
    else:
        origin = request.headers.get("origin")
        same_origin = f"{request.url.scheme}://{request.url.netloc}"
        cross_origin_request = (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.headers.get("sec-fetch-site") == "cross-site"
        ) or (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and origin
            and origin not in ALLOWED_ORIGINS
            and origin != same_origin
        )
        if cross_origin_request:
            response = JSONResponse(
                {"status": "error", "detail": "Cross-origin requests are not allowed."},
                status_code=403,
            )
        else:
            owner_id = _decode_session(request.cookies.get(SESSION_COOKIE_NAME))
            if not owner_id:
                response = JSONResponse(
                    {"status": "error", "detail": "Owner authentication is required."},
                    status_code=401,
                )
            else:
                requested_owner = request.query_params.get("owner_id")
                if requested_owner and (not requested_owner.isdigit() or int(requested_owner) != owner_id):
                    response = JSONResponse(
                        {"status": "error", "detail": "This resource belongs to another shop."},
                        status_code=403,
                    )
                else:
                    request.state.owner_id = owner_id
                    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if owner_api or path.startswith("/qr/"):
        response.headers.setdefault("Cache-Control", "no-store")
    if APP_ENV == "production":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response

def generate_order_id():
    date_prefix = datetime.now().strftime("SP%Y%m%d")
    number = random.randint(10000, 99999)
    return f"{date_prefix}{number}"

def generate_pin():
    return ''.join(random.choices(string.digits, k=4))

# WebSocket connections for real-time updates
class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[int, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, owner_id: int):
        await websocket.accept()
        self.active_connections.setdefault(owner_id, []).append(websocket)

    def disconnect(self, websocket: WebSocket, owner_id: int):
        connections = self.active_connections.get(owner_id, [])
        if websocket in connections:
            connections.remove(websocket)
        if not connections:
            self.active_connections.pop(owner_id, None)

    async def broadcast(self, message: dict):
        owner_id = message.get("owner_id")
        if not owner_id:
            return
        connections = list(self.active_connections.get(int(owner_id), []))
        for connection in connections:
            try:
                await connection.send_text(json.dumps(message))
            except (RuntimeError, WebSocketDisconnect):
                self.disconnect(connection, int(owner_id))

manager = ConnectionManager()

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: int):
    origin = websocket.headers.get("origin")
    owner_id = _decode_session(websocket.cookies.get(SESSION_COOKIE_NAME))
    if not owner_id or (origin and origin not in ALLOWED_ORIGINS):
        await websocket.close(code=1008)
        return
    await manager.connect(websocket, owner_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, owner_id)

class PrintingProfilePayload(BaseModel):
    owner_id: int
    profile_name: str
    printer_id: int | None = None
    paper_size: str = "A4"
    orientation: str = "Portrait"
    color_mode: str = "Auto"
    duplex: bool = False
    quality: str = "Standard"
    copies: int = 1


class ReprintRequest(BaseModel):
    owner_id: int
    profile_id: int | None = None


class PrintSettingsConfirmation(BaseModel):
    owner_id: int


def _validated_print_settings(value: str | dict | None) -> dict:
    if isinstance(value, str):
        try:
            data = json.loads(value or "{}")
        except json.JSONDecodeError as error:
            raise HTTPException(status_code=400, detail="Print settings must be valid JSON.") from error
    else:
        data = value or {}
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Print settings must be an object.")

    settings = {
        "paper_size": data.get("paper_size", "A4"),
        "orientation": data.get("orientation", "Portrait"),
        "color_mode": data.get("color_mode", "Auto"),
        "duplex": data.get("duplex", False),
        "quality": data.get("quality", "Standard"),
        "copies": data.get("copies", 1),
    }
    if settings["paper_size"] not in {"A3", "A4", "A5", "Letter", "Legal", "Custom"}:
        raise HTTPException(status_code=400, detail="Unsupported paper size.")
    if settings["orientation"] not in {"Portrait", "Landscape"}:
        raise HTTPException(status_code=400, detail="Unsupported page orientation.")
    if settings["color_mode"] not in {"Auto", "Color", "B&W", "Mixed"}:
        raise HTTPException(status_code=400, detail="Unsupported color mode.")
    if settings["quality"] not in {"Draft", "Standard", "High"}:
        raise HTTPException(status_code=400, detail="Unsupported print quality.")
    if not isinstance(settings["duplex"], bool):
        raise HTTPException(status_code=400, detail="Duplex must be true or false.")
    if isinstance(settings["copies"], bool) or not isinstance(settings["copies"], int) or not 1 <= settings["copies"] <= 99:
        raise HTTPException(status_code=400, detail="Copies must be between 1 and 99.")
    return settings


def _validate_profile(payload: PrintingProfilePayload) -> dict:
    profile_name = payload.profile_name.strip()
    if not profile_name or len(profile_name) > 100:
        raise HTTPException(status_code=400, detail="Profile name must be between 1 and 100 characters.")
    settings = _validated_print_settings({
        "paper_size": payload.paper_size,
        "orientation": payload.orientation,
        "color_mode": payload.color_mode,
        "duplex": payload.duplex,
        "quality": payload.quality,
        "copies": payload.copies,
    })
    return {"profile_name": profile_name, "printer_id": payload.printer_id, **settings}


def _qr_for_order(order_id: str, pickup_pin: str, owner_id: int, cursor) -> None:
    qr_data = f"ORDER:{order_id}:PIN:{pickup_pin}:OWNER:{owner_id}"
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(qr_data)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    qr_path = os.path.join(QRCODE_DIR, f"{order_id}.png")
    image.save(qr_path)
    cursor.execute(
        "INSERT INTO qr_codes (order_id, owner_id, qr_data, qr_image_path) VALUES (%s, %s, %s, %s)",
        (order_id, owner_id, qr_data, qr_path),
    )


@app.post("/register/")
def register_owner(
    request: Request,
    shop_name: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
):
    _check_registration_rate_limit(request)
    shop_name = shop_name.strip()
    username = username.strip()
    if not shop_name or len(shop_name) > 255 or not username or len(username) > 100:
        logger.warning(
            "security_event event=auth_registration_rejected client_ip=%s reason=invalid_input",
            _client_ip(request),
        )
        raise HTTPException(status_code=400, detail="Enter a valid shop name and username.")
    if len(password) < 8 or len(password) > 200:
        logger.warning(
            "security_event event=auth_registration_rejected client_ip=%s reason=password_policy",
            _client_ip(request),
        )
        raise HTTPException(status_code=400, detail="Password must be between 8 and 200 characters.")
    db = get_db_connection()
    cursor = db.cursor()
    try:
        cursor.execute(
            "INSERT INTO shop_owners (shop_name, username, password) VALUES (%s, %s, %s)",
            (shop_name, username, _hash_password(password)),
        )
        owner_id = cursor.lastrowid
        db.commit()
        logger.info("security_event event=auth_registration_succeeded client_ip=%s", _client_ip(request))
        return {"status": "success", "message": "Shop registered successfully!", "owner_id": owner_id}
    except mysql.connector.IntegrityError as error:
        db.rollback()
        if error.errno == 1062:
            logger.warning(
                "security_event event=auth_registration_rejected client_ip=%s reason=registration_conflict",
                _client_ip(request),
            )
            raise HTTPException(
                status_code=400,
                detail="Unable to complete registration with the supplied details.",
            ) from None
        raise
    finally:
        cursor.close()
        db.close()

@app.post("/login/")
def login_owner(request: Request, username: str = Form(...), password: str = Form(...)):
    _check_login_rate_limit(request, username)
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM shop_owners WHERE username = %s", (username.strip(),))
        user = cursor.fetchone()
        password_valid, needs_upgrade = _verify_password(password, user.get("password") if user else None)
        if not user or not password_valid:
            logger.warning("security_event event=auth_login_failed client_ip=%s", _client_ip(request))
            raise HTTPException(status_code=401, detail="Invalid username or password.")
        if needs_upgrade:
            cursor.execute(
                "UPDATE shop_owners SET password = %s WHERE owner_id = %s",
                (_hash_password(password), user["owner_id"]),
            )
            db.commit()
        logger.info("security_event event=auth_login_succeeded client_ip=%s", _client_ip(request))
        response = JSONResponse({
            "status": "success",
            "shop_name": user["shop_name"],
            "owner_id": user["owner_id"],
        })
        response.set_cookie(
            SESSION_COOKIE_NAME,
            _encode_session(int(user["owner_id"])),
            max_age=SESSION_DURATION_SECONDS,
            httponly=True,
            secure=APP_ENV == "production",
            samesite="lax",
            path="/",
        )
        return response
    finally:
        cursor.close()
        db.close()


@app.get("/session/")
def get_owner_session(request: Request):
    owner_id = _owner_id(request)
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT owner_id, shop_name FROM shop_owners WHERE owner_id = %s",
            (owner_id,),
        )
        owner = cursor.fetchone()
        if not owner:
            raise HTTPException(status_code=401, detail="Owner account is no longer available.")
        return {"status": "success", "data": owner}
    finally:
        cursor.close()
        db.close()


@app.post("/logout/")
def logout_owner():
    response = JSONResponse({"status": "success"})
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=APP_ENV == "production",
        samesite="lax",
    )
    return response

@app.post("/analyze/")
async def analyze_only_endpoint(request: Request, files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="Choose at least one PDF to analyze.")
    async with _analyzed_pdf_uploads(files, request) as (_, documents):
        total_pages = sum(item["total_pages"] for item in documents)
        bw_pages = sum(item["bw_pages"] for item in documents)
        color_pages = sum(item["color_pages"] for item in documents)
        recommendations = [
            {"filename": item["filename"], **recommendation}
            for item in documents
            for recommendation in item["recommendations"]
        ]
        return {
            "status": "success",
            "total_pages": total_pages,
            "bw_pages": bw_pages,
            "color_pages": color_pages,
            "analyzed_price": (bw_pages * 2.00) + (color_pages * 10.00),
            "forced_bw_price": total_pages * 2.00,
            "documents": documents,
            "recommendations": recommendations,
        }

@app.post("/upload/")
async def upload_and_save_endpoint(request: Request, files: List[UploadFile] = File(...), payment_method: str = Form("UPI"), force_bw: str = Form("false"), 
                                   customer_name: str = Form(""), customer_phone: str = Form(""), customer_email: str = Form(""),
                                   owner_id: int = Form(...), print_settings: str = Form("{}")):
    if not files:
        raise HTTPException(status_code=400, detail="Choose at least one PDF to upload.")
    is_forced = force_bw.lower() == "true"
    settings = _validated_print_settings(print_settings)
    if is_forced:
        settings["color_mode"] = "B&W"

    async with _analyzed_pdf_uploads(files, request) as (staged_files, analyses):
        return _persist_customer_order(
            staged_files,
            analyses,
            payment_method=payment_method,
            is_forced=is_forced,
            customer_name=customer_name,
            customer_phone=customer_phone,
            customer_email=customer_email,
            owner_id=owner_id,
            settings=settings,
        )


def _persist_customer_order(
    staged_files: list[tuple[str, str]],
    analyses: list[dict],
    *,
    payment_method: str,
    is_forced: bool,
    customer_name: str,
    customer_phone: str,
    customer_email: str,
    owner_id: int,
    settings: dict,
) -> dict:
    total_pages = sum(item["total_pages"] for item in analyses)
    classified_bw = sum(item["bw_pages"] for item in analyses)
    classified_color = sum(item["color_pages"] for item in analyses)
    bw_pages = total_pages if is_forced else classified_bw
    color_pages = 0 if is_forced else classified_color
    total_price = (bw_pages * 2.00) + (color_pages * 10.00)

    db = get_db_connection()
    cursor = db.cursor()
    saved_paths = []
    created_qr_path = None
    try:
        cursor.execute("SELECT owner_id FROM shop_owners WHERE owner_id = %s", (owner_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Shop owner not found.")
        cursor.execute("SELECT branch_id FROM branches WHERE owner_id = %s LIMIT 1", (owner_id,))
        branch = cursor.fetchone()
        if not branch:
            cursor.execute(
                "INSERT INTO branches (owner_id, branch_name, branch_code, address) VALUES (%s, %s, %s, %s)",
                (owner_id, "Main Branch", f"SP{uuid.uuid4().hex[:8].upper()}", ""),
            )
            branch_id = cursor.lastrowid
        else:
            branch_id = branch[0]

        cursor.execute(
            "INSERT INTO customers (name, phone, email, owner_id) VALUES (%s, %s, %s, %s)",
            (customer_name, customer_phone, customer_email, owner_id),
        )
        customer_id = cursor.lastrowid

        order_id = None
        for _ in range(10):
            candidate = generate_order_id()
            cursor.execute("SELECT order_id FROM orders WHERE order_id = %s", (candidate,))
            if not cursor.fetchone():
                order_id = candidate
                break
        if not order_id:
            raise HTTPException(status_code=503, detail="Could not allocate a unique order ID. Please try again.")
        pickup_pin = generate_pin()
        tracking_token = secrets.token_urlsafe(32)
        original_names = [item["filename"] for item in analyses]
        cursor.execute("""
            INSERT INTO orders (order_id, customer_id, owner_id, branch_id, filename, total_pages, bw_pages, color_pages, total_price, payment_status, order_status, estimated_completion, tracking_token_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            order_id, customer_id, owner_id, branch_id, ", ".join(original_names)[:255],
            total_pages, bw_pages, color_pages, total_price, "Pending", "Waiting",
            datetime.now() + timedelta(hours=2), _tracking_token_hash(tracking_token),
        ))
        cursor.execute(
            "INSERT INTO order_history (order_id, owner_id, action, status) VALUES (%s, %s, %s, %s)",
            (order_id, owner_id, "Order Submitted", "Waiting"),
        )

        for (original_name, temporary_path), analysis in zip(staged_files, analyses):
            storage_name = f"{uuid.uuid4().hex}.pdf"
            storage_path = os.path.join(UPLOAD_DIR, storage_name)
            shutil.copyfile(temporary_path, storage_path)
            saved_paths.append(storage_path)
            cursor.execute("""
                INSERT INTO order_documents (
                    order_id, original_filename, storage_filename, sha256,
                    total_pages, bw_pages, color_pages, analysis_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                order_id, Path(original_name).name[:255], storage_name, analysis["sha256"],
                analysis["total_pages"], analysis["bw_pages"], analysis["color_pages"],
                json.dumps(analysis, ensure_ascii=False),
            ))

        cursor.execute("""
            INSERT INTO order_print_settings
                (order_id, paper_size, orientation, color_mode, duplex, quality, copies)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            order_id, settings["paper_size"], settings["orientation"], settings["color_mode"],
            settings["duplex"], settings["quality"], settings["copies"],
        ))
        created_qr_path = os.path.join(QRCODE_DIR, f"{order_id}.png")
        _qr_for_order(order_id, pickup_pin, owner_id, cursor)
        db.commit()
    except Exception:
        db.rollback()
        for saved_path in saved_paths:
            if os.path.exists(saved_path):
                os.remove(saved_path)
        if created_qr_path and os.path.exists(created_qr_path):
            os.remove(created_qr_path)
        raise
    finally:
        cursor.close()
        db.close()

    asyncio.create_task(manager.broadcast({
        "type": "new_order", "owner_id": owner_id, "order_id": order_id, "status": "Waiting",
    }))
    return {
        "status": "success",
        "order_id": order_id,
        "tracking_token": tracking_token,
        "pickup_pin": pickup_pin,
        "total_price": total_price,
        "total_pages": total_pages,
        "bw_pages": bw_pages,
        "color_pages": color_pages,
        "payment_method": payment_method,
        "payment_status": "Pending",
    }

@app.get("/profiles/")
def get_printing_profiles(request: Request):
    owner_id = _owner_id(request)
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT * FROM printing_profiles WHERE owner_id = %s ORDER BY profile_name",
            (owner_id,),
        )
        return {"status": "success", "data": cursor.fetchall()}
    finally:
        cursor.close()
        db.close()


@app.post("/profiles/")
def create_printing_profile(payload: PrintingProfilePayload, request: Request):
    owner_id = _owner_id(request)
    _require_matching_owner(payload.owner_id, owner_id)
    profile = _validate_profile(payload)
    db = get_db_connection()
    cursor = db.cursor()
    try:
        cursor.execute("SELECT owner_id FROM shop_owners WHERE owner_id = %s", (owner_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Shop owner not found.")
        if profile["printer_id"] is not None:
            cursor.execute("""
                SELECT p.printer_id FROM printers p
                JOIN branches b ON p.branch_id = b.branch_id
                WHERE p.printer_id = %s AND b.owner_id = %s
            """, (profile["printer_id"], owner_id))
            if not cursor.fetchone():
                raise HTTPException(status_code=400, detail="The selected printer does not belong to this shop.")
        cursor.execute("""
            INSERT INTO printing_profiles
                (owner_id, profile_name, printer_id, paper_size, orientation, color_mode, duplex, quality, copies)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            owner_id, profile["profile_name"], profile["printer_id"], profile["paper_size"],
            profile["orientation"], profile["color_mode"], profile["duplex"], profile["quality"], profile["copies"],
        ))
        profile_id = cursor.lastrowid
        db.commit()
        return {"status": "success", "data": {"profile_id": profile_id, **profile}}
    except Exception:
        db.rollback()
        raise
    finally:
        cursor.close()
        db.close()


@app.put("/profiles/{profile_id}")
def update_printing_profile(profile_id: int, payload: PrintingProfilePayload, request: Request):
    owner_id = _owner_id(request)
    _require_matching_owner(payload.owner_id, owner_id)
    profile = _validate_profile(payload)
    db = get_db_connection()
    cursor = db.cursor()
    try:
        cursor.execute(
            "SELECT profile_id FROM printing_profiles WHERE profile_id = %s AND owner_id = %s",
            (profile_id, owner_id),
        )
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Printing profile not found.")
        if profile["printer_id"] is not None:
            cursor.execute("""
                SELECT p.printer_id FROM printers p
                JOIN branches b ON p.branch_id = b.branch_id
                WHERE p.printer_id = %s AND b.owner_id = %s
            """, (profile["printer_id"], owner_id))
            if not cursor.fetchone():
                raise HTTPException(status_code=400, detail="The selected printer does not belong to this shop.")
        cursor.execute("""
            UPDATE printing_profiles
            SET profile_name = %s, printer_id = %s, paper_size = %s, orientation = %s,
                color_mode = %s, duplex = %s, quality = %s, copies = %s
            WHERE profile_id = %s AND owner_id = %s
        """, (
            profile["profile_name"], profile["printer_id"], profile["paper_size"], profile["orientation"],
            profile["color_mode"], profile["duplex"], profile["quality"], profile["copies"],
            profile_id, owner_id,
        ))
        db.commit()
        return {"status": "success", "data": {"profile_id": profile_id, **profile}}
    except Exception:
        db.rollback()
        raise
    finally:
        cursor.close()
        db.close()


@app.delete("/profiles/{profile_id}")
def delete_printing_profile(profile_id: int, request: Request):
    owner_id = _owner_id(request)
    db = get_db_connection()
    cursor = db.cursor()
    try:
        cursor.execute(
            "DELETE FROM printing_profiles WHERE profile_id = %s AND owner_id = %s",
            (profile_id, owner_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Printing profile not found.")
        db.commit()
        return {"status": "success"}
    except Exception:
        db.rollback()
        raise
    finally:
        cursor.close()
        db.close()


@app.get("/reprints/search")
def search_reprint_orders(request: Request, q: str = ""):
    owner_id = _owner_id(request)
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        query = """
            SELECT o.order_id, o.customer_id, o.owner_id, o.filename, o.total_pages,
                   o.bw_pages, o.color_pages, o.total_price, o.order_status, o.created_at,
                   c.name AS customer_name, c.phone AS customer_phone
            FROM orders o
            LEFT JOIN customers c ON c.customer_id = o.customer_id
            WHERE o.owner_id = %s
        """
        params: list = [owner_id]
        if q.strip():
            term = f"%{q.strip()}%"
            query += " AND (o.order_id LIKE %s OR o.filename LIKE %s OR c.name LIKE %s OR c.phone LIKE %s)"
            params.extend([term, term, term, term])
        query += " ORDER BY o.created_at DESC LIMIT 100"
        cursor.execute(query, tuple(params))
        return {"status": "success", "data": cursor.fetchall()}
    finally:
        cursor.close()
        db.close()


@app.get("/orders/{order_id}/documents")
def get_order_documents(order_id: str, request: Request):
    owner_id = _owner_id(request)
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT filename FROM orders WHERE order_id = %s AND owner_id = %s", (order_id, owner_id))
        order = cursor.fetchone()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found for this shop.")
        cursor.execute("""
            SELECT document_id, original_filename, storage_filename, total_pages, bw_pages, color_pages
            FROM order_documents
            WHERE order_id = %s AND file_deleted_at IS NULL
            ORDER BY document_id
        """, (order_id,))
        documents = cursor.fetchall()
        if documents:
            result = [{
                "filename": item["original_filename"],
                "url": f"/files/orders/{order_id}/{item['document_id']}",
                "total_pages": item["total_pages"],
                "bw_pages": item["bw_pages"],
                "color_pages": item["color_pages"],
            } for item in documents]
        else:
            cursor.execute(
                "SELECT COUNT(*) AS document_count FROM order_documents WHERE order_id = %s",
                (order_id,),
            )
            has_document_records = cursor.fetchone()["document_count"] > 0
            legacy_name = os.path.basename(order["filename"] or "")
            legacy_path = os.path.join(UPLOAD_DIR, legacy_name)
            result = [{
                "filename": legacy_name,
                "url": f"/files/legacy/{order_id}",
                "total_pages": None,
                "bw_pages": None,
                "color_pages": None,
            }] if not has_document_records and legacy_name and os.path.isfile(legacy_path) else []
        return {"status": "success", "data": result}
    finally:
        cursor.close()
        db.close()


@app.get("/files/orders/{order_id}/{document_id}")
def download_order_document(order_id: str, document_id: int, request: Request):
    owner_id = _owner_id(request)
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT d.storage_filename, d.original_filename
            FROM order_documents d
            JOIN orders o ON o.order_id = d.order_id
            WHERE d.order_id = %s AND d.document_id = %s AND o.owner_id = %s
              AND d.file_deleted_at IS NULL
        """, (order_id, document_id, owner_id))
        document = cursor.fetchone()
        if not document:
            raise HTTPException(status_code=404, detail="Document not found for this shop.")
        stored_name = os.path.basename(document["storage_filename"])
        file_path = os.path.abspath(os.path.join(UPLOAD_DIR, stored_name))
        if os.path.commonpath([os.path.abspath(UPLOAD_DIR), file_path]) != os.path.abspath(UPLOAD_DIR):
            raise HTTPException(status_code=404, detail="Document not found.")
        if not os.path.isfile(file_path):
            raise HTTPException(status_code=404, detail="The original document is no longer available.")
        return FileResponse(file_path, media_type="application/pdf", filename=Path(document["original_filename"]).name)
    finally:
        cursor.close()
        db.close()


@app.get("/files/legacy/{order_id}")
def download_legacy_order_document(order_id: str, request: Request):
    owner_id = _owner_id(request)
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT filename FROM orders WHERE order_id = %s AND owner_id = %s",
            (order_id, owner_id),
        )
        order = cursor.fetchone()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found for this shop.")
        safe_name = os.path.basename(order["filename"] or "")
        file_path = os.path.abspath(os.path.join(UPLOAD_DIR, safe_name))
        if not safe_name or os.path.commonpath([os.path.abspath(UPLOAD_DIR), file_path]) != os.path.abspath(UPLOAD_DIR):
            raise HTTPException(status_code=404, detail="Document not found.")
        if not os.path.isfile(file_path):
            raise HTTPException(status_code=404, detail="The original document is no longer available.")
        return FileResponse(file_path, media_type="application/pdf", filename=safe_name)
    finally:
        cursor.close()
        db.close()


@app.get("/files/batch/{filename}")
def download_batch_file(filename: str, request: Request):
    owner_id = _owner_id(request)
    if not filename.startswith(f"batch-{owner_id}-") or not filename.endswith(("-bw.pdf", "-color.pdf")):
        raise HTTPException(status_code=404, detail="Batch file not found.")
    if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-." for character in filename):
        raise HTTPException(status_code=404, detail="Batch file not found.")
    file_path = os.path.abspath(os.path.join(UPLOAD_DIR, filename))
    if os.path.commonpath([os.path.abspath(UPLOAD_DIR), file_path]) != os.path.abspath(UPLOAD_DIR):
        raise HTTPException(status_code=404, detail="Batch file not found.")
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Batch file not found.")
    return FileResponse(file_path, media_type="application/pdf", filename=filename)


@app.get("/orders/{order_id}/print-settings")
def get_order_print_settings(order_id: str, request: Request):
    owner_id = _owner_id(request)
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT order_id FROM orders WHERE order_id = %s AND owner_id = %s", (order_id, owner_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Order not found for this shop.")
        cursor.execute("SELECT * FROM order_print_settings WHERE order_id = %s", (order_id,))
        return {"status": "success", "data": cursor.fetchone()}
    finally:
        cursor.close()
        db.close()


@app.post("/orders/{order_id}/tracking-link")
def create_order_tracking_link(order_id: str, request: Request):
    owner_id = _owner_id(request)
    tracking_token = secrets.token_urlsafe(32)
    db = get_db_connection()
    cursor = db.cursor()
    try:
        cursor.execute(
            "UPDATE orders SET tracking_token_hash = %s WHERE order_id = %s AND owner_id = %s",
            (_tracking_token_hash(tracking_token), order_id, owner_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Order not found for this shop.")
        db.commit()
        return {"status": "success", "tracking_token": tracking_token}
    except Exception:
        db.rollback()
        raise
    finally:
        cursor.close()
        db.close()


@app.post("/orders/{order_id}/confirm-settings")
def confirm_order_print_settings(order_id: str, payload: PrintSettingsConfirmation, http_request: Request):
    owner_id = _owner_id(http_request)
    _require_matching_owner(payload.owner_id, owner_id)
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT order_id FROM orders WHERE order_id = %s AND owner_id = %s", (order_id, owner_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Order not found for this shop.")
        cursor.execute(
            "SELECT owner_confirmed FROM order_print_settings WHERE order_id = %s",
            (order_id,),
        )
        settings_row = cursor.fetchone()
        if not settings_row:
            raise HTTPException(status_code=404, detail="No saved print preferences are available to confirm.")
        if not settings_row["owner_confirmed"]:
            cursor.execute("UPDATE order_print_settings SET owner_confirmed = TRUE WHERE order_id = %s", (order_id,))
            cursor.execute(
                "INSERT INTO order_history (order_id, owner_id, action, status, notes) VALUES (%s, %s, %s, %s, %s)",
                (order_id, owner_id, "Print Settings Confirmed", "Confirmed", "Owner confirmed print preferences; no printer job was started."),
            )
        cursor.execute("SELECT * FROM order_print_settings WHERE order_id = %s", (order_id,))
        settings = cursor.fetchone()
        db.commit()
        return {"status": "success", "data": settings}
    except Exception:
        db.rollback()
        raise
    finally:
        cursor.close()
        db.close()


@app.get("/orders/{order_id}/reprint")
def get_reprint_details(order_id: str, request: Request, profile_id: int | None = None):
    owner_id = _owner_id(request)
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT order_id, customer_id, owner_id, branch_id, filename, total_pages,
                   bw_pages, color_pages, total_price, payment_status, order_status,
                   estimated_completion, created_at
            FROM orders WHERE order_id = %s AND owner_id = %s
        """, (order_id, owner_id))
        order = cursor.fetchone()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found for this shop.")
        cursor.execute(
            "SELECT document_id, original_filename, sha256, total_pages, bw_pages, color_pages FROM order_documents WHERE order_id = %s AND file_deleted_at IS NULL",
            (order_id,),
        )
        documents = cursor.fetchall()
        cursor.execute(
            "SELECT storage_filename FROM order_documents WHERE order_id = %s AND file_deleted_at IS NULL",
            (order_id,),
        )
        stored_files = cursor.fetchall()
        cursor.execute(
            "SELECT COUNT(*) AS document_count FROM order_documents WHERE order_id = %s",
            (order_id,),
        )
        has_document_records = cursor.fetchone()["document_count"] > 0
        cursor.execute("SELECT * FROM order_print_settings WHERE order_id = %s", (order_id,))
        settings = cursor.fetchone()
        settings_available = settings is not None
        if profile_id is not None:
            cursor.execute(
                "SELECT * FROM printing_profiles WHERE profile_id = %s AND owner_id = %s",
                (profile_id, owner_id),
            )
            settings = cursor.fetchone()
            if not settings:
                raise HTTPException(status_code=404, detail="Printing profile not found for this shop.")
        if settings is None:
            settings = {
                "profile_id": None, "paper_size": "A4", "orientation": "Portrait",
                "color_mode": "Auto", "duplex": False, "quality": "Standard", "copies": 1,
            }
        source_files_available = bool(stored_files) and all(
            os.path.isfile(os.path.join(UPLOAD_DIR, os.path.basename(item["storage_filename"])))
            for item in stored_files
        )
        if not stored_files and not has_document_records:
            source_files_available = bool(
                order["filename"]
                and os.path.isfile(os.path.join(UPLOAD_DIR, os.path.basename(order["filename"])))
            )
        copies = int(settings["copies"])
        estimated_total_pages = int(order["total_pages"] or 0) * copies
        if settings["color_mode"] == "B&W":
            estimated_bw_pages, estimated_color_pages = estimated_total_pages, 0
        else:
            estimated_bw_pages = int(order["bw_pages"] or 0) * copies
            estimated_color_pages = int(order["color_pages"] or 0) * copies
        estimated_total_price = estimated_bw_pages * 2.00 + estimated_color_pages * 10.00
        return {
            "status": "success",
            "data": {
                "order": order,
                "documents": documents,
                "settings": settings,
                "settings_available": settings_available,
                "source_files_available": source_files_available,
                "estimated_total_pages": estimated_total_pages,
                "estimated_total_price": estimated_total_price,
            },
        }
    finally:
        cursor.close()
        db.close()


@app.post("/orders/{order_id}/reprint")
def create_smart_reprint(
    order_id: str,
    payload: ReprintRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
):
    owner_id = _owner_id(http_request)
    _require_matching_owner(payload.owner_id, owner_id)
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    copied_paths = []
    qr_path = None
    try:
        cursor.execute(
            "SELECT * FROM orders WHERE order_id = %s AND owner_id = %s",
            (order_id, owner_id),
        )
        source_order = cursor.fetchone()
        if not source_order:
            raise HTTPException(status_code=404, detail="Order not found for this shop.")
        cursor.execute(
            "SELECT * FROM order_documents WHERE order_id = %s AND file_deleted_at IS NULL ORDER BY document_id",
            (order_id,),
        )
        stored_documents = cursor.fetchall()
        cursor.execute(
            "SELECT COUNT(*) AS document_count FROM order_documents WHERE order_id = %s",
            (order_id,),
        )
        has_document_records = cursor.fetchone()["document_count"] > 0

        source_documents = []
        if stored_documents:
            for item in stored_documents:
                safe_name = os.path.basename(item["storage_filename"])
                source_path = os.path.join(UPLOAD_DIR, safe_name)
                if not os.path.isfile(source_path):
                    raise HTTPException(status_code=404, detail=f"Original file {item['original_filename']} is no longer available.")
                with open(source_path, "rb") as source_file:
                    data = source_file.read()
                source_documents.append((item["original_filename"], data, json.loads(item["analysis_json"])))
        elif has_document_records:
            raise HTTPException(status_code=404, detail="The original documents have expired and are no longer available.")
        else:
            legacy_name = os.path.basename(source_order["filename"] or "")
            legacy_path = os.path.join(UPLOAD_DIR, legacy_name)
            if not legacy_name or not os.path.isfile(legacy_path):
                raise HTTPException(status_code=404, detail="The original document is no longer available for reprinting.")
            with open(legacy_path, "rb") as source_file:
                data = source_file.read()
            source_documents.append((legacy_name, data, analyze_pdf(data, legacy_name)))

        cursor.execute("SELECT * FROM order_print_settings WHERE order_id = %s", (order_id,))
        settings = cursor.fetchone()
        if payload.profile_id is not None:
            cursor.execute(
                "SELECT * FROM printing_profiles WHERE profile_id = %s AND owner_id = %s",
                (payload.profile_id, owner_id),
            )
            settings = cursor.fetchone()
            if not settings:
                raise HTTPException(status_code=404, detail="Printing profile not found for this shop.")
        settings = settings or {
            "profile_id": None, "paper_size": "A4", "orientation": "Portrait",
            "color_mode": "Auto", "duplex": False, "quality": "Standard", "copies": 1,
        }
        copies = int(settings["copies"])
        total_pages = int(source_order["total_pages"] or 0) * copies
        if settings["color_mode"] == "B&W":
            bw_pages, color_pages = total_pages, 0
        else:
            bw_pages = int(source_order["bw_pages"] or 0) * copies
            color_pages = int(source_order["color_pages"] or 0) * copies
        total_price = (bw_pages * 2.00) + (color_pages * 10.00)

        new_order_id = None
        for _ in range(10):
            candidate = generate_order_id()
            cursor.execute("SELECT order_id FROM orders WHERE order_id = %s", (candidate,))
            if not cursor.fetchone():
                new_order_id = candidate
                break
        if not new_order_id:
            raise HTTPException(status_code=503, detail="Could not allocate a unique order ID. Please try again.")

        pickup_pin = generate_pin()
        tracking_token = secrets.token_urlsafe(32)
        new_storage_names = []
        for original_name, data, analysis in source_documents:
            storage_name = f"{uuid.uuid4().hex}.pdf"
            target_path = os.path.join(UPLOAD_DIR, storage_name)
            with open(target_path, "wb") as target_file:
                target_file.write(data)
            copied_paths.append(target_path)
            new_storage_names.append((original_name, storage_name, analysis))

        cursor.execute("""
            INSERT INTO orders (order_id, customer_id, owner_id, branch_id, filename, total_pages, bw_pages, color_pages, total_price, payment_status, order_status, estimated_completion, tracking_token_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            new_order_id, source_order["customer_id"], source_order["owner_id"], source_order["branch_id"],
            ", ".join(item[0] for item in source_documents)[:255], total_pages, bw_pages, color_pages,
            total_price, "Pending", "Waiting", datetime.now() + timedelta(hours=2),
            _tracking_token_hash(tracking_token),
        ))
        cursor.execute(
            "INSERT INTO order_history (order_id, owner_id, action, status, notes) VALUES (%s, %s, %s, %s, %s)",
            (new_order_id, owner_id, "Smart Reprint", "Waiting", f"Reprinted from {order_id}"),
        )
        for original_name, storage_name, analysis in new_storage_names:
            cursor.execute("""
                INSERT INTO order_documents (
                    order_id, original_filename, storage_filename, sha256,
                    total_pages, bw_pages, color_pages, analysis_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                new_order_id, Path(original_name).name[:255], storage_name, analysis["sha256"],
                analysis["total_pages"], analysis["bw_pages"], analysis["color_pages"],
                json.dumps(analysis, ensure_ascii=False),
            ))
        cursor.execute("""
            INSERT INTO order_print_settings
                (order_id, profile_id, printer_id, paper_size, orientation, color_mode, duplex, quality, copies, owner_confirmed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
        """, (
            new_order_id, settings.get("profile_id"), settings.get("printer_id"), settings["paper_size"],
            settings["orientation"], settings["color_mode"], settings["duplex"], settings["quality"],
            settings["copies"],
        ))
        _qr_for_order(new_order_id, pickup_pin, owner_id, cursor)
        qr_path = os.path.join(QRCODE_DIR, f"{new_order_id}.png")
        db.commit()
    except Exception:
        db.rollback()
        for copied_path in copied_paths:
            if os.path.exists(copied_path):
                os.remove(copied_path)
        if qr_path and os.path.exists(qr_path):
            os.remove(qr_path)
        raise
    finally:
        cursor.close()
        db.close()

    background_tasks.add_task(manager.broadcast, {
        "type": "new_order", "owner_id": owner_id, "order_id": new_order_id, "status": "Waiting",
    })
    return {
        "status": "success",
        "order_id": new_order_id,
        "tracking_token": tracking_token,
        "pickup_pin": pickup_pin,
        "total_price": total_price,
        "total_pages": total_pages,
        "bw_pages": bw_pages,
        "color_pages": color_pages,
        "source_order_id": order_id,
        "settings": settings,
        "payment_status": "Pending",
    }

@app.get("/orders/")
def get_all_orders(request: Request):
    owner_id = _owner_id(request)
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT order_id, customer_id, owner_id, branch_id, filename, total_pages,
                   bw_pages, color_pages, total_price, payment_status, order_status,
                   estimated_completion, created_at
            FROM orders WHERE owner_id = %s ORDER BY created_at DESC
            """,
            (owner_id,),
        )
        orders = cursor.fetchall()
        db.close()
        return {"status": "success", "data": orders}
    except HTTPException:
        raise
    except Exception:
        return _internal_error_response("loading orders")

@app.get("/orders/{order_id}")
def get_order(order_id: str, request: Request, tracking_token: str | None = None):
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        owner_id = getattr(request.state, "owner_id", None)
        if owner_id:
            cursor.execute(
                """
                SELECT order_id, customer_id, owner_id, branch_id, filename, total_pages,
                       bw_pages, color_pages, total_price, payment_status, order_status,
                       estimated_completion, created_at
                FROM orders WHERE order_id = %s AND owner_id = %s
                """,
                (order_id, owner_id),
            )
        else:
            owner_id = _customer_tracking_owner(cursor, order_id, tracking_token)
            cursor.execute(
                """
                SELECT order_id, customer_id, owner_id, branch_id, filename, total_pages,
                       bw_pages, color_pages, total_price, payment_status, order_status,
                       estimated_completion, created_at
                FROM orders WHERE order_id = %s AND owner_id = %s
                """,
                (order_id, owner_id),
            )
        order = cursor.fetchone()
        if order:
            return {"status": "success", "data": order}
        return {"status": "error", "message": "Order not found"}
    finally:
        cursor.close()
        db.close()

class MergeRequest(BaseModel):
    order_ids: List[str]

@app.post("/merge/")
def merge_documents(payload: MergeRequest, request: Request):
    owner_id = _owner_id(request)
    if not payload.order_ids:
        raise HTTPException(status_code=400, detail="Select at least one order for batch printing.")
    if len(payload.order_ids) > 100:
        raise HTTPException(status_code=400, detail="Select no more than 100 orders for one batch.")
    placeholders = ",".join(["%s"] * len(payload.order_ids))
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    selected_order_ids = []
    try:
        cursor.execute(
            f"SELECT order_id, filename, color_pages FROM orders WHERE owner_id = %s AND order_id IN ({placeholders})",
            (owner_id, *payload.order_ids),
        )
        orders = cursor.fetchall()
        if len(orders) != len(set(payload.order_ids)):
            raise HTTPException(status_code=404, detail="One or more selected orders were not found for this shop.")
        selected_order_ids = [order["order_id"] for order in orders]
        source_files = []
        for order in orders:
            cursor.execute(
                "SELECT original_filename, storage_filename FROM order_documents WHERE order_id = %s AND file_deleted_at IS NULL ORDER BY document_id",
                (order["order_id"],),
            )
            documents = cursor.fetchall()
            if documents:
                paths = [
                    (item["original_filename"], os.path.join(UPLOAD_DIR, os.path.basename(item["storage_filename"])))
                    for item in documents
                ]
            else:
                cursor.execute(
                    "SELECT COUNT(*) AS document_count FROM order_documents WHERE order_id = %s",
                    (order["order_id"],),
                )
                if cursor.fetchone()["document_count"]:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Original file(s) for order {order['order_id']} have expired.",
                    )
                legacy_name = os.path.basename(order["filename"] or "")
                paths = [(legacy_name, os.path.join(UPLOAD_DIR, legacy_name))] if legacy_name else []
            if not paths or any(not os.path.isfile(path) for _, path in paths):
                raise HTTPException(status_code=404, detail=f"Original file(s) for order {order['order_id']} are unavailable.")
            cursor.execute("SELECT color_mode FROM order_print_settings WHERE order_id = %s", (order["order_id"],))
            settings = cursor.fetchone()
            source_files.extend((path, settings and settings["color_mode"] == "B&W") for _, path in paths)
    finally:
        cursor.close()
        db.close()

    master_bw = pymupdf.open()
    master_color = pymupdf.open()
    has_bw, has_color = False, False
    generated_files = []
    try:
        for file_path, forced_bw in source_files:
            with open(file_path, "rb") as source_file:
                document_data = source_file.read()
            analysis = analyze_pdf(document_data, os.path.basename(file_path))
            document = pymupdf.open(stream=document_data, filetype="pdf")
            for page_num, page_result in enumerate(analysis["pages"]):
                is_bw = forced_bw or not page_result["color"]
                target = master_bw if is_bw else master_color
                target.insert_pdf(document, from_page=page_num, to_page=page_num)
                has_bw = has_bw or is_bw
                has_color = has_color or not is_bw
            document.close()

        urls = []
        export_id = uuid.uuid4().hex
        if has_bw:
            filename = f"batch-{owner_id}-{export_id}-bw.pdf"
            master_bw.save(os.path.join(UPLOAD_DIR, filename))
            generated_files.append(filename)
            urls.append(f"/files/batch/{filename}")
        if has_color:
            filename = f"batch-{owner_id}-{export_id}-color.pdf"
            master_color.save(os.path.join(UPLOAD_DIR, filename))
            generated_files.append(filename)
            urls.append(f"/files/batch/{filename}")
        if generated_files:
            tracking_db = get_db_connection()
            tracking_cursor = None
            try:
                tracking_cursor = tracking_db.cursor()
                tracking_cursor.execute(
                    "INSERT INTO batch_exports (export_id, owner_id) VALUES (%s, %s)",
                    (export_id, owner_id),
                )
                tracking_cursor.executemany(
                    "INSERT INTO batch_export_files (filename, export_id) VALUES (%s, %s)",
                    [(filename, export_id) for filename in generated_files],
                )
                tracking_cursor.executemany(
                    "INSERT INTO batch_export_orders (export_id, order_id) VALUES (%s, %s)",
                    [(export_id, order_id) for order_id in selected_order_ids],
                )
                tracking_db.commit()
            except Exception:
                tracking_db.rollback()
                raise
            finally:
                if tracking_cursor:
                    tracking_cursor.close()
                tracking_db.close()
        return {"status": "success", "urls": urls}
    except Exception:
        for filename in generated_files:
            _delete_upload_file(filename)
        raise
    finally:
        master_bw.close()
        master_color.close()

@app.delete("/orders/{order_id}")
def complete_and_delete_order(order_id: str, background_tasks: BackgroundTasks, request: Request):
    owner_id = _owner_id(request)
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT owner_id, order_status FROM orders WHERE order_id = %s AND owner_id = %s",
            (order_id, owner_id),
        )
        order = cursor.fetchone()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found.")
        if order["order_status"] != "Completed":
            cursor.execute(
                "UPDATE orders SET order_status = 'Completed', completed_at = CURRENT_TIMESTAMP WHERE order_id = %s AND owner_id = %s",
                (order_id, owner_id),
            )
            cursor.execute(
                "INSERT INTO order_history (order_id, owner_id, action, status) VALUES (%s, %s, %s, %s)",
                (order_id, order["owner_id"], "Owner Confirmed Completion", "Completed"),
            )
            db.commit()
            background_tasks.add_task(manager.broadcast, {
                "type": "order_completed",
                "owner_id": owner_id,
                "order_id": order_id,
                "status": "Completed",
            })
        return {"status": "success"}
    except Exception:
        db.rollback()
        raise
    finally:
        cursor.close()
        db.close()

# Customer tracking page
@app.get("/track/{order_id}")
def track_order(order_id: str):
    return FileResponse(FRONTEND_INDEX)


@app.get("/qr/{order_id}")
def get_order_qr(order_id: str, request: Request, tracking_token: str | None = None):
    session_owner_id = _decode_session(request.cookies.get(SESSION_COOKIE_NAME))
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        if session_owner_id:
            cursor.execute(
                "SELECT owner_id, qr_image_path FROM qr_codes WHERE order_id = %s AND owner_id = %s",
                (order_id, session_owner_id),
            )
        else:
            customer_owner_id = _customer_tracking_owner(cursor, order_id, tracking_token)
            cursor.execute(
                "SELECT owner_id, qr_image_path FROM qr_codes WHERE order_id = %s AND owner_id = %s",
                (order_id, customer_owner_id),
            )
        qr_record = cursor.fetchone()
        if not qr_record:
            raise HTTPException(status_code=404, detail="Pickup QR code not found.")
        file_path = os.path.abspath(qr_record["qr_image_path"])
        if os.path.commonpath([os.path.abspath(QRCODE_DIR), file_path]) != os.path.abspath(QRCODE_DIR):
            raise HTTPException(status_code=404, detail="Pickup QR code not found.")
        if not os.path.isfile(file_path):
            raise HTTPException(status_code=404, detail="Pickup QR code not found.")
        return FileResponse(file_path, media_type="image/png", filename=f"smartprint-{order_id}.png")
    finally:
        cursor.close()
        db.close()

# Owner dashboard
@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/dashboard/", response_class=HTMLResponse)
def dashboard():
    return FileResponse(FRONTEND_INDEX)

@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin.html", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
def frontend_legacy_routes():
    return FileResponse(FRONTEND_INDEX)

# Branch management endpoints
@app.post("/branches/")
def create_branch(
    request: Request,
    branch_name: str = Form(...),
    address: str = Form(""),
    owner_id: int = Form(...),
):
    owner_id = _owner_id(request)
    try:
        branch_code = f"BR{random.randint(100, 999)}"
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("INSERT INTO branches (owner_id, branch_name, branch_code, address) VALUES (%s, %s, %s, %s)", 
                      (owner_id, branch_name, branch_code, address))
        db.commit()
        db.close()
        return {"status": "success", "message": "Branch created successfully!", "branch_code": branch_code}
    except HTTPException:
        raise
    except Exception:
        return _internal_error_response("creating a branch")

@app.post("/printers/")
def add_printer(
    request: Request,
    printer_name: str = Form(...),
    model: str = Form(""),
    branch_id: int = Form(...),
):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        owner_id = _owner_id(request)
        cursor.execute("SELECT branch_id FROM branches WHERE branch_id = %s AND owner_id = %s", (branch_id, owner_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Branch not found for this shop.")
        cursor.execute("INSERT INTO printers (branch_id, printer_name, model) VALUES (%s, %s, %s)", 
                      (branch_id, printer_name, model))
        db.commit()
        db.close()
        
        # Broadcast new printer
        asyncio.create_task(manager.broadcast({
            "type": "new_printer",
            "owner_id": owner_id,
            "printer_name": printer_name,
            "branch_id": branch_id
        }))
        
        return {"status": "success", "message": "Printer added successfully!"}
    except HTTPException:
        raise
    except Exception:
        return _internal_error_response("registering a printer")

@app.get("/inventory/")
def get_inventory(request: Request, branch_id: int = None):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        owner_id = _owner_id(request)
        if branch_id:
            cursor.execute(
                "SELECT i.* FROM inventory i JOIN branches b ON b.branch_id = i.branch_id WHERE i.branch_id = %s AND b.owner_id = %s",
                (branch_id, owner_id),
            )
        else:
            cursor.execute(
                "SELECT i.* FROM inventory i JOIN branches b ON b.branch_id = i.branch_id WHERE b.owner_id = %s",
                (owner_id,),
            )
        inventory = cursor.fetchall()
        db.close()
        return {"status": "success", "data": inventory}
    except HTTPException:
        raise
    except Exception:
        return _internal_error_response("loading inventory")

@app.post("/inventory/")
def update_inventory(request: Request, inventory_id: int = Form(...), current_stock: int = Form(...)):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        owner_id = _owner_id(request)
        cursor.execute("""
            SELECT i.inventory_id FROM inventory i
            JOIN branches b ON b.branch_id = i.branch_id
            WHERE i.inventory_id = %s AND b.owner_id = %s
        """, (inventory_id, owner_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Inventory item not found for this shop.")
        cursor.execute(
            "UPDATE inventory i JOIN branches b ON b.branch_id = i.branch_id SET i.current_stock = %s WHERE i.inventory_id = %s AND b.owner_id = %s",
            (current_stock, inventory_id, owner_id),
        )
        db.commit()
        db.close()
        return {"status": "success", "message": "Inventory updated successfully!"}
    except HTTPException:
        raise
    except Exception:
        return _internal_error_response("updating inventory")

# Notification endpoints
@app.post("/notifications/send")
def send_notification(
    request: Request,
    user_type: str = Form(...),
    user_id: int = Form(...),
    message: str = Form(...),
    type: str = Form("system"),
):
    try:
        owner_id = _owner_id(request)
        if user_type != "owner" or user_id != owner_id:
            raise HTTPException(status_code=403, detail="Notifications may only be sent to this shop owner.")
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("INSERT INTO notifications (user_type, user_id, message, type) VALUES (%s, %s, %s, %s)", 
                      (user_type, user_id, message, type))
        db.commit()
        db.close()
        return {"status": "success", "message": "Notification sent!"}
    except HTTPException:
        raise
    except Exception:
        return _internal_error_response("creating a notification")

@app.get("/notifications/{user_type}/{user_id}")
def get_notifications(user_type: str, user_id: int, request: Request):
    try:
        owner_id = _owner_id(request)
        if user_type != "owner" or user_id != owner_id:
            raise HTTPException(status_code=403, detail="Notifications are only available to this shop owner.")
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM notifications WHERE user_type = %s AND user_id = %s ORDER BY created_at DESC", 
                      (user_type, user_id))
        notifications = cursor.fetchall()
        db.close()
        return {"status": "success", "data": notifications}
    except HTTPException:
        raise
    except HTTPException:
        raise
    except Exception:
        return _internal_error_response("loading notifications")

# WhatsApp notification endpoint
@app.post("/whatsapp/send")
def send_whatsapp_notification(
    request: Request,
    order_id: str = Form(...),
    phone: str = Form(...),
    message_type: str = Form(...),
):
    _owner_id(request)
    raise HTTPException(status_code=503, detail="WhatsApp delivery is not configured.")

# Email notification endpoint
@app.post("/email/send")
def send_email_notification(
    request: Request,
    order_id: str = Form(...),
    email: str = Form(...),
    type: str = Form(...),
):
    _owner_id(request)
    raise HTTPException(status_code=503, detail="Email delivery is not configured.")

# Progress timeline
@app.get("/progress/{order_id}")
def get_progress(order_id: str, request: Request, tracking_token: str | None = None):
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        owner_id = getattr(request.state, "owner_id", None)
        if owner_id:
            cursor.execute(
                "SELECT order_id FROM orders WHERE order_id = %s AND owner_id = %s",
                (order_id, owner_id),
            )
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="Order not found.")
        else:
            owner_id = _customer_tracking_owner(cursor, order_id, tracking_token)
        cursor.execute(
            "SELECT action, status, timestamp FROM order_history WHERE order_id = %s AND owner_id = %s ORDER BY timestamp ASC",
            (order_id, owner_id),
        )
        history = cursor.fetchall()
        return {"status": "success", "data": history}
    finally:
        cursor.close()
        db.close()

# Analytics endpoints
@app.get("/analytics/revenue")
def get_revenue_analytics(request: Request, period: str = "monthly", branch_id: int = None):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        owner_id = _owner_id(request)
        branch_clause = " AND branch_id = %s" if branch_id else ""
        params = (owner_id, branch_id) if branch_id else (owner_id,)
        if period == "daily":
            cursor.execute("""
                SELECT DATE(created_at) as date, SUM(total_price) as daily_revenue, COUNT(order_id) as order_count
                FROM orders WHERE owner_id = %s AND order_status = 'Completed'
                """ + branch_clause + """
                GROUP BY DATE(created_at) ORDER BY date DESC LIMIT 30
            """, params)
        elif period == "monthly":
            cursor.execute("""
                SELECT MONTH(created_at) as month, YEAR(created_at) as year, SUM(total_price) as monthly_revenue, COUNT(order_id) as order_count
                FROM orders WHERE owner_id = %s AND order_status = 'Completed'
                """ + branch_clause + """
                GROUP BY YEAR(created_at), MONTH(created_at) ORDER BY year DESC, month DESC LIMIT 12
            """, params)
        elif period == "yearly":
            cursor.execute("""
                SELECT YEAR(created_at) as year, SUM(total_price) as yearly_revenue, COUNT(order_id) as order_count
                FROM orders WHERE owner_id = %s AND order_status = 'Completed'
                """ + branch_clause + """
                GROUP BY YEAR(created_at) ORDER BY year DESC LIMIT 5
            """, params)
        else:
            raise HTTPException(status_code=400, detail="Period must be daily, monthly, or yearly.")
        data = cursor.fetchall()
        db.close()
        return {"status": "success", "data": data, "period": period}
    except HTTPException:
        raise
    except Exception:
        return _internal_error_response("loading revenue analytics")

@app.get("/analytics/orders-trend")
def get_orders_trend(request: Request, period: str = "monthly", branch_id: int = None):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        owner_id = _owner_id(request)
        branch_clause = " AND branch_id = %s" if branch_id else ""
        params = (owner_id, branch_id) if branch_id else (owner_id,)
        if period == "daily":
            cursor.execute("""
                SELECT DATE(created_at) as date, COUNT(order_id) as order_count
                FROM orders WHERE owner_id = %s
                """ + branch_clause + """
                GROUP BY DATE(created_at) ORDER BY date DESC LIMIT 30
            """, params)
        elif period == "monthly":
            cursor.execute("""
                SELECT MONTH(created_at) as month, YEAR(created_at) as year, COUNT(order_id) as order_count
                FROM orders WHERE owner_id = %s
                """ + branch_clause + """
                GROUP BY YEAR(created_at), MONTH(created_at) ORDER BY year DESC, month DESC LIMIT 12
            """, params)
        elif period == "yearly":
            cursor.execute("""
                SELECT YEAR(created_at) as year, COUNT(order_id) as order_count
                FROM orders WHERE owner_id = %s
                """ + branch_clause + """
                GROUP BY YEAR(created_at) ORDER BY year DESC LIMIT 5
            """, params)
        else:
            raise HTTPException(status_code=400, detail="Period must be daily, monthly, or yearly.")
        data = cursor.fetchall()
        db.close()
        return {"status": "success", "data": data, "period": period}
    except HTTPException:
        raise
    except Exception:
        return _internal_error_response("loading order trend analytics")

@app.get("/analytics/color-bw-ratio")
def get_color_bw_ratio(request: Request, branch_id: int = None):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        owner_id = _owner_id(request)
        query = """
            SELECT 
                SUM(color_pages) as total_color,
                SUM(bw_pages) as total_bw,
                SUM(total_pages) as total_pages,
                COUNT(order_id) as order_count
            FROM orders WHERE owner_id = %s
        """
        params = [owner_id]
        if branch_id:
            query += " AND branch_id = %s"
            params.append(branch_id)
        cursor.execute(query, params)
        result = cursor.fetchone()
        db.close()
        
        total_color = result['total_color'] or 0
        total_bw = result['total_bw'] or 0
        total_pages = result['total_pages'] or 0
        
        return {
            "status": "success", 
            "data": {
                "total_color_pages": total_color,
                "total_bw_pages": total_bw,
                "total_pages": total_pages,
                "color_ratio": round((total_color / total_pages * 100) if total_pages > 0 else 0, 2),
                "bw_ratio": round((total_bw / total_pages * 100) if total_pages > 0 else 0, 2)
            }
        }
    except HTTPException:
        raise
    except Exception:
        return _internal_error_response("loading print ratio analytics")

@app.get("/analytics/inventory")
def get_inventory_analytics(request: Request, branch_id: int = None):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        owner_id = _owner_id(request)
        if branch_id:
            cursor.execute("""
                SELECT i.* FROM inventory i
                JOIN branches b ON b.branch_id = i.branch_id
                WHERE i.branch_id = %s AND b.owner_id = %s
            """, (branch_id, owner_id))
        else:
            cursor.execute("""
                SELECT i.* FROM inventory i
                JOIN branches b ON b.branch_id = i.branch_id
                WHERE b.owner_id = %s
            """, (owner_id,))
        inventory = cursor.fetchall()
        db.close()
        
        low_stock = [item for item in inventory if item['current_stock'] <= item['minimum_stock']]
        
        return {
            "status": "success", 
            "data": {
                "items": inventory,
                "low_stock_count": len(low_stock),
                "low_stock_items": low_stock
            }
        }
    except HTTPException:
        raise
    except Exception:
        return _internal_error_response("loading inventory analytics")

@app.get("/printers/status")
def get_printers_status(request: Request):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        owner_id = _owner_id(request)
        cursor.execute("""
            SELECT p.* FROM printers p
            JOIN branches b ON b.branch_id = p.branch_id
            WHERE b.owner_id = %s
        """, (owner_id,))
        printers = cursor.fetchall()
        db.close()
        return {"status": "success", "data": printers}
    except HTTPException:
        raise
    except Exception:
        return _internal_error_response("loading printer status")

# Order search endpoints
@app.get("/search/orders/{search_type}/{value}")
def search_orders(search_type: str, value: str, request: Request):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        owner_id = _owner_id(request)
        query = "SELECT * FROM orders WHERE owner_id = %s AND "
        params = [owner_id]
        if search_type == "order_id":
            query += "order_id = %s"
        elif search_type == "phone":
            query += "customer_id IN (SELECT customer_id FROM customers WHERE phone = %s AND owner_id = %s)"
        elif search_type == "pin":
            query += "order_id = %s"
        else:
            raise HTTPException(status_code=400, detail="Unsupported order search type.")

        params.append(value)
        if search_type == "phone":
            params.append(owner_id)
        
        cursor.execute(query, tuple(params))
        orders = cursor.fetchall()
        db.close()
        return {"status": "success", "data": orders}
    except HTTPException:
        raise
    except Exception:
        return _internal_error_response("searching orders")

# Background task for real-time updates
@app.on_event("startup")
async def startup_event():
    # Start background broadcast task
    asyncio.create_task(background_broadcast())
    asyncio.create_task(file_retention_worker())


async def file_retention_worker():
    while True:
        try:
            removed = await asyncio.to_thread(cleanup_expired_files)
            if removed["documents"] or removed["batch_exports"]:
                logger.info("Expired print files were removed: %s", removed)
        except Exception:
            logger.exception("Scheduled PDF retention cleanup failed.")
        await asyncio.sleep(60 * 60)

async def background_broadcast():
    """Background task that periodically broadcasts status updates"""
    while True:
        await asyncio.sleep(5)
        # Could broadcast server status, etc.
        pass

os.makedirs(FRONTEND_DIST, exist_ok=True)
app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)