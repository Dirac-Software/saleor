"""File storage for product ingestion Excel files."""

import os
import secrets
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.core.cache import cache
from django.core.files.uploadedfile import UploadedFile

# Store files for 1 hour
FILE_EXPIRY_SECONDS = 3600
CACHE_KEY_PREFIX = "product_ingestion_file:"


def save_uploaded_file(uploaded_file: UploadedFile) -> tuple[str, str]:
    """Save an uploaded Excel file temporarily.

    Args:
        uploaded_file: Django UploadedFile instance

    Returns:
        Tuple of (file_id, file_path) where:
        - file_id: Unique identifier for retrieving the file later
        - file_path: Absolute path to the saved file

    """
    import logging

    logger = logging.getLogger(__name__)

    # Generate unique file ID
    file_id = secrets.token_urlsafe(16)

    # Create temp directory if it doesn't exist
    temp_dir = Path(tempfile.gettempdir()) / "saleor_product_ingestion"
    temp_dir.mkdir(exist_ok=True)

    # Save file with unique name
    if not uploaded_file.name:
        raise ValueError("Uploaded file must have a name")
    file_extension = Path(uploaded_file.name).suffix
    file_path = temp_dir / f"{file_id}{file_extension}"

    logger.info("Saving uploaded file to: %s", file_path)

    # Write file
    bytes_written = 0
    with open(file_path, "wb") as f:
        for chunk in uploaded_file.chunks():
            bytes_written += len(chunk)
            f.write(chunk)

    logger.info("Wrote %d bytes to %s", bytes_written, file_path)

    if bytes_written == 0:
        raise ValueError("Uploaded file contains no data (0 bytes)")

    # Store file path in cache with expiry
    cache_key = f"{CACHE_KEY_PREFIX}{file_id}"
    cache.set(
        cache_key,
        {
            "path": str(file_path),
            "original_name": uploaded_file.name,
            "created_at": datetime.now(UTC).isoformat(),
            "size_bytes": bytes_written,
        },
        timeout=FILE_EXPIRY_SECONDS,
    )

    return file_id, str(file_path)


def get_ingestion_file(file_id: str) -> str | None:
    """Retrieve file path for a previously uploaded file.

    Args:
        file_id: File identifier from save_uploaded_file()

    Returns:
        Absolute path to file, or None if not found or expired

    """
    cache_key = f"{CACHE_KEY_PREFIX}{file_id}"
    file_data = cache.get(cache_key)

    if not file_data:
        return None

    file_path = file_data["path"]

    # Check if file still exists
    if not os.path.exists(file_path):
        # Clean up cache entry
        cache.delete(cache_key)
        return None

    return file_path


def cleanup_expired_files() -> int:
    """Clean up expired ingestion files from temp directory.

    This should be called periodically (e.g., via Celery task).

    Returns:
        Number of files deleted

    """
    temp_dir = Path(tempfile.gettempdir()) / "saleor_product_ingestion"
    if not temp_dir.exists():
        return 0

    deleted_count = 0
    expiry_time = datetime.now(UTC) - timedelta(seconds=FILE_EXPIRY_SECONDS)

    for file_path in temp_dir.glob("*"):
        # Check file modification time
        if file_path.is_file():
            file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=UTC)
            if file_mtime < expiry_time:
                try:
                    file_path.unlink()
                    deleted_count += 1
                except OSError:
                    pass

    return deleted_count
