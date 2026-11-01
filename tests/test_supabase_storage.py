import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

os.environ["SMARTPRINT_ENV"] = "development"

from fastapi import HTTPException
from storage3.exceptions import StorageApiError

import main
from object_storage import (
    ObjectNotFoundError,
    ObjectStorageError,
    SupabaseObjectStorage,
)


class FakeBucket:
    def __init__(self):
        self.calls = []
        self.info_result = {"metadata": {"size": "4"}}
        self.exists_result = True
        self.upload_result = None

    def create_signed_upload_url(self, key):
        self.calls.append(("create_signed_upload_url", key))
        return {
            "signed_url": "https://project.supabase.co/storage/v1/object/upload/sign/bucket/key?token=abc",
            "token": "abc",
        }

    def create_signed_url(self, key, expires_in, options=None):
        self.calls.append(("create_signed_url", key, expires_in, options))
        return {"signedURL": "https://project.supabase.co/storage/v1/object/sign/bucket/key?token=abc"}

    def info(self, key):
        self.calls.append(("info", key))
        if isinstance(self.info_result, Exception):
            raise self.info_result
        return self.info_result

    def exists(self, key):
        self.calls.append(("exists", key))
        if isinstance(self.exists_result, Exception):
            raise self.exists_result
        return self.exists_result

    def upload(self, key, data, file_options):
        self.calls.append(("upload", key, data, file_options))
        if isinstance(self.upload_result, Exception):
            raise self.upload_result
        return self.upload_result

    def copy(self, source, destination):
        self.calls.append(("copy", source, destination))

    def remove(self, paths):
        self.calls.append(("remove", paths))


class FakeStorage:
    def __init__(self, bucket):
        self.bucket = bucket

    def from_(self, bucket_name):
        return self.bucket


class FakeSupabaseClient:
    def __init__(self, bucket):
        self.storage = FakeStorage(bucket)


def make_storage(bucket=None):
    return SupabaseObjectStorage(
        url="https://project.supabase.co",
        service_role_key="test-service-role-key",
        bucket="smartprint-backend",
        client=FakeSupabaseClient(bucket or FakeBucket()),
    )


