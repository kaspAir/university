# ADR-0001 Python-Monorepo mit Paketpfaden

Stand: 11.10.2026 · Status: angenommen (Entwicklung) · Bezug: Paket-CLAUDE.md «Proposed repository», Architektur Kap. 3 (modularer Kern)

## Entscheid
Ein Repository, mehrere Python-Pakete unter `packages/`, `apps/`, `services/`. Sie werden über `pythonpath` (pytest) bzw. `PYTHONPATH` (Laufzeit, Container) eingebunden, nicht als getrennt installierbare Distributionen.

## Gründe
- Die Modulgrenzen (Domain ohne Framework, Infrastruktur, API, Gateway) bleiben als Verzeichnisse sichtbar und durch Importregeln prüfbar.
- Ein Schema-Strang (Alembic) und ein Testlauf; keine Versionskoordination zwischen Distributionen in S0.

## Folgen
- Importrichtung ist Pflicht: `campus_domain` importiert nichts aus Infrastruktur, API oder Gateway. Ein statischer Test prüft das.
- Eine spätere Trennung in Distributionen ist ohne Umbenennung möglich.
