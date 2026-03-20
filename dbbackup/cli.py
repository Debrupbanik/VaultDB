"""
DBBackup CLI - Command Line Interface.

The main entry point for the database backup utility.
Provides commands for backup, restore, test, list, schedule, and configuration.
"""

import os
import sys
import time
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.text import Text
from rich import box

from . import __version__
from .config import (
    AppConfig, DatabaseConfig, StorageConfig, BackupConfig,
    load_config, save_config, generate_sample_config, ensure_dirs,
    CONFIG_DIR, BACKUP_DIR,
)
from .connectors import get_connector
from .compression import compress_file, decompress_file, get_compression_ratio
from .storage import LocalStorage, get_storage_backend
from .logger import (
    setup_logger, log_backup_activity, get_backup_history,
    format_size, format_duration,
)
from .notifications import send_slack_notification
from .scheduler import BackupScheduler


console = Console()


# ═══════════════════════════════════════════════════════════
# CLI App and Global Options
# ═══════════════════════════════════════════════════════════

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

BANNER = """
[bold cyan]╔══════════════════════════════════════════════════════╗
║          [bold white]🗄️  DBBackup Utility v{version}[bold cyan]              ║
║     [dim white]Backup & Restore Any Database with Ease[bold cyan]        ║
╚══════════════════════════════════════════════════════╝[/]
""".format(version=__version__)


@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.version_option(__version__, "-v", "--version", prog_name="dbbackup")
@click.option(
    "--config", "-c",
    type=click.Path(),
    default=None,
    help="Path to configuration file (default: ~/.dbbackup/config.yaml)",
)
@click.option("--verbose", is_flag=True, help="Enable verbose output")
@click.pass_context
def cli(ctx, config, verbose):
    """🗄️  DBBackup - Database Backup & Restore Utility

    A powerful CLI tool for backing up and restoring databases.
    Supports MySQL, PostgreSQL, MongoDB, and SQLite.

    \b
    Quick Start:
      dbbackup init                    # Generate sample config
      dbbackup test --dbms sqlite \\
        --database ./mydb.sqlite       # Test connection
      dbbackup backup --dbms sqlite \\
        --database ./mydb.sqlite       # Create a backup
      dbbackup list                    # List all backups
      dbbackup restore <backup_file>   # Restore from backup

    \b
    Examples:
      # Backup a PostgreSQL database
      dbbackup backup --dbms postgresql --host localhost \\
        --port 5432 --username admin --password secret \\
        --database myapp_prod

      # Backup MySQL with gzip compression to S3
      dbbackup backup --dbms mysql --host db.example.com \\
        --database orders --compression gzip \\
        --storage s3 --s3-bucket my-backups

      # Restore a specific backup
      dbbackup restore backups/mydb_20240101_020000.sql.gz

      # Schedule automatic daily backups
      dbbackup schedule --interval 1440 --dbms postgresql \\
        --database myapp_prod
    """
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    ctx.obj["verbose"] = verbose
    ctx.obj["app_config"] = load_config(config)

    log_level = "DEBUG" if verbose else "INFO"
    ctx.obj["logger"] = setup_logger(level=log_level)

    if ctx.invoked_subcommand is None:
        console.print(BANNER)
        click.echo(ctx.get_help())


# ═══════════════════════════════════════════════════════════
# INIT Command
# ═══════════════════════════════════════════════════════════

@cli.command()
@click.option(
    "--output", "-o",
    type=click.Path(),
    default=None,
    help="Output path for the config file",
)
def init(output):
    """📋 Initialize a new configuration file with defaults.

    Generates a sample configuration file with all available options
    and helpful comments. Edit the file to match your setup.
    """
    console.print(BANNER)
    ensure_dirs()

    output_path = output or str(CONFIG_DIR / "config.yaml")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if os.path.exists(output_path):
        if not click.confirm(
            f"Config file already exists at {output_path}. Overwrite?",
            default=False,
        ):
            console.print("[yellow]Aborted.[/]")
            return

    generate_sample_config(output_path)

    console.print(Panel(
        f"[green]✅ Configuration file created at:[/]\n"
        f"   [bold]{output_path}[/]\n\n"
        f"[dim]Edit this file with your database credentials and preferences.\n"
        f"Then run [bold]dbbackup test[/bold] to verify your connection.[/]",
        title="[bold green]Configuration Initialized[/]",
        border_style="green",
    ))


