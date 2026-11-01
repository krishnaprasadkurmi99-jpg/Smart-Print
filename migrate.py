"""Run additive SmartPrint schema migrations with migration-only credentials."""

import base64
import hashlib
import os
import re
import secrets
import sys

import mysql.connector


TABLE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS shop_owners (
        owner_id INT AUTO_INCREMENT PRIMARY KEY,
        shop_name VARCHAR(255),
        username VARCHAR(100) UNIQUE,
        password VARCHAR(255),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS customers (
        customer_id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(255),
        phone VARCHAR(50),
        email VARCHAR(255),
        owner_id INT,
        FOREIGN KEY (owner_id) REFERENCES shop_owners(owner_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS branches (
        branch_id INT AUTO_INCREMENT PRIMARY KEY,
        owner_id INT,
        branch_name VARCHAR(255),
        branch_code VARCHAR(50) UNIQUE,
        address TEXT,
        qr_code VARCHAR(255),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (owner_id) REFERENCES shop_owners(owner_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS printers (
        printer_id INT AUTO_INCREMENT PRIMARY KEY,
        branch_id INT,
        printer_name VARCHAR(255),
        model VARCHAR(255),
        status VARCHAR(50) DEFAULT 'Online',
        last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (branch_id) REFERENCES branches(branch_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS inventory (
        inventory_id INT AUTO_INCREMENT PRIMARY KEY,
        branch_id INT,
        item_name VARCHAR(255),
        current_stock INT DEFAULT 0,
        minimum_stock INT DEFAULT 10,
        unit_measure VARCHAR(50) DEFAULT 'sheets',
        last_restocked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (branch_id) REFERENCES branches(branch_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        order_id VARCHAR(50) PRIMARY KEY,
        customer_id INT,
        owner_id INT,
        branch_id INT,
        filename VARCHAR(255),
        total_pages INT,
        bw_pages INT,
        color_pages INT,
        total_price DECIMAL(10, 2),
        payment_status VARCHAR(50),
        order_status VARCHAR(50) DEFAULT 'Waiting',
        estimated_completion TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
        FOREIGN KEY (owner_id) REFERENCES shop_owners(owner_id),
        FOREIGN KEY (branch_id) REFERENCES branches(branch_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS order_history (
        history_id INT AUTO_INCREMENT PRIMARY KEY,
        order_id VARCHAR(50),
        owner_id INT,
        action VARCHAR(50),
        status VARCHAR(50),
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        notes TEXT,
        FOREIGN KEY (order_id) REFERENCES orders(order_id),
        FOREIGN KEY (owner_id) REFERENCES shop_owners(owner_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notifications (
        notification_id INT AUTO_INCREMENT PRIMARY KEY,
        user_type ENUM('customer', 'owner'),
        user_id INT,
        message TEXT,
        type VARCHAR(50),
        read_status BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS qr_codes (
        qr_id INT AUTO_INCREMENT PRIMARY KEY,
        order_id VARCHAR(50),
        owner_id INT,
        qr_data TEXT,
        qr_image_path VARCHAR(255),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (order_id) REFERENCES orders(order_id),
        FOREIGN KEY (owner_id) REFERENCES shop_owners(owner_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS printing_profiles (
        profile_id INT AUTO_INCREMENT PRIMARY KEY,
        owner_id INT NOT NULL,
        profile_name VARCHAR(100) NOT NULL,
        printer_id INT NULL,
        paper_size VARCHAR(30) NOT NULL DEFAULT 'A4',
        orientation VARCHAR(20) NOT NULL DEFAULT 'Portrait',
        color_mode VARCHAR(20) NOT NULL DEFAULT 'Auto',
        duplex BOOLEAN NOT NULL DEFAULT FALSE,
        quality VARCHAR(20) NOT NULL DEFAULT 'Standard',
        copies INT NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (owner_id) REFERENCES shop_owners(owner_id),
        FOREIGN KEY (printer_id) REFERENCES printers(printer_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS order_documents (
        document_id INT AUTO_INCREMENT PRIMARY KEY,
        order_id VARCHAR(50) NOT NULL,
        original_filename VARCHAR(255) NOT NULL,
        storage_filename VARCHAR(100) NOT NULL,
        sha256 CHAR(64) NOT NULL,
        total_pages INT NOT NULL,
        bw_pages INT NOT NULL,
        color_pages INT NOT NULL,
        analysis_json LONGTEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        file_deleted_at TIMESTAMP NULL,
        FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS order_print_settings (
        order_id VARCHAR(50) PRIMARY KEY,
        profile_id INT NULL,
        printer_id INT NULL,
        paper_size VARCHAR(30) NOT NULL DEFAULT 'A4',
        orientation VARCHAR(20) NOT NULL DEFAULT 'Portrait',
        color_mode VARCHAR(20) NOT NULL DEFAULT 'Auto',
        duplex BOOLEAN NOT NULL DEFAULT FALSE,
        quality VARCHAR(20) NOT NULL DEFAULT 'Standard',
        copies INT NOT NULL DEFAULT 1,
        owner_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS batch_exports (
        export_id CHAR(32) PRIMARY KEY,
        owner_id INT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (owner_id) REFERENCES shop_owners(owner_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS batch_export_files (
        filename VARCHAR(255) PRIMARY KEY,
        export_id CHAR(32) NOT NULL,
        FOREIGN KEY (export_id) REFERENCES batch_exports(export_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS batch_export_orders (
        export_id CHAR(32) NOT NULL,
        order_id VARCHAR(50) NOT NULL,
        PRIMARY KEY (export_id, order_id),
        FOREIGN KEY (export_id) REFERENCES batch_exports(export_id) ON DELETE CASCADE,
        FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS security_rate_limits (
        bucket_key CHAR(64) PRIMARY KEY,
        window_started_at DATETIME NOT NULL,
        attempts INT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """,
)


def _column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE %s", (column,))
    return cursor.fetchone() is not None


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return "scrypt${}${}".format(
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def run_migrations() -> None:
    host = os.getenv("SMARTPRINT_DB_HOST", "localhost")
    port = int(os.getenv("SMARTPRINT_DB_PORT", "3306"))
    database = os.getenv("SMARTPRINT_DB_NAME", "smart_print")
    user = os.getenv("SMARTPRINT_MIGRATION_DB_USER")
    password = os.getenv("SMARTPRINT_MIGRATION_DB_PASSWORD")
    if not user or password is None:
        raise RuntimeError(
            "Set SMARTPRINT_MIGRATION_DB_USER and SMARTPRINT_MIGRATION_DB_PASSWORD."
        )
    if os.getenv("SMARTPRINT_ENV", "development").lower() == "production" and (
        not password or user.lower() == "root"
    ):
        raise RuntimeError(
            "Production migrations require a non-root migration user with a password."
        )
    if not re.fullmatch(r"[A-Za-z0-9_]+", database):
        raise RuntimeError("SMARTPRINT_DB_NAME may contain only letters, numbers, and underscores.")

    connection = mysql.connector.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
    )
    cursor = connection.cursor()
    try:
        for statement in TABLE_STATEMENTS:
            cursor.execute(statement)

        migrations = (
            ("orders", "tracking_token_hash", "CHAR(64) NULL"),
            ("orders", "completed_at", "TIMESTAMP NULL"),
            ("order_documents", "file_deleted_at", "TIMESTAMP NULL"),
            ("order_print_settings", "printer_id", "INT NULL AFTER profile_id"),
            (
                "order_print_settings",
                "owner_confirmed",
                "BOOLEAN NOT NULL DEFAULT FALSE",
            ),
        )
        for table, column, definition in migrations:
            if not _column_exists(cursor, table, column):
                cursor.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}")

        cursor.execute(
            "UPDATE orders SET completed_at = CURRENT_TIMESTAMP "
            "WHERE order_status = 'Completed' AND completed_at IS NULL"
        )

        cursor.execute("SELECT owner_id, password FROM shop_owners")
        for owner_id, stored_password in cursor.fetchall():
            if stored_password and not stored_password.startswith("scrypt$"):
                cursor.execute(
                    "UPDATE shop_owners SET password = %s WHERE owner_id = %s",
                    (_hash_password(stored_password), owner_id),
                )

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


if __name__ == "__main__":
    try:
        run_migrations()
    except Exception as error:
        print(f"SmartPrint migration failed: {error}", file=sys.stderr)
        raise
    print("SmartPrint schema migrations completed.")
