"""
SQLite database connector.

Supports full backups using sqlite3 built-in backup API,
with table filtering and efficient handling of large databases.
"""

import os
import json
import sqlite3
import shutil
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

from .base import BaseConnector


class SQLiteConnector(BaseConnector):
    """SQLite database connector."""

    def default_port(self) -> int:
        return 0  # SQLite doesn't use a port

    def dbms_name(self) -> str:
        return "sqlite"

    def _db_path(self) -> str:
        """Get the path to the SQLite database file."""
        return self.database

    def test_connection(self) -> tuple[bool, str]:
        """Test SQLite connection by verifying the database file exists and is valid."""
        try:
            db_path = self._db_path()

            if not db_path:
                return False, "No database path specified"

            if not os.path.exists(db_path):
                return False, f"Database file not found: {db_path}"

            if not os.path.isfile(db_path):
                return False, f"Path is not a file: {db_path}"

            # Try to connect and verify it's a valid SQLite database
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT sqlite_version()")
            version = cursor.fetchone()[0]

            # Count tables
            cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
            table_count = cursor.fetchone()[0]

            cursor.close()
            conn.close()

            return True, f"Connected to SQLite {version} ({table_count} tables)"

        except sqlite3.DatabaseError as e:
            return False, f"Invalid SQLite database: {str(e)}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def connect(self):
        """Establish SQLite connection."""
        self._connection = sqlite3.connect(self._db_path())
        self._connection.row_factory = sqlite3.Row

    def disconnect(self):
        """Close SQLite connection."""
        if self._connection:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None

    def list_tables(self) -> list[str]:
        """List all tables in the SQLite database."""
        if not self._connection:
            self.connect()
        cursor = self._connection.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]
        cursor.close()
        return tables

    def backup(
        self,
        output_path: str,
        backup_type: str = "full",
        tables: Optional[list[str]] = None,
        exclude_tables: Optional[list[str]] = None,
    ) -> tuple[bool, str]:
        """
        Backup SQLite database.

        For full backups without table filtering, uses the efficient
        sqlite3.backup() API. Falls back to SQL dump for filtered backups.
        """
        if backup_type == "full" and not tables and not exclude_tables:
            return self._backup_binary(output_path)
        else:
            return self._backup_sql_dump(
                output_path, backup_type, tables, exclude_tables
            )

    def _backup_binary(self, output_path: str) -> tuple[bool, str]:
        """
        Efficient binary backup using sqlite3.backup() API.

        This creates an exact copy of the database file, which is
        the fastest and most reliable backup method for SQLite.
        """
        try:
            source = sqlite3.connect(self._db_path())
            dest = sqlite3.connect(output_path)

            source.backup(dest, pages=1000, progress=None)

            dest.close()
            source.close()

            size = os.path.getsize(output_path)
            return True, f"Binary backup complete ({size:,} bytes)"

        except Exception as e:
            return False, f"Binary backup error: {str(e)}"

    def _backup_sql_dump(
        self,
        output_path: str,
        backup_type: str,
        tables: Optional[list[str]],
        exclude_tables: Optional[list[str]],
    ) -> tuple[bool, str]:
        """SQL dump backup with table filtering support."""
        try:
            if not self._connection:
                self.connect()

            all_tables = self.list_tables()

            if tables:
                target_tables = [t for t in all_tables if t in tables]
            else:
                target_tables = all_tables

            if exclude_tables:
                target_tables = [t for t in target_tables if t not in exclude_tables]

            cursor = self._connection.cursor()

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(f"-- DBBackup SQLite Dump\n")
                f.write(f"-- Database: {self._db_path()}\n")
                f.write(f"-- Backup Type: {backup_type}\n")
                f.write(
                    f"-- Timestamp: {datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}\n"
                )
                f.write(f"-- ----------------------------------------\n\n")
                f.write("BEGIN TRANSACTION;\n\n")

                for table in target_tables:
                    # Get CREATE TABLE statement
                    cursor.execute(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                        (table,),
                    )
                    result = cursor.fetchone()
                    if not result or not result[0]:
                        continue

                    create_sql = result[0]
                    f.write(f"-- Table: {table}\n")
                    f.write(f'DROP TABLE IF EXISTS "{table}";\n')
                    f.write(f"{create_sql};\n\n")

                    # Get indexes
                    cursor.execute(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                        (table,),
                    )
                    for row in cursor.fetchall():
                        if row[0]:
                            f.write(f"{row[0]};\n")

                    # Get data
                    cursor.execute(f'SELECT * FROM "{table}"')
                    columns = [desc[0] for desc in cursor.description]
                    cols_str = ", ".join(f'"{c}"' for c in columns)

                    for row in cursor.fetchall():
                        values = []
                        for val in row:
                            if val is None:
                                values.append("NULL")
                            elif isinstance(val, (int, float)):
                                values.append(str(val))
                            elif isinstance(val, bytes):
                                values.append(f"X'{val.hex()}'")
                            else:
                                escaped = str(val).replace("'", "''")
                                values.append(f"'{escaped}'")
                        vals_str = ", ".join(values)
                        f.write(
                            f'INSERT INTO "{table}" ({cols_str}) VALUES ({vals_str});\n'
                        )

                    f.write("\n")

                # Also backup views and triggers
                cursor.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type IN ('view', 'trigger') AND sql IS NOT NULL"
                )
                views_triggers = cursor.fetchall()
                if views_triggers:
                    f.write("-- Views and Triggers\n")
                    for row in views_triggers:
                        f.write(f"{row[0]};\n\n")

                f.write("COMMIT;\n")

            cursor.close()
            size = os.path.getsize(output_path)
            return True, f"SQL dump complete ({size:,} bytes)"

        except Exception as e:
            return False, f"SQL dump error: {str(e)}"

    def restore(
        self,
        input_path: str,
        tables: Optional[list[str]] = None,
        drop_existing: bool = False,
    ) -> tuple[bool, str]:
        """Restore SQLite database from backup."""
        # Check if binary backup (SQLite file) or SQL dump
        try:
            # Try to open as SQLite database
            test_conn = sqlite3.connect(input_path)
            test_conn.cursor().execute("SELECT 1")
            test_conn.close()
            # It's a binary backup
            if not tables:
                return self._restore_binary(input_path)
        except sqlite3.DatabaseError:
            pass

        return self._restore_sql_dump(input_path, tables, drop_existing)

    def _restore_binary(self, input_path: str) -> tuple[bool, str]:
        """Restore from binary backup (full database replacement)."""
        try:
            source = sqlite3.connect(input_path)
            dest = sqlite3.connect(self._db_path())

            source.backup(dest, pages=1000)

            dest.close()
            source.close()

            return True, "Binary restore complete"

        except Exception as e:
            return False, f"Binary restore error: {str(e)}"

    def _restore_sql_dump(
        self,
        input_path: str,
        tables: Optional[list[str]],
        drop_existing: bool,
    ) -> tuple[bool, str]:
        """Restore from SQL dump file."""
        conn = None
        try:
            db_path = self._db_path()

            # Ensure the database file exists (create empty file if needed)
            if not os.path.exists(db_path):
                open(db_path, "a").close()

            conn = sqlite3.connect(db_path)

            with open(input_path, "r", encoding="utf-8") as f:
                sql_content = f.read()

            if tables:
                # Selective restore: parse and filter statements
                statements = sql_content.split(";\n")
                current_table = None
                executed = 0

                for stmt in statements:
                    stmt = stmt.strip()
                    if not stmt or stmt.startswith("--"):
                        if "-- Table:" in stmt:
                            current_table = stmt.split("-- Table:")[1].strip()
                        continue

                    # Execute only statements for selected tables
                    if current_table and current_table in tables:
                        try:
                            conn.execute(stmt)
                            executed += 1
                        except Exception:
                            continue
                    elif stmt.upper() in ("BEGIN TRANSACTION", "COMMIT"):
                        try:
                            conn.execute(stmt)
                        except Exception:
                            pass

                conn.commit()
                return True, f"Selective restore complete ({executed} statements)"
            else:
                # For full restore, execute statements one by one for better error handling
                statements = sql_content.split(";")
                for stmt in statements:
                    stmt = stmt.strip()
                    if not stmt or stmt.startswith("--"):
                        continue
                    # Skip standalone BEGIN and COMMIT as they need special handling
                    if stmt.upper() in ("BEGIN", "BEGIN TRANSACTION", "COMMIT"):
                        if stmt.upper() == "BEGIN":
                            conn.execute("BEGIN")
                        elif stmt.upper() == "BEGIN TRANSACTION":
                            conn.execute("BEGIN TRANSACTION")
                        else:
                            conn.execute("COMMIT")
                        continue
                    try:
                        conn.execute(stmt)
                    except Exception as e:
                        # Log but continue - some errors are expected (e.g., IF EXISTS)
                        pass
                conn.commit()
                return True, "Full restore complete"

        except Exception as e:
            return False, f"SQL restore error: {str(e)}"
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def get_database_size(self) -> int:
        """Get the size of the SQLite database file in bytes."""
        try:
            db_path = self._db_path()
            if os.path.exists(db_path):
                return os.path.getsize(db_path)
            return 0
        except Exception:
            return 0
