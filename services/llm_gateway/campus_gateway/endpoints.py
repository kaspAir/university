"""Binding of access keys to approved provider endpoints (S1.1 finding 5).

A key may only travel to the endpoint it was approved for: official adapters accept the official host (or none),
`openai_compatible` needs an api_base whose host is listed in CAMPUS_APPROVED_API_HOSTS. Callers never assemble
adapter, key and address freely; the combination comes from a registered catalog deployment.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from campus_domain.errors import CampusError, ErrorCode

OFFICIAL_API_BASES: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
}


def approved_hosts() -> set[str]:
    raw = os.environ.get("CAMPUS_APPROVED_API_HOSTS", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def check_api_base(adapter_name: str, api_base: str | None) -> None:
    """Raise ENDPOINT_NOT_APPROVED unless the (adapter, api_base) pair is permitted."""
    if adapter_name == "mock":
        return
    if adapter_name in OFFICIAL_API_BASES:
        if api_base is None or api_base.rstrip("/") == OFFICIAL_API_BASES[adapter_name]:
            return
        raise CampusError(ErrorCode.ENDPOINT_NOT_APPROVED,
                          f"Adapter {adapter_name} sendet Schlüssel nur an {OFFICIAL_API_BASES[adapter_name]}, nicht an {api_base}",
                          blocked_action="llm.invoke", required_resolution="api_base entfernen oder openai_compatible mit genehmigtem Host verwenden")
    if adapter_name == "openai_compatible":
        if not api_base:
            raise CampusError(ErrorCode.ENDPOINT_NOT_APPROVED, "openai_compatible braucht eine api_base", blocked_action="llm.invoke")
        parts = urlsplit(api_base)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or host not in approved_hosts():
            raise CampusError(ErrorCode.ENDPOINT_NOT_APPROVED,
                              f"Endpunkt {api_base} ist nicht in CAMPUS_APPROVED_API_HOSTS freigegeben (https und Hostname nötig)",
                              blocked_action="llm.invoke", required_resolution="Host in CAMPUS_APPROVED_API_HOSTS aufnehmen (Betriebsentscheid)")
        return
    raise CampusError(ErrorCode.ADAPTER_NOT_APPROVED, f"Unbekannter Adapter {adapter_name}", blocked_action="llm.invoke")
