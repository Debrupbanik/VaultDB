"""
Storage backends for DBBackup utility.

Supports local filesystem, AWS S3, Google Cloud Storage,
and Azure Blob Storage for backup file storage.
"""

import os
import shutil
from pathlib import Path
from typing import Optional
from datetime import datetime


class LocalStorage:
    """Local filesystem storage backend."""

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def store(self, source_path: str, dest_name: Optional[str] = None) -> str:
        """
        Store a backup file in local storage.

        Args:
            source_path: Path to the backup file
            dest_name: Optional destination filename

        Returns:
            Path to the stored file
        """
        if dest_name is None:
            dest_name = os.path.basename(source_path)

        dest_path = self.base_path / dest_name
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # If source is already in the destination, skip copy
        if os.path.abspath(source_path) != os.path.abspath(str(dest_path)):
            shutil.copy2(source_path, dest_path)

        return str(dest_path)

    def retrieve(self, filename: str, dest_path: str) -> str:
        """Retrieve a backup file from local storage."""
        source = self.base_path / filename
        if not source.exists():
            raise FileNotFoundError(f"Backup file not found: {source}")
        shutil.copy2(source, dest_path)
        return dest_path

    def list_backups(self) -> list[dict]:
        """List all backup files in local storage."""
        backups = []
        if self.base_path.exists():
            for f in sorted(self.base_path.iterdir(), reverse=True):
                if f.is_file():
                    stat = f.stat()
                    backups.append({
                        "name": f.name,
                        "path": str(f),
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    })
        return backups

    def delete(self, filename: str) -> bool:
        """Delete a backup file from local storage."""
        path = self.base_path / filename
        if path.exists():
            path.unlink()
            return True
        return False

    def cleanup(self, retention_days: int = 30, max_backups: int = 0):
        """Clean up old backups based on retention policy."""
        backups = self.list_backups()
        now = datetime.now()
        deleted = 0

        # Delete by age
        if retention_days > 0:
            for backup in backups:
                modified = datetime.fromisoformat(backup["modified"])
                age_days = (now - modified).days
                if age_days > retention_days:
                    self.delete(backup["name"])
                    deleted += 1

        # Delete by count (keep newest)
        if max_backups > 0:
            remaining = self.list_backups()
            if len(remaining) > max_backups:
                for backup in remaining[max_backups:]:
                    self.delete(backup["name"])
                    deleted += 1

        return deleted


class S3Storage:
    """AWS S3 storage backend."""

    def __init__(
        self,
        bucket: str,
        prefix: str = "dbbackup/",
        region: str = "us-east-1",
        access_key: str = "",
        secret_key: str = "",
        endpoint_url: str = "",
    ):
        self.bucket = bucket
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""
        self.region = region

        import boto3
        session_kwargs = {}
        if access_key and secret_key:
            session_kwargs["aws_access_key_id"] = access_key
            session_kwargs["aws_secret_access_key"] = secret_key
        session_kwargs["region_name"] = region

        client_kwargs = {}
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url

        session = boto3.Session(**session_kwargs)
        self.s3 = session.client("s3", **client_kwargs)

    def store(self, source_path: str, dest_name: Optional[str] = None) -> str:
        """Upload backup file to S3."""
        if dest_name is None:
            dest_name = os.path.basename(source_path)

        key = f"{self.prefix}{dest_name}"

        # Use multipart upload for large files
        from boto3.s3.transfer import TransferConfig
        config = TransferConfig(
            multipart_threshold=64 * 1024 * 1024,  # 64MB
            multipart_chunksize=64 * 1024 * 1024,
        )

        self.s3.upload_file(source_path, self.bucket, key, Config=config)
        return f"s3://{self.bucket}/{key}"

    def retrieve(self, filename: str, dest_path: str) -> str:
        """Download backup file from S3."""
        key = f"{self.prefix}{filename}"
        self.s3.download_file(self.bucket, key, dest_path)
        return dest_path

    def list_backups(self) -> list[dict]:
        """List backup files in S3."""
        backups = []
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                name = obj["Key"].removeprefix(self.prefix)
                if name:
                    backups.append({
                        "name": name,
                        "path": f"s3://{self.bucket}/{obj['Key']}",
                        "size": obj["Size"],
                        "modified": obj["LastModified"].isoformat(),
                    })
        return sorted(backups, key=lambda x: x["modified"], reverse=True)

    def delete(self, filename: str) -> bool:
        """Delete a backup file from S3."""
        key = f"{self.prefix}{filename}"
        self.s3.delete_object(Bucket=self.bucket, Key=key)
        return True

    def cleanup(self, retention_days: int = 30, max_backups: int = 0):
        """Clean up old backups in S3."""
        backups = self.list_backups()
        now = datetime.now()
        deleted = 0

        if retention_days > 0:
            for backup in backups:
                modified = datetime.fromisoformat(
                    backup["modified"].replace("+00:00", "")
                )
                age_days = (now - modified).days
                if age_days > retention_days:
                    self.delete(backup["name"])
                    deleted += 1

        if max_backups > 0:
            remaining = self.list_backups()
            if len(remaining) > max_backups:
                for backup in remaining[max_backups:]:
                    self.delete(backup["name"])
                    deleted += 1

        return deleted


