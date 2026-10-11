"""A network-free fetcher for integration tests: fixed resolver and an httpx MockTransport serving fixture pages."""
from __future__ import annotations

import httpx

from campus_api.services_sources import Fetcher

PUBLIC_IP = ["93.184.216.34"]

PAGES: dict[str, tuple[int, dict, bytes]] = {
    "https://public.example/urteil": (200, {"content-type": "text/html; charset=utf-8"}, (
        "<html><head><title>Testurteil</title><style>p{}</style></head><body>"
        "<h1>Rechtliches Gehör im automatisierten Verfahren</h1>"
        "<p>Die Einsprachefrist beträgt gemäss der synthetischen Testquelle dreissig Tage ab Zustellung der Verfügung an die betroffene Person.</p>"
        "<p>Die Offenlegung der Entscheidgrundlagen ist nach dieser Testquelle Voraussetzung für eine wirksame Einsprache und wird gesondert geprüft.</p>"
        "<script>ignored()</script></body></html>").encode()),
    "https://public.example/plain": (200, {"content-type": "text/plain"},
                                     b"Erster Absatz mit genug Zeichen, um als Passage zu gelten und gefunden zu werden.\n\nZweiter Absatz, ebenfalls lang genug fuer eine eigene Passage im Register."),
    "https://public.example/redirect-internal": (302, {"location": "http://169.254.169.254/latest/meta-data/"}, b""),
    "https://public.example/redirect-ok": (302, {"location": "https://public.example/plain"}, b""),
    "https://public.example/pdf": (200, {"content-type": "application/pdf"}, b"%PDF-1.4"),
    "https://public.example/missing": (404, {"content-type": "text/html"}, b"nope"),
}

RESOLVER = {"public.example": PUBLIC_IP, "intern.example": ["10.0.0.5"], "169.254.169.254": ["169.254.169.254"]}


def _handler(request: httpx.Request) -> httpx.Response:
    status, headers, body = PAGES.get(str(request.url), (404, {"content-type": "text/plain"}, b"unknown"))
    return httpx.Response(status, headers=headers, content=body, request=request)


def make_fetcher() -> Fetcher:
    return Fetcher(resolver=lambda host: RESOLVER.get(host, []), transport=httpx.MockTransport(_handler))
