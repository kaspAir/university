# Betrieb und lokale Entwicklung (Stufe S0)

Stand 11.10.2026. Referenzumgebung ist Linux; unter Windows läuft alles mit Docker Desktop und einem lokalen Python 3.12+.

## Voraussetzungen

- Docker (für PostgreSQL 16, Jenkins und die CI-Testcontainer)
- Python 3.12 oder neuer
- Kein Node für S0 (die Weboberfläche folgt, siehe `docs/S0_VORSCHLAG.md`, Lücke L1)

## Einmalig einrichten

```bash
cd /c/Projekte/university
docker compose up -d db
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Die Datenbank wird mit zwei Rollen angelegt (`docker/postgres-init.sql`): `campus_owner` führt Migrationen aus,
`campus_app` ist die Anwendungsrolle **ohne** `UPDATE`/`DELETE` auf `audit_events` (ADR-0004). Beide Passwörter in
`.env.example` sind Entwicklungswerte.

## Schema

```bash
set -a; source .env; set +a
.venv/Scripts/python -m alembic upgrade head
```

`alembic downgrade base` ist getestet. Die S0-Basis (Revision `0001_s0_core`) legt alle Tabellen, den
Append-only-Trigger auf `audit_events` und die Rechte der Anwendungsrolle an.

## Workspace, erster Benutzer, Entwürfe

```bash
export PYTHONPATH="packages/domain;packages/contracts;packages/infrastructure;services/llm_gateway;apps/api"
.venv/Scripts/python -m campus_api.cli init-workspace --id ws-dev --name "Entwicklungscampus"
CAMPUS_ADMIN_PASSWORD='<mindestens 12 Zeichen>' .venv/Scripts/python -m campus_api.cli create-user --workspace ws-dev --username leitung --display-name "Forschungsleitung" --roles research_director,operator,producer
.venv/Scripts/python -m campus_api.cli import-policy --workspace ws-dev config/routing_policy.example.yaml
.venv/Scripts/python -m campus_api.cli import-catalog --workspace ws-dev config/model_catalog.example.yaml
```

Die CLI legt Entwürfe an. Aktivieren ist ein eigener Entscheid über die API: Policy durch `research_director`
(Schutz- und Prüfstandards, Art. 11), Katalog durch `operator` (administrativer Zugang, Kap. 8). Die Vorlagen aus
`config/` haben deaktivierte Provider und ungesetzte Limits; mit ihnen startet kein Lauf (`LIMITS_NOT_SET`,
`NO_ELIGIBLE_MODEL`). Das ist gewollt.

## API starten

```bash
.venv/Scripts/python -m uvicorn campus_api.main:app --port 8000
```

- OpenAPI: `http://localhost:8000/api/v1/docs`
- Liveness: `GET /api/v1/health` · Readiness: `GET /api/v1/ready` (Datenbank, aktive Policy, aktiver Katalog)
- Jede Antwort trägt `X-Campus-Capability-Level` (in S0 immer `development_mock`) und `X-Trace-Id`.

Anmeldung: `POST /api/v1/auth/login` setzt ein HttpOnly-Cookie und liefert `csrf_token`. Schreibende Aufrufe brauchen
die Header `X-CSRF-Token` und `Idempotency-Key`.

## Rollen (S0)

| Rolle | Darf |
| --- | --- |
| `research_director` | Mandate anlegen und entscheiden, Policy aktivieren, Budgets, Projekte, Stopp/Wiederaufnahme |
| `operator` | Benutzer, Katalog importieren/aktivieren, Preise, Läufe abbrechen; **keine** fachlichen Freigaben |
| `integrity_officer` | Stopp, Läufe abbrechen (Sperrrechte; Besetzung offen, Lücke L3) |
| `producer` / `reviewer` | Aufgaben, Agenten/Skills registrieren, Routen autorisieren, Läufe starten |

## Tests

```bash
export CAMPUS_TEST_DATABASE_URL="postgresql+psycopg://campus_owner:campus_owner_dev@localhost:5433/campus_test"
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m ruff check apps packages services tests migrations
```

Integrationstests setzen die Testdatenbank per `alembic downgrade base` / `upgrade head` zurück. Ohne erreichbares
PostgreSQL werden sie **übersprungen und als übersprungen gemeldet**, nie durch SQLite ersetzt.

## Provider-Adapter (S1)

Standardmässig ist nur `mock` freigegeben. Echte Adapter werden per Liste freigegeben und holen ihre Schlüssel aus
Umgebungsvariablen des Gateway-Prozesses:

```bash
export CAMPUS_APPROVED_ADAPTERS=mock,anthropic,openai
export CAMPUS_SECRET_PROVIDERS_ANTHROPIC='...'     # secret://providers/anthropic
export CAMPUS_SECRET_PROVIDERS_OPENAI='...'        # secret://providers/openai
.venv/Scripts/python -m campus_api.cli probe-provider --adapter anthropic --secret-ref secret://providers/anthropic
```

Discovery listet native Modellkennungen; sie ist weder Datenfreigabe noch Qualifikation. Der Weg bis zum aktivierten
Katalog mit zwei echten Anbietern steht in `docs/S1_UMSETZUNG.md`, Abschnitt 3. Live-Tests gegen echte Anbieter laufen
nur mit `CAMPUS_LIVE_TESTS=1` und kosten Geld.

Ein beendeter Lauf wird mit `POST /api/v1/runs/{id}/review` durch das in der Route autorisierte, kanonisch andere
Modell geprüft. Einwände bleiben als `findings` offen, bis Menschen sie bearbeiten.

## Was noch fehlt

- Zwei echte freigegebene Modellzugänge (Betriebsentscheid, S1-Tor); bis dahin Leistungsebene `development_mock`.
- Kein Temporal, keine Quellenaufnahme, keine Aussagen, keine Sandbox. Lauf und Prüfung sind synchrone Aufrufe
  mit Journal und Kostenabrechnung; sie zeigen den Kontrollweg, nicht Forschung (S2 ff.).
- Keine Weboberfläche. Kein Deploy (Zielhost offen).

## Abhängigkeiten

`requirements.txt` und `requirements-dev.txt` sind das Ergebnis eines tatsächlichen, getesteten `pip freeze`
(Datum im Dateikopf). Container-Digests werden erst nach einem getesteten Build festgeschrieben.