# ═══════════════════════════════════════════════════════════
# TEST Command
# ═══════════════════════════════════════════════════════════

@cli.command()
@click.option("--dbms", "-d", type=click.Choice(["mysql", "postgresql", "mongodb", "sqlite"]),
              help="Database management system")
@click.option("--host", "-H", default=None, help="Database host")
@click.option("--port", "-P", type=int, default=None, help="Database port")
@click.option("--username", "-u", default=None, help="Database username")
@click.option("--password", "-p", default=None, help="Database password")
@click.option("--database", "-D", default=None, help="Database name (or file path for SQLite)")
@click.pass_context
def test(ctx, dbms, host, port, username, password, database):
    """🔌 Test database connection.

    Validates your database credentials and connectivity before
    performing any backup or restore operations.
    """
    console.print(BANNER)
    config = ctx.obj["app_config"]
    logger = ctx.obj["logger"]

    # Override config with CLI options
    db_config = _merge_db_options(config.database, dbms, host, port, username, password, database)

    console.print(Panel(
        f"[bold]DBMS:[/] {db_config.dbms}\n"
        f"[bold]Host:[/] {db_config.host}\n"
        f"[bold]Port:[/] {db_config.effective_port()}\n"
        f"[bold]Database:[/] {db_config.database}\n"
        f"[bold]Username:[/] {db_config.username or '(none)'}",
        title="[bold cyan]Connection Parameters[/]",
        border_style="cyan",
    ))

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        progress.add_task("Testing connection...", total=None)
        try:
            connector = get_connector(
                dbms=db_config.dbms,
                host=db_config.host,
                port=db_config.effective_port(),
                username=db_config.username,
                password=db_config.password,
                database=db_config.database,
                connection_uri=db_config.connection_uri,
                ssl=db_config.ssl,
                ssl_ca=db_config.ssl_ca,
                ssl_cert=db_config.ssl_cert,
                ssl_key=db_config.ssl_key,
            )
            success, message = connector.test_connection()
        except Exception as e:
            success, message = False, str(e)

    if success:
        console.print(Panel(
            f"[green]✅ {message}[/]",
            title="[bold green]Connection Successful[/]",
            border_style="green",
        ))

        # Try to list tables
        try:
            connector.connect()
            tables = connector.list_tables()
            db_size = connector.get_database_size()
            connector.disconnect()

            if tables:
                table_widget = Table(
                    title=f"[bold]Tables/Collections ({len(tables)})[/]",
                    box=box.ROUNDED,
                    show_lines=False,
                )
                table_widget.add_column("#", style="dim", width=4)
                table_widget.add_column("Name", style="cyan")
                for i, t in enumerate(tables, 1):
                    table_widget.add_row(str(i), t)
                console.print(table_widget)

            if db_size > 0:
                console.print(f"\n  📊 Database size: [bold]{format_size(db_size)}[/]")

        except Exception:
            pass

        log_backup_activity("test", db_config.dbms, db_config.database, "success")
    else:
        console.print(Panel(
            f"[red]❌ {message}[/]",
            title="[bold red]Connection Failed[/]",
            border_style="red",
        ))
        log_backup_activity("test", db_config.dbms, db_config.database, "failed", error=message)
        sys.exit(1)


# ═══════════════════════════════════════════════════════════
# BACKUP Command
# ═══════════════════════════════════════════════════════════

@cli.command()
@click.option("--dbms", "-d", type=click.Choice(["mysql", "postgresql", "mongodb", "sqlite"]),
              help="Database management system")
@click.option("--host", "-H", default=None, help="Database host")
@click.option("--port", "-P", type=int, default=None, help="Database port")
@click.option("--username", "-u", default=None, help="Database username")
@click.option("--password", "-p", default=None, help="Database password")
@click.option("--database", "-D", default=None, help="Database name (or file path for SQLite)")
@click.option("--backup-type", "-t",
              type=click.Choice(["full", "incremental", "differential"]),
              default=None, help="Backup type (default: full)")
@click.option("--compression", "-C",
              type=click.Choice(["gzip", "bzip2", "lzma", "none"]),
              default=None, help="Compression method (default: gzip)")
@click.option("--compression-level", type=int, default=None, help="Compression level 1-9")
@click.option("--output", "-o", type=click.Path(), default=None,
              help="Custom output path for backup file")
