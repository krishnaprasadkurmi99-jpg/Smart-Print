import json
import logging


logger = logging.getLogger(__name__)


class RequestBodyTooLarge(Exception):
    pass


class UploadRequestSizeLimitMiddleware:
    def __init__(self, app, *, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") not in {"/upload/", "/analyze/"}
        ):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                declared_length = int(content_length)
            except ValueError:
                declared_length = None
            if declared_length is not None and declared_length > self.max_bytes:
                self._log_limit(scope, declared_length)
                await self._send_too_large(send)
                return

        received_bytes = 0

        async def limited_receive():
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_bytes:
                    self._log_limit(scope, received_bytes)
                    raise RequestBodyTooLarge
            return message

        response_started = False

        async def tracked_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except RequestBodyTooLarge:
            if not response_started:
                await self._send_too_large(send)

    def _log_limit(self, scope, observed_bytes: int) -> None:
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"
        logger.warning(
            "security_event event=upload_request_too_large client_ip=%s "
            "path=%s observed_bytes=%d limit_bytes=%d",
            client_ip,
            scope.get("path", "unknown"),
            observed_bytes,
            self.max_bytes,
        )

    @staticmethod
    async def _send_too_large(send) -> None:
        body = json.dumps({"detail": "The total upload request exceeds the configured size limit."}).encode()
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})
