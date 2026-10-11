"""Internet access rules (architecture chapter 12, OWASP SSRF prevention, T07, T15, T16).

Pure functions: URL normalization, target classification, export check. The fetch itself lives in the service.
Policy is action and data class based, not a static domain list: any public target may be read (T15); internal,
local, private and metadata addresses are never reachable (T07); sensitive labels never leave through a query (T16).
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from .errors import CampusError, ErrorCode

ALLOWED_SCHEMES = {"https", "http"}
ALLOWED_PORTS = {80, 443}
MAX_REDIRECTS = 5
MAX_BYTES = 5 * 1024 * 1024
EXPORTABLE_LABELS = {"public"}

_BLOCKED_HOSTNAMES = {"localhost", "metadata", "metadata.google.internal", "instance-data"}


@dataclass(frozen=True)
class Target:
    url: str
    scheme: str
    host: str
    port: int


def normalize_url(url: str) -> Target:
    """Lower-case scheme/host, default port, no fragment, no credentials. Rejects non-http(s), odd ports, userinfo."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise CampusError(ErrorCode.DATA_EXPORT_DENIED, f"Schema {scheme or '(leer)'} ist für Abrufe nicht zugelassen",
                          blocked_action="source.fetch")
    if parts.username or parts.password:
        raise CampusError(ErrorCode.DATA_EXPORT_DENIED, "Zugangsdaten in der URL sind nicht zugelassen", blocked_action="source.fetch")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "URL ohne Host", blocked_action="source.fetch")
    port = parts.port or (443 if scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise CampusError(ErrorCode.DATA_EXPORT_DENIED, f"Port {port} ist für Abrufe nicht zugelassen", blocked_action="source.fetch")
    netloc = host if port == (443 if scheme == "https" else 80) else f"{host}:{port}"
    normalized = urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
    return Target(url=normalized, scheme=scheme, host=host, port=port)


def classify_address(ip_text: str) -> str | None:
    """Return a block reason for a resolved address, or None if it is a routable public address."""
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return "UNPARSEABLE_ADDRESS"
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return classify_address(str(ip.ipv4_mapped))  # judge the embedded IPv4 address, not the IPv6 wrapper
    # Order matters: the stdlib counts link-local and unspecified addresses as "private" too.
    if ip.is_loopback:
        return "LOOPBACK"
    if ip.is_link_local:
        return "LINK_LOCAL_OR_METADATA"
    if ip.is_unspecified or ip.is_reserved:
        return "RESERVED"
    if ip.is_multicast:
        return "MULTICAST"
    if ip.is_private:
        return "PRIVATE_NETWORK"
    if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"):
        return "CARRIER_GRADE_NAT"
    return None


def check_target(target: Target, resolved_addresses: list[str]) -> None:
    """All resolved addresses must be public; the hostname itself must not be a known internal name."""
    if target.host in _BLOCKED_HOSTNAMES or target.host.endswith((".local", ".internal", ".localhost")):
        raise CampusError(ErrorCode.DATA_EXPORT_DENIED, f"Ziel {target.host} ist ein interner Name", blocked_action="source.fetch")
    if not resolved_addresses:
        raise CampusError(ErrorCode.DATA_EXPORT_DENIED, f"Ziel {target.host} ist nicht auflösbar", blocked_action="source.fetch",
                          retryable=True)
    for address in resolved_addresses:
        reason = classify_address(address)
        if reason:
            raise CampusError(ErrorCode.DATA_EXPORT_DENIED, f"Ziel {target.host} ({address}) ist nicht öffentlich: {reason} (T07)",
                              blocked_action="source.fetch")


def export_check(target: Target, task_data_labels: list[str]) -> None:
    """Query strings are outbound data: with non-public labels on the task they are not allowed to leave (T16)."""
    if target.url.split("?", 1)[1:] and not set(task_data_labels) <= EXPORTABLE_LABELS:
        raise CampusError(ErrorCode.DATA_EXPORT_DENIED,
                          "Suchparameter in der URL gelten als Weitergabe; die Aufgabe trägt nicht-öffentliche Datenklassen",
                          blocked_action="source.fetch", required_resolution="URL ohne Parameter oder Aufgabe ohne vertrauliche Daten")