@click.option("--tables", default=None, help="Comma-separated list of tables to include")
@click.option("--exclude-tables", default=None,
              help="Comma-separated list of tables to exclude")
@click.option("--storage", "-s",
              type=click.Choice(["local", "s3", "gcs", "azure"]),
              default="local", help="Storage destination")
@click.option("--s3-bucket", default=None, help="AWS S3 bucket name")
@click.option("--s3-region", default=None, help="AWS S3 region")
@click.option("--no-notify", is_flag=True, help="Suppress notifications")
@click.pass_context
def backup(ctx, dbms, host, port, username, password, database,
           backup_type, compression, compression_level, output,
           tables, exclude_tables, storage, s3_bucket, s3_region, no_notify):
    """💾 Create a database backup.

    \b
    Supports full, incremental, and differential backups with
    optional compression and cloud storage upload.

    \b
    Examples:
      # Simple SQLite backup
      dbbackup backup --dbms sqlite --database ./myapp.db

      # PostgreSQL with bzip2 compression
      dbbackup backup --dbms postgresql --host localhost \\
        --username admin --password secret --database myapp \\
        --compression bzip2

      # Backup specific tables to S3
      dbbackup backup --dbms mysql --database orders \\
        --tables users,products --storage s3 --s3-bucket my-backups

      # Backup with custom output path
      dbbackup backup --dbms sqlite --database ./myapp.db \\
        --output /mnt/nas/backups/myapp.sql
    """
    console.print(BANNER)
    config = ctx.obj["app_config"]
    logger = ctx.obj["logger"]
    start_time = time.time()

    # Merge configurations
    db_config = _merge_db_options(config.database, dbms, host, port, username, password, database)
    bk_type = backup_type or config.backup.backup_type
    comp_method = compression or config.backup.compression
    comp_level = compression_level or config.backup.compression_level
    include_tables = tables.split(",") if tables else config.backup.include_tables
    excluded_tables = exclude_tables.split(",") if exclude_tables else config.backup.exclude_tables

    console.print(Panel(
        f"[bold]DBMS:[/] {db_config.dbms}   |   "
        f"[bold]Database:[/] {db_config.database}   |   "
        f"[bold]Type:[/] {bk_type}\n"
        f"[bold]Compression:[/] {comp_method} (level {comp_level})   |   "
        f"[bold]Storage:[/] {storage}",
        title="[bold cyan]🔧 Backup Configuration[/]",
        border_style="cyan",
    ))

    ensure_dirs()

    # Generate backup filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    db_name = Path(db_config.database).stem if db_config.dbms == "sqlite" else db_config.database
    ext = ".json" if db_config.dbms == "mongodb" else ".sql"
    backup_filename = f"{db_name}_{db_config.dbms}_{bk_type}_{timestamp}{ext}"

    if output:
        raw_backup_path = output
    else:
        raw_backup_path = str(BACKUP_DIR / backup_filename)

    # Ensure parent directory exists
    Path(raw_backup_path).parent.mkdir(parents=True, exist_ok=True)

    # Step 1: Test connection
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        transient=True,
    ) as progress:
        task = progress.add_task("🔌 Testing connection...", total=None)

        try:
            connector = get_connector(
                dbms=db_config.dbms,
                host=db_config.host,
                port=db_config.effective_port(),
                username=db_config.username,
                password=db_config.password,
                database=db_config.database,
                connection_uri=db_config.connection_uri,
                ssl=db_config.ssl,
            )
            success, msg = connector.test_connection()
            if not success:
                _backup_failed(db_config, bk_type, comp_method, config, msg, start_time, no_notify, logger)
                return
        except Exception as e:
            _backup_failed(db_config, bk_type, comp_method, config, str(e), start_time, no_notify, logger)
            return

        console.print("  [green]✅ Connection verified[/]")

        # Step 2: Perform backup
        progress.update(task, description="💾 Creating backup...")

        try:
            success, msg = connector.backup(
                output_path=raw_backup_path,
                backup_type=bk_type,
                tables=include_tables or None,
                exclude_tables=excluded_tables or None,
            )
            if not success:
                _backup_failed(db_config, bk_type, comp_method, config, msg, start_time, no_notify, logger)
                return
        except Exception as e:
            _backup_failed(db_config, bk_type, comp_method, config, str(e), start_time, no_notify, logger)
            return

        console.print(f"  [green]✅ Backup created[/]")

    # Step 3: Compress
    final_path = raw_backup_path
    original_size = os.path.getsize(raw_backup_path)
    compressed_size = original_size

    if comp_method != "none":
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            transient=True,
        ) as progress:
            progress.add_task(f"📦 Compressing ({comp_method})...", total=None)
            try:
                final_path, original_size, compressed_size = compress_file(
                    raw_backup_path, method=comp_method, level=comp_level,
                )
                ratio = get_compression_ratio(original_size, compressed_size)
                console.print(
                    f"  [green]✅ Compressed[/] "
                    f"({format_size(original_size)} → {format_size(compressed_size)}, "
                    f"{ratio:.1f}% reduction)"
                )
            except Exception as e:
                logger.warning(f"Compression failed, keeping uncompressed: {e}")
                final_path = raw_backup_path

    # Step 4: Store
    stored_paths = [final_path]
    if storage != "local" or config.storage.s3_bucket or config.storage.gcs_bucket:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            transient=True,
        ) as progress:
            progress.add_task(f"☁️  Uploading to {storage}...", total=None)
            try:
                backends = get_storage_backend(config.storage)
                for backend in backends:
                    result = backend.store(final_path)
                    stored_paths.append(result)
                console.print(f"  [green]✅ Stored to {storage}[/]")
            except Exception as e:
                logger.warning(f"Cloud upload failed: {e}")

    # Complete
    duration = time.time() - start_time
    final_size = os.path.getsize(final_path) if os.path.exists(final_path) else compressed_size

    # Log activity
    log_backup_activity(
        operation="backup",
        dbms=db_config.dbms,
        database=db_config.database,
        status="success",
        backup_file=os.path.basename(final_path),
        backup_size=final_size,
        duration_seconds=duration,
        backup_type=bk_type,
        compression=comp_method,
    )

    # Success panel
    console.print()
    console.print(Panel(
        f"[green]✅ Backup completed successfully![/]\n\n"
        f"  📁 [bold]File:[/]        {final_path}\n"
        f"  📊 [bold]Size:[/]        {format_size(final_size)}\n"
        f"  ⏱️  [bold]Duration:[/]    {format_duration(duration)}\n"
        f"  🔧 [bold]Type:[/]        {bk_type}\n"
        f"  📦 [bold]Compression:[/] {comp_method}",
        title="[bold green]💾 Backup Complete[/]",
        border_style="green",
        padding=(1, 2),
    ))

    # Send notification
    if not no_notify and config.notification.slack_webhook_url:
        if config.notification.notify_on_success:
            send_slack_notification(
                webhook_url=config.notification.slack_webhook_url,
                operation="backup",
                status="success",
                database=db_config.database,
                dbms=db_config.dbms,
                duration=duration,
                backup_file=os.path.basename(final_path),
                backup_size=final_size,
                channel=config.notification.slack_channel,
                username=config.notification.slack_username,
            )