class GCSStorage:
    """Google Cloud Storage backend."""

    def __init__(
        self,
        bucket: str,
        prefix: str = "dbbackup/",
        credentials_file: str = "",
    ):
        self.bucket_name = bucket
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""

        from google.cloud import storage
        if credentials_file:
            self.client = storage.Client.from_service_account_json(credentials_file)
        else:
            self.client = storage.Client()
        self.bucket = self.client.bucket(bucket)

    def store(self, source_path: str, dest_name: Optional[str] = None) -> str:
        """Upload backup file to GCS."""
        if dest_name is None:
            dest_name = os.path.basename(source_path)

        blob_name = f"{self.prefix}{dest_name}"
        blob = self.bucket.blob(blob_name)
        blob.upload_from_filename(source_path)
        return f"gs://{self.bucket_name}/{blob_name}"

    def retrieve(self, filename: str, dest_path: str) -> str:
        """Download backup file from GCS."""
        blob_name = f"{self.prefix}{filename}"
        blob = self.bucket.blob(blob_name)
        blob.download_to_filename(dest_path)
        return dest_path

    def list_backups(self) -> list[dict]:
        """List backup files in GCS."""
        backups = []
        blobs = self.client.list_blobs(self.bucket_name, prefix=self.prefix)
        for blob in blobs:
            name = blob.name.removeprefix(self.prefix)
            if name:
                backups.append({
                    "name": name,
                    "path": f"gs://{self.bucket_name}/{blob.name}",
                    "size": blob.size,
                    "modified": blob.updated.isoformat() if blob.updated else "",
                })
        return sorted(backups, key=lambda x: x["modified"], reverse=True)

    def delete(self, filename: str) -> bool:
        """Delete a backup file from GCS."""
        blob_name = f"{self.prefix}{filename}"
        blob = self.bucket.blob(blob_name)
        blob.delete()
        return True

    def cleanup(self, retention_days: int = 30, max_backups: int = 0):
        """Clean up old backups in GCS."""
        backups = self.list_backups()
        deleted = 0
        now = datetime.now()

        if retention_days > 0:
            for backup in backups:
                if backup["modified"]:
                    modified = datetime.fromisoformat(
                        backup["modified"].replace("+00:00", "")
                    )
                    age_days = (now - modified).days
                    if age_days > retention_days:
                        self.delete(backup["name"])
                        deleted += 1

        if max_backups > 0:
            remaining = self.list_backups()
            if len(remaining) > max_backups:
                for backup in remaining[max_backups:]:
                    self.delete(backup["name"])
                    deleted += 1

        return deleted


