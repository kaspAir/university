# Vorschlag: erste sichtbare Oberfläche und kleinste lauffähige Serverversion

Stand: 12.10.2026 · Zweck: den Campus erstmals im Browser sehen, zugriffsgeschützt, mit echten Daten der Instanz; danach einen ersten
Forschungsauftrag beobachten. Kein neuer Entwicklungsmeilenstein; die offenen Punkte von S4/S5 bleiben, wo sie dokumentiert sind.

## 1 Ist-Stand (geprüft am 12.10.2026)

### 1.1 Was bereits implementiert ist und eine Oberfläche sofort nutzen kann

Alle Lesesichten existieren als geschützte JSON-Endpunkte unter `/api/v1` (Anmeldung mit Argon2-Passwort, serverseitige Sitzung, CSRF, Rollen):

| Bereich (Handoff Kap. 18) | Vorhandene Endpunkte | Daten |
| --- | --- | --- |
| Campusübersicht | `/workspace`, `/institutes`, `/mandates`, `/decisions`, `/budget/tree`, `/health` | Stopp-Zustand, Leistungsebene, fünf Institute (Stammdaten), Mandate mit Zustand, vorbehaltene Entscheide, Budgetbaum |
| Dossiers | `/projects`, `/projects/{id}`, `/projects/{id}/dossier`, `/claims`, `/sources`, `/runs`, `/artifacts`, `/events`, `/tasks/{id}`, `/tasks/{id}/reviews` | Frage/Mandat, Protokoll, Aussagen mit drei Achsen, Belege, Läufe, Gegenprüfungen, Einwände, Fassungen |
| Register | `/inventory`, `/inventory/{ref}/rationale`, `/needs`, `/qualifications` | Agenten- und Skill-Fassungen, Bedarf, Qualifikationsstand, Herkunft |
| Modelle | `/model-catalog/status`, `/policies`, `/processing-profiles` | Aktiver Katalog, Policy, Datenfreigaben |
| Prüfung und Freigabe | `/decisions`, `/mandates/{id}`, `/artifacts` | Entscheide mit Alternativen, Einwänden, Folgen |
| Betrieb | `/health`, `/ready`, `/sandbox/status`, `/budget/tree`, `/pilots` | Komponenten, Sandboxpfad, Mittel, Pilotstand |

Schreibend vorhanden: Stopp/Wiederaufnahme, Mandatsentscheid (Art. 11), Projekt- und Aufgabenanlage, Route, Lauf, Prüfung, Dossier, Freigaben,
Fabrik, Experimente, Pilot. Alles mit Audit und Idempotenz.

Kennzeichnung synthetischer Daten ist im Datenmodell vorhanden: Leistungsebene in jeder Antwort (`X-Campus-Capability-Level`), `synthetic` an
Artefakten, Runner-Art an Experimenten, Provider-Kennung an jedem Aufruf. Die Oberfläche muss sie nur zeigen.

### 1.2 Was für die erste Website fehlt

1. **Es gibt keine Weboberfläche.** `apps/web/` existiert nicht. Der Handoff beschreibt React/TypeScript mit dem Design System «Modernist»
   (Tokens in `design-system/styles.css`). React braucht Node 20+. Lokal ist Node 16 mit defektem npm, auf dem Zielhost gibt es kein Node.
2. **Die Deploy-Stage bricht absichtlich ab** («Zielhost und Betriebsform offen»). Der Host ist der eigentliche Entscheid, siehe 2.
3. **Eine Betriebs-Konfiguration** (Compose oder Prozessstart, TLS, `.env` mit echten Geheimnissen, Workspace-Bootstrap mit Admin-Konto) fehlt.

### 1.3 Was erst für echten Forschungsbetrieb nötig ist (bleibt offen, nicht Teil dieser Website)

- Zwei echte freigegebene Modellzugänge (S1-Tor). Ohne sie arbeitet die Instanz mit dem Mock-Adapter; jeder Lauf ist als synthetisch gekennzeichnet.
- Egress-Proxy (schliesst DNS-Rebinding) für `restricted_research`; Sandbox-Abnahme auf dem Zielhost für Experimente.
- Betriebsentscheide Anhang F für den Pilot; Backup/Restore-Test; Monitoring; Mehrbenutzer-Identität (OIDC).
- Schreibdialoge der Oberfläche über den Mandatsentscheid und den Stopp hinaus.

