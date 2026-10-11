"""Blob store port with a local, content-addressed implementation for the pilot (chapter 19)."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol


class BlobStore(Protocol):
    def put(self, data: bytes) -> str: ...

    def get(self, sha256: str) -> bytes: ...

    def exists(self, sha256: str) -> bool: ...


class LocalBlobStore:
    def __init__(self, root: Path):
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ValueError("ungültige Blob-Kennung")
        return self._root / sha256[:2] / sha256

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self._path(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".staging")  # staged first, referenced only after completion (chapter 16)
            tmp.write_bytes(data)
            tmp.replace(path)
        return digest

    def get(self, sha256: str) -> bytes:
        return self._path(sha256).read_bytes()

    def exists(self, sha256: str) -> bool:
        return self._path(sha256).exists()
