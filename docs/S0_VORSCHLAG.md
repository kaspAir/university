# S0 – Vorschlag: Repository-Struktur und kleinste vollständige Scheibe

Stand 11.10.2026 · Grundlage: Entwicklungspaket V0.1 (`00_Basisdokumente/Phronesis_Campus_V0.1/CLAUDE.md`, Baufolge Kap. 20), Forschungsverfassung V0.2, UI-Handoff (`design_handoff_phronesis_campus_ui/`).

Das Paket verlangt, vor dem Bau «die Repository-Struktur und die kleinste vollständige S0-Scheibe» vorzuschlagen. Dieses Dokument ist dieser Vorschlag. Er wird mit der Umsetzung fortgeschrieben; Abweichungen stehen im Abschnitt «Entscheide und Abweichungen».

## 1 Repository-Struktur

Die Struktur folgt dem Vorschlag der CLAUDE.md des Pakets. Ein Python-Monorepo, mehrere Pakete, ein Schema-Strang.

| Pfad | Inhalt | S0 |
| --- | --- | --- |
| `apps/api/campus_api/` | FastAPI: Anmeldung, Sitzungen, CSRF, Commands, Fehlerobjekt, Health | ja |
| `apps/web/` | React + TypeScript nach UI-Handoff (Modernist) | **nein**, siehe Lücke L1 |
| `packages/domain/campus_domain/` | Fachregeln ohne Framework: Statusachsen, Geld, Routing-Prüfung, Startwächter, Rollen | ja |
| `packages/contracts/campus_contracts/` | Die sechs JSON-Schemas des Pakets (unverändert kopiert) + typisierte Modelle + Kompatibilitätstests | ja |
| `packages/infrastructure/campus_infra/` | SQLAlchemy-Modelle, Audit/Outbox, Budget-Reservierung mit Sperren, Blob-Store-Port | ja |
| `services/llm_gateway/campus_gateway/` | Gateway-Vertrag, Aufrufjournal, **nur Mock-Adapter** | ja (Mock) |
| `services/retrieval/`, `services/sandbox_controller/`, `workers/research/` | Internetdienst, Sandbox, Temporal-Worker | nein (S2/S4) |
| `migrations/` | Alembic, eine kontrollierte Schema-Kette | ja |
| `config/` | Vorlagen aus dem Paket (Provider deaktiviert), keine Geheimnisse | ja |
| `skills/` | deklarative Pakete, keine Kern-Importe | nein (S3) |
| `tests/unit`, `tests/integration`, `tests/acceptance`, `tests/adapter_contracts`, `tests/security` | Testebenen nach Kap. 22 | ja (teilweise) |
| `docs/adr/` | Umsetzungsentscheide ADR-0001 ff. (die Architektur-ADR-01…06 bleiben im Paket) | ja |

Die Basisdokumente bleiben unter `00_Basisdokumente/` und werden nicht verändert (Hash-Manifest).

## 2 Die kleinste vollständige S0-Scheibe

S0 laut Paket: «domain contracts, migrations, local real authentication, authorization, policy, audit/outbox, budget and mock adapters». Abnahme laut Architektur Kap. 20: «Keine LLMs für fachliche Grundtests nötig. Fehlende Freigabe, Rechte und Budget werden reproduzierbar abgefangen.»

Die Scheibe ist so geschnitten, dass **ein** Weg durchgehend prüfbar ist: *Mandat anlegen → menschlich freigeben → Budgetbaum → Task → Routenvorschlag durch den Kontrollkern prüfen → Lauf starten (Mock) → alles im Audit.* Jeder Wächter auf diesem Weg hat einen Negativtest.

### 2.1 Enthalten

