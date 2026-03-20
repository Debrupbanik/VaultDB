# 🗄️ DBBackup — Database Backup & Restore Utility

A powerful, cross-platform CLI utility for backing up and restoring any database. Supports **MySQL**, **PostgreSQL**, **MongoDB**, and **SQLite** with automatic compression, cloud storage, scheduling, and Slack notifications.

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-blue.svg" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="MIT License">
  <img src="https://img.shields.io/badge/Platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg" alt="Platform">
</p>

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🗄️ **Multi-DBMS Support** | MySQL, PostgreSQL, MongoDB, SQLite |
| 💾 **Backup Types** | Full, Incremental, Differential |
| 📦 **Compression** | gzip, bzip2, lzma with configurable levels |
| ☁️ **Cloud Storage** | AWS S3, Google Cloud Storage, Azure Blob |
| ⏰ **Scheduling** | Interval-based, cron-like, or daily/weekly |
| 📢 **Notifications** | Slack webhooks on success/failure |
| 🔄 **Restore** | Full or selective table/collection restore |
| 📜 **Activity Logging** | Structured JSON logs with history viewer |
| 🧹 **Cleanup** | Retention-based old backup removal |
| 🔒 **Secure** | SSL/TLS connections, credential masking |

---

## 📦 Installation

### From Source

```bash
# Clone and install
cd "Database_backup utility"
pip install -e .

# Verify installation
dbbackup --version
```

### Install Dependencies Only

```bash
pip install click rich pyyaml boto3 google-cloud-storage azure-storage-blob \
  pymysql psycopg2-binary pymongo schedule requests cryptography
```

---

## 🚀 Quick Start

### 1. Initialize Configuration

```bash
dbbackup init
```

This creates a sample configuration file at `~/.dbbackup/config.yaml`.

### 2. Test Connection

```bash
# SQLite
dbbackup test --dbms sqlite --database ./myapp.db

# PostgreSQL
dbbackup test --dbms postgresql --host localhost --port 5432 \
  --username admin --password secret --database myapp

# MySQL
dbbackup test --dbms mysql --host localhost --username root \
  --password mypass --database orders

# MongoDB
dbbackup test --dbms mongodb --host localhost --port 27017 \
  --database myapp
```

### 3. Create a Backup

```bash
# Simple SQLite backup
dbbackup backup --dbms sqlite --database ./myapp.db

# PostgreSQL with bzip2 compression
dbbackup backup --dbms postgresql --host db.example.com \
  --username admin --password secret --database production \
  --compression bzip2

# MySQL - backup specific tables
dbbackup backup --dbms mysql --host localhost \
  --username root --password pass --database orders \
  --tables users,products,invoices

# Custom output path
dbbackup backup --dbms sqlite --database ./app.db \
  --output /mnt/backups/app_backup.sql
```

### 4. Restore from Backup

```bash
# Full restore
dbbackup restore backups/mydb_full_20240101.sql.gz \
  --dbms postgresql --database mydb_restored

# Selective table restore
dbbackup restore backups/mydb_full_20240101.sql.gz \
  --dbms mysql --database mydb --tables users,orders

# SQLite restore (auto-detects binary vs SQL dump)
dbbackup restore backups/myapp.db --dbms sqlite --database ./restored.db
```

### 5. Schedule Automatic Backups

```bash
# Every 6 hours
dbbackup schedule --dbms postgresql --database myapp --interval 360

# Daily at 2 AM
dbbackup schedule --dbms mysql --database orders \
  --cron "0 2 * * *"

# Every 30 minutes with lzma compression
dbbackup schedule --dbms sqlite --database ./myapp.db \
  --interval 30 --compression lzma
```

---

## 📖 Commands Reference

| Command | Description |
|---------|-------------|
| `dbbackup init` | Generate sample configuration file |
| `dbbackup test` | Test database connection |
| `dbbackup backup` | Create a database backup |
| `dbbackup restore <file>` | Restore database from backup |
| `dbbackup list` | List available backup files |
| `dbbackup history` | Show backup activity history |
| `dbbackup schedule` | Run automated backup scheduler |
| `dbbackup cleanup` | Remove old backups by retention policy |
| `dbbackup --help` | Show help for any command |

