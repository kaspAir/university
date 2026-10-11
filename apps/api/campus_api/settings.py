from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CAPABILITY_LEVELS = ("development_mock", "restricted_research", "isolated_execution")


@dataclass(frozen=True)
class Settings:
    database_url: str
    capability_level: str
    cookie_secure: bool
    session_ttl_hours: int
    blob_root: Path

    @staticmethod
    def from_env() -> Settings:
        level = os.environ.get("CAMPUS_CAPABILITY_LEVEL", "development_mock")
        if level not in CAPABILITY_LEVELS:
            raise RuntimeError(f"CAMPUS_CAPABILITY_LEVEL ungültig: {level}")
        if level != "development_mock":
            # S0 has no sandbox and no approved provider adapters: higher levels are not available, not faked.
            raise RuntimeError(
                f"Leistungsebene {level} ist in Stufe S0 nicht verfügbar (kein Sandboxpfad, keine freigegebenen Adapter)"
            )
        url = os.environ.get("CAMPUS_DATABASE_URL")
        if not url:
            raise RuntimeError("CAMPUS_DATABASE_URL ist nicht gesetzt")
        return Settings(
            database_url=url,
            capability_level=level,
            cookie_secure=os.environ.get("CAMPUS_COOKIE_SECURE", "true").lower() == "true",
            session_ttl_hours=int(os.environ.get("CAMPUS_SESSION_TTL_HOURS", "12")),
            blob_root=Path(os.environ.get("CAMPUS_BLOB_ROOT", "var/blobs")),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