| Baustein | Umfang in S0 | Verfassung / Architektur | Tests |
| --- | --- | --- | --- |
| Identität | Lokaler Admin mit Argon2-Hash, serverseitige Sitzung (Cookie HttpOnly/SameSite), CSRF-Token, kein Standardpasswort | Kap. 15, 19 | integration |
| Berechtigung | Rollen `research_director`, `integrity_officer`, `operator`, `reviewer`, `producer`; Grants je Workspace; Betrieb ≠ fachliche Freigabe | Art. 6–7, Kap. 15 | unit, integration |
| Mandat | `MandateVersion` (append-only), `POST /mandates`, `POST /mandates/{id}/approve` mit `HumanDecision` (Alternativen, Gründe, Einwände, Folgen, gebundene Fassung), keine Zustimmung durch Vorbelegung | Art. 9, 11 | T02 |
| Policy | `PolicyVersion` aus `routing_policy.example.yaml`, Entwurf → aktiv; `null`-Limits blockieren jeden echten Lauf | Kap. 9–10, config/README | integration |
| Budget | Budgetbaum in Ganzzahl-Minor-Einheiten + Währung; atomare Reservierung mit Zeilensperren in fester Reihenfolge; Kindbudget nie grösser als frei; Reserve/Aufruf/Abrechnung getrennt; Kosten «offen» statt 0 | Art. 10, Kap. 16 | T03, A04, A05, A14 |
| Modellkatalog | `ModelCatalogRevision` aus Katalog-Dokument (Schema geprüft), Entwurf → aktivieren getrennt; kanonische Identität | Kap. 8 | A01 (statisch), A03 |
| Routing-Kontrollkern | Harte Filter → Kandidatenbewertung mit Ausschlussgründen; Prüfung eines Routenvorschlags (Belege existieren, Kandidat zulässig, Prüfer kanonisch verschieden und nicht Mitwirkender); `RoutingDecision` nur durch den Kern; unbekannte Fähigkeit = Ausschluss | Kap. 9–11, I04 | A02, A03, A08 |
| Register (minimal) | `AgentProfileVersion` und `SkillVersion` als Kandidaten aus den Schemas; Status aktiv/ruhend getrennt von Qualifikation | Art. 25–26 | T11, A09 |
| Lauf-Wächter | `POST /runs` prüft: Mandat freigegeben, Policy aktiv mit Limits, Agent registriert, Route autorisiert, Reservierung inkl. Prüfreserve; sonst Fehlerobjekt mit `blocked_action` + `required_resolution` | Art. 12, Kap. 16–17 | T02, T11, A04 |
| Gateway + Mock | Vertrag `LLMRequest`/`LLMResponse`, Aufrufjournal mit idempotenter Kennung und Zustand `outcome_unknown`; Mock-Adapter; andere Adapter sind nicht freigegeben und werfen einen Fehler statt zu simulieren | Kap. 11, 16, I01, I08 | adapter_contracts, A06 (Journal) |
| Audit/Outbox | Jedes Command schreibt `AuditEvent` + `OutboxEvent` in **einer** Transaktion; Audit ist auf Datenbankebene unveränderlich (Trigger) und die Anwendungsrolle hat kein UPDATE/DELETE | Kap. 15–16 | integration |
| API-Verträge | `/api/v1`, OpenAPI; schreibende Aufrufe mit `Idempotency-Key` und erwarteter Objektversion; einheitliches Fehlerobjekt `code, message, trace_id, blocked_action, retryable, required_resolution, policy_version` | Kap. 17 | integration |
| Ereignisstrom | `GET /projects/{id}/events` als Polling über die Outbox | Kap. 17 | integration |
| Betrieb | `/health` (Prozess) und `/ready` (DB, aktive Policy, aktiver Katalog); Docker Compose mit PostgreSQL 16; Leistungsebene `development_mock` sichtbar in jeder Antwort | Kap. 13, 19 | – |

### 2.2 Bewusst nicht in S0

- Echte Provider-Adapter, Selector-Pool, Modell-Gegenprüfung mit zwei echten Modellen (S1).
- Temporal, Quellenabruf, Aussagen/Evidenz, Dossier (S2).
- Fabrik, Qualifikationsläufe, automatische Aktivierung (S3). Sandbox (S4).
- Weboberfläche (siehe L1).

### 2.3 Abnahmekriterien S0

1. `alembic upgrade head` auf leerer PostgreSQL-Datenbank läuft durch; `downgrade` ebenso.
2. Ohne freigegebenes Mandat startet kein Lauf (T02); ohne registrierten Agenten ebenso (T11); beides als Fehlerobjekt, nicht als Ausnahme.
3. Kindbudget über dem freien Elternbetrag wird abgewiesen (T03); zwei parallele Reservierungen auf den letzten Betrag ergeben genau eine Zusage (A05).
4. Ein Routenvorschlag auf ein nicht zulässiges Modell wird abgewiesen, die Entscheidnotiz bleibt gespeichert (A02); zwei Aliase desselben kanonischen Modells gelten nicht als Prüfvielfalt (A03); Erzeugung ohne Prüfreserve startet nicht (A04).
5. Preis unbekannt → keine Reservierung mit 0, sondern `PRICING_UNKNOWN` (A14).
6. `UPDATE`/`DELETE` auf `audit_events` scheitert in der Datenbank.
7. Kein Modell- oder Anbietername in `packages/domain` und `apps/api` (statischer Test, I01).
8. Alle Tests laufen gegen echtes PostgreSQL (kein SQLite-Ersatz) lokal und in Jenkins.

## 3 Entscheide und Abweichungen

Siehe `docs/adr/`. Kurzfassung:

