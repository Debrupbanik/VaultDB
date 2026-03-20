"""Web API wrapper for VaultDB - Database Backup Utility."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="VaultDB API",
    description="Database Backup & Restore Web API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class BackupRequest(BaseModel):
    dbms: str
    host: str = "localhost"
    port: int = 5432
    username: str = ""
    password: str = ""
    database: str
    compression: str = "gzip"
    backup_type: str = "full"


class RestoreRequest(BaseModel):
    backup_file: str
    dbms: str
    host: str = "localhost"
    port: int = 5432
    username: str = ""
    password: str = ""
    database: str
    drop_existing: bool = False


class ConnectionTestRequest(BaseModel):
    dbms: str
    host: str = "localhost"
    port: int = 5432
    username: str = ""
    password: str = ""
    database: str


@app.get("/")
async def root():
    return {
        "name": "VaultDB API",
        "version": "1.0.0",
        "status": "healthy",
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/test-connection")
async def test_connection(req: ConnectionTestRequest):
    """Test database connection."""
    try:
        from dbbackup.connectors import get_connector

        connector = get_connector(req.dbms)(
            host=req.host,
            port=req.port,
            username=req.username,
            password=req.password,
            database=req.database,
        )

        success = connector.test_connection()
        connector.close()

        if success:
            return {"status": "success", "message": "Connection successful"}
        else:
            return {"status": "failed", "message": "Connection failed"}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Connection error: {str(e)}")


@app.post("/backup")
async def create_backup(req: BackupRequest, background_tasks: BackgroundTasks):
    """Create a database backup."""
    try:
        from dbbackup.connectors import get_connector
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        db_name = Path(req.database).stem if req.dbms == "sqlite" else req.database
        output_file = f"/tmp/{db_name}_{req.dbms}_{timestamp}.sql"

        if req.compression != "none":
            output_file += f".{req.compression}"

        connector = get_connector(req.dbms)(
            host=req.host,
            port=req.port,
            username=req.username,
            password=req.password,
            database=req.database,
        )

        def run_backup():
            connector.backup(output_file)
            connector.close()

        background_tasks.add_task(run_backup)

        return {
            "status": "started",
            "message": "Backup started in background",
            "output_file": output_file,
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backup error: {str(e)}")


@app.post("/restore")
async def restore_backup(req: RestoreRequest, background_tasks: BackgroundTasks):
    """Restore a database from backup."""
    try:
        from dbbackup.connectors import get_connector

        if not Path(req.backup_file).exists():
            raise HTTPException(status_code=404, detail="Backup file not found")

        connector = get_connector(req.dbms)(
            host=req.host,
            port=req.port,
            username=req.username,
            password=req.password,
            database=req.database,
        )

        def run_restore():
            connector.restore(req.backup_file)
            connector.close()

        background_tasks.add_task(run_restore)

        return {
            "status": "started",
            "message": "Restore started in background",
            "backup_file": req.backup_file,
            "database": req.database,
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore error: {str(e)}")


@app.get("/backups")
async def list_backups():
    """List available backups."""
    try:
        backup_dir = Path.home() / ".dbbackup" / "backups"
        if not backup_dir.exists():
            return {"backups": []}

        backups = [
            {
                "name": f.name,
                "path": str(f),
                "size": f.stat().st_size,
                "modified": f.stat().st_mtime,
            }
            for f in backup_dir.iterdir()
            if f.is_file()
        ]

        return {"backups": backups}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/history")
async def get_history():
    """Get backup history."""
    try:
        log_file = Path.home() / ".dbbackup" / "logs" / "history.json"
        if not log_file.exists():
            return {"history": []}

        import json

        with open(log_file) as f:
            history = json.load(f)

        return {"history": history}

    except Exception as e:
        return {"history": [], "error": str(e)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
