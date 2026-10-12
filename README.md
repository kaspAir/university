# Phronesis-Forschungscampus

Ein autonomer Forschungscampus mit spezialisierten Agenten, gemeinsamem wissenschaftlichem
Gedächtnis und begründeter, konfigurierbarer Auswahl mehrerer Sprachmodelle.

## Grundlagen

Die massgebliche Norm ist die **Forschungsverfassung V0.2**; die technische Bauweise beschreibt
die **Systemarchitektur V0.1**. Beide liegen mit ihren Referenzdokumenten und dem Entwicklungspaket
unter `00_Basisdokumente/`, die Oberfläche ist in `design_handoff_phronesis_campus_ui/` übergeben.

| Dokument | Rolle |
| --- | --- |
| `00_Basisdokumente/Phronesis_Forschungsverfassung_V0.2.docx` | Oberste Norm (Art. 1–28, Anhänge A–G) |
| `00_Basisdokumente/Phronesis_Campus_Architektur_V0.1.md` | Systemarchitektur, Baufolge S0–S5, Tests T01–T17 / A01–A14 |
| `00_Basisdokumente/Phronesis_Campus_V0.1/` | Entwicklungspaket: CLAUDE.md, Schemas, Fixtures, Diagramme, Abnahmeplan (hash-manifestiert) |
| `design_handoff_phronesis_campus_ui/` | UI-Handoff: Design System «Modernist», Ansichten, nicht verhandelbare UI-Regeln |
| `CLAUDE.md` | Entwicklungsanweisung (Paketfassung plus Repo-Bezug) |

## Stand: S0, S1 (mit Härtung S1.1), S2, S3 und S4 gebaut; offline geprüft, Sandbox lokal gemessen

S0 = Verträge, Migrationen, echte lokale Anmeldung, Rollen, Policy, Audit/Outbox, Budget, Mock-Adapter.
S1 = Provider-Adapter (Anthropic, OpenAI) hinter dem Gateway, Selector mit Kernprüfung, Neuplanung nach Ausfall,
Gegenprüfung durch kanonisch anderes Modell, Aktivierungstor (Datenfreigabe, Preisbindung, Nachweise).
S1.1 = gemeinsame Aufrufprüfung für jeden Modellaufruf, dauerhafte Aufruf- und Kostennachweise, Fassungs- und Routenbindung,
Nachweise je Aufgabenklasse, konsistente Prüfabschlüsse, Schlüsselbindung an genehmigte Endpunkte.
S2 = Internetdienst mit kontrolliertem Abruf, versionierte Quellen mit Fundstellen, Aussagen mit Evidenz (kein erfundener Messnachweis),
Rückzug mit Neubewertung, Dossier mit hashgebundener Freigabe, dauerhafter Ablauf auf Temporal.
S3 = Fabrik: Bedarfsnachweis, Kandidaten mit begrenzten Rechten, geschütztes versioniertes Prüfmaterial, unabhängige Qualifikation durch
kanonisch anderes, selbst qualifiziertes Prüfmodell, automatische Aktivierung ohne Rückschritt, Herkunftsansicht, Stilllegung
([docs/S3_UMSETZUNG.md](docs/S3_UMSETZUNG.md)).
S4 = Sandbox-Controller mit gehärteten Docker-Läufen, Probe und menschlicher Abnahme je Runner/Image/Host, Experimente mit gemessenen
Nachweisen (stdout ist Inhalt), Experimente als Belege nur wenn gemessen, Prüfmaterial im Tresor hinter eigener Datenbankrolle
([docs/S4_UMSETZUNG.md](docs/S4_UMSETZUNG.md)).

**Nichts davon ist live nachgewiesen**; die Sandbox-Härtung ist mit echten Containern auf dem Entwicklungs-Host gemessen, nicht auf einem
Zielhost. Das Tor «zwei echte freigegebene Modelle» ist ein Betriebsentscheid, siehe
[docs/S1_UMSETZUNG.md](docs/S1_UMSETZUNG.md) Abschnitt 3. Befunde und Abnahmepunkte: [docs/S2_UMSETZUNG.md](docs/S2_UMSETZUNG.md).

Vorschlag S0: [docs/S0_VORSCHLAG.md](docs/S0_VORSCHLAG.md) · Betrieb: [docs/BETRIEB.md](docs/BETRIEB.md) ·
Entscheide: [docs/adr/](docs/adr/) · CI: [docs/JENKINS.md](docs/JENKINS.md)

```
apps/api/campus_api/            FastAPI: Anmeldung, Sitzungen, CSRF, Commands, Fehlerobjekt, Health
packages/domain/campus_domain/  Fachregeln ohne Framework: Statusachsen, Geld, Routing-Kern, Startwächter, Rollen
packages/contracts/             Sechs JSON-Schemas des Pakets + typisierte Modelle + Routing-Policy
packages/infrastructure/        SQLAlchemy-Modelle, Audit/Outbox, Budget-Sperren, Aufrufjournal, Blob-Store
services/llm_gateway/           Gateway-Vertrag, Mock-Adapter, Anthropic- und OpenAI-Adapter, Endpunktbindung, Geheimnisauflösung
services/sandbox_controller/    Sandbox-Controller: Job-/Ergebnisvertrag, Docker-Runner (gehärtet), Mock-Runner, Probe
workers/research/               Temporal-Workflow und idempotente Aktivitäten (Quellen, Route, Erzeugung, Prüfung, Dossier)
migrations/                     Alembic, Revisionen 0001 (eingefrorene S0-Basis) bis 0009 (Tresor, Sandbox)
config/                         Vorlagen aus dem Paket, Provider deaktiviert
tests/                          unit · integration (PostgreSQL) · adapter_contracts · security · sandbox (echte Container, opt-in)
```

Kurzstart (Details in `docs/BETRIEB.md`):

```bash
docker compose up -d db
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements-dev.txt
cp .env.example .env
set -a; source .env; set +a
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m pytest -q
```

## Noch offen

Echte Modellzugänge (Live-Tor), Egress-Proxy, Sandbox-Abnahme auf dem Zielhost, Weboberfläche, Deploy, S5 (kontrollierter Pilot mit
unabhängiger wissenschaftlicher Bewertung). Siehe Baufolge S1–S5 in der Architektur (Kap. 20) und die offenen Entscheide in
[docs/S4_UMSETZUNG.md](docs/S4_UMSETZUNG.md) Abschnitt 5.
