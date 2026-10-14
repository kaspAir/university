"""Search service adapters for the campus's own source discovery. Same discipline as the model adapters (ADR-0007): the key lives only
in this process (`secret://search/<name>` -> CAMPUS_SECRET_SEARCH_<NAME>), travels only to the one approved endpoint, no retries, no
substitution, every failure typed. A hit is a pointer (title, url, snippet); it becomes a source only after the internet service fetched it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from .secrets import EnvSecretResolver

SEARCH_ENDPOINTS = {"brave": "https://api.search.brave.com/res/v1/web/search"}  # Brave Web Search API, read 14.10.2026


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    age: str | None = None


class SearchUnavailable(Exception):
    """Rate limit, 5xx or connection failure: retryable through a later round, never silently."""


class SearchRejected(Exception):
    """4xx other than rate limit: key, plan or request problem; a configuration issue."""


class BraveSearch:
    name = "brave"
    secret_ref = "secret://search/brave"

    def __init__(self, secrets: EnvSecretResolver | None = None, timeout_seconds: float = 20.0):
        self._secrets = secrets or EnvSecretResolver()
        self._timeout = timeout_seconds

    def search(self, query: str, *, count: int = 10, language: str = "de", country: str = "CH") -> list[SearchHit]:
        params = {"q": query, "count": max(1, min(count, 20)), "search_lang": language, "country": country}
        headers = {"Accept": "application/json", "X-Subscription-Token": self._secrets.resolve(self.secret_ref)}
        try:
            response = httpx.get(SEARCH_ENDPOINTS[self.name], params=params, headers=headers, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise SearchUnavailable(f"{type(exc).__name__}") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise SearchUnavailable(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise SearchRejected(f"HTTP {response.status_code}")
        results = ((response.json().get("web") or {}).get("results") or [])
        return [SearchHit(title=str(r.get("title") or ""), url=str(r.get("url") or ""), snippet=str(r.get("description") or ""),
                          age=r.get("age") or r.get("page_age")) for r in results if r.get("url")]


def build_search(provider: str | None):
    """The configured search service or None (discovery is then reported as unavailable, never simulated)."""
    provider = (provider or os.environ.get("CAMPUS_SEARCH_PROVIDER") or "none").lower()
    if provider == "none":
        return None
    if provider == "brave":
        return BraveSearch()
    raise ValueError(f"Unbekannter Suchdienst CAMPUS_SEARCH_PROVIDER={provider}")