def _backup_failed(db_config, bk_type, comp_method, config, error, start_time, no_notify, logger):
    """Handle backup failure."""
    duration = time.time() - start_time
    console.print(Panel(
        f"[red]❌ Backup failed![/]\n\n"
        f"  [bold]Error:[/] {error}",
        title="[bold red]Backup Failed[/]",
        border_style="red",
        padding=(1, 2),
    ))

    log_backup_activity(
        operation="backup",
        dbms=db_config.dbms,
        database=db_config.database,
        status="failed",
        duration_seconds=duration,
        backup_type=bk_type,
        compression=comp_method,
        error=error,
    )

    if not no_notify and config.notification.slack_webhook_url:
        if config.notification.notify_on_failure:
            send_slack_notification(
                webhook_url=config.notification.slack_webhook_url,
                operation="backup",
                status="failed",
                database=db_config.database,
                dbms=db_config.dbms,
                duration=duration,
                error=error,
                channel=config.notification.slack_channel,
                username=config.notification.slack_username,
            )

    sys.exit(1)


# ═══════════════════════════════════════════════════════════
# RESTORE Command
# ═══════════════════════════════════════════════════════════

@cli.command()
@click.argument("backup_file", type=click.Path(exists=True))
@click.option("--dbms", "-d", type=click.Choice(["mysql", "postgresql", "mongodb", "sqlite"]),
              help="Database management system")
