"""
MySQL database connector.

Supports full backups using SQL dump format,
with table filtering and large database handling.
"""

import os
import json
import subprocess
import shutil
from typing import Optional
from pathlib import Path

from .base import BaseConnector


class MySQLConnector(BaseConnector):
    """MySQL/MariaDB database connector."""

    def default_port(self) -> int:
        return 3306

    def dbms_name(self) -> str:
        return "mysql"

    def _get_connection_args(self) -> dict:
        """Build pymysql connection arguments."""
        args = {
            "host": self.host,
            "port": self.port,
            "user": self.username,
            "password": self.password,
            "database": self.database,
            "charset": "utf8mb4",
            "connect_timeout": 10,
        }
        if self.ssl:
            ssl_config = {}
            if self.ssl_ca:
                ssl_config["ca"] = self.ssl_ca
            if self.ssl_cert:
                ssl_config["cert"] = self.ssl_cert
            if self.ssl_key:
                ssl_config["key"] = self.ssl_key
            if ssl_config:
                args["ssl"] = ssl_config
        return args

    def test_connection(self) -> tuple[bool, str]:
        """Test MySQL connection."""
        try:
            import pymysql
            conn = pymysql.connect(**self._get_connection_args())
            cursor = conn.cursor()
            cursor.execute("SELECT VERSION()")
            version = cursor.fetchone()[0]
            cursor.close()
            conn.close()
            return True, f"Connected to MySQL {version}"
        except ImportError:
            return False, "pymysql is not installed. Run: pip install pymysql"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def connect(self):
        """Establish MySQL connection."""
        import pymysql
        self._connection = pymysql.connect(**self._get_connection_args())

    def disconnect(self):
        """Close MySQL connection."""
        if self._connection:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None

    def list_tables(self) -> list[str]:
        """List all tables in the MySQL database."""
        if not self._connection:
            self.connect()
        cursor = self._connection.cursor()
        cursor.execute("SHOW TABLES")
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
        Backup MySQL database using mysqldump or pymysql.

        Tries mysqldump first (faster, more reliable for large DBs),
        falls back to pure Python implementation.
        """
        # Try mysqldump first
        if shutil.which("mysqldump"):
            return self._backup_mysqldump(output_path, backup_type, tables, exclude_tables)
        else:
            return self._backup_python(output_path, backup_type, tables, exclude_tables)

    def _backup_mysqldump(
        self,
        output_path: str,
        backup_type: str,
        tables: Optional[list[str]],
        exclude_tables: Optional[list[str]],
    ) -> tuple[bool, str]:
        """Backup using mysqldump command-line tool."""
        try:
            cmd = [
                "mysqldump",
                f"--host={self.host}",
                f"--port={self.port}",
                f"--user={self.username}",
                f"--password={self.password}",
                "--single-transaction",
                "--routines",
                "--triggers",
                "--events",
                "--set-gtid-purged=OFF",
            ]

            if backup_type == "full":
                cmd.append("--all-databases" if not self.database else self.database)
            elif backup_type == "incremental":
                cmd.extend(["--flush-logs", "--master-data=2"])
                cmd.append(self.database)
            else:
                cmd.append(self.database)

            if tables:
                cmd.extend(tables)

            if exclude_tables:
                for table in exclude_tables:
                    cmd.append(f"--ignore-table={self.database}.{table}")

            with open(output_path, "w") as f:
                result = subprocess.run(
                    cmd, stdout=f, stderr=subprocess.PIPE,
                    timeout=3600, text=True
                )

            if result.returncode != 0:
                return False, f"mysqldump failed: {result.stderr.strip()}"

            size = os.path.getsize(output_path)
            return True, f"Backup complete ({size:,} bytes)"

        except subprocess.TimeoutExpired:
            return False, "Backup timed out after 1 hour"
        except Exception as e:
            return False, f"mysqldump error: {str(e)}"

    def _backup_python(
        self,
        output_path: str,
        backup_type: str,
        tables: Optional[list[str]],
        exclude_tables: Optional[list[str]],
    ) -> tuple[bool, str]:
        """Pure Python backup fallback using pymysql."""
        try:
            if not self._connection:
                self.connect()

            cursor = self._connection.cursor()
            all_tables = self.list_tables()

            if tables:
                target_tables = [t for t in all_tables if t in tables]
            else:
                target_tables = all_tables

            if exclude_tables:
                target_tables = [t for t in target_tables if t not in exclude_tables]

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(f"-- DBBackup MySQL Dump\n")
                f.write(f"-- Database: {self.database}\n")
                f.write(f"-- Backup Type: {backup_type}\n")
                f.write(f"-- ----------------------------------------\n\n")
                f.write(f"SET FOREIGN_KEY_CHECKS=0;\n")
                f.write(f"SET SQL_MODE='NO_AUTO_VALUE_ON_ZERO';\n\n")

                for table in target_tables:
                    # Get CREATE TABLE statement
                    cursor.execute(f"SHOW CREATE TABLE `{table}`")
                    create_stmt = cursor.fetchone()[1]
                    f.write(f"-- Table: {table}\n")
                    f.write(f"DROP TABLE IF EXISTS `{table}`;\n")
                    f.write(f"{create_stmt};\n\n")

                    # Get data
                    cursor.execute(f"SELECT * FROM `{table}`")
                    rows = cursor.fetchall()
                    if rows:
                        # Get column names
                        columns = [desc[0] for desc in cursor.description]
                        cols_str = ", ".join(f"`{c}`" for c in columns)

                        for row in rows:
                            values = []
                            for val in row:
                                if val is None:
                                    values.append("NULL")
                                elif isinstance(val, (int, float)):
                                    values.append(str(val))
                                elif isinstance(val, bytes):
                                    values.append(f"X'{val.hex()}'")
                                else:
                                    escaped = str(val).replace("\\", "\\\\").replace("'", "\\'")
                                    values.append(f"'{escaped}'")
                            vals_str = ", ".join(values)
                            f.write(f"INSERT INTO `{table}` ({cols_str}) VALUES ({vals_str});\n")
                        f.write("\n")

                f.write(f"SET FOREIGN_KEY_CHECKS=1;\n")

            cursor.close()
            size = os.path.getsize(output_path)
            return True, f"Backup complete ({size:,} bytes) [Python mode]"

        except Exception as e:
            return False, f"Python backup error: {str(e)}"

    def restore(
        self,
        input_path: str,
        tables: Optional[list[str]] = None,
        drop_existing: bool = False,
    ) -> tuple[bool, str]:
        """Restore MySQL database from SQL dump."""
        # Try mysql client first
        if shutil.which("mysql") and not tables:
            return self._restore_mysql_client(input_path, drop_existing)
        else:
            return self._restore_python(input_path, tables, drop_existing)

    def _restore_mysql_client(
        self,
        input_path: str,
        drop_existing: bool,
    ) -> tuple[bool, str]:
        """Restore using mysql command-line client."""
        try:
            cmd = [
                "mysql",
                f"--host={self.host}",
                f"--port={self.port}",
                f"--user={self.username}",
                f"--password={self.password}",
                self.database,
            ]

            with open(input_path, "r") as f:
                result = subprocess.run(
                    cmd, stdin=f, stderr=subprocess.PIPE,
                    timeout=3600, text=True
                )

            if result.returncode != 0:
                return False, f"mysql restore failed: {result.stderr.strip()}"

            return True, "Restore complete"

        except Exception as e:
            return False, f"Restore error: {str(e)}"

    def _restore_python(
        self,
        input_path: str,
        tables: Optional[list[str]],
        drop_existing: bool,
    ) -> tuple[bool, str]:
        """Restore using pymysql (supports selective table restore)."""
        try:
            if not self._connection:
                self.connect()

            cursor = self._connection.cursor()

            with open(input_path, "r", encoding="utf-8") as f:
                sql_content = f.read()

            # Split into individual statements
            statements = sql_content.split(";\n")
            executed = 0
            current_table = None

            for stmt in statements:
                stmt = stmt.strip()
                if not stmt or stmt.startswith("--"):
                    # Track current table from comments
                    if "-- Table:" in stmt:
                        current_table = stmt.split("-- Table:")[1].strip()
                    continue

                # If selective restore, skip non-matching tables
                if tables and current_table and current_table not in tables:
                    continue

                try:
                    cursor.execute(stmt)
                    executed += 1
                except Exception:
                    continue  # Skip individual statement errors

            self._connection.commit()
            cursor.close()
            return True, f"Restore complete ({executed} statements executed)"

        except Exception as e:
            return False, f"Restore error: {str(e)}"

    def get_database_size(self) -> int:
        """Get the size of the MySQL database in bytes."""
        try:
            if not self._connection:
                self.connect()
            cursor = self._connection.cursor()
            cursor.execute(
                "SELECT SUM(data_length + index_length) "
                "FROM information_schema.TABLES "
                "WHERE table_schema = %s",
                (self.database,)
            )
            result = cursor.fetchone()
            cursor.close()
            return int(result[0]) if result and result[0] else 0
        except Exception:
            return 0
