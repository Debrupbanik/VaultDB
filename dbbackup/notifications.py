"""
Notification system for DBBackup utility.

Supports Slack webhook notifications for backup operation results.
"""

import json
import requests
from datetime import datetime
from typing import Optional

from .logger import format_size, format_duration


def send_slack_notification(
    webhook_url: str,
    operation: str,
    status: str,
    database: str,
    dbms: str,
    duration: float = 0.0,
    backup_file: str = "",
    backup_size: int = 0,
    error: str = "",
    channel: str = "",
    username: str = "DBBackup Bot",
) -> tuple[bool, str]:
    """
    Send a Slack notification about a backup operation.

    Args:
        webhook_url: Slack incoming webhook URL
        operation: Type of operation (backup, restore)
        status: Operation status (success, failed)
        database: Database name
        dbms: Database management system
        duration: Duration in seconds
        backup_file: Backup filename
        backup_size: Backup size in bytes
        error: Error message if failed
        channel: Slack channel override
        username: Bot username

    Returns:
        Tuple of (success: bool, message: str)
    """
    if not webhook_url:
        return False, "No Slack webhook URL configured"

    # Build notification message
    is_success = status.lower() == "success"
    emoji = "✅" if is_success else "❌"
    color = "#36a64f" if is_success else "#dc3545"

    fields = [
        {"title": "Operation", "value": operation.title(), "short": True},
        {"title": "Status", "value": f"{emoji} {status.title()}", "short": True},
        {"title": "Database", "value": f"{dbms.upper()} → `{database}`", "short": True},
        {"title": "Duration", "value": format_duration(duration), "short": True},
    ]

    if backup_file:
        fields.append({
            "title": "Backup File",
            "value": f"`{backup_file}`",
            "short": False,
        })

    if backup_size > 0:
        fields.append({
            "title": "Size",
            "value": format_size(backup_size),
            "short": True,
        })

    if error:
        fields.append({
            "title": "Error",
            "value": f"```{error}```",
            "short": False,
        })

    payload = {
        "username": username,
        "icon_emoji": ":floppy_disk:",
        "attachments": [
            {
                "fallback": f"DBBackup {operation} {status} for {database}",
                "color": color,
                "title": f"Database {operation.title()} - {status.title()}",
                "fields": fields,
                "footer": "DBBackup Utility",
                "ts": int(datetime.now().timestamp()),
            }
        ],
    }

    if channel:
        payload["channel"] = channel

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=10,
            headers={"Content-Type": "application/json"},
        )

        if response.status_code == 200:
            return True, "Notification sent successfully"
        else:
            return False, f"Slack API error: {response.status_code} - {response.text}"

    except requests.exceptions.Timeout:
        return False, "Slack notification timed out"
    except requests.exceptions.ConnectionError:
        return False, "Could not connect to Slack"
    except Exception as e:
        return False, f"Notification error: {str(e)}"
