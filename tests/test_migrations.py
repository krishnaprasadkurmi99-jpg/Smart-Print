import os
import subprocess
import sys
import unittest
from unittest.mock import patch

import main
import migrate


class MigrationCursor:
    def __init__(self):
        self.statements = []
        self.last_query = ""
        self.updated_password = None

    def execute(self, query, params=()):
        self.last_query = query
        self.statements.append((query, params))
        if query.startswith("UPDATE shop_owners SET password"):
            self.updated_password = params[0]

    def fetchone(self):
        return ("password",)

    def fetchall(self):
        if self.last_query == "SELECT owner_id, password FROM shop_owners":
            return [(17, "legacy-password")]
        return []

    def close(self):
        pass


class MigrationConnection:
    def __init__(self):
        self.fake_cursor = MigrationCursor()
        self.committed = False

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class MigrationTests(unittest.TestCase):
    def test_importing_application_does_not_connect_or_run_schema_migrations(self):
        environment = os.environ.copy()
        result = subprocess.run(
            [sys.executable, "-c", "import main"],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_migrations_are_additive_and_hash_legacy_passwords(self):
        connection = MigrationConnection()
        with (
            patch.dict(
                os.environ,
                {
                    "SMARTPRINT_DB_HOST": "localhost",
                    "SMARTPRINT_DB_PORT": "3306",
                    "SMARTPRINT_DB_NAME": "smart_print_test",
                    "SMARTPRINT_MIGRATION_DB_USER": "migration_user",
                    "SMARTPRINT_MIGRATION_DB_PASSWORD": "",
                    "SMARTPRINT_ENV": "development",
                },
                clear=False,
            ),
            patch.object(migrate.mysql.connector, "connect", return_value=connection),
        ):
            migrate.run_migrations()

        statements = [query.upper() for query, _ in connection.fake_cursor.statements]
        self.assertTrue(connection.committed)
        self.assertFalse(any("DROP TABLE" in query for query in statements))
        self.assertFalse(any("CREATE DATABASE" in query for query in statements))
        self.assertTrue(
            any("CREATE TABLE IF NOT EXISTS SECURITY_RATE_LIMITS" in query for query in statements)
        )
        self.assertTrue(
            main._verify_password("legacy-password", connection.fake_cursor.updated_password)[0]
        )


if __name__ == "__main__":
    unittest.main()
