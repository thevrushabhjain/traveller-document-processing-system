# file_utils.py (updated to include PDF support)
"""File-system helper utilities."""
from __future__ import annotations

import mimetypes
import re
import uuid
from pathlib import Path
from typing import Iterable

SUPPORTED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
SUPPORTED_PDF_EXT = {".pdf"}
SUPPORTED_EXT: set[str] = SUPPORTED_IMAGE_EXT | SUPPORTED_PDF_EXT


def sanitize_filename(name: str) -> str:
    """Remove path separators and unusual characters from an uploaded filename."""
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")
    return name or "upload"


def unique_upload_path(directory: Path, original_name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    safe = sanitize_filename(original_name)
    return directory / f"{uuid.uuid4().hex}_{safe}"


def is_pdf(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_PDF_EXT


def is_image(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_IMAGE_EXT


def is_supported(name: str) -> bool:
    return Path(name).suffix.lower() in SUPPORTED_EXT


def guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def human_size(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f}{unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f}TB"


def iter_files(paths: Iterable[Path]) -> Iterable[Path]:
    for p in paths:
        if p.is_dir():
            for f in p.rglob("*"):
                if f.is_file() and is_supported(f.name):
                    yield f
        elif p.is_file():
            yield p