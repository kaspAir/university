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

## Stand: S0 gebaut, S1 gebaut bis zum Tor

S0 = Verträge, Migrationen, echte lokale Anmeldung, Rollen, Policy, Audit/Outbox, Budget, Mock-Adapter.
S1 = Provider-Adapter (Anthropic, OpenAI) hinter dem Gateway, Selector mit Kernprüfung, Neuplanung nach Ausfall,
Gegenprüfung durch kanonisch anderes Modell, Aktivierungstor (Datenfreigabe, Preisbindung, Nachweise).
**Das S1-Tor «zwei echte freigegebene Modelle» ist ein Betriebsentscheid und noch offen**, siehe
[docs/S1_UMSETZUNG.md](docs/S1_UMSETZUNG.md) Abschnitt 3.

Vorschlag S0: [docs/S0_VORSCHLAG.md](docs/S0_VORSCHLAG.md) · Betrieb: [docs/BETRIEB.md](docs/BETRIEB.md) ·
Entscheide: [docs/adr/](docs/adr/) · CI: [docs/JENKINS.md](docs/JENKINS.md)

```
apps/api/campus_api/            FastAPI: Anmeldung, Sitzungen, CSRF, Commands, Fehlerobjekt, Health
packages/domain/campus_domain/  Fachregeln ohne Framework: Statusachsen, Geld, Routing-Kern, Startwächter, Rollen
packages/contracts/             Sechs JSON-Schemas des Pakets + typisierte Modelle + Routing-Policy
packages/infrastructure/        SQLAlchemy-Modelle, Audit/Outbox, Budget-Sperren, Aufrufjournal, Blob-Store
services/llm_gateway/           Gateway-Vertrag und Mock-Adapter (einziger freigegebener Adapter)
migrations/                     Alembic, Revision 0001_s0_core
config/                         Vorlagen aus dem Paket, Provider deaktiviert
tests/                          unit · integration (PostgreSQL) · adapter_contracts · security
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

## Nicht Teil von S0

Echte Modellzugänge, Temporal, Quellenabruf, Aussagen und Dossier, Fabrik und Qualifikation, Sandbox,
Weboberfläche, Deploy. Siehe Baufolge S1–S5 in der Architektur (Kap. 20) und die Lücken L1–L4 im S0-Vorschlag.