### Global Options

| Option | Description |
|--------|-------------|
| `-c, --config PATH` | Path to configuration file |
| `-v, --version` | Show version number |
| `--verbose` | Enable verbose debug output |
| `-h, --help` | Show help message |

### Backup Options

| Option | Description |
|--------|-------------|
| `-d, --dbms` | Database type: `mysql`, `postgresql`, `mongodb`, `sqlite` |
| `-H, --host` | Database host (default: localhost) |
| `-P, --port` | Database port (auto-detected per DBMS) |
| `-u, --username` | Database username |
| `-p, --password` | Database password |
| `-D, --database` | Database name or file path (SQLite) |
| `-t, --backup-type` | `full`, `incremental`, or `differential` |
| `-C, --compression` | `gzip`, `bzip2`, `lzma`, or `none` |
| `--compression-level` | 1-9 (default: 6) |
| `-o, --output` | Custom output file path |
| `--tables` | Comma-separated tables to include |
| `--exclude-tables` | Comma-separated tables to exclude |
| `-s, --storage` | `local`, `s3`, `gcs`, or `azure` |
| `--no-notify` | Suppress Slack notifications |

---

## ⚙️ Configuration

The configuration file (`~/.dbbackup/config.yaml`) supports all options:

```yaml
# Database Connection
database:
  dbms: postgresql
  host: localhost
  port: 5432
  username: admin
  password: secret
  database: production

# Storage
storage:
  local_path: ~/.dbbackup/backups
  s3_bucket: my-backup-bucket
  s3_region: us-east-1

# Notifications
notification:
  slack_webhook_url: https://hooks.slack.com/services/...
  slack_channel: "#backups"
  notify_on_success: true
  notify_on_failure: true

# Schedule
schedule:
  enabled: true
  cron_expression: "0 2 * * *"
  retention_days: 30

# Backup Settings
backup:
  backup_type: full
  compression: gzip
  compression_level: 6
```

CLI arguments always override config file settings.

---

## ☁️ Cloud Storage

### AWS S3

```yaml
storage:
  s3_bucket: my-backup-bucket
  s3_prefix: "dbbackup/"
  s3_region: us-east-1
  s3_access_key: AKIAIOSFODNN7EXAMPLE
  s3_secret_key: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
```

### Google Cloud Storage

```yaml
storage:
  gcs_bucket: my-backup-bucket
  gcs_prefix: "dbbackup/"
  gcs_credentials_file: /path/to/credentials.json
```

### Azure Blob Storage

```yaml
storage:
  azure_container: backups
  azure_prefix: "dbbackup/"
  azure_connection_string: "DefaultEndpointsProtocol=https;..."
```

---

## 📢 Slack Notifications

Configure Slack notifications to receive alerts on backup operations:

1. Create an [Incoming Webhook](https://api.slack.com/messaging/webhooks) in Slack
2. Add the webhook URL to your config:

```yaml
notification:
  slack_webhook_url: https://hooks.slack.com/services/T00/B00/xxxx
  slack_channel: "#database-backups"
  notify_on_success: true
  notify_on_failure: true
```

---

## 🏗️ Architecture

```
dbbackup/
├── __init__.py           # Package metadata
├── cli.py                # CLI commands (Click framework)
├── config.py             # Configuration management (YAML)
├── compression.py        # gzip/bzip2/lzma compression
├── storage.py            # Local + cloud storage backends
├── notifications.py      # Slack webhook notifications
├── scheduler.py          # Backup scheduling engine
├── logger.py             # Logging and activity tracking
└── connectors/
    ├── __init__.py        # Connector factory
    ├── base.py            # Abstract base connector
    ├── mysql_connector.py
    ├── postgresql_connector.py
    ├── mongodb_connector.py
    └── sqlite_connector.py
```

---

## 🧪 Testing

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=dbbackup --cov-report=term-missing

# Run specific test class
pytest tests/test_dbbackup.py::TestSQLiteConnector -v
```

---

## 📝 License

MIT License — see [LICENSE](LICENSE) for details.
