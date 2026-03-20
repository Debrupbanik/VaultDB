"""
Tests for DBBackup utility.

Tests cover configuration, compression, connectors, storage, and CLI operations.
"""

import os
import sys
import json
import sqlite3
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

import pytest
from click.testing import CliRunner

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dbbackup.config import (
    AppConfig, DatabaseConfig, StorageConfig, BackupConfig,
    load_config, save_config, generate_sample_config,
)
from dbbackup.compression import (
    compress_file, decompress_file, get_compression_ratio,
    get_compression_extension,
)
from dbbackup.logger import (
    log_backup_activity, get_backup_history, format_size, format_duration,
)
from dbbackup.storage import LocalStorage
from dbbackup.connectors import get_connector
from dbbackup.connectors.sqlite_connector import SQLiteConnector
from dbbackup.cli import cli


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    d = tempfile.mkdtemp(prefix="dbbackup_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_db(temp_dir):
    """Create a sample SQLite database with test data."""
    db_path = os.path.join(temp_dir, "test.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create tables
    cursor.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE,
            age INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            product TEXT NOT NULL,
            amount REAL,
            status TEXT DEFAULT 'pending',
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Insert test data
    users = [
        ("Alice Johnson", "alice@example.com", 30),
        ("Bob Smith", "bob@example.com", 25),
        ("Charlie Brown", "charlie@example.com", 35),
        ("Diana Prince", "diana@example.com", 28),
        ("Eve Wilson", "eve@example.com", 32),
    ]
    cursor.executemany("INSERT INTO users (name, email, age) VALUES (?, ?, ?)", users)

    orders = [
        (1, "Laptop", 999.99, "completed"),
        (1, "Mouse", 29.99, "completed"),
        (2, "Keyboard", 79.99, "pending"),
        (3, "Monitor", 399.99, "shipped"),
        (4, "Headphones", 149.99, "completed"),
    ]
    cursor.executemany(
        "INSERT INTO orders (user_id, product, amount, status) VALUES (?, ?, ?, ?)", orders
    )

    settings = [
        ("version", "1.0"),
        ("theme", "dark"),
        ("language", "en"),
    ]
    cursor.executemany("INSERT INTO settings (key, value) VALUES (?, ?)", settings)

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


# ═══════════════════════════════════════════════════════════
# Configuration Tests
# ═══════════════════════════════════════════════════════════

class TestConfig:
    """Test configuration management."""

    def test_default_config(self):
        """Test default configuration values."""
        config = AppConfig()
        assert config.database.dbms == "sqlite"
        assert config.database.host == "localhost"
        assert config.backup.compression == "gzip"
        assert config.backup.backup_type == "full"

    def test_save_and_load_config(self, temp_dir):
        """Test saving and loading configuration."""
        config_path = os.path.join(temp_dir, "config.yaml")

        config = AppConfig()
        config.database.dbms = "postgresql"
        config.database.host = "db.example.com"
        config.database.port = 5432
        config.database.username = "admin"
        config.database.database = "production"
        config.backup.compression = "bzip2"

        save_config(config, config_path)
        loaded = load_config(config_path)

        assert loaded.database.dbms == "postgresql"
        assert loaded.database.host == "db.example.com"
        assert loaded.database.port == 5432
        assert loaded.database.username == "admin"
        assert loaded.database.database == "production"
        assert loaded.backup.compression == "bzip2"

    def test_generate_sample_config(self, temp_dir):
        """Test sample config generation."""
        output = os.path.join(temp_dir, "sample.yaml")
        generate_sample_config(output)
        assert os.path.exists(output)
        with open(output) as f:
            content = f.read()
        assert "database:" in content
        assert "storage:" in content
        assert "notification:" in content

    def test_database_default_ports(self):
        """Test default port assignment for different DBMS."""
        db = DatabaseConfig(dbms="mysql")
        assert db.get_default_port() == 3306

        db = DatabaseConfig(dbms="postgresql")
        assert db.get_default_port() == 5432

        db = DatabaseConfig(dbms="mongodb")
        assert db.get_default_port() == 27017

        db = DatabaseConfig(dbms="sqlite")
        assert db.get_default_port() == 0

    def test_effective_port(self):
        """Test effective port calculation."""
        db = DatabaseConfig(dbms="mysql", port=3307)
        assert db.effective_port() == 3307

        db = DatabaseConfig(dbms="mysql", port=0)
        assert db.effective_port() == 3306

    def test_load_nonexistent_config(self):
        """Test loading from nonexistent file returns defaults."""
        config = load_config("/nonexistent/path/config.yaml")
        assert config.database.dbms == "sqlite"


# ═══════════════════════════════════════════════════════════
# Compression Tests
# ═══════════════════════════════════════════════════════════

class TestCompression:
    """Test compression and decompression functions."""

    def _create_test_file(self, temp_dir, size_kb=10):
        """Create a test file with reproducible content."""
        path = os.path.join(temp_dir, "test_data.sql")
        with open(path, "w") as f:
            # Write repetitive data (which compresses well)
            line = "INSERT INTO users (name, email) VALUES ('Test User', 'test@example.com');\n"
            for _ in range(size_kb * 20):  # ~50 bytes per line
                f.write(line)
        return path

    def test_gzip_compression(self, temp_dir):
        """Test gzip compression and decompression."""
        input_path = self._create_test_file(temp_dir)
        original_size = os.path.getsize(input_path)

        output_path, orig, compressed = compress_file(
            input_path, method="gzip", level=6, remove_original=False
        )

        assert output_path.endswith(".gz")
        assert os.path.exists(output_path)
        assert compressed < original_size  # Should be smaller

        # Decompress
        decompressed = decompress_file(output_path, remove_compressed=False)
        assert os.path.exists(decompressed)

        # Verify content matches
        with open(input_path, "r") as f1, open(decompressed, "r") as f2:
            assert f1.read() == f2.read()

    def test_bzip2_compression(self, temp_dir):
        """Test bzip2 compression and decompression."""
        input_path = self._create_test_file(temp_dir)

        output_path, orig, compressed = compress_file(
            input_path, method="bzip2", level=6, remove_original=False
        )

        assert output_path.endswith(".bz2")
        assert os.path.exists(output_path)

        decompressed = decompress_file(output_path, remove_compressed=False)
        with open(input_path, "r") as f1, open(decompressed, "r") as f2:
            assert f1.read() == f2.read()

    def test_lzma_compression(self, temp_dir):
        """Test lzma compression and decompression."""
        input_path = self._create_test_file(temp_dir)

        output_path, orig, compressed = compress_file(
            input_path, method="lzma", level=3, remove_original=False
        )

        assert output_path.endswith(".xz")
        assert os.path.exists(output_path)

        decompressed = decompress_file(output_path, remove_compressed=False)
        with open(input_path, "r") as f1, open(decompressed, "r") as f2:
            assert f1.read() == f2.read()

    def test_no_compression(self, temp_dir):
        """Test 'none' compression passes through."""
        input_path = self._create_test_file(temp_dir)
        original_size = os.path.getsize(input_path)

        output_path, orig, compressed = compress_file(
            input_path, method="none"
        )

        assert output_path == input_path
        assert orig == compressed == original_size

    def test_compression_ratio(self):
        """Test compression ratio calculation."""
        assert get_compression_ratio(1000, 300) == 70.0
        assert get_compression_ratio(1000, 1000) == 0.0
        assert get_compression_ratio(0, 0) == 0.0

    def test_compression_extensions(self):
        """Test compression extension mapping."""
        assert get_compression_extension("gzip") == ".gz"
        assert get_compression_extension("bzip2") == ".bz2"
        assert get_compression_extension("lzma") == ".xz"
        assert get_compression_extension("none") == ""

    def test_remove_original(self, temp_dir):
        """Test that original file is removed when requested."""
        input_path = self._create_test_file(temp_dir)
        assert os.path.exists(input_path)

        output_path, _, _ = compress_file(
            input_path, method="gzip", remove_original=True
        )

        assert os.path.exists(output_path)
        assert not os.path.exists(input_path)


# ═══════════════════════════════════════════════════════════
# SQLite Connector Tests
# ═══════════════════════════════════════════════════════════

class TestSQLiteConnector:
    """Test SQLite database connector."""

    def test_connection(self, sample_db):
        """Test SQLite connection."""
        connector = SQLiteConnector(database=sample_db)
        success, msg = connector.test_connection()
        assert success
        assert "SQLite" in msg

    def test_connection_invalid_path(self):
        """Test connection to nonexistent database."""
        connector = SQLiteConnector(database="/nonexistent/db.sqlite")
        success, msg = connector.test_connection()
        assert not success

    def test_connection_empty_path(self):
        """Test connection with empty path."""
        connector = SQLiteConnector(database="")
        success, msg = connector.test_connection()
        assert not success

    def test_list_tables(self, sample_db):
        """Test listing tables."""
        connector = SQLiteConnector(database=sample_db)
        connector.connect()
        tables = connector.list_tables()
        connector.disconnect()

        assert "users" in tables
        assert "orders" in tables
        assert "settings" in tables
        assert len(tables) == 3

    def test_full_backup_binary(self, sample_db, temp_dir):
        """Test full binary backup (sqlite3.backup API)."""
        connector = SQLiteConnector(database=sample_db)
        output_path = os.path.join(temp_dir, "backup.db")

        success, msg = connector.backup(output_path, backup_type="full")
        assert success
        assert os.path.exists(output_path)
        assert os.path.getsize(output_path) > 0

        # Verify backup is a valid SQLite database
        conn = sqlite3.connect(output_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        assert cursor.fetchone()[0] == 5
        cursor.execute("SELECT COUNT(*) FROM orders")
        assert cursor.fetchone()[0] == 5
        conn.close()

    def test_backup_with_table_filter(self, sample_db, temp_dir):
        """Test backup with specific tables."""
        connector = SQLiteConnector(database=sample_db)
        output_path = os.path.join(temp_dir, "backup_filtered.sql")

        success, msg = connector.backup(
            output_path, backup_type="full",
            tables=["users", "settings"]
        )
        assert success

        with open(output_path, "r") as f:
            content = f.read()
        assert "users" in content
        assert "settings" in content
        # orders should not be in backup
        assert "INSERT INTO \"orders\"" not in content

    def test_backup_with_exclude(self, sample_db, temp_dir):
        """Test backup excluding specific tables."""
        connector = SQLiteConnector(database=sample_db)
        output_path = os.path.join(temp_dir, "backup_excluded.sql")

        success, msg = connector.backup(
            output_path, backup_type="full",
            exclude_tables=["settings"]
        )
        assert success

        with open(output_path, "r") as f:
            content = f.read()
        assert "users" in content
        assert "orders" in content

    def test_full_backup_and_restore(self, sample_db, temp_dir):
        """Test complete backup and restore cycle."""
        connector = SQLiteConnector(database=sample_db)
        backup_path = os.path.join(temp_dir, "backup.db")

        # Backup
        success, msg = connector.backup(backup_path, backup_type="full")
        assert success

        # Create a new database to restore into
        restored_db = os.path.join(temp_dir, "restored.db")
        # Create empty SQLite DB
        sqlite3.connect(restored_db).close()

        restore_connector = SQLiteConnector(database=restored_db)
        success, msg = restore_connector.restore(backup_path)
        assert success

        # Verify restored data
        conn = sqlite3.connect(restored_db)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        assert cursor.fetchone()[0] == 5
        cursor.execute("SELECT COUNT(*) FROM orders")
        assert cursor.fetchone()[0] == 5
        cursor.execute("SELECT COUNT(*) FROM settings")
        assert cursor.fetchone()[0] == 3
        conn.close()

    def test_sql_dump_restore(self, sample_db, temp_dir):
        """Test SQL dump backup and restore."""
        connector = SQLiteConnector(database=sample_db)
        backup_path = os.path.join(temp_dir, "backup.sql")

        # SQL dump backup (triggered by table filter)
        success, msg = connector.backup(
            backup_path, backup_type="full",
            tables=["users", "orders"]
        )
        assert success

        # Restore to new DB
        restored_db = os.path.join(temp_dir, "restored.db")
        restore_connector = SQLiteConnector(database=restored_db)
        success, msg = restore_connector.restore(backup_path)
        assert success

        conn = sqlite3.connect(restored_db)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        assert cursor.fetchone()[0] == 5
        conn.close()

    def test_selective_restore(self, sample_db, temp_dir):
        """Test selective table restore."""
        connector = SQLiteConnector(database=sample_db)
        backup_path = os.path.join(temp_dir, "backup.sql")

        # Create SQL dump
        success, _ = connector.backup(
            backup_path, backup_type="full",
            tables=["users", "orders", "settings"]
        )
        assert success

        # Restore only users table
        restored_db = os.path.join(temp_dir, "selective_restored.db")
        restore_connector = SQLiteConnector(database=restored_db)
        success, msg = restore_connector.restore(
            backup_path, tables=["users"]
        )
        assert success

    def test_get_database_size(self, sample_db):
        """Test database size retrieval."""
        connector = SQLiteConnector(database=sample_db)
        size = connector.get_database_size()
        assert size > 0

    def test_context_manager(self, sample_db):
        """Test context manager protocol."""
        with SQLiteConnector(database=sample_db) as connector:
            tables = connector.list_tables()
            assert len(tables) > 0


# ═══════════════════════════════════════════════════════════
# Connector Factory Tests
# ═══════════════════════════════════════════════════════════

class TestConnectorFactory:
    """Test connector factory function."""

    def test_get_sqlite_connector(self):
        """Test getting SQLite connector."""
        connector = get_connector("sqlite", database="test.db")
        assert isinstance(connector, SQLiteConnector)

    def test_get_connector_case_insensitive(self):
        """Test case-insensitive DBMS name."""
        connector = get_connector("SQLite", database="test.db")
        assert isinstance(connector, SQLiteConnector)

    def test_unsupported_dbms(self):
        """Test unsupported DBMS raises ValueError."""
        with pytest.raises(ValueError, match="Unsupported DBMS"):
            get_connector("oracle")

    def test_postgres_alias(self):
        """Test 'postgres' alias for PostgreSQL."""
        from dbbackup.connectors.postgresql_connector import PostgreSQLConnector
        connector = get_connector("postgres", database="test")
        assert isinstance(connector, PostgreSQLConnector)

    def test_mongo_alias(self):
        """Test 'mongo' alias for MongoDB."""
        from dbbackup.connectors.mongodb_connector import MongoDBConnector
        connector = get_connector("mongo", database="test")
        assert isinstance(connector, MongoDBConnector)


# ═══════════════════════════════════════════════════════════
# Local Storage Tests
# ═══════════════════════════════════════════════════════════

class TestLocalStorage:
    """Test local storage backend."""

    def test_store_file(self, temp_dir):
        """Test storing a backup file."""
        storage_dir = os.path.join(temp_dir, "storage")
        storage = LocalStorage(storage_dir)

        # Create a test file
        test_file = os.path.join(temp_dir, "test_backup.sql")
        with open(test_file, "w") as f:
            f.write("-- Test backup\n")

        result = storage.store(test_file)
        assert os.path.exists(result)
        assert "storage" in result

    def test_list_backups(self, temp_dir):
        """Test listing backup files."""
        storage_dir = os.path.join(temp_dir, "storage")
        storage = LocalStorage(storage_dir)

        # Create test files
        for i in range(3):
            path = os.path.join(storage_dir, f"backup_{i}.sql")
            with open(path, "w") as f:
                f.write(f"-- Backup {i}\n")

        backups = storage.list_backups()
        assert len(backups) == 3

    def test_delete_backup(self, temp_dir):
        """Test deleting a backup file."""
        storage_dir = os.path.join(temp_dir, "storage")
        storage = LocalStorage(storage_dir)

        path = os.path.join(storage_dir, "test.sql")
        with open(path, "w") as f:
            f.write("-- Test\n")

        assert storage.delete("test.sql")
        assert not os.path.exists(path)

    def test_delete_nonexistent(self, temp_dir):
        """Test deleting a nonexistent file."""
        storage = LocalStorage(os.path.join(temp_dir, "storage"))
        assert not storage.delete("nonexistent.sql")

    def test_retrieve_file(self, temp_dir):
        """Test retrieving a backup file."""
        storage_dir = os.path.join(temp_dir, "storage")
        storage = LocalStorage(storage_dir)

        # Store a file
        src = os.path.join(temp_dir, "original.sql")
        with open(src, "w") as f:
            f.write("-- Original backup\n")

        storage.store(src, "stored_backup.sql")

        # Retrieve it
        dest = os.path.join(temp_dir, "retrieved.sql")
        storage.retrieve("stored_backup.sql", dest)

        assert os.path.exists(dest)
        with open(dest) as f:
            assert f.read() == "-- Original backup\n"


# ═══════════════════════════════════════════════════════════
# Logger Tests
# ═══════════════════════════════════════════════════════════

class TestLogger:
    """Test logging and activity tracking."""

    def test_format_size(self):
        """Test file size formatting."""
        assert format_size(0) == "0.0 B"
        assert format_size(1023) == "1023.0 B"
        assert format_size(1024) == "1.0 KB"
        assert format_size(1048576) == "1.0 MB"
        assert format_size(1073741824) == "1.0 GB"

    def test_format_duration(self):
        """Test duration formatting."""
        assert format_duration(0.5) == "0.5s"
        assert format_duration(30) == "30.0s"
        assert format_duration(90) == "1m 30s"
        assert format_duration(3661) == "1h 1m"


# ═══════════════════════════════════════════════════════════
# CLI Tests
# ═══════════════════════════════════════════════════════════

class TestCLI:
    """Test CLI commands."""

    def test_help(self, runner):
        """Test help output."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "DBBackup" in result.output

    def test_version(self, runner):
        """Test version output."""
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "1.0.0" in result.output

    def test_init_command(self, runner, temp_dir):
        """Test init command creates config file."""
        config_path = os.path.join(temp_dir, "test_config.yaml")
        result = runner.invoke(cli, ["init", "--output", config_path])
        assert result.exit_code == 0
        assert os.path.exists(config_path)

    def test_test_command_sqlite(self, runner, sample_db):
        """Test the test command with SQLite."""
        result = runner.invoke(cli, [
            "test",
            "--dbms", "sqlite",
            "--database", sample_db,
        ])
        assert result.exit_code == 0
        assert "Connection Successful" in result.output

    def test_test_command_invalid_db(self, runner):
        """Test the test command with invalid database."""
        result = runner.invoke(cli, [
            "test",
            "--dbms", "sqlite",
            "--database", "/nonexistent/test.db",
        ])
        assert result.exit_code != 0

    def test_backup_command_sqlite(self, runner, sample_db, temp_dir):
        """Test backup command with SQLite."""
        output = os.path.join(temp_dir, "cli_backup.sql")
        result = runner.invoke(cli, [
            "backup",
            "--dbms", "sqlite",
            "--database", sample_db,
            "--output", output,
            "--compression", "none",
            "--no-notify",
        ])
        assert result.exit_code == 0
        assert "Backup Complete" in result.output or "Backup complete" in result.output

    def test_backup_with_compression(self, runner, sample_db, temp_dir):
        """Test backup with gzip compression."""
        output = os.path.join(temp_dir, "compressed_backup.db")
        result = runner.invoke(cli, [
            "backup",
            "--dbms", "sqlite",
            "--database", sample_db,
            "--output", output,
            "--compression", "gzip",
            "--no-notify",
        ])
        assert result.exit_code == 0

    def test_restore_command_sqlite(self, runner, sample_db, temp_dir):
        """Test full backup and restore cycle via CLI."""
        # First backup
        backup_path = os.path.join(temp_dir, "for_restore.db")
        result = runner.invoke(cli, [
            "backup",
            "--dbms", "sqlite",
            "--database", sample_db,
            "--output", backup_path,
            "--compression", "none",
            "--no-notify",
        ])
        assert result.exit_code == 0

        # Then restore
        restored_db = os.path.join(temp_dir, "restored_cli.db")
        sqlite3.connect(restored_db).close()  # create empty DB

        result = runner.invoke(cli, [
            "restore", backup_path,
            "--dbms", "sqlite",
            "--database", restored_db,
            "--no-confirm",
            "--no-notify",
        ])
        assert result.exit_code == 0

    def test_list_command(self, runner):
        """Test list command."""
        result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0

    def test_history_command(self, runner):
        """Test history command."""
        result = runner.invoke(cli, ["history"])
        assert result.exit_code == 0

    def test_cleanup_dry_run(self, runner):
        """Test cleanup with dry run."""
        result = runner.invoke(cli, ["cleanup", "--dry-run", "--retention-days", "0"])
        assert result.exit_code == 0


# ═══════════════════════════════════════════════════════════
# Integration Tests
# ═══════════════════════════════════════════════════════════

class TestIntegration:
    """End-to-end integration tests."""

    def test_full_workflow_sqlite(self, sample_db, temp_dir):
        """Test complete backup → compress → restore workflow."""
        # 1. Create connector
        connector = SQLiteConnector(database=sample_db)

        # 2. Test connection
        success, msg = connector.test_connection()
        assert success

        # 3. List tables
        connector.connect()
        tables = connector.list_tables()
        assert len(tables) == 3
        connector.disconnect()

        # 4. Backup
        backup_path = os.path.join(temp_dir, "integration_backup.sql")
        success, msg = connector.backup(
            backup_path, backup_type="full",
            tables=["users", "orders"]
        )
        assert success

        # 5. Compress
        compressed_path, orig_size, comp_size = compress_file(
            backup_path, method="gzip", level=6
        )
        assert comp_size < orig_size

        # 6. Decompress
        decompressed_path = decompress_file(compressed_path)
        assert os.path.exists(decompressed_path)

        # 7. Restore to new database
        restored_db = os.path.join(temp_dir, "restored.db")
        restore_connector = SQLiteConnector(database=restored_db)
        success, msg = restore_connector.restore(decompressed_path)
        assert success

        # 8. Verify restored data
        restore_connector.connect()
        tables = restore_connector.list_tables()
        assert "users" in tables
        restore_connector.disconnect()

        # 9. Store locally
        storage = LocalStorage(os.path.join(temp_dir, "stored_backups"))
        stored = storage.store(compressed_path)
        assert os.path.exists(stored)

        # 10. List stored backups
        backups = storage.list_backups()
        assert len(backups) == 1

    def test_multiple_compression_methods(self, sample_db, temp_dir):
        """Test backup with all compression methods."""
        connector = SQLiteConnector(database=sample_db)

        for method in ["gzip", "bzip2", "lzma"]:
            backup_path = os.path.join(temp_dir, f"backup_{method}.db")
            success, _ = connector.backup(backup_path, backup_type="full")
            assert success

            compressed_path, orig, comp = compress_file(
                backup_path, method=method, remove_original=True
            )
            assert os.path.exists(compressed_path)
            assert comp <= orig  # Should be same or smaller

            # Decompress and verify
            decompressed = decompress_file(compressed_path)
            conn = sqlite3.connect(decompressed)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM users")
            assert cursor.fetchone()[0] == 5
            conn.close()

    def test_backup_large_data(self, temp_dir):
        """Test backup with larger dataset."""
        db_path = os.path.join(temp_dir, "large.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE big_table (
                id INTEGER PRIMARY KEY,
                data TEXT,
                number REAL,
                flag INTEGER
            )
        """)

        # Insert 10,000 rows
        rows = [
            (i, f"Data entry number {i} with some extra text to make it larger", i * 1.5, i % 2)
            for i in range(10000)
        ]
        cursor.executemany("INSERT INTO big_table VALUES (?, ?, ?, ?)", rows)
        conn.commit()
        conn.close()

        connector = SQLiteConnector(database=db_path)
        backup_path = os.path.join(temp_dir, "large_backup.db")

        success, msg = connector.backup(backup_path)
        assert success
        assert os.path.getsize(backup_path) > 0

        # Compress and check ratio
        comp_path, orig, comp = compress_file(backup_path, method="gzip")
        ratio = get_compression_ratio(orig, comp)
        assert ratio > 0  # Should achieve some compression
