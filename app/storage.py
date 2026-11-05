import mimetypes
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from .config import settings

# What the grader can read directly: PDFs as documents, images via vision, plain text inline.
SUBMISSION_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".txt": "text/plain",
    ".md": "text/plain",
}


def save_upload(upload: UploadFile, folder: str, allowed: dict | None = None) -> tuple[str, str, str]:
    """Save an uploaded file under UPLOAD_DIR/folder. Returns (relative_path, original_name, content_type)."""
    original = Path(upload.filename or "file").name
    ext = Path(original).suffix.lower()
    if allowed is not None and ext not in allowed:
        raise HTTPException(400, f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(allowed))}")

    data = upload.file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(400, f"'{original}' is larger than {settings.max_upload_mb} MB")

    target_dir = settings.upload_dir / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    (target_dir / stored_name).write_bytes(data)

    content_type = (allowed or {}).get(ext) or mimetypes.guess_type(original)[0] or "application/octet-stream"
    return f"{folder}/{stored_name}", original, content_type


def absolute_path(relative: str) -> Path:
    path = (settings.upload_dir / relative).resolve()
    if settings.upload_dir not in path.parents:
        raise HTTPException(400, "Invalid file path")
    return path


def has_file(upload: UploadFile | None) -> bool:
    return upload is not None and bool(upload.filename)