class SupabaseStorageTests(unittest.TestCase):
    def test_environment_initializes_official_client_without_exposing_credentials(self):
        fake_client = FakeSupabaseClient(FakeBucket())
        with (
            patch.dict(os.environ, {
                "SUPABASE_URL": "https://project.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY": "test-key",
                "SUPABASE_BUCKET": "smartprint-backend",
                "SMARTPRINT_STORAGE_TIMEOUT_SECONDS": "25",
                "SMARTPRINT_STORAGE_DOWNLOAD_URL_TTL_SECONDS": "180",
            }, clear=False),
            patch("object_storage.create_client", return_value=fake_client) as create_client,
        ):
            storage = SupabaseObjectStorage.from_environment()

        self.assertIs(storage.client, fake_client)
        self.assertEqual(storage.bucket_name, "smartprint-backend")
        self.assertEqual(storage.timeout_seconds, 25)
        self.assertEqual(storage.download_url_ttl_seconds, 180)
        self.assertEqual(create_client.call_args.args, (
            "https://project.supabase.co",
            "test-key",
        ))
        self.assertEqual(
            create_client.call_args.kwargs["options"].storage_client_timeout,
            25,
        )

    def test_missing_environment_variables_are_rejected(self):
        for missing_name in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
            env = {
                "SUPABASE_URL": "https://project.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY": "test-key",
                "SUPABASE_BUCKET": "smartprint-backend",
            }
            del env[missing_name]
            with self.subTest(variable=missing_name), patch.dict(os.environ, env, clear=False):
                with patch("object_storage.create_client") as create_client:
                    with self.assertRaises(RuntimeError):
                        SupabaseObjectStorage.from_environment()
                create_client.assert_not_called()

    def test_configuration_requires_https_and_valid_bucket(self):
        with self.assertRaisesRegex(RuntimeError, "HTTPS"):
            SupabaseObjectStorage(
                url="http://project.supabase.co",
                service_role_key="test-key",
                bucket="smartprint-backend",
                client=FakeSupabaseClient(FakeBucket()),
            )
        with self.assertRaisesRegex(RuntimeError, "bucket"):
            SupabaseObjectStorage(
                url="https://project.supabase.co",
                service_role_key="test-key",
                bucket="../public",
                client=FakeSupabaseClient(FakeBucket()),
            )

    def test_signed_upload_and_download_are_private_and_use_expected_options(self):
        bucket = FakeBucket()
        storage = make_storage(bucket)

        upload_url = storage.presign_upload("staging/random-key", "application/pdf", 1024)
        download_url = storage.presign_download(
            "orders/document.pdf",
            'customer"\r\n.pdf',
            "application/pdf",
        )

        self.assertIn("storage/v1/object/upload/sign/", upload_url)
        self.assertNotIn("/public/", upload_url)
        self.assertIn("/object/sign/", download_url)
        self.assertEqual(bucket.calls[0], ("create_signed_upload_url", "staging/random-key"))
        signed_download = bucket.calls[1]
        self.assertEqual(signed_download[1], "orders/document.pdf")
        self.assertEqual(signed_download[2], 300)
        self.assertEqual(signed_download[3], {"download": "customer.pdf"})

    def test_upload_and_download_reject_invalid_types_sizes_and_keys(self):
        storage = make_storage()
        with self.assertRaises(ValueError):
            storage.presign_upload("staging/file.pdf", "text/plain", 10)
        with self.assertRaises(ValueError):
            storage.presign_upload("staging/file.pdf", "application/pdf", 0)
        for key in ("../file.pdf", "staging\\file.pdf", "/absolute.pdf"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                storage.presign_upload(key, "application/pdf", 10)

    def test_head_checks_storage_metadata_and_rejects_malformed_metadata(self):
        bucket = FakeBucket()
        storage = make_storage(bucket)
        self.assertEqual(storage.head("staging/file"), {"ContentLength": 4})
        bucket.info_result = {"metadata": {}}
        with self.assertRaises(ObjectStorageError):
            storage.head("staging/file")

    def test_missing_objects_and_supabase_api_errors_are_explicit(self):
        bucket = FakeBucket()
        storage = make_storage(bucket)
        bucket.exists_result = StorageApiError("missing", "NotFound", 404)
        self.assertFalse(storage.exists("missing.pdf"))
        bucket.info_result = StorageApiError("missing", "NotFound", 404)
        with self.assertRaises(ObjectNotFoundError):
            storage.head("missing.pdf")

        bucket.exists_result = StorageApiError("unavailable", "InternalError", 500)
        with self.assertRaises(ObjectStorageError):
            storage.exists("present.pdf")

    def test_upload_file_put_bytes_copy_and_delete_use_storage_api(self):
        bucket = FakeBucket()
        storage = make_storage(bucket)
        with tempfile.NamedTemporaryFile() as source:
            storage.upload_file(source.name, "orders/file.pdf", "application/pdf")
            upload_call = bucket.calls[-1]
            self.assertEqual(upload_call[0:2], ("upload", "orders/file.pdf"))
            self.assertEqual(upload_call[2], source.name)
            self.assertEqual(upload_call[3]["content-type"], "application/pdf")
            self.assertEqual(upload_call[3]["upsert"], "false")

        storage.put_bytes(b"png", "qrcodes/order.png", "image/png")
        storage.copy("orders/file.pdf", "orders/copy.pdf")
        storage.delete("orders/copy.pdf")
        self.assertIn(("copy", "orders/file.pdf", "orders/copy.pdf"), bucket.calls)
        self.assertIn(("remove", ["orders/copy.pdf"]), bucket.calls)

    def test_failed_storage_upload_is_reported_without_api_details(self):
        bucket = FakeBucket()
        bucket.upload_result = StorageApiError(
            "service-role-key-or-document-detail", "InternalError", 500
        )
        storage = make_storage(bucket)
        with tempfile.NamedTemporaryFile() as source:
            with self.assertRaises(ObjectStorageError) as error:
                storage.upload_file(source.name, "orders/file.pdf", "application/pdf")
        self.assertNotIn("service-role-key-or-document-detail", str(error.exception))

    def test_download_streams_to_temporary_file_with_request_timeout(self):
        storage = make_storage()

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def raise_for_status(self):
                pass

            def iter_bytes(self, chunk_size):
                self.chunk_size = chunk_size
                yield b"%PDF"
                yield b"-data"

        class FakeHttpClient:
            def __init__(self, **kwargs):
                self.timeout = kwargs["timeout"]
                self.response = FakeResponse()

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def stream(self, method, url):
                self.request = (method, url)
                return self.response

        with tempfile.TemporaryDirectory() as temp_dir:
            destination = os.path.join(temp_dir, "document.pdf")
            with patch("object_storage.httpx.Client", FakeHttpClient):
                storage.download_file("orders/document.pdf", destination)
            with open(destination, "rb") as downloaded:
                self.assertEqual(downloaded.read(), b"%PDF-data")


class RecordingCursor:
    def __init__(self):
        self.query = ""
        self.params = None
        self.rows = None

    def execute(self, query, params=()):
        self.query = query
        self.params = params

    def fetchone(self):
        return self.rows

    def close(self):
        pass


class RecordingConnection:
    def __init__(self):
        self.cursor_instance = RecordingCursor()
        self.committed = False

    def cursor(self, **kwargs):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class DirectUploadTests(unittest.IsolatedAsyncioTestCase):
    def make_request(self, path="/upload/presign"):
        return main.Request({
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "client": ("192.0.2.10", 12345),
            "server": ("testserver", 80),
            "scheme": "https",
            "query_string": b"",
            "http_version": "1.1",
        })

    async def test_presign_endpoint_persists_only_hashed_upload_token(self):
        connection = RecordingConnection()
        storage = make_storage()
        with (
            patch.object(main, "OBJECT_STORAGE", storage),
            patch.object(main, "get_db_connection", return_value=connection),
        ):
            result = await main.presign_customer_upload(
                main.PresignUploadRequest(files=[
                    main.PresignUploadFile(filename="notes.pdf", size=1024),
                ]),
                self.make_request(),
            )

        self.assertTrue(connection.committed)
        self.assertNotEqual(connection.cursor_instance.params[0], result["upload_token"])
        self.assertEqual(len(result["files"]), 1)
        self.assertTrue(storage.bucket.calls[0][1].startswith("staging/"))

    async def test_filename_and_actual_upload_size_limits_are_enforced(self):
        with (
            patch.object(main, "OBJECT_STORAGE", make_storage()),
            self.assertRaises(HTTPException) as invalid_file,
        ):
            await main.presign_customer_upload(
                main.PresignUploadRequest(files=[
                    main.PresignUploadFile(filename="notes.txt", size=1),
                ]),
                self.make_request(),
            )
        self.assertEqual(invalid_file.exception.status_code, 400)

        connection = RecordingConnection()
        with (
            patch.object(main, "OBJECT_STORAGE", make_storage()),
            patch.object(main, "get_db_connection", return_value=connection),
        ):
            with self.assertRaises(HTTPException) as oversized:
                await main.presign_customer_upload(
                    main.PresignUploadRequest(files=[
                        main.PresignUploadFile(
                            filename="large.pdf",
                            size=main.MAX_UPLOAD_FILE_BYTES + 1,
                        ),
                    ]),
                    self.make_request(),
                )
        self.assertEqual(oversized.exception.status_code, 413)
        self.assertFalse(connection.committed)

    async def test_staged_object_temp_file_is_removed_after_analysis(self):
        written_paths = []

        class FakeStorage:
            def head(self, key):
                return {"ContentLength": 4}

            def download_file(self, key, destination):
                written_paths.append(destination)
                with open(destination, "wb") as target:
                    target.write(b"%PDF")

        @asynccontextmanager
        async def fake_analysis_slot(staged_files, request):
            yield [{"filename": staged_files[0][0]}]

        with (
            patch.object(main, "_load_upload_intent", return_value=(
                "hashed-intent",
                [{"filename": "notes.pdf", "key": "staging/random", "size": 4}],
            )),
            patch.object(main, "OBJECT_STORAGE", FakeStorage()),
            patch.object(main, "_pdf_analysis_slot", fake_analysis_slot),
        ):
            async with main._analyzed_object_uploads("token", self.make_request("/analyze/")) as (
                staged_files, _, _, _,
            ):
                self.assertTrue(os.path.isfile(staged_files[0][1]))

        self.assertEqual(len(written_paths), 1)
        self.assertFalse(os.path.exists(written_paths[0]))

    async def test_wrong_download_size_is_rejected_and_cleaned(self):
        written_paths = []

        class FakeStorage:
            def head(self, key):
                return {"ContentLength": 4}

            def download_file(self, key, destination):
                written_paths.append(destination)
                with open(destination, "wb") as target:
                    target.write(b"bad")

        with (
            patch.object(main, "_load_upload_intent", return_value=(
                "hashed-intent",
                [{"filename": "notes.pdf", "key": "staging/random", "size": 4}],
            )),
            patch.object(main, "OBJECT_STORAGE", FakeStorage()),
        ):
            with self.assertRaises(HTTPException) as error:
                async with main._analyzed_object_uploads("token", self.make_request("/analyze/")):
                    self.fail("A size mismatch must be rejected.")

        self.assertEqual(error.exception.status_code, 400)
        self.assertFalse(os.path.exists(written_paths[0]))

    async def test_actual_object_over_file_limit_is_rejected_before_download(self):
        class FakeStorage:
            def head(self, key):
                return {"ContentLength": main.MAX_UPLOAD_FILE_BYTES + 1}

            def download_file(self, key, destination):
                raise AssertionError("Oversized objects must not be downloaded.")

        with (
            patch.object(main, "_load_upload_intent", return_value=(
                "hashed-intent",
                [{"filename": "notes.pdf", "key": "staging/random", "size": 1}],
            )),
            patch.object(main, "OBJECT_STORAGE", FakeStorage()),
        ):
            with self.assertRaises(HTTPException) as error:
                async with main._analyzed_object_uploads("token", self.make_request("/analyze/")):
                    self.fail("An oversized object must not be analyzed.")

        self.assertEqual(error.exception.status_code, 413)

    async def test_actual_aggregate_object_size_is_checked_before_download(self):
        class FakeStorage:
            def head(self, key):
                return {"ContentLength": 3}

            def download_file(self, key, destination):
                raise AssertionError("Oversized aggregate objects must not be downloaded.")

        with (
            patch.object(main, "_load_upload_intent", return_value=(
                "hashed-intent",
                [
                    {"filename": "one.pdf", "key": "staging/one", "size": 2},
                    {"filename": "two.pdf", "key": "staging/two", "size": 2},
                ],
            )),
            patch.object(main, "MAX_UPLOAD_FILE_BYTES", 10),
            patch.object(main, "MAX_UPLOAD_REQUEST_BYTES", 5),
            patch.object(main, "OBJECT_STORAGE", FakeStorage()),
        ):
            with self.assertRaises(HTTPException) as error:
                async with main._analyzed_object_uploads("token", self.make_request("/analyze/")):
                    self.fail("An oversized aggregate must not be analyzed.")

        self.assertEqual(error.exception.status_code, 413)

    def test_unauthorized_order_document_is_not_looked_up_in_storage(self):
        connection = RecordingConnection()
        connection.cursor_instance.rows = None

        class StorageMustNotBeAccessed:
            def exists(self, key):
                raise AssertionError("Storage must not be queried before authorization.")

        request = main.Request({
            "type": "http",
            "method": "GET",
            "path": "/files/orders/order/1",
            "headers": [],
            "client": ("192.0.2.10", 12345),
            "server": ("testserver", 80),
            "scheme": "https",
            "query_string": b"",
            "http_version": "1.1",
        })
        with (
            patch.object(main, "_owner_id", return_value=7),
            patch.object(main, "get_db_connection", return_value=connection),
            patch.object(main, "OBJECT_STORAGE", StorageMustNotBeAccessed()),
        ):
            with self.assertRaises(HTTPException) as error:
                main.download_order_document("order", 1, request)

        self.assertEqual(error.exception.status_code, 404)
        self.assertIn("o.owner_id = %s", connection.cursor_instance.query)
        self.assertEqual(connection.cursor_instance.params, ("order", 1, 7))

    def test_local_development_storage_still_writes_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "source.pdf")
            with open(source, "wb") as file:
                file.write(b"%PDF")
            with (
                patch.object(main, "OBJECT_STORAGE", None),
                patch.object(main, "UPLOAD_DIR", temp_dir),
            ):
                main._storage_put_file(source, "local-copy.pdf")
                with open(os.path.join(temp_dir, "local-copy.pdf"), "rb") as file:
                    self.assertEqual(file.read(), b"%PDF")


if __name__ == "__main__":
    unittest.main()
