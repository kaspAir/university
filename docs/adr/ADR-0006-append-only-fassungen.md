# ADR-0006 Versionierte Fachobjekte append-only

Stand: 11.10.2026 · Status: angenommen · Bezug: Architektur Kap. 5 («Versionierte Objekte sind append-only auf Anwendungsebene»), I05

## Entscheid
Fachliche Änderungen an Mandat, Policy, Katalog, Agentenprofil und Skill erzeugen eine neue Fassung (`version`-Zähler je logischer Kennung). Der Zustand einer Fassung (z. B. `approved`, `superseded`) ist das einzige veränderliche Feld und wird mit Audit-Ereignis gesetzt. Eine Freigabe bindet `(logische Kennung, Fassung, Hash)`.
