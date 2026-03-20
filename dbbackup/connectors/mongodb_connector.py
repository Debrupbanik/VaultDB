"""
MongoDB database connector.

Supports full backups using mongodump/BSON export or pymongo,
with collection filtering and large database handling.
"""

import os
import json
import subprocess
import shutil
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path
from bson import json_util

from .base import BaseConnector


class MongoDBConnector(BaseConnector):
    """MongoDB database connector."""

    def default_port(self) -> int:
        return 27017

    def dbms_name(self) -> str:
        return "mongodb"

    def __init__(self, auth_database: str = "admin", **kwargs):
        super().__init__(**kwargs)
        self.auth_database = auth_database

    def _get_connection_uri(self) -> str:
        """Build MongoDB connection URI."""
        if self.connection_uri:
            return self.connection_uri

        auth = ""
        if self.username and self.password:
            auth = f"{self.username}:{self.password}@"

        uri = f"mongodb://{auth}{self.host}:{self.port}/{self.database}"
        params = []
        if self.username:
            params.append(f"authSource={self.auth_database}")
        if self.ssl:
            params.append("tls=true")
            if self.ssl_ca:
                params.append(f"tlsCAFile={self.ssl_ca}")
            if self.ssl_cert:
                params.append(f"tlsCertificateKeyFile={self.ssl_cert}")
        if params:
            uri += "?" + "&".join(params)
        return uri

    def test_connection(self) -> tuple[bool, str]:
        """Test MongoDB connection."""
        try:
            import pymongo

            client = pymongo.MongoClient(
                self._get_connection_uri(),
                serverSelectionTimeoutMS=10000,
            )
            # Force a connection attempt
            server_info = client.server_info()
            version = server_info.get("version", "unknown")
            client.close()
            return True, f"Connected to MongoDB {version}"
        except ImportError:
            return False, "pymongo is not installed. Run: pip install pymongo"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def connect(self):
        """Establish MongoDB connection."""
        import pymongo

        self._client = pymongo.MongoClient(
            self._get_connection_uri(),
            serverSelectionTimeoutMS=10000,
        )
        self._connection = self._client[self.database]

    def disconnect(self):
        """Close MongoDB connection."""
        if hasattr(self, "_client") and self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
            self._connection = None

    def list_tables(self) -> list[str]:
        """List all collections in the MongoDB database."""
        if not self._connection:
            self.connect()
        return sorted(self._connection.list_collection_names())

    def backup(
        self,
        output_path: str,
        backup_type: str = "full",
        tables: Optional[list[str]] = None,
        exclude_tables: Optional[list[str]] = None,
    ) -> tuple[bool, str]:
        """Backup MongoDB database."""
        if shutil.which("mongodump"):
            return self._backup_mongodump(output_path, backup_type, tables, exclude_tables)
        else:
            return self._backup_python(output_path, backup_type, tables, exclude_tables)

    def _backup_mongodump(
        self,
        output_path: str,
        backup_type: str,
        tables: Optional[list[str]],
        exclude_tables: Optional[list[str]],
    ) -> tuple[bool, str]:
        """Backup using mongodump command-line tool."""
        try:
            cmd = [
                "mongodump",
                f"--uri={self._get_connection_uri()}",
                f"--archive={output_path}",
            ]

            if tables:
                for collection in tables:
                    cmd.extend(["--collection", collection])

            if exclude_tables:
                for collection in exclude_tables:
                    cmd.extend(["--excludeCollection", collection])

            result = subprocess.run(cmd, stderr=subprocess.PIPE, timeout=3600, text=True)

            if result.returncode != 0:
                return False, f"mongodump failed: {result.stderr.strip()}"

            size = os.path.getsize(output_path)
            return True, f"Backup complete ({size:,} bytes)"

        except subprocess.TimeoutExpired:
            return False, "Backup timed out after 1 hour"
        except Exception as e:
            return False, f"mongodump error: {str(e)}"

    def _backup_python(
        self,
        output_path: str,
        backup_type: str,
        tables: Optional[list[str]],
        exclude_tables: Optional[list[str]],
    ) -> tuple[bool, str]:
        """Pure Python backup using pymongo (JSON export)."""
        try:
            if not self._connection:
                self.connect()

            collections = self.list_tables()

            if tables:
                collections = [c for c in collections if c in tables]
            if exclude_tables:
                collections = [c for c in collections if c not in exclude_tables]

            backup_data = {
                "_metadata": {
                    "dbms": "mongodb",
                    "database": self.database,
                    "backup_type": backup_type,
                    "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "collections": collections,
                },
                "collections": {},
            }

            for collection_name in collections:
                collection = self._connection[collection_name]
                documents = list(collection.find())
                # Serialize using bson json_util for proper type handling
                backup_data["collections"][collection_name] = json.loads(json_util.dumps(documents))

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(backup_data, f, indent=2, default=str)

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
        """Restore MongoDB database."""
        # Check if it's a mongodump archive or JSON export
        try:
            with open(input_path, "r") as f:
                first_char = f.read(1)
            if first_char == "{":
                return self._restore_python(input_path, tables, drop_existing)
        except UnicodeDecodeError:
            pass

        if shutil.which("mongorestore"):
            return self._restore_mongorestore(input_path, drop_existing)
        else:
            return self._restore_python(input_path, tables, drop_existing)

    def _restore_mongorestore(
        self,
        input_path: str,
        drop_existing: bool,
    ) -> tuple[bool, str]:
        """Restore using mongorestore."""
        try:
            cmd = [
                "mongorestore",
                f"--uri={self._get_connection_uri()}",
                f"--archive={input_path}",
            ]
            if drop_existing:
                cmd.append("--drop")

            result = subprocess.run(cmd, stderr=subprocess.PIPE, timeout=3600, text=True)

            if result.returncode != 0:
                return False, f"mongorestore failed: {result.stderr.strip()}"

            return True, "Restore complete"

        except Exception as e:
            return False, f"Restore error: {str(e)}"

    def _restore_python(
        self,
        input_path: str,
        tables: Optional[list[str]],
        drop_existing: bool,
    ) -> tuple[bool, str]:
        """Restore from JSON export using pymongo."""
        try:
            if not self._connection:
                self.connect()

            with open(input_path, "r", encoding="utf-8") as f:
                backup_data = json.load(f)

            if "collections" not in backup_data:
                return False, "Invalid backup format: missing 'collections' key"

            restored = 0
            for collection_name, documents in backup_data["collections"].items():
                if tables and collection_name not in tables:
                    continue

                collection = self._connection[collection_name]

                if drop_existing:
                    collection.drop()

                if documents:
                    # Deserialize BSON extended JSON
                    docs = json_util.loads(json.dumps(documents))
                    if isinstance(docs, list) and docs:
                        collection.insert_many(docs)
                        restored += len(docs)

            return True, f"Restore complete ({restored} documents restored)"

        except Exception as e:
            return False, f"Restore error: {str(e)}"

    def get_database_size(self) -> int:
        """Get the size of the MongoDB database in bytes."""
        try:
            if not self._connection:
                self.connect()
            stats = self._connection.command("dbStats")
            return int(stats.get("dataSize", 0))
        except Exception:
            return 0
