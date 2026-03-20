"""
Logging system for DBBackup utility.

Provides structured logging with both file and console output,
backup activity tracking, and log rotation.
"""

import logging
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.logging import RichHandler

from .config import LOG_DIR, ensure_dirs


console = Console()

# Backup activity log (structured JSON log)
ACTIVITY_LOG = LOG_DIR / "backup_activity.jsonl"


def setup_logger(
    name: str = "dbbackup",
    level: str = "INFO",
    log_file: Optional[str] = None,
) -> logging.Logger:
    """Set up and return a configured logger with rich console and file handlers."""
    ensure_dirs()

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()

    # Rich console handler (pretty output)
    console_handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
    )
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter("%(message)s")
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    # File handler (detailed log)
    file_path = log_file or str(LOG_DIR / "dbbackup.log")
    file_handler = logging.FileHandler(file_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_format)
    logger.addHandler(file_handler)

    return logger


def log_backup_activity(
    operation: str,
    dbms: str,
    database: str,
    status: str,
    backup_file: str = "",
    backup_size: int = 0,
    duration_seconds: float = 0.0,
    backup_type: str = "full",
    compression: str = "gzip",
    error: str = "",
    extra: Optional[dict] = None,
):
    """Log a structured backup activity record to the JSONL activity log."""
    ensure_dirs()

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "operation": operation,  # backup, restore, test, schedule
        "dbms": dbms,
        "database": database,
        "status": status,  # success, failed, in_progress
        "backup_file": backup_file,
        "backup_size_bytes": backup_size,
        "duration_seconds": round(duration_seconds, 2),
        "backup_type": backup_type,
        "compression": compression,
        "error": error,
    }
    if extra:
        record["extra"] = extra

    with open(ACTIVITY_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def get_backup_history(limit: int = 20) -> list:
    """Retrieve recent backup activity records."""
    if not ACTIVITY_LOG.exists():
        return []

    records = []
    with open(ACTIVITY_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    # Return most recent records
    return records[-limit:]


def clear_activity_log():
    """Clear the backup activity log."""
    if ACTIVITY_LOG.exists():
        ACTIVITY_LOG.unlink()


def format_size(size_bytes: int) -> str:
    """Format byte size into human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        mins = int(seconds // 60)
        secs = seconds % 60
        return f"{mins}m {secs:.0f}s"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins}m"