@click.option("--host", "-H", default=None, help="Database host")
@click.option("--port", "-P", type=int, default=None, help="Database port")
@click.option("--username", "-u", default=None, help="Database username")
@click.option("--password", "-p", default=None, help="Database password")
@click.option("--database", "-D", default=None, help="Target database name")
@click.option("--tables", default=None,
              help="Comma-separated list of tables for selective restore")
@click.option("--drop-existing", is_flag=True,
              help="Drop existing tables/collections before restore")
@click.option("--no-confirm", is_flag=True, help="Skip confirmation prompt")
@click.option("--no-notify", is_flag=True, help="Suppress notifications")
@click.pass_context
def restore(ctx, backup_file, dbms, host, port, username, password,
            database, tables, drop_existing, no_confirm, no_notify):
    """🔄 Restore a database from a backup file.

    \b
    Supports selective table/collection restore and handles
    compressed backup files automatically.

    \b
    Examples:
      # Full restore from backup
      dbbackup restore backups/mydb_full_20240101.sql.gz \\
        --dbms postgresql --database mydb_restored

      # Selective table restore
      dbbackup restore backups/mydb_full_20240101.sql.gz \\
        --dbms mysql --database mydb --tables users,orders

      # SQLite restore
      dbbackup restore backups/mydb_sqlite_full_20240101.sql \\
        --dbms sqlite --database ./restored.db
    """
    console.print(BANNER)
    config = ctx.obj["app_config"]
    logger = ctx.obj["logger"]
    start_time = time.time()

    db_config = _merge_db_options(config.database, dbms, host, port, username, password, database)
    restore_tables = tables.split(",") if tables else None

    console.print(Panel(
        f"[bold]Backup File:[/] {backup_file}\n"
        f"[bold]Target DBMS:[/] {db_config.dbms}\n"
        f"[bold]Target Database:[/] {db_config.database}\n"
        f"[bold]Tables:[/] {', '.join(restore_tables) if restore_tables else 'All'}\n"
        f"[bold]Drop Existing:[/] {'Yes' if drop_existing else 'No'}",
        title="[bold cyan]🔄 Restore Configuration[/]",
        border_style="cyan",
    ))

    # Confirmation
    if not no_confirm:
        console.print()
        console.print("[yellow]⚠️  WARNING: This will modify the target database.[/]")
        if drop_existing:
            console.print("[red]⚠️  Existing tables will be DROPPED![/]")
        if not click.confirm("Do you want to proceed?", default=False):
            console.print("[yellow]Aborted.[/]")
            return

    # Decompress if needed
    actual_file = backup_file
    temp_decompressed = None

    if backup_file.endswith((".gz", ".bz2", ".xz")):
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            progress.add_task("📦 Decompressing backup...", total=None)
            temp_dir = tempfile.mkdtemp(prefix="dbbackup_restore_")
            temp_file = os.path.join(temp_dir, os.path.basename(backup_file).rsplit(".", 1)[0])
            actual_file = decompress_file(backup_file, output_path=temp_file)
            temp_decompressed = actual_file
            console.print("  [green]✅ Decompressed[/]")

    try:
        # Test connection
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task("🔌 Testing connection...", total=None)

            connector = get_connector(
                dbms=db_config.dbms,
                host=db_config.host,
                port=db_config.effective_port(),
                username=db_config.username,
                password=db_config.password,
                database=db_config.database,
                connection_uri=db_config.connection_uri,
                ssl=db_config.ssl,
            )
            success, msg = connector.test_connection()
            if not success:
                console.print(Panel(
                    f"[red]❌ Connection failed: {msg}[/]",
                    title="[bold red]Restore Failed[/]",
                    border_style="red",
                ))
                sys.exit(1)

            console.print("  [green]✅ Connection verified[/]")

            # Perform restore
            progress.update(task, description="🔄 Restoring database...")

            success, msg = connector.restore(
                input_path=actual_file,
                tables=restore_tables,
                drop_existing=drop_existing,
            )

        duration = time.time() - start_time

        if success:
            console.print()
            console.print(Panel(
                f"[green]✅ Restore completed successfully![/]\n\n"
                f"  📁 [bold]Source:[/]    {backup_file}\n"
                f"  🗄️  [bold]Target:[/]    {db_config.dbms}://{db_config.database}\n"
                f"  ⏱️  [bold]Duration:[/]  {format_duration(duration)}\n"
                f"  📋 [bold]Details:[/]   {msg}",
                title="[bold green]🔄 Restore Complete[/]",
                border_style="green",
                padding=(1, 2),
            ))

            log_backup_activity(
                operation="restore",
                dbms=db_config.dbms,
                database=db_config.database,
                status="success",
                backup_file=os.path.basename(backup_file),
                duration_seconds=duration,
            )
        else:
            console.print(Panel(
                f"[red]❌ Restore failed: {msg}[/]",
                title="[bold red]Restore Failed[/]",
                border_style="red",
            ))

            log_backup_activity(
                operation="restore",
                dbms=db_config.dbms,
                database=db_config.database,
                status="failed",
                backup_file=os.path.basename(backup_file),
                duration_seconds=duration,
                error=msg,
            )
            sys.exit(1)

    finally:
        # Clean up temp file
        if temp_decompressed and os.path.exists(temp_decompressed):
            try:
                os.remove(temp_decompressed)
                os.rmdir(os.path.dirname(temp_decompressed))
            except Exception:
                pass

    # Send notification
    if not no_notify and config.notification.slack_webhook_url:
        send_slack_notification(
            webhook_url=config.notification.slack_webhook_url,
            operation="restore",
            status="success" if success else "failed",
            database=db_config.database,
            dbms=db_config.dbms,
            duration=duration,
            backup_file=os.path.basename(backup_file),
            error="" if success else msg,
            channel=config.notification.slack_channel,
            username=config.notification.slack_username,
        )


