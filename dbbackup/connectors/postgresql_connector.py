"""
PostgreSQL database connector.

Supports full backups using pg_dump or psycopg2,
with table filtering and large database handling.
"""

import os
import subprocess
import shutil
from typing import Optional
from pathlib import Path

from .base import BaseConnector


class PostgreSQLConnector(BaseConnector):
    """PostgreSQL database connector."""

    def default_port(self) -> int:
        return 5432

    def dbms_name(self) -> str:
        return "postgresql"

    def _get_connection_args(self) -> dict:
        """Build psycopg2 connection arguments."""
        if self.connection_uri:
            return {"dsn": self.connection_uri}

        args = {
            "host": self.host,
            "port": self.port,
            "user": self.username,
            "password": self.password,
            "dbname": self.database,
            "connect_timeout": 10,
        }
        if self.ssl:
            args["sslmode"] = "require"
            if self.ssl_ca:
                args["sslrootcert"] = self.ssl_ca
            if self.ssl_cert:
                args["sslcert"] = self.ssl_cert
            if self.ssl_key:
                args["sslkey"] = self.ssl_key
        return args

    def _get_env(self) -> dict:
        """Get environment with PGPASSWORD set."""
        env = os.environ.copy()
        env["PGPASSWORD"] = self.password
        return env

    def test_connection(self) -> tuple[bool, str]:
        """Test PostgreSQL connection."""
        try:
            import psycopg2
            conn = psycopg2.connect(**self._get_connection_args())
            cursor = conn.cursor()
            cursor.execute("SELECT version()")
            version = cursor.fetchone()[0]
            cursor.close()
            conn.close()
            # Extract just the version number
            version_short = version.split(",")[0] if "," in version else version
            return True, f"Connected to {version_short}"
        except ImportError:
            return False, "psycopg2 is not installed. Run: pip install psycopg2-binary"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def connect(self):
        """Establish PostgreSQL connection."""
        import psycopg2
        self._connection = psycopg2.connect(**self._get_connection_args())
        self._connection.autocommit = True

    def disconnect(self):
        """Close PostgreSQL connection."""
        if self._connection:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None

    def list_tables(self) -> list[str]:
        """List all tables in the PostgreSQL database."""
        if not self._connection:
            self.connect()
        cursor = self._connection.cursor()
        cursor.execute(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' "
            "ORDER BY tablename"
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
        """Backup PostgreSQL database using pg_dump or psycopg2."""
        if shutil.which("pg_dump"):
            return self._backup_pgdump(output_path, backup_type, tables, exclude_tables)
        else:
            return self._backup_python(output_path, backup_type, tables, exclude_tables)

    def _backup_pgdump(
        self,
        output_path: str,
        backup_type: str,
        tables: Optional[list[str]],
        exclude_tables: Optional[list[str]],
    ) -> tuple[bool, str]:
        """Backup using pg_dump command-line tool."""
        try:
            cmd = [
                "pg_dump",
                f"--host={self.host}",
                f"--port={self.port}",
                f"--username={self.username}",
                "--no-password",
                "--format=plain",
                "--verbose",
                "--create",
                "--clean",
            ]

            if tables:
                for table in tables:
                    cmd.extend(["--table", table])

            if exclude_tables:
                for table in exclude_tables:
                    cmd.extend(["--exclude-table", table])

            cmd.append(self.database)

            with open(output_path, "w") as f:
                result = subprocess.run(
                    cmd, stdout=f, stderr=subprocess.PIPE,
                    env=self._get_env(), timeout=3600, text=True
                )

            if result.returncode != 0:
                stderr = result.stderr.strip()
                # pg_dump writes info to stderr even on success
                if "error" in stderr.lower() or "fatal" in stderr.lower():
                    return False, f"pg_dump failed: {stderr}"

            size = os.path.getsize(output_path)
            return True, f"Backup complete ({size:,} bytes)"

        except subprocess.TimeoutExpired:
            return False, "Backup timed out after 1 hour"
        except Exception as e:
            return False, f"pg_dump error: {str(e)}"

    def _backup_python(
        self,
        output_path: str,
        backup_type: str,
        tables: Optional[list[str]],
        exclude_tables: Optional[list[str]],
    ) -> tuple[bool, str]:
        """Pure Python backup using psycopg2."""
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
                f.write(f"-- DBBackup PostgreSQL Dump\n")
                f.write(f"-- Database: {self.database}\n")
                f.write(f"-- Backup Type: {backup_type}\n")
                f.write(f"-- ----------------------------------------\n\n")

                for table in target_tables:
                    # Get table schema
                    cursor.execute(
                        "SELECT column_name, data_type, character_maximum_length, "
                        "is_nullable, column_default "
                        "FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = %s "
                        "ORDER BY ordinal_position",
                        (table,)
                    )
                    columns = cursor.fetchall()

                    f.write(f"-- Table: {table}\n")
                    f.write(f"DROP TABLE IF EXISTS \"{table}\" CASCADE;\n")

                    # Build CREATE TABLE
                    col_defs = []
                    for col_name, data_type, max_len, nullable, default in columns:
                        col_def = f'    "{col_name}" {data_type}'
                        if max_len:
                            col_def += f"({max_len})"
                        if nullable == "NO":
                            col_def += " NOT NULL"
                        if default:
                            col_def += f" DEFAULT {default}"
                        col_defs.append(col_def)

                    f.write(f'CREATE TABLE "{table}" (\n')
                    f.write(",\n".join(col_defs))
                    f.write("\n);\n\n")

                    # Get data
                    cursor.execute(f'SELECT * FROM "{table}"')
                    col_names = [desc[0] for desc in cursor.description]
                    rows = cursor.fetchall()

                    if rows:
                        cols_str = ", ".join(f'"{c}"' for c in col_names)
                        for row in rows:
                            values = []
                            for val in row:
                                if val is None:
                                    values.append("NULL")
                                elif isinstance(val, (int, float)):
                                    values.append(str(val))
                                elif isinstance(val, bool):
                                    values.append("TRUE" if val else "FALSE")
                                elif isinstance(val, bytes):
                                    values.append(f"E'\\\\x{val.hex()}'")
                                else:
                                    escaped = str(val).replace("'", "''")
                                    values.append(f"'{escaped}'")
                            vals_str = ", ".join(values)
                            f.write(f'INSERT INTO "{table}" ({cols_str}) VALUES ({vals_str});\n')
                        f.write("\n")

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
        """Restore PostgreSQL database from SQL dump."""
        if shutil.which("psql") and not tables:
            return self._restore_psql(input_path, drop_existing)
        else:
            return self._restore_python(input_path, tables, drop_existing)

    def _restore_psql(
        self,
        input_path: str,
        drop_existing: bool,
    ) -> tuple[bool, str]:
        """Restore using psql command-line client."""
        try:
            cmd = [
                "psql",
                f"--host={self.host}",
                f"--port={self.port}",
                f"--username={self.username}",
                "--no-password",
                "--dbname", self.database,
                "--file", input_path,
            ]

            result = subprocess.run(
                cmd, stderr=subprocess.PIPE,
                env=self._get_env(), timeout=3600, text=True
            )

            if result.returncode != 0:
                stderr = result.stderr.strip()
                if "fatal" in stderr.lower():
                    return False, f"psql restore failed: {stderr}"

            return True, "Restore complete"

        except Exception as e:
            return False, f"Restore error: {str(e)}"

    def _restore_python(
        self,
        input_path: str,
        tables: Optional[list[str]],
        drop_existing: bool,
    ) -> tuple[bool, str]:
        """Restore using psycopg2."""
        try:
            if not self._connection:
                self.connect()

            cursor = self._connection.cursor()

            with open(input_path, "r", encoding="utf-8") as f:
                sql_content = f.read()

            statements = sql_content.split(";\n")
            executed = 0
            current_table = None

            for stmt in statements:
                stmt = stmt.strip()
                if not stmt or stmt.startswith("--"):
                    if "-- Table:" in stmt:
                        current_table = stmt.split("-- Table:")[1].strip()
                    continue

                if tables and current_table and current_table not in tables:
                    continue

                try:
                    cursor.execute(stmt)
                    executed += 1
                except Exception:
                    continue

            cursor.close()
            return True, f"Restore complete ({executed} statements executed)"

        except Exception as e:
            return False, f"Restore error: {str(e)}"

    def get_database_size(self) -> int:
        """Get the size of the PostgreSQL database in bytes."""
        try:
            if not self._connection:
                self.connect()
            cursor = self._connection.cursor()
            cursor.execute(
                "SELECT pg_database_size(%s)",
                (self.database,)
            )
            result = cursor.fetchone()
            cursor.close()
            return int(result[0]) if result else 0
        except Exception:
            return 0
