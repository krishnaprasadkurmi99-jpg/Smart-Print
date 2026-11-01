import json
import unittest
from unittest.mock import patch

import main
from fastapi import HTTPException
from starlette.requests import Request


class TrackingCursor:
    def __init__(self, owner_id: int, token_hash: str):
        self.order = {"owner_id": owner_id, "tracking_token_hash": token_hash}

    def execute(self, query: str, params: tuple[str]):
        self.params = params

    def fetchone(self):
        return self.order


class SecurityTests(unittest.TestCase):
    def test_password_hashes_support_legacy_upgrade(self):
        stored = main._hash_password("correct horse battery")

        self.assertNotEqual(stored, "correct horse battery")
        self.assertTrue(main._verify_password("correct horse battery", stored)[0])
        self.assertFalse(main._verify_password("wrong password", stored)[0])
        self.assertEqual(main._verify_password("legacy", "legacy"), (True, True))
        self.assertEqual(main._verify_password("wrong", "legacy"), (False, True))

    def test_signed_sessions_reject_tampering_and_expiration(self):
        token = main._encode_session(42)

        self.assertEqual(main._decode_session(token), 42)
        self.assertIsNone(main._decode_session(token + "tampered"))
        with patch.object(main.time, "time", return_value=2**31):
            self.assertIsNone(main._decode_session(token))

    def test_customer_tracking_requires_the_order_token(self):
        token = "customer-only-random-token"
        cursor = TrackingCursor(8, main._tracking_token_hash(token))

        self.assertEqual(main._customer_tracking_owner(cursor, "SPTEST", token), 8)
        with self.assertRaises(HTTPException):
            main._customer_tracking_owner(cursor, "SPTEST", "wrong-token")

    def test_internal_errors_do_not_expose_exception_details(self):
        with self.assertLogs(main.logger, level="ERROR"):
            try:
                raise RuntimeError("sensitive database detail")
            except RuntimeError:
                response = main._internal_error_response("testing")

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("sensitive database detail", json.loads(response.body)["message"])

    def test_unconfigured_notifications_do_not_report_success(self):
        request = Request({"type": "http", "headers": [], "method": "POST", "path": "/"})
        request.state.owner_id = 8

        with self.assertRaises(HTTPException) as whatsapp_error:
            main.send_whatsapp_notification(request, "SPTEST", "5550000", "ready_for_pickup")
        with self.assertRaises(HTTPException) as email_error:
            main.send_email_notification(request, "SPTEST", "customer@example.com", "ready_for_pickup")

        self.assertEqual(whatsapp_error.exception.status_code, 503)
        self.assertEqual(email_error.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
