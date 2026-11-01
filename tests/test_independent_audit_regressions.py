import asyncio
import io
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

os.environ["SMARTPRINT_ENV"] = "development"

from fastapi import UploadFile
from starlette.datastructures import FormData

import main
from starlette.requests import Request
from starlette.responses import JSONResponse


class RecordingOrderCursor:
    def __init__(self):
        self.queries = []
        self.current_query = ""
        self.lastrowid = 41

    def execute(self, query, params=()):
        self.current_query = query
        self.queries.append((query, params))

    def fetchone(self):
        if "SELECT owner_id FROM shop_owners" in self.current_query:
            return (7,)
        if "SELECT branch_id FROM branches" in self.current_query:
            return (12,)
        if "SELECT order_id FROM orders" in self.current_query:
            return None
        return None

    def close(self):
        pass


class RecordingOrderConnection:
    def __init__(self):
        self.cursor_instance = RecordingOrderCursor()
        self.committed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class IndependentAuditRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_upload_does_not_require_an_owner_session(self):
        persisted_order = {}

        @asynccontextmanager
        async def analyzed_uploads(request):
            yield [("customer.pdf", "staged.pdf")], [
                {
                    "filename": "customer.pdf",
                    "total_pages": 1,
                    "bw_pages": 1,
                    "color_pages": 0,
                }
            ], None, None, FormData([
                ("owner_id", "7"),
                ("payment_method", "UPI"),
                ("force_bw", "false"),
                ("customer_name", "Attacker-controlled name"),
                ("customer_phone", "5550100"),
                ("customer_email", "attacker@example.test"),
                ("print_settings", "{}"),
            ])

        def persist_order(*args, **kwargs):
            persisted_order.update(kwargs)
            return {"status": "success"}

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/upload/",
                "headers": [],
                "client": ("192.0.2.10", 12345),
                "server": ("testserver", 80),
                "scheme": "http",
                "query_string": b"",
                "http_version": "1.1",
            }
        )

        async def route(request):
            result = await main.upload_and_save_endpoint(request=request)
            return JSONResponse(result)

        with (
            patch.object(main, "_analyzed_request_uploads", analyzed_uploads),
            patch.object(main, "_persist_customer_order", side_effect=persist_order),
        ):
            response = await main.protect_owner_apis(request, route)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(request.cookies.get(main.SESSION_COOKIE_NAME))
        self.assertEqual(persisted_order["owner_id"], 7)
        self.assertEqual(persisted_order["customer_phone"], "5550100")
        self.assertEqual(persisted_order["customer_name"], "Attacker-controlled name")
        self.assertEqual(persisted_order["customer_email"], "attacker@example.test")

    def test_public_order_creates_guest_customer_without_reusing_or_mutating_existing_record(self):
        connection = RecordingOrderConnection()
        with (
            tempfile.TemporaryDirectory() as upload_directory,
            patch.object(main, "get_db_connection", return_value=connection),
            patch.object(main, "generate_order_id", return_value="SPAUDIT"),
            patch.object(main, "_qr_for_order"),
            patch.object(main.asyncio, "create_task", side_effect=lambda task: task.close()),
            patch.object(main, "UPLOAD_DIR", upload_directory),
        ):
            result = main._persist_customer_order(
                [],
                [
                    {
                        "filename": "customer.pdf",
                        "total_pages": 1,
                        "bw_pages": 1,
                        "color_pages": 0,
                    }
                ],
                payment_method="UPI",
                is_forced=False,
                customer_name="Guest name",
                customer_phone="5550100",
                customer_email="guest@example.test",
                owner_id=7,
                settings={
                    "paper_size": "A4",
                    "orientation": "Portrait",
                    "color_mode": "Auto",
                    "duplex": False,
                    "quality": "Standard",
                    "copies": 1,
                },
            )

        queries = [query for query, _ in connection.cursor_instance.queries]
        customer_insert = next(
            params
            for query, params in connection.cursor_instance.queries
            if query.startswith("INSERT INTO customers")
        )
        self.assertEqual(result["status"], "success")
        self.assertTrue(connection.committed)
        self.assertFalse(any("SELECT customer_id FROM customers" in query for query in queries))
        self.assertFalse(any("UPDATE customers" in query for query in queries))
        self.assertEqual(customer_insert, ("Guest name", "5550100", "guest@example.test", 7))

    async def test_cancelled_pdf_analysis_removes_staged_temporary_file(self):
        started = threading.Event()
        finish = threading.Event()
        staged_paths = []

        def slow_analysis(files):
            staged_paths.append(files[0][1])
            started.set()
            finish.wait(timeout=3)
            return []

        async def analyze_upload():
            async with main._analyzed_pdf_uploads(
                [UploadFile(filename="cancel.pdf", file=io.BytesIO(b"pdf"))],
                Request(
                    {
                        "type": "http",
                        "method": "POST",
                        "path": "/analyze/",
                        "headers": [],
                        "client": ("192.0.2.10", 12345),
                        "server": ("testserver", 80),
                        "scheme": "http",
                        "query_string": b"",
                        "http_version": "1.1",
                    }
                ),
            ):
                self.fail("cancelled analysis must not yield")

        with patch.object(main, "analyze_files", side_effect=slow_analysis):
            task = asyncio.create_task(analyze_upload())
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(len(staged_paths), 1)
        self.assertFalse(os.path.exists(staged_paths[0]))

    def test_forwarded_for_header_is_ignored_without_trusted_proxy_middleware(self):
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/login/",
                "headers": [(b"x-forwarded-for", b"203.0.113.99")],
                "client": ("192.0.2.10", 12345),
                "server": ("testserver", 80),
                "scheme": "http",
                "query_string": b"",
                "http_version": "1.1",
            }
        )

        self.assertEqual(main._client_ip(request), "192.0.2.10")

    def test_missing_environment_mode_fails_closed(self):
        environment = os.environ.copy()
        for name in (
            "SMARTPRINT_ENV",
            "SMARTPRINT_DB_USER",
            "SMARTPRINT_DB_PASSWORD",
            "SMARTPRINT_DB_HOST",
            "SMARTPRINT_SESSION_SECRET",
            "SMARTPRINT_ALLOWED_ORIGINS",
        ):
            environment.pop(name, None)

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import main",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SMARTPRINT_ENV must be explicitly set", result.stderr)

    def test_operations_guide_documents_required_production_mode(self):
        operations_guide = (
            Path(__file__).resolve().parents[1] / "SECURITY-OPERATIONS.md"
        ).read_text(encoding="utf-8")

        self.assertIn("SMARTPRINT_ENV=production", operations_guide)

    def test_production_mode_rejects_root_database_user(self):
        environment = os.environ.copy()
        environment.update(
            {
                "SMARTPRINT_ENV": "production",
                "SMARTPRINT_ALLOWED_ORIGINS": "https://shop.example.test",
                "SMARTPRINT_DB_HOST": "localhost",
                "SMARTPRINT_DB_USER": "root",
                "SMARTPRINT_DB_PASSWORD": "test-only-password",
                "SMARTPRINT_SESSION_SECRET": "test-only-session-secret",
            }
        )
        result = subprocess.run(
            [sys.executable, "-c", "import main"],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Configure a non-root production database user", result.stderr)


if __name__ == "__main__":
    unittest.main()
