"""
Configuration management for DBBackup utility.

Handles loading/saving YAML configuration files for database connections,
storage settings, notification preferences, and scheduling options.
"""

import os
import yaml
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional


# Default configuration directory
CONFIG_DIR = Path.home() / ".dbbackup"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
LOG_DIR = CONFIG_DIR / "logs"
BACKUP_DIR = CONFIG_DIR / "backups"


@dataclass
class DatabaseConfig:
    """Database connection configuration."""
    dbms: str = "sqlite"           # mysql, postgresql, mongodb, sqlite
    host: str = "localhost"
    port: int = 0                  # 0 = use default for DBMS
    username: str = ""
    password: str = ""
    database: str = ""
    auth_database: str = "admin"   # MongoDB auth database
    connection_uri: str = ""       # Optional: full connection URI
    ssl: bool = False
    ssl_ca: str = ""
    ssl_cert: str = ""
    ssl_key: str = ""

    def get_default_port(self) -> int:
        """Return the default port for the selected DBMS."""
        defaults = {
            "mysql": 3306,
            "postgresql": 5432,
            "mongodb": 27017,
            "sqlite": 0,
        }
        return defaults.get(self.dbms, 0)

    def effective_port(self) -> int:
        """Return the configured port or the default."""
        return self.port if self.port != 0 else self.get_default_port()


@dataclass
class StorageConfig:
    """Backup storage configuration."""
    local_path: str = str(BACKUP_DIR)
    # AWS S3
    s3_bucket: str = ""
    s3_prefix: str = "dbbackup/"
    s3_region: str = "us-east-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_endpoint_url: str = ""      # For S3-compatible services
    # Google Cloud Storage
    gcs_bucket: str = ""
    gcs_prefix: str = "dbbackup/"
    gcs_credentials_file: str = ""
    # Azure Blob Storage
    azure_container: str = ""
    azure_prefix: str = "dbbackup/"
    azure_connection_string: str = ""


@dataclass
class NotificationConfig:
    """Notification settings."""
    slack_webhook_url: str = ""
    slack_channel: str = ""
    slack_username: str = "DBBackup Bot"
    notify_on_success: bool = True
    notify_on_failure: bool = True


@dataclass
class ScheduleConfig:
    """Backup scheduling configuration."""
    enabled: bool = False
    cron_expression: str = "0 2 * * *"  # Default: daily at 2 AM
    interval_minutes: int = 0            # Alternative: interval-based
    retention_days: int = 30             # Days to keep old backups
    max_backups: int = 0                 # Max backups to keep (0 = unlimited)


@dataclass
class BackupConfig:
    """Backup operation settings."""
    backup_type: str = "full"       # full, incremental, differential
    compression: str = "gzip"       # gzip, bzip2, lzma, none
    compression_level: int = 6      # 1-9
    encrypt: bool = False
    encryption_key: str = ""
    include_tables: list = field(default_factory=list)  # Empty = all tables
    exclude_tables: list = field(default_factory=list)
    chunk_size: int = 64 * 1024 * 1024  # 64MB chunks for large DBs


@dataclass
class AppConfig:
    """Root application configuration."""
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    notification: NotificationConfig = field(default_factory=NotificationConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)


def ensure_dirs():
    """Ensure required directories exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load configuration from YAML file."""
    path = Path(config_path) if config_path else CONFIG_FILE
    if not path.exists():
        return AppConfig()

    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}

    config = AppConfig()
    if "database" in data:
        config.database = DatabaseConfig(**{
            k: v for k, v in data["database"].items()
            if k in DatabaseConfig.__dataclass_fields__
        })
    if "storage" in data:
        config.storage = StorageConfig(**{
            k: v for k, v in data["storage"].items()
            if k in StorageConfig.__dataclass_fields__
        })
    if "notification" in data:
        config.notification = NotificationConfig(**{
            k: v for k, v in data["notification"].items()
            if k in NotificationConfig.__dataclass_fields__
        })
    if "schedule" in data:
        config.schedule = ScheduleConfig(**{
            k: v for k, v in data["schedule"].items()
            if k in ScheduleConfig.__dataclass_fields__
        })
    if "backup" in data:
        config.backup = BackupConfig(**{
            k: v for k, v in data["backup"].items()
            if k in BackupConfig.__dataclass_fields__
        })

    return config


def save_config(config: AppConfig, config_path: Optional[str] = None):
    """Save configuration to YAML file."""
    ensure_dirs()
    path = Path(config_path) if config_path else CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    data = asdict(config)
    # Mask sensitive fields for display purposes in exports
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def generate_sample_config(output_path: str):
    """Generate a sample configuration file with defaults and comments."""
    sample = """# ═══════════════════════════════════════════════════════════
# DBBackup Configuration File
# ═══════════════════════════════════════════════════════════

# Database Connection Settings
database:
  # Supported: mysql, postgresql, mongodb, sqlite
  dbms: postgresql
  host: localhost
  port: 5432
  username: myuser
  password: mypassword
  database: mydb
  # MongoDB-specific: authentication database
  auth_database: admin
  # Optional: use a full connection URI instead of individual params
  # connection_uri: "postgresql://user:pass@host:5432/mydb"
  # SSL settings
  ssl: false
  ssl_ca: ""
  ssl_cert: ""
  ssl_key: ""

# Storage Configuration
storage:
  # Local backup directory
  local_path: ~/.dbbackup/backups

  # AWS S3 (optional)
  s3_bucket: ""
  s3_prefix: "dbbackup/"
  s3_region: "us-east-1"
  s3_access_key: ""
  s3_secret_key: ""
  s3_endpoint_url: ""

  # Google Cloud Storage (optional)
  gcs_bucket: ""
  gcs_prefix: "dbbackup/"
  gcs_credentials_file: ""

  # Azure Blob Storage (optional)
  azure_container: ""
  azure_prefix: "dbbackup/"
  azure_connection_string: ""

# Notification Settings
notification:
  slack_webhook_url: ""
  slack_channel: "#backups"
  slack_username: "DBBackup Bot"
  notify_on_success: true
  notify_on_failure: true

# Schedule Settings
schedule:
  enabled: false
  # Cron expression (minute hour day month weekday)
  cron_expression: "0 2 * * *"
  # Or use interval in minutes (overrides cron if > 0)
  interval_minutes: 0
  # Retention policy
  retention_days: 30
  max_backups: 0  # 0 = unlimited

# Backup Settings
backup:
  # Types: full, incremental, differential
  backup_type: full
  # Compression: gzip, bzip2, lzma, none
  compression: gzip
  compression_level: 6
  # Encryption (AES-256)
  encrypt: false
  encryption_key: ""
  # Table filtering (empty = all tables)
  include_tables: []
  exclude_tables: []
  # Chunk size for large databases (bytes)
  chunk_size: 67108864  # 64MB
"""
    with open(output_path, "w") as f:
        f.write(sample)
