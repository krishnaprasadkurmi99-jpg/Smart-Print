import asyncio
import io
import logging
import os
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ["SMARTPRINT_ENV"] = "development"

import pymupdf
from fastapi import HTTPException, UploadFile
from starlette.requests import Request

import main
from upload_security import UploadRequestSizeLimitMiddleware


def make_request(path="/upload/", ip="192.0.2.10"):
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "client": (ip, 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
            "http_version": "1.1",
        }
    )


def make_upload(content=b"pdf bytes", filename="document.pdf"):
    return UploadFile(filename=filename, file=io.BytesIO(content))


class SharedRateLimitStore:
    def __init__(self):
        self.attempts = {}


class RateLimitCursor:
    def __init__(self, store):
        self.store = store
        self.result = None

    def execute(self, query, params=()):
        if query.strip().startswith("INSERT INTO security_rate_limits"):
            key = params[0]
            self.store.attempts[key] = self.store.attempts.get(key, 0) + 1
        elif "SELECT attempts" in query:
            window_seconds, key = params
            self.result = {
                "attempts": self.store.attempts[key],
                "retry_after": window_seconds,
            }

    def fetchone(self):
        return self.result

    def close(self):
        pass


class RateLimitConnection:
    def __init__(self, store):
        self.store = store

    def cursor(self, dictionary=False):
        return RateLimitCursor(self.store)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class LoginCursor:
    def __init__(self, user):
        self.user = user

    def execute(self, query, params=()):
        pass

    def fetchone(self):
        return self.user

    def close(self):
        pass


class LoginConnection:
    def __init__(self, user):
        self.user = user

    def cursor(self, dictionary=False):
        return LoginCursor(self.user)

    def close(self):
        pass


class UploadMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def run_middleware(self, headers, chunks, max_bytes=5):
        app_called = False

        async def app(scope, receive, send):
            nonlocal app_called
            app_called = True
            while True:
                message = await receive()
                if not message.get("more_body"):
                    break

        messages = []

        async def receive():
            return chunks.pop(0)

        async def send(message):
            messages.append(message)

        middleware = UploadRequestSizeLimitMiddleware(app, max_bytes=max_bytes)
        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/upload/",
                "headers": headers,
                "client": ("192.0.2.10", 1234),
            },
            receive,
            send,
        )
        return app_called, messages

    async def test_declared_oversized_request_returns_413_without_reading_body(self):
        called, messages = await self.run_middleware(
            [(b"content-length", b"6")], [{"type": "http.request", "body": b"abcdef"}]
        )

        self.assertFalse(called)
        self.assertEqual(messages[0]["status"], 413)

    async def test_streamed_oversized_request_returns_413(self):
        with self.assertLogs("upload_security", level=logging.WARNING) as logs:
            called, messages = await self.run_middleware(
                [],
                [
                    {"type": "http.request", "body": b"abc", "more_body": True},
                    {"type": "http.request", "body": b"def", "more_body": False},
                ],
            )

        self.assertTrue(called)
        self.assertEqual(messages[0]["status"], 413)
        self.assertIn("event=upload_request_too_large", logs.output[0])

    async def test_request_at_limit_is_allowed(self):
        called, messages = await self.run_middleware(
            [(b"content-length", b"5")],
            [{"type": "http.request", "body": b"abcde", "more_body": False}],
        )

        self.assertTrue(called)
        self.assertEqual(messages, [])


class UploadProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def test_per_file_limit_returns_413_and_closes_upload(self):
        upload = make_upload(b"123456")

        with patch.object(main, "MAX_UPLOAD_FILE_BYTES", 5):
            with tempfile.TemporaryDirectory() as working_directory:
                with self.assertLogs(main.logger, level=logging.WARNING) as logs:
                    with self.assertRaises(HTTPException) as error:
                        await main._stage_pdf_uploads(
                            [upload], working_directory, make_request()
                        )

        self.assertEqual(error.exception.status_code, 413)
        self.assertTrue(upload.file.closed)
        self.assertIn("reason=per_file_size", logs.output[0])

    async def test_excess_file_count_returns_413(self):
        uploads = [make_upload(), make_upload()]

        with patch.object(main, "MAX_UPLOAD_FILES", 1):
            with self.assertRaises(HTTPException) as error:
                async with main._analyzed_pdf_uploads(uploads, make_request()):
                    self.fail("file-count limit should reject before analysis")

        self.assertEqual(error.exception.status_code, 413)
        self.assertTrue(all(upload.file.closed for upload in uploads))

    async def test_valid_pdf_is_staged_and_analyzed(self):
        document = pymupdf.open()
        document.new_page()
        pdf_content = document.tobytes()
        document.close()

        async with main._analyzed_pdf_uploads(
            [make_upload(pdf_content)], make_request()
        ) as (staged_files, analyses):
            self.assertTrue(os.path.isfile(staged_files[0][1]))
            self.assertEqual(analyses[0]["total_pages"], 1)

        self.assertFalse(os.path.exists(staged_files[0][1]))

    async def test_temporary_files_are_removed_after_analysis_failure(self):
        real_temporary_directory = tempfile.TemporaryDirectory
        created_directories = []

        def capture_temporary_directory(*args, **kwargs):
            directory = real_temporary_directory(*args, **kwargs)
            created_directories.append(directory.name)
            return directory

        with (
            patch.object(main.tempfile, "TemporaryDirectory", capture_temporary_directory),
            patch.object(main, "analyze_files", side_effect=ValueError("invalid PDF")),
        ):
            with self.assertRaises(HTTPException) as error:
                async with main._analyzed_pdf_uploads(
                    [make_upload()], make_request()
                ):
                    self.fail("failed analysis must not yield")

        self.assertEqual(error.exception.status_code, 400)
        self.assertTrue(created_directories)
        self.assertFalse(os.path.exists(created_directories[0]))

    async def test_pdf_concurrency_limit_rejects_excess_request(self):
        def slow_analysis(files):
            time.sleep(0.2)
            return []

        async def process_one():
            async with main._analyzed_pdf_uploads([make_upload()], make_request()):
                return True

        with (
            patch.object(main, "PDF_PROCESSING_SEMAPHORE", asyncio.Semaphore(1)),
            patch.object(main, "PDF_PROCESSING_QUEUE_TIMEOUT", 0.05),
            patch.object(main, "analyze_files", side_effect=slow_analysis),
        ):
            first = asyncio.create_task(process_one())
            await asyncio.sleep(0.02)
            with self.assertRaises(HTTPException) as error:
                await process_one()
            self.assertEqual(error.exception.status_code, 503)
            self.assertTrue(await first)


class AuthenticationRateLimitTests(unittest.TestCase):
    def setUp(self):
        self.store = SharedRateLimitStore()
        self.get_connection = patch.object(
            main, "get_db_connection", side_effect=lambda: RateLimitConnection(self.store)
        )
        self.get_connection.start()
        self.randbelow = patch.object(main.secrets, "randbelow", return_value=99)
        self.randbelow.start()
        self.addCleanup(self.get_connection.stop)
        self.addCleanup(self.randbelow.stop)

    def test_login_throttle_is_shared_across_worker_connections(self):
        request = make_request(path="/login/")

        with (
            patch.object(main, "AUTH_LOGIN_PAIR_LIMIT", 2),
            patch.object(main, "AUTH_LOGIN_IP_LIMIT", 100),
        ):
            main._check_login_rate_limit(request, "private-user-name")
            main._check_login_rate_limit(request, "private-user-name")
            with self.assertLogs(main.logger, level=logging.WARNING) as logs:
                with self.assertRaises(HTTPException) as error:
                    main._check_login_rate_limit(request, "private-user-name")

        self.assertEqual(error.exception.status_code, 429)
        self.assertNotIn("private-user-name", "\n".join(logs.output))

    def test_registration_throttle_is_shared_across_worker_connections(self):
        request = make_request(path="/register/")

        with patch.object(main, "AUTH_REGISTER_IP_LIMIT", 2):
            main._check_registration_rate_limit(request)
            main._check_registration_rate_limit(request)
            with self.assertRaises(HTTPException) as error:
                main._check_registration_rate_limit(request)

        self.assertEqual(error.exception.status_code, 429)

    def test_valid_login_still_succeeds(self):
        password_hash = main._hash_password("valid-password")
        connection = LoginConnection(
            {"owner_id": 12, "shop_name": "Example Print Shop", "password": password_hash}
        )
        with (
            patch.object(main, "_check_login_rate_limit"),
            patch.object(main, "get_db_connection", return_value=connection),
        ):
            response = main.login_owner(
                make_request(path="/login/"),
                username="owner",
                password="valid-password",
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("smartprint_session", response.headers["set-cookie"])

    def test_unknown_user_and_wrong_password_share_generic_login_error(self):
        stored_password = main._hash_password("actual-password")
        connections = [
            LoginConnection(None),
            LoginConnection(
                {"owner_id": 12, "shop_name": "Example Print Shop", "password": stored_password}
            ),
        ]
        errors = []
        with (
            patch.object(main, "_check_login_rate_limit"),
            patch.object(main, "get_db_connection", side_effect=connections),
        ):
            for password in ("guess-one", "guess-two"):
                with self.assertRaises(HTTPException) as error:
                    main.login_owner(
                        make_request(path="/login/"),
                        username="owner",
                        password=password,
                    )
                errors.append((error.exception.status_code, error.exception.detail))

        self.assertEqual(errors, [(401, "Invalid username or password.")] * 2)


if __name__ == "__main__":
    unittest.main()
