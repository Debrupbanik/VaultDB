"""
Base database connector interface.

All database-specific connectors must implement this abstract base class
to ensure a consistent API across different DBMS.
"""

from abc import ABC, abstractmethod
from typing import Optional
from pathlib import Path


class BaseConnector(ABC):
    """Abstract base class for database connectors."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 0,
        username: str = "",
        password: str = "",
        database: str = "",
        connection_uri: str = "",
        ssl: bool = False,
        ssl_ca: str = "",
        ssl_cert: str = "",
        ssl_key: str = "",
        **kwargs,
    ):
        self.host = host
        self.port = port or self.default_port()
        self.username = username
        self.password = password
        self.database = database
        self.connection_uri = connection_uri
        self.ssl = ssl
        self.ssl_ca = ssl_ca
        self.ssl_cert = ssl_cert
        self.ssl_key = ssl_key
        self._connection = None

    @abstractmethod
    def default_port(self) -> int:
        """Return the default port for this DBMS."""
        pass

    @abstractmethod
    def dbms_name(self) -> str:
        """Return the name of the DBMS."""
        pass

    @abstractmethod
    def test_connection(self) -> tuple[bool, str]:
        """
        Test the database connection.

        Returns:
            Tuple of (success: bool, message: str)
        """
        pass

    @abstractmethod
    def connect(self):
        """Establish a connection to the database."""
        pass

    @abstractmethod
    def disconnect(self):
        """Close the database connection."""
        pass

    @abstractmethod
    def list_tables(self) -> list[str]:
        """List all tables/collections in the database."""
        pass

    @abstractmethod
    def backup(
        self,
        output_path: str,
        backup_type: str = "full",
        tables: Optional[list[str]] = None,
        exclude_tables: Optional[list[str]] = None,
    ) -> tuple[bool, str]:
        """
        Perform a database backup.

        Args:
            output_path: Path to write the backup file
            backup_type: Type of backup (full, incremental, differential)
            tables: Specific tables to include (None = all)
            exclude_tables: Tables to exclude

        Returns:
            Tuple of (success: bool, message: str)
        """
        pass

    @abstractmethod
    def restore(
        self,
        input_path: str,
        tables: Optional[list[str]] = None,
        drop_existing: bool = False,
    ) -> tuple[bool, str]:
        """
        Restore a database from backup.

        Args:
            input_path: Path to the backup file
            tables: Specific tables to restore (None = all)
            drop_existing: Whether to drop existing tables before restore

        Returns:
            Tuple of (success: bool, message: str)
        """
        pass

    @abstractmethod
    def get_database_size(self) -> int:
        """Return the approximate size of the database in bytes."""
        pass

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False