- **ADR-0001** Python-Monorepo mit `pythonpath`-Paketen statt mehrerer installierbarer Distributionen (weniger Reibung in S0; Umstellung später möglich).
- **ADR-0002** Serverseitige Sitzungen mit Argon2 statt JWT (Widerruf, kein Signaturgeheimnis im Browser).
- **ADR-0003** Geld als Ganzzahl in Minor-Einheiten plus Währungscode; Umrechnungskurs wird protokolliert, nicht angenommen.
- **ADR-0004** Audit-Unveränderlichkeit doppelt: Datenbank-Trigger **und** Anwendungsrolle ohne UPDATE/DELETE.
- **ADR-0005** Idempotenz über gespeicherte Antwort je `(Akteur, Command, Key)`; abweichender Inhalt unter gleichem Key → `IDEMPOTENCY_MISMATCH`.
- **ADR-0006** Versionierte Fachobjekte append-only auf Anwendungsebene; «aktuell» ist eine Sicht auf die höchste Fassung.

## 4 Bekannte Lücken

- **L1 Weboberfläche.** Der UI-Handoff sieht Token-CSS, App-Shell und Campusübersicht gegen eine Mock-API bereits in S0 vor. Auf dem Entwicklungsrechner ist Node 16 mit defektem npm installiert; React/TypeScript-Werkzeuge brauchen Node 20+. Die Oberfläche beginnt, sobald Node aktualisiert ist; der S0-Kern ist davon unabhängig.
- **L2 Zielhost.** `university.kaspair.ch` zeigt auf einen Managed-Host ohne Docker. PostgreSQL, Temporal und Sandbox verlangen einen anderen Host. Deploy-Stage bleibt deaktiviert.
- **L3 Externe Rollen.** Integritätsverantwortliche Person und Beschwerdestelle sind nicht besetzt; S0 kann die Rollen anlegen, aber keine Unabhängigkeit behaupten (Art. 7).
- **L4 Pinning.** Abhängigkeiten werden erst nach erfolgreichem Testlauf eingefroren (`requirements.txt` aus `pip freeze`), nicht vorab behauptet.

## 5 Stand der Umsetzung (11.10.2026, Abend)

Die Scheibe aus Abschnitt 2 ist gebaut. Nachweise:

| Abnahmekriterium (2.3) | Nachweis |
| --- | --- |
| 1 Migration auf/ab/auf | `alembic upgrade head` → `downgrade base` → `upgrade head` auf leerer Datenbank durchgelaufen; Anwendungsrolle hat auf `audit_events` nur `SELECT`, `INSERT` |
| 2 T02, T11 als Fehlerobjekt | `tests/unit/test_guards_mandate_authz.py`, `tests/integration/test_s0_flow.py::test_t02_*`, `::test_t11_*` |
| 3 T03, A05 | `::test_t03_*`, `::test_a05_parallel_reservations_do_not_overallocate` (zwei Threads, genau eine Zusage) |
| 4 A02, A03, A04 | `tests/unit/test_routing.py`, `::test_a02_rejected_proposal_keeps_its_note`, `::test_a04_missing_review_reserve_blocks_before_generation` |
| 5 A14 | `tests/unit/test_budget_money.py::test_a14_*`, `::test_a14_unknown_price_blocks_the_route` |
| 6 Audit unveränderlich | `::test_audit_events_are_immutable_in_the_database` (Trigger weist `UPDATE` und `DELETE` ab) |
| 7 I01 statisch | `tests/security/test_static_boundaries.py` |
| 8 Echtes PostgreSQL | Integrationstests gegen PostgreSQL 16 lokal; Jenkins mit Sidecar-Container (`Jenkinsfile`) |

Testlauf: 75 Tests grün (Unit, Integration, Adaptervertrag, Grenzen), Linter ohne Befund. Die API wurde lokal
gestartet: Health, Readiness, Anmeldung, Fehlerobjekt und OpenAPI (34 Pfade) geprüft.

Zusätzlich umgesetzt, über 2.1 hinaus: Aufrufjournal mit `outcome_unknown` (A06), Stopp/Wiederaufnahme mit
Begründungspflicht (Art. 12), Registerauskunft ohne LLM (A09), Selbstzertifikat abgewiesen (T12, nur die
Registrierungsseite), aggregierte offene Entscheide (`GET /decisions`) für die spätere Campusübersicht.

Nicht behauptet: T01, T04–T10, T13–T17 und die übrigen A-Fälle sind nicht implementiert und gelten als **nicht
bestanden**, bis die betreffende Stufe sie liefert.

## 6 Nächster Entscheid (nach S0)

Für S1 braucht es **zwei reale, freigegebene Modellzugänge verschiedener Anbieter** mit Datenfreigabe, Preisfassung und Qualifikationsnachweis (Paket: «two real approved models are a gate, not merely two mock labels»). Das ist ein Betriebsentscheid der Forschungsleitung, kein Entwicklungsschritt.
