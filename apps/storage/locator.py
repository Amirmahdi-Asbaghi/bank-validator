"""Local file storage for uploaded validation files.

In STEP 14 this will be replaced by MinIO/S3 via django-storages.
For now we store files on disk under MEDIA_ROOT/uploads/.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from django.conf import settings


def uploads_dir() -> Path:
    base = Path(getattr(settings, "MEDIA_ROOT", Path(settings.BASE_DIR) / "media"))
    path = base / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_uploaded_file(filename: str, content: bytes) -> Path:
    """Save the uploaded bytes to disk and return the path."""
    ext = Path(filename).suffix.lower() or ".bin"
    target = uploads_dir() / f"{uuid.uuid4().hex}{ext}"
    target.write_bytes(content)
    return target


def read_uploaded_file(path: str) -> bytes:
    return Path(path).read_bytes()