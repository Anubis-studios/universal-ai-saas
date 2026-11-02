"""Persist generated media to disk and hand back a URL served by /media."""

from __future__ import annotations

import base64
import os
import secrets
import time
from pathlib import Path

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "outputs")).resolve()

EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "video/mp4": "mp4"}


def ensure_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def save_bytes(data: bytes, mime: str, stem: str = "image") -> str:
    """Write bytes under outputs/ and return the public URL path."""
    ensure_dir()
    ext = EXTENSIONS.get(mime.split(";")[0], "bin")
    name = f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.{ext}"
    (OUTPUT_DIR / name).write_bytes(data)
    return f"/media/{name}"


def save_b64(b64: str, mime: str, stem: str = "image") -> str:
    return save_bytes(base64.b64decode(b64), mime, stem)
