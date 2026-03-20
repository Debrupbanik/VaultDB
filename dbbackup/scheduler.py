"""
Backup scheduler for DBBackup utility.

Supports interval-based and cron-like scheduling for automated backups.
"""

import time
import signal
import threading
from datetime import datetime, timedelta
from typing import Callable, Optional

import schedule


class BackupScheduler:
    """Manages scheduled backup execution."""

    def __init__(self, backup_func: Callable, logger=None):
        """
        Initialize the backup scheduler.

        Args:
            backup_func: The function to call for backup execution
            logger: Optional logger instance
        """
        self.backup_func = backup_func
        self.logger = logger
        self._running = False
        self._thread = None

    def schedule_interval(self, minutes: int):
        """Schedule backups at a fixed interval."""
        schedule.every(minutes).minutes.do(self._run_backup)
        if self.logger:
            self.logger.info(f"Scheduled backup every {minutes} minutes")

    def schedule_daily(self, hour: int = 2, minute: int = 0):
        """Schedule daily backups at a specific time."""
        time_str = f"{hour:02d}:{minute:02d}"
        schedule.every().day.at(time_str).do(self._run_backup)
        if self.logger:
            self.logger.info(f"Scheduled daily backup at {time_str}")

    def schedule_hourly(self):
        """Schedule hourly backups."""
        schedule.every().hour.do(self._run_backup)
        if self.logger:
            self.logger.info("Scheduled hourly backup")

    def schedule_weekly(self, day: str = "sunday", hour: int = 2, minute: int = 0):
        """Schedule weekly backups."""
        time_str = f"{hour:02d}:{minute:02d}"
        day_scheduler = getattr(schedule.every(), day.lower(), None)
        if day_scheduler:
            day_scheduler.at(time_str).do(self._run_backup)
            if self.logger:
                self.logger.info(f"Scheduled weekly backup on {day} at {time_str}")

    def schedule_cron(self, expression: str):
        """
        Parse a simplified cron expression and set up scheduling.

        Supports: minute hour day month weekday
        Only basic values are supported (no ranges or lists).
        """
        parts = expression.strip().split()
        if len(parts) != 5:
            raise ValueError(
                f"Invalid cron expression: '{expression}'. "
                "Expected format: 'minute hour day month weekday'"
            )

        minute, hour, day, month, weekday = parts

        # Simple cron parsing
        if minute != "*" and hour != "*" and day == "*" and month == "*":
            if weekday == "*":
                # Daily at specific time
                self.schedule_daily(int(hour), int(minute))
            else:
                # Weekly on specific day
                weekday_names = {
                    "0": "sunday", "1": "monday", "2": "tuesday",
                    "3": "wednesday", "4": "thursday", "5": "friday",
                    "6": "saturday", "7": "sunday",
                }
                day_name = weekday_names.get(weekday, weekday.lower())
                self.schedule_weekly(day_name, int(hour), int(minute))
        elif minute == "0" and hour == "*":
            # Hourly
            self.schedule_hourly()
        elif minute.startswith("*/"):
            # Every N minutes
            interval = int(minute.split("/")[1])
            self.schedule_interval(interval)
        else:
            # Default: daily at specified time or midnight
            h = int(hour) if hour != "*" else 0
            m = int(minute) if minute != "*" else 0
            self.schedule_daily(h, m)

    def _run_backup(self):
        """Execute the backup function."""
        try:
            if self.logger:
                self.logger.info("Scheduled backup starting...")
            self.backup_func()
        except Exception as e:
            if self.logger:
                self.logger.error(f"Scheduled backup failed: {e}")

    def start(self):
        """Start the scheduler in a background thread."""
        self._running = True

        def _scheduler_loop():
            while self._running:
                schedule.run_pending()
                time.sleep(1)

        self._thread = threading.Thread(target=_scheduler_loop, daemon=True)
        self._thread.start()

        if self.logger:
            self.logger.info("Backup scheduler started")

    def stop(self):
        """Stop the scheduler."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        schedule.clear()
        if self.logger:
            self.logger.info("Backup scheduler stopped")

    def run_blocking(self):
        """Run the scheduler in blocking mode (main thread)."""
        self._running = True

        def _signal_handler(signum, frame):
            self._running = False

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        if self.logger:
            self.logger.info("Backup scheduler running (Ctrl+C to stop)...")

        try:
            while self._running:
                schedule.run_pending()
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            schedule.clear()
            if self.logger:
                self.logger.info("Backup scheduler stopped")

    def next_run(self) -> Optional[datetime]:
        """Get the time of the next scheduled run."""
        jobs = schedule.get_jobs()
        if jobs:
            return jobs[0].next_run
        return None