## 1a Befunde zum Host (12.10.2026, von aussen erhoben)

- `university.kaspair.ch` löst auf `83.228.238.194` und `2001:1600:18:104::7d` auf: derselbe Infomaniak-Managed-Host wie `hermespia.ch`
  (Konto `u7031y`). Ausgeliefert wird die Infomaniak-Platzhalterseite («Diese Website ist zurzeit in Bearbeitung», Apache, HSTS aktiv).
- SSH-Zugang von der Entwicklungsumgebung besteht nicht (nur Passwort/Schlüssel des Kontos; der HERMES-PIA-Deploy-Schlüssel liegt im Jenkins-
  Credential-Store, nicht als Datei). Ein eigener Deploy-Schlüssel für den Campus ist erzeugt (`~/.ssh/university_deploy`, ed25519); sein
  öffentlicher Teil ist im Infomaniak-Manager beim SSH-Benutzer zu hinterlegen. Danach lässt sich der Host mit `ops/host_check.sh` prüfen.
- Bis dahin gelten die dokumentierten Fakten des Hosts (kein Docker, kein Root, Python 3.9.2, kein PostgreSQL bekannt). Sie sind zu
  verifizieren, nicht anzunehmen.

## 2 Der Host: eine Entscheidung, zwei Wege

Der Code verlangt **Python 3.12, PostgreSQL 16** und optional **Temporal**. Der vorbereitete Host `university.kaspair.ch` (Infomaniak Managed,
83.228.238.194, derselbe wie hermespia.ch) bietet nach bisherigem Stand: kein Docker, kein Root, Python 3.9.2, kein PostgreSQL bekannt (die
Schwesterprodukte nutzen SQLite und MariaDB), kein Temporal möglich. SSH von diesem Rechner aus war heute nicht möglich (nur Schlüssel im
Jenkins-Volume), die Angaben stammen aus dem dokumentierten Stand.

| | Weg A: bestehender Managed-Host | Weg B: kleiner Linux-Server mit Docker (empfohlen) |
| --- | --- | --- |
| Python 3.12 | Nur, wenn ein Userland-Python ohne Root installierbar ist (z. B. `uv python install 3.12`). **Zu verifizieren.** | Container, gegeben |
| PostgreSQL | **Offen.** Entweder bietet Infomaniak eine PostgreSQL-Instanz zum Hosting, oder es braucht eine externe verwaltete Datenbank (Datenresidenz CH beachten). SQLite ist ausgeschlossen (Paket CLAUDE.md). | Compose-Dienst `db`, wie lokal |
| Temporal | Nicht möglich. Dauerhafter Ablauf (`/tasks/{id}/workflow`) meldet `WORKFLOW_SERVICE_UNAVAILABLE`; Läufe gehen synchron über `/runs` (für das Beobachten eines ersten Auftrags ausreichend). | Compose-Dienst `temporal`, wie lokal |
| Prozess und TLS | Gunicorn hinter PHP-Proxy (Muster hermespia.ch), Neustart per Cron | Compose: `api`, `worker`, `caddy` (TLS automatisch) |
| Deploy aus Jenkins | SSH, `git pull`, `pip install`, Neustart (Muster vorhanden) | SSH, `docker compose pull/up` (Images aus dem CI-Build) |
| Sandbox (S4) | nicht möglich | möglich, Abnahme auf dem Host |
| Aufwand bis sichtbar | zwei Verifikationen vorab, dann ein halber Tag | Server bereitstellen (durch dich), dann ein halber Tag |
| Risiko | PostgreSQL ungelöst; Python-Workaround ausserhalb des Supports | neuer Host, laufende Kosten |

**Empfehlung: Weg B.** Ein kleiner Server (2 vCPU, 4 GB, Schweiz) mit Docker trägt die Architektur ohne Kompromiss, inklusive Temporal und
späterer Sandbox-Abnahme. Weg A ist möglich, wenn PostgreSQL verfügbar ist und das Userland-Python funktioniert; beides wäre zuerst auf dem
Host zu prüfen, nicht anzunehmen. `university.kaspair.ch` kann in beiden Fällen die Adresse sein (DNS auf den neuen Host umstellen).

## 3 Die kleinste Oberfläche: serverseitig, ohne Node