class AzureStorage:
    """Azure Blob Storage backend."""

    def __init__(
        self,
        container: str,
        prefix: str = "dbbackup/",
        connection_string: str = "",
    ):
        self.container_name = container
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""

        from azure.storage.blob import BlobServiceClient
        self.blob_service = BlobServiceClient.from_connection_string(connection_string)
        self.container_client = self.blob_service.get_container_client(container)

    def store(self, source_path: str, dest_name: Optional[str] = None) -> str:
        """Upload backup file to Azure Blob Storage."""
        if dest_name is None:
            dest_name = os.path.basename(source_path)

        blob_name = f"{self.prefix}{dest_name}"
        blob_client = self.container_client.get_blob_client(blob_name)

        with open(source_path, "rb") as data:
            blob_client.upload_blob(data, overwrite=True)

        return f"azure://{self.container_name}/{blob_name}"

    def retrieve(self, filename: str, dest_path: str) -> str:
        """Download backup file from Azure Blob Storage."""
        blob_name = f"{self.prefix}{filename}"
        blob_client = self.container_client.get_blob_client(blob_name)

        with open(dest_path, "wb") as f:
            download = blob_client.download_blob()
            download.readinto(f)

        return dest_path

    def list_backups(self) -> list[dict]:
        """List backup files in Azure Blob Storage."""
        backups = []
        blobs = self.container_client.list_blobs(name_starts_with=self.prefix)
        for blob in blobs:
            name = blob.name.removeprefix(self.prefix)
            if name:
                backups.append({
                    "name": name,
                    "path": f"azure://{self.container_name}/{blob.name}",
                    "size": blob.size,
                    "modified": blob.last_modified.isoformat() if blob.last_modified else "",
                })
        return sorted(backups, key=lambda x: x["modified"], reverse=True)

    def delete(self, filename: str) -> bool:
        """Delete a backup file from Azure Blob Storage."""
        blob_name = f"{self.prefix}{filename}"
        blob_client = self.container_client.get_blob_client(blob_name)
        blob_client.delete_blob()
        return True

    def cleanup(self, retention_days: int = 30, max_backups: int = 0):
        """Clean up old backups in Azure Blob Storage."""
        backups = self.list_backups()
        deleted = 0
        now = datetime.now()

        if retention_days > 0:
            for backup in backups:
                if backup["modified"]:
                    modified = datetime.fromisoformat(
                        backup["modified"].replace("+00:00", "")
                    )
                    age_days = (now - modified).days
                    if age_days > retention_days:
                        self.delete(backup["name"])
                        deleted += 1

        if max_backups > 0:
            remaining = self.list_backups()
            if len(remaining) > max_backups:
                for backup in remaining[max_backups:]:
                    self.delete(backup["name"])
                    deleted += 1

        return deleted


def get_storage_backend(config) -> LocalStorage:
    """
    Get the appropriate storage backend based on configuration.

    Returns a list of storage backends (local + any configured cloud backends).
    """
    backends = []

    # Local storage is always available
    backends.append(LocalStorage(config.local_path))

    # Add cloud backends if configured
    if config.s3_bucket:
        try:
            backends.append(S3Storage(
                bucket=config.s3_bucket,
                prefix=config.s3_prefix,
                region=config.s3_region,
                access_key=config.s3_access_key,
                secret_key=config.s3_secret_key,
                endpoint_url=config.s3_endpoint_url,
            ))
        except Exception:
            pass

    if config.gcs_bucket:
        try:
            backends.append(GCSStorage(
                bucket=config.gcs_bucket,
                prefix=config.gcs_prefix,
                credentials_file=config.gcs_credentials_file,
            ))
        except Exception:
            pass

    if config.azure_container and config.azure_connection_string:
        try:
            backends.append(AzureStorage(
                container=config.azure_container,
                prefix=config.azure_prefix,
                connection_string=config.azure_connection_string,
            ))
        except Exception:
            pass

    return backends
