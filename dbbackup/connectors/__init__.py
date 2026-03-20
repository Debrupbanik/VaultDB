"""
Database connectors for DBBackup utility.

Each connector provides a unified interface for:
- Testing database connectivity
- Performing backup operations (full, incremental, differential)
- Restoring from backup files
- Listing available tables/collections
"""

from .base import BaseConnector
from .mysql_connector import MySQLConnector
from .postgresql_connector import PostgreSQLConnector
from .mongodb_connector import MongoDBConnector
from .sqlite_connector import SQLiteConnector


CONNECTOR_MAP = {
    "mysql": MySQLConnector,
    "postgresql": PostgreSQLConnector,
    "postgres": PostgreSQLConnector,
    "mongodb": MongoDBConnector,
    "mongo": MongoDBConnector,
    "sqlite": SQLiteConnector,
}


def get_connector(dbms: str, **kwargs) -> BaseConnector:
    """Factory function to get the appropriate database connector."""
    dbms_lower = dbms.lower().strip()
    connector_class = CONNECTOR_MAP.get(dbms_lower)
    if not connector_class:
        supported = ", ".join(sorted(set(
            k for k, v in CONNECTOR_MAP.items()
            if k == v.__name__.replace("Connector", "").lower()
        )))
        raise ValueError(
            f"Unsupported DBMS: '{dbms}'. Supported: {supported}"
        )
    return connector_class(**kwargs)