Vorschlag: Die ersten Ansichten werden **im bestehenden FastAPI-Prozess** gerendert (Jinja2-Templates, das Token-CSS des Design Systems
unverändert übernommen, kein Build-Schritt, kein Node). Sie rufen dieselben Dienste wie die JSON-Endpunkte und laufen hinter derselben
Sitzung. Das verletzt die Architektur nicht: React/TypeScript bleibt die Zielvorgabe für die vollständige Oberfläche; die Fachregeln hängen
nicht vom Frontend ab (Kap. 18 «der Fachkern hängt nicht davon ab»). Wenn Node 20 verfügbar ist, ersetzt die React-Fassung die Templates
Ansicht für Ansicht, bei gleichen Endpunkten.

Ansichten der ersten Fassung (Handoff-Reihenfolge, nur Lesen plus zwei Handlungen):

| Ansicht | Zeigt | Handlung |
| --- | --- | --- |
| Anmeldung | Benutzername, Passwort | Anmelden, Abmelden |
| Campus | Begrüssungssatz mit Anzahl offener Entscheide, Leistungsebene **immer sichtbar**, Stopp-Zustand, fünf Institute mit Zahl der Projekte, Mittel (Budgetbaum), Zähler je Bereich | **Stopp / Wiederaufnahme** (Leitung, Integrität) |
| Dossiers | Projekte mit Institut, Mandat, Arbeitsstand, Anzahl Aussagen und offener Einwände | – |
| Dossier | Frage und Mandat, Protokoll (Aufgaben), Aussagen mit drei Achsen und Flags, Belege, Läufe mit Modellroute, Gegenprüfungen, Einwände, Fassungen und Freigaben | – |
| Register | Agenten- und Skill-Fassungen mit Betriebs- und Qualifikationsstand; Herkunftsansicht | – |
| Prüfung und Freigabe | Offene Mandatsentwürfe mit Alternativen, Einwänden, Folgen | **Mandatsentscheid** (vier Optionen, Art. 11) |
| Betrieb | Komponenten (`/ready`), Katalog, Sandboxpfad, Pilotstand | – |

Regeln, die die Oberfläche durchsetzt (aus Architektur Kap. 18/20): Leistungsebene und Datenstand in jeder Ansicht; Läufe über den
Mock-Adapter tragen sichtbar «synthetisch»; Artefakte mit `synthetic` ebenso; keine Bestandsaussage ohne Verweis auf den Eintrag; Tabellen
funktionieren ohne Sprachmodell (A09). Es werden keine Fixture-Daten eingespielt: die Serverinstanz startet mit Workspace, Instituten und
deinem Konto, sonst leer. Was du siehst, ist, was du anlegst.

## 4 Reihenfolge bis zum ersten Blick

1. **Hostentscheid** (du): Weg B, oder Weg A mit den zwei Verifikationen.
2. **Oberfläche** (ich): Templates, Token-CSS, Router `/campus/...`, Tests für Zugriffsschutz und Kennzeichnung; ~1 Tag.
3. **Betriebsform** (ich, nach 1): Weg B: `docker-compose.prod.yml` (db, temporal, api, worker, caddy), Image-Build in Jenkins, Deploy-Stage per
   SSH für `main`; Weg A: Startskript nach hermespia-Muster, Deploy-Stage per SSH; ~halber Tag.
4. **Bootstrap auf dem Server** (gemeinsam): `.env` mit echten Geheimnissen (nie im Repo), Migrationen, Workspace mit Instituten, dein Konto
   als Forschungsleitung, Policy und Katalog mit Mock-Adapter, Leistungsebene `development_mock`.
5. **Erster Blick**: Campus im Browser, leer bis auf Institute und dein Konto.
6. **Erster Auftrag beobachten**: Mandat anlegen und entscheiden (Oberfläche), Projekt und Aufgabe anlegen (API), Lauf und Gegenprüfung
   beobachten (Dossier). Mit Mock-Adapter ist er sichtbar als synthetisch gekennzeichnet; **echt** wird er erst mit zwei freigegebenen
   Modellzugängen (S1-Tor, `docs/S1_UMSETZUNG.md` Abschnitt 3). Das ist dann ein Konfigurationsschritt, kein Bauabschnitt.

## 5 Was dieser Vorschlag nicht tut

Keine neue Architekturphase, kein React-Build, keine Mock-Daten als Forschung, keine Änderung an den offenen S5-Punkten (Anhang F,
Einzelagent-Bedingung, Testkennungen), keine Behauptung von Live-Nachweisen.
