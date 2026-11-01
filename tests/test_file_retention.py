import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import main


class RetentionCursor:
    def __init__(self, batch_files):
        self.batch_files = batch_files
        self.rows = []
        self.current_export_id = None
        self.deleted_documents = []
        self.deleted_exports = []
        self.queries = []
        self.cutoff = None

    def execute(self, query, params=()):
        self.queries.append((query, params))
        if "SELECT d.document_id, d.storage_filename" in query:
            self.cutoff = params[0]
            self.rows = [{"document_id": 7, "storage_filename": "expired-source.pdf"}]
        elif "SELECT o.filename" in query:
            self.rows = []
        elif "SELECT be.export_id" in query:
            self.rows = [{"export_id": "completed-export"}]
        elif "SELECT filename FROM batch_export_files" in query:
            self.current_export_id = params[0]
            self.rows = [{"filename": name} for name in self.batch_files[self.current_export_id]]
        elif "DELETE FROM batch_exports" in query:
            self.deleted_exports.append(params[0])

    def executemany(self, query, params):
        self.queries.append((query, params))
        self.deleted_documents.extend(row[0] for row in params)

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class RetentionConnection:
    def __init__(self, cursor):
        self.test_cursor = cursor
        self.committed = False

    def cursor(self, **kwargs):
        return self.test_cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class FileRetentionTests(unittest.TestCase):
    def test_completed_files_expire_but_uncompleted_batch_files_are_not_selected(self):
        with tempfile.TemporaryDirectory() as upload_dir:
            expired_source = os.path.join(upload_dir, "expired-source.pdf")
            expired_batch = os.path.join(upload_dir, "expired-batch.pdf")
            pending_batch = os.path.join(upload_dir, "pending-batch.pdf")
            for path in (expired_source, expired_batch, pending_batch):
                with open(path, "wb") as file:
                    file.write(b"test PDF data")

            cursor = RetentionCursor({
                "completed-export": ["expired-batch.pdf"],
                "pending-export": ["pending-batch.pdf"],
            })
            connection = RetentionConnection(cursor)
            with patch.object(main, "UPLOAD_DIR", upload_dir), patch.object(
                main, "get_db_connection", return_value=connection
            ):
                result = main.cleanup_expired_files()

            self.assertFalse(os.path.exists(expired_source))
            self.assertFalse(os.path.exists(expired_batch))
            self.assertTrue(os.path.exists(pending_batch))
            self.assertEqual(result, {"documents": 1, "batch_exports": 1})
            self.assertEqual(cursor.deleted_documents, [7])
            self.assertEqual(cursor.deleted_exports, ["completed-export"])
            self.assertTrue(connection.committed)
            self.assertTrue(any("o.order_status = 'Completed'" in query for query, _ in cursor.queries))
            self.assertTrue(any("COUNT(DISTINCT beo.order_id)" in query for query, _ in cursor.queries))
            age_seconds = (datetime.now() - cursor.cutoff).total_seconds()
            self.assertAlmostEqual(age_seconds, main.FILE_RETENTION_DAYS * 24 * 60 * 60, delta=5)

    def test_file_deletion_rejects_paths_outside_upload_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            upload_dir = os.path.join(temp_dir, "uploads")
            os.makedirs(upload_dir)
            outside_file = os.path.join(temp_dir, "keep.pdf")
            with open(outside_file, "wb") as file:
                file.write(b"keep")

            with patch.object(main, "UPLOAD_DIR", upload_dir):
                self.assertFalse(main._delete_upload_file("..\\keep.pdf"))

            self.assertTrue(os.path.exists(outside_file))


if __name__ == "__main__":
    unittest.main()
