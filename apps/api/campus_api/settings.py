from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CAPABILITY_LEVELS = ("development_mock", "restricted_research", "isolated_execution")
SANDBOX_RUNNERS = ("none", "mock", "docker")


@dataclass(frozen=True)
class Settings:
    database_url: str
    capability_level: str
    cookie_secure: bool
    session_ttl_hours: int
    blob_root: Path
    vault_database_url: str | None = None  # role campus_vault; protected evaluation material (S4)
    sandbox_runner: str = "none"  # none | mock | docker (S4)
    sandbox_image: str | None = None  # digest-pinned image for the docker runner
    sandbox_workroot: Path = Path("var/sandbox")
    search_provider: str | None = None  # none | brave: the search service the core may call for source discovery (key in the process env)
    search_pricing_ref: str | None = None  # registered price per million search requests; without it discovery refuses (A14)

    @staticmethod
    def from_env() -> Settings:
        level = os.environ.get("CAMPUS_CAPABILITY_LEVEL", "development_mock")
        if level not in CAPABILITY_LEVELS:
            raise RuntimeError(f"CAMPUS_CAPABILITY_LEVEL ungültig: {level}")
        if level != "development_mock":
            # restricted_research needs the egress proxy that closes the DNS-rebinding gap (BETRIEB.md); isolated_execution needs that
            # plus an accepted sandbox path on the target host. Neither is available yet, so neither level is claimed.
            raise RuntimeError(
                f"Leistungsebene {level} ist nicht verfügbar: restricted_research setzt den Egress-Proxy voraus, isolated_execution zusätzlich "
                "eine Sandbox-Abnahme auf dem Zielhost (docs/BETRIEB.md)"
            )
        url = os.environ.get("CAMPUS_DATABASE_URL")
        if not url:
            raise RuntimeError("CAMPUS_DATABASE_URL ist nicht gesetzt")
        runner = os.environ.get("CAMPUS_SANDBOX_RUNNER", "none")
        if runner not in SANDBOX_RUNNERS:
            raise RuntimeError(f"CAMPUS_SANDBOX_RUNNER ungültig: {runner} (none | mock | docker)")
        image = os.environ.get("CAMPUS_SANDBOX_IMAGE") or None
        if runner == "docker" and not (image and "@sha256:" in image):
            raise RuntimeError("CAMPUS_SANDBOX_IMAGE muss für den Docker-Runner per Digest festgeschrieben sein (name@sha256:...)")
        return Settings(
            database_url=url,
            capability_level=level,
            cookie_secure=os.environ.get("CAMPUS_COOKIE_SECURE", "true").lower() == "true",
            session_ttl_hours=int(os.environ.get("CAMPUS_SESSION_TTL_HOURS", "12")),
            blob_root=Path(os.environ.get("CAMPUS_BLOB_ROOT", "var/blobs")),
            vault_database_url=os.environ.get("CAMPUS_VAULT_DATABASE_URL") or None,
            sandbox_runner=runner,
            sandbox_image=image,
            sandbox_workroot=Path(os.environ.get("CAMPUS_SANDBOX_WORKROOT", "var/sandbox")),
            search_provider=(os.environ.get("CAMPUS_SEARCH_PROVIDER") or "none").lower() if (os.environ.get("CAMPUS_SEARCH_PROVIDER") or "none").lower() != "none" else None,
            search_pricing_ref=os.environ.get("CAMPUS_SEARCH_PRICING_REF") or None,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