# ═══════════════════════════════════════════════════════════
# LIST Command
# ═══════════════════════════════════════════════════════════

@cli.command("list")
@click.option("--storage", "-s",
              type=click.Choice(["local", "s3", "gcs", "azure", "all"]),
              default="local", help="Storage backend to list")
@click.option("--limit", "-n", type=int, default=20, help="Maximum number of backups to show")
@click.pass_context
def list_backups(ctx, storage, limit):
    """📂 List available backups.

    Shows all backup files with their size, date, and location.
    """
    console.print(BANNER)
    config = ctx.obj["app_config"]

    # Local backups
    local_storage = LocalStorage(config.storage.local_path)
    backups = local_storage.list_backups()

    if not backups:
        console.print(Panel(
            "[yellow]No backup files found.[/]\n\n"
            f"Backup directory: {config.storage.local_path}\n"
            "Run [bold]dbbackup backup[/bold] to create your first backup.",
            title="[bold yellow]📂 No Backups[/]",
            border_style="yellow",
        ))
        return

    table = Table(
        title=f"[bold]💾 Available Backups ({len(backups)} total)[/]",
        box=box.ROUNDED,
        show_lines=False,
        padding=(0, 1),
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("Filename", style="cyan", no_wrap=True)
    table.add_column("Size", style="green", justify="right")
    table.add_column("Modified", style="yellow")
    table.add_column("Location", style="dim")

    for i, bk in enumerate(backups[:limit], 1):
        size_str = format_size(bk["size"])
        modified = bk["modified"][:19].replace("T", " ")
        table.add_row(
            str(i),
            bk["name"],
            size_str,
            modified,
            "📁 Local",
        )

    console.print(table)

    # Total size
    total_size = sum(b["size"] for b in backups)
    console.print(f"\n  📊 Total: [bold]{format_size(total_size)}[/] across {len(backups)} backups")


# ═══════════════════════════════════════════════════════════
# HISTORY Command
# ═══════════════════════════════════════════════════════════

@cli.command()
@click.option("--limit", "-n", type=int, default=20, help="Number of records to show")
@click.pass_context
def history(ctx, limit):
    """📜 Show backup activity history.

    Displays a log of all backup and restore operations with
    timestamps, status, duration, and file sizes.
    """
    console.print(BANNER)

    records = get_backup_history(limit)
    if not records:
        console.print(Panel(
            "[yellow]No activity history found.[/]\n"
            "Backup operations will be logged here automatically.",
            title="[bold yellow]📜 Activity History[/]",
            border_style="yellow",
        ))
        return

    table = Table(
        title=f"[bold]📜 Backup Activity History[/]",
        box=box.ROUNDED,
        show_lines=True,
    )
    table.add_column("Timestamp", style="dim", width=20)
    table.add_column("Operation", style="cyan", width=10)
    table.add_column("DBMS", style="blue", width=12)
    table.add_column("Database", style="white", width=15)
    table.add_column("Status", width=10)
    table.add_column("Size", style="green", justify="right", width=10)
    table.add_column("Duration", style="yellow", justify="right", width=10)

    for record in reversed(records):
        status = record.get("status", "unknown")
        status_str = f"[green]✅ {status}[/]" if status == "success" else f"[red]❌ {status}[/]"

        size = record.get("backup_size_bytes", 0)
        size_str = format_size(size) if size > 0 else "-"

        duration = record.get("duration_seconds", 0)
        duration_str = format_duration(duration) if duration > 0 else "-"

        timestamp = record.get("timestamp", "")[:19].replace("T", " ")

        table.add_row(
            timestamp,
            record.get("operation", ""),
            record.get("dbms", ""),
            record.get("database", ""),
            status_str,
            size_str,
            duration_str,
        )

    console.print(table)


# ═══════════════════════════════════════════════════════════
# SCHEDULE Command
# ═══════════════════════════════════════════════════════════

@cli.command()
@click.option("--dbms", "-d", type=click.Choice(["mysql", "postgresql", "mongodb", "sqlite"]),
              help="Database management system")
@click.option("--host", "-H", default=None, help="Database host")
@click.option("--port", "-P", type=int, default=None, help="Database port")
@click.option("--username", "-u", default=None, help="Database username")
@click.option("--password", "-p", default=None, help="Database password")
@click.option("--database", "-D", default=None, help="Database name")
@click.option("--interval", "-i", type=int, default=None,
              help="Backup interval in minutes")
@click.option("--cron", default=None, help="Cron expression (e.g., '0 2 * * *')")
@click.option("--compression", "-C",
              type=click.Choice(["gzip", "bzip2", "lzma", "none"]),
              default="gzip", help="Compression method")
@click.pass_context
def schedule(ctx, dbms, host, port, username, password, database,
             interval, cron, compression):
    """⏰ Schedule automatic backups.

    \b
    Runs a background process that performs backups at specified intervals.
    Use Ctrl+C to stop the scheduler.

    \b
    Examples:
      # Every 6 hours
      dbbackup schedule --dbms postgresql --database myapp --interval 360

      # Daily at 2 AM
      dbbackup schedule --dbms mysql --database orders --cron "0 2 * * *"

      # Every 30 minutes
      dbbackup schedule --dbms sqlite --database ./myapp.db --interval 30
    """
    console.print(BANNER)
    config = ctx.obj["app_config"]
    logger = ctx.obj["logger"]

    db_config = _merge_db_options(config.database, dbms, host, port, username, password, database)

    def run_scheduled_backup():
        """Execute a scheduled backup."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            db_name = (
                Path(db_config.database).stem
                if db_config.dbms == "sqlite"
                else db_config.database
            )
            ext = ".json" if db_config.dbms == "mongodb" else ".sql"
            filename = f"{db_name}_{db_config.dbms}_full_{timestamp}{ext}"
            output_path = str(BACKUP_DIR / filename)

            connector = get_connector(
                dbms=db_config.dbms,
                host=db_config.host,
                port=db_config.effective_port(),
                username=db_config.username,
                password=db_config.password,
                database=db_config.database,
            )

            start = time.time()
            success, msg = connector.backup(output_path=output_path, backup_type="full")

            if success and compression != "none":
                output_path, _, final_size = compress_file(
                    output_path, method=compression
                )
            else:
                final_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0

            duration = time.time() - start

            log_backup_activity(
                operation="scheduled_backup",
                dbms=db_config.dbms,
                database=db_config.database,
                status="success" if success else "failed",
                backup_file=os.path.basename(output_path),
                backup_size=final_size,
                duration_seconds=duration,
                error="" if success else msg,
            )

            if success:
                logger.info(
                    f"Scheduled backup complete: {os.path.basename(output_path)} "
                    f"({format_size(final_size)}, {format_duration(duration)})"
                )
            else:
                logger.error(f"Scheduled backup failed: {msg}")

        except Exception as e:
            logger.error(f"Scheduled backup error: {e}")

    scheduler = BackupScheduler(run_scheduled_backup, logger=logger)

    if interval:
        scheduler.schedule_interval(interval)
        schedule_desc = f"every {interval} minutes"
    elif cron:
        scheduler.schedule_cron(cron)
        schedule_desc = f"cron: {cron}"
    elif config.schedule.interval_minutes > 0:
        scheduler.schedule_interval(config.schedule.interval_minutes)
        schedule_desc = f"every {config.schedule.interval_minutes} minutes"
    elif config.schedule.cron_expression:
        scheduler.schedule_cron(config.schedule.cron_expression)
        schedule_desc = f"cron: {config.schedule.cron_expression}"
    else:
        console.print(Panel(
            "[yellow]No schedule configured.[/]\n"
            "Use --interval or --cron to specify a schedule.\n\n"
            "Examples:\n"
            "  --interval 60      (every hour)\n"
            "  --cron '0 2 * * *' (daily at 2 AM)",
            title="[bold yellow]⏰ No Schedule[/]",
            border_style="yellow",
        ))
        return

    console.print(Panel(
        f"[green]⏰ Backup scheduler started[/]\n\n"
        f"  📅 [bold]Schedule:[/]  {schedule_desc}\n"
        f"  🗄️  [bold]Database:[/]  {db_config.dbms}://{db_config.database}\n"
        f"  📦 [bold]Compress:[/]  {compression}\n\n"
        f"  [dim]Press Ctrl+C to stop the scheduler[/]",
        title="[bold green]⏰ Scheduler Active[/]",
        border_style="green",
        padding=(1, 2),
    ))

    scheduler.run_blocking()

    console.print("\n[yellow]⏰ Scheduler stopped.[/]")


# ═══════════════════════════════════════════════════════════
# CLEANUP Command
# ═══════════════════════════════════════════════════════════

@cli.command()
@click.option("--retention-days", "-r", type=int, default=30,
              help="Delete backups older than N days")
@click.option("--max-backups", "-m", type=int, default=0,
              help="Keep at most N most recent backups (0 = unlimited)")
@click.option("--dry-run", is_flag=True, help="Show what would be deleted without deleting")
@click.option("--no-confirm", is_flag=True, help="Skip confirmation")
@click.pass_context
def cleanup(ctx, retention_days, max_backups, dry_run, no_confirm):
    """🧹 Clean up old backup files.

    Removes old backup files based on retention policy.
    """
    console.print(BANNER)
    config = ctx.obj["app_config"]

    local_storage = LocalStorage(config.storage.local_path)
    backups = local_storage.list_backups()

    if not backups:
        console.print("[yellow]No backups found to clean up.[/]")
        return

    # Find backups to delete
    now = datetime.now()
    to_delete = []

    if retention_days > 0:
        for bk in backups:
            modified = datetime.fromisoformat(bk["modified"])
            age = (now - modified).days
            if age > retention_days:
                to_delete.append(bk)

    if max_backups > 0 and len(backups) > max_backups:
        for bk in backups[max_backups:]:
            if bk not in to_delete:
                to_delete.append(bk)

    if not to_delete:
        console.print("[green]✅ No backups need to be cleaned up.[/]")
        return

    console.print(f"\n[yellow]Found {len(to_delete)} backup(s) to remove:[/]\n")
    for bk in to_delete:
        console.print(f"  ❌ {bk['name']} ({format_size(bk['size'])})")

    total_free = sum(b["size"] for b in to_delete)
    console.print(f"\n  Total space to free: [bold]{format_size(total_free)}[/]")

    if dry_run:
        console.print("\n[dim](Dry run — no files were deleted)[/]")
        return

    if not no_confirm:
        if not click.confirm("\nProceed with deletion?", default=False):
            console.print("[yellow]Aborted.[/]")
            return

    deleted = 0
    for bk in to_delete:
        if local_storage.delete(bk["name"]):
            deleted += 1

    console.print(f"\n[green]✅ Deleted {deleted} backup(s), freed {format_size(total_free)}[/]")


# ═══════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════

def _merge_db_options(
    config_db: DatabaseConfig,
    dbms: Optional[str],
    host: Optional[str],
    port: Optional[int],
    username: Optional[str],
    password: Optional[str],
    database: Optional[str],
) -> DatabaseConfig:
    """Merge CLI options with configuration file settings."""
    return DatabaseConfig(
        dbms=dbms or config_db.dbms,
        host=host or config_db.host,
        port=port or config_db.port,
        username=username or config_db.username,
        password=password if password is not None else config_db.password,
        database=database or config_db.database,
        connection_uri=config_db.connection_uri,
        ssl=config_db.ssl,
        ssl_ca=config_db.ssl_ca,
        ssl_cert=config_db.ssl_cert,
        ssl_key=config_db.ssl_key,
        auth_database=config_db.auth_database,
    )


# ═══════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    cli()
