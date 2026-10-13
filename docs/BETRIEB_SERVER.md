# Betrieb der Pilotumgebung auf university.kaspair.ch (Userland)

Stand: 12.10.2026 · Entscheid: ADR-0015 · Host: Infomaniak Managed, `u7031y_kaspar@83.228.238.194`, geteilt mit den Schwesterprodukten.
**Pilotumgebung, nicht produktionsreif** (Temporal im Entwicklungsmodus, keine Sandbox, selbst betriebene Datenbank, Leistungsebene
`development_mock`). `GET /api/v1/health` nennt die Einschränkungen unter `pilot_limitations`.

## Layout auf dem Host (alles unterhalb von `$HOME`, nichts ausserhalb)

| Pfad | Inhalt |
| --- | --- |
| `~/campus` | Repository (Branch `main`, Checkout eines exakten Commits), `.venv` (Python 3.12) |
| `~/campus-data/pg` | PostgreSQL 16 Datenverzeichnis (0700); Socket in `~/campus-data/pg-run`, **kein TCP** |
| `~/campus-data/temporal/temporal.db` | Temporal Dev-Server, SQLite-Persistenz, nur 127.0.0.1:7260, ohne Web-UI |
| `~/campus-data/blobs` | Artefakt-Blobs (hashadressiert) |
| `~/campus-data/logs` | `postgres.log`, `temporal.log`, `api.log`, `worker.log`, `campusctl.log`, `keepalive.log`, `backup.log`, `restore_check.log`, `deploy.log` |
| `~/campus-data/backups` | tägliche Sicherungen, 14 Tage |
| `~/campus-data/secrets/campus.env` | Umgebung und Geheimnisse (0600); nie kopieren, nie ausgeben |
| `~/campus-data/run` | PID-Dateien, `deployed.sha`, `keepalive.lock` |
| `~/.campus-tools` | `uv`, CPython 3.12, Temporal-Binärdatei |
| `~/sites/university.kaspair.ch` | `index.php` (Proxy zu 127.0.0.1:8060), `.htaccess` |

Ports des Kontos: API 127.0.0.1:8060, Temporal 127.0.0.1:7260. PostgreSQL ohne Port.

## Skripte (`ops/server/`)

| Skript | Zweck |
| --- | --- |
| `install.sh` | Einmalige, idempotente Installation: Vorprüfung (RAM ≥ 2 GB frei, Prozesslimit, Ports frei), Werkzeuge, Repository, venv, PostgreSQL-Initialisierung mit Rollen, Schema. Schreibt **keine** Crontab und **nicht** ins Web-Root. |
| `campusctl.sh {start|stop|restart|status|health} [pg|temporal|api|worker]` | Steuerung und Gesundheit je Komponente (pg_isready, Temporal `cluster health`, `/api/v1/ready`, Worker-PID). |
| `keepalive.sh` | Cron alle 3 Minuten und `@reboot`: `flock`, Gesundheitsprüfung, startet nur Ungesundes neu, protokolliert, begrenzt sein Log. |
| `backup.sh` | Täglich: `pg_dump -Fc`, SQLite-Online-Backup von Temporal, Blobs als tar; 14 Tage Aufbewahrung; `backup.log`. |
| `restore_check.sh` | Täglich nach dem Backup: jüngsten Dump in `campus_restorecheck` einspielen, Migrationskopf und Tabellenzahl prüfen, Prüfdatenbank löschen; `restore_check.log` mit OK/FAIL. |
| `deploy.sh <sha>` | Aus Jenkins per SSH: Lock gegen Keepalive, Backup, Checkout, Abhängigkeiten, `alembic upgrade head`, Neustart API+Worker, Gesundheit mit Build-Hash; Rollback des Codes bei Fehlern. |
| `bootstrap_campus.py` | Einmalig über die lokale API: Pilot-Policy (Limits sichtbar im Skript), synthetischer Preis, Datenfreigaben der Mock-Anbieter, Fixture-Katalog (Mock), ein Mandatsentwurf, der auf den Entscheid in der Oberfläche wartet. |
| `proxy/index.php`, `proxy/htaccess` | PHP-Proxy und Rewrite für die Site. |
| `crontab_install.sh` | Idempotente Ergänzung der geteilten Crontab mit Sicherung und Nachkontrolle (`--remove` entfernt die Campus-Zeilen). |
| `synthetic_task.py` | Nach der Mandatsfreigabe in der Oberfläche: ein synthetischer Auftrag über den echten Kontrollweg (Projekt, Aufgabe, Route, Lauf, Gegenprüfung, Dossier), alles als synthetisch gekennzeichnet. |
| `live_setup.py {status|discover|prepare|activate}` | Echte Anbieter in Betrieb nehmen (ADR-0016): Discovery über das Vorlagen-Deployment, Preisfassungen, Datenfreigaben, Katalog- und Policy-Entwurf, Aktivierung erst nach bestätigten Proben; schreibt nichts ohne `--yes`. |
| `campus-admin probe-invocation` (`python -m campus_api.cli`) | Betriebsprobe: EIN kleiner echter Aufruf gegen ein Deployment des Katalog-Entwurfs mit Journal, Nutzungswerten, effektiver Modellkennung, strukturierter Ausgabe; Kostenobergrenze Pflicht. |
| `live_order.py {mandate|register}` | TEST-02, der erste Live-Auftrag: Mandatsentwurf mit kleinem Budget; nach dem Entscheid in der Oberfläche Bedarf, Skill- und Profil-Kandidat für die Analyse öffentlicher Quellen. Projekt, Aufgabe, Ablauf und Beurteilung im Browser. |

## Erstinstallation (Reihenfolge)

1. Commit mit `ops/server/` auf `main` pushen (Installation klont `main`).
2. Auf dem Host: `bash <(curl …)` **nicht** verwenden; stattdessen `git clone` und `bash ~/campus/ops/server/install.sh`.
3. `bash ~/campus/ops/server/campusctl.sh start` und `… status`.
4. Workspace und Konto: `cd ~/campus && set -a && . ~/campus-data/secrets/campus.env && set +a && .venv/bin/python -m campus_api.cli init-workspace --id ws-campus --name "Phronesis-Forschungscampus"`; Konto mit `CAMPUS_ADMIN_PASSWORD` aus einer 0600-Datei anlegen (`create-user … --roles research_director,operator`).
5. `.venv/bin/python ops/server/bootstrap_campus.py --workspace ws-campus --username <user> --password-file <datei>`.
6. Web-Root: `proxy/index.php` und `proxy/htaccess` nach `~/sites/university.kaspair.ch/` (Platzhalter-`index.html` entfernen).
7. Crontab ergänzen mit `bash ops/server/crontab_install.sh`: sichert die bestehende Crontab nach `backups/`, ergänzt nur Zeilen mit dem
   Marker `# campus` (Keepalive alle 3 Minuten und bei Neustart; Backup 02:30 und **nur bei dessen Erfolg** die Wiederherstellungsprüfung in
   derselben Zeile), ist idempotent und prüft danach, dass jede fremde Zeile unverändert vorhanden ist; sonst stellt es die Sicherung wieder her.
   Keepalive, Backup, Wiederherstellungsprüfung und Deploy teilen sich eine `flock`-Sperre und überlappen nie.
   Gemessen am 12.10.2026: Backup < 1 s (156 KB Dump), Wiederherstellungsprüfung 2 s in der isolierten Prüfdatenbank `campus_restorecheck`,
   die danach gelöscht ist; der tägliche Lauf um 02:30 ist damit angemessen.
8. Jenkins: Credential `university-deploy` (SSH Username with private key, Benutzer `u7031y_kaspar`, Schlüssel `~/.ssh/university_deploy` der Entwicklungsmaschine); Job `university main` deployt, die anderen Jobs nicht.

## Was der PHP-Proxy tut und was geprüft ist

Leitet jede Methode mit Body weiter, reicht alle Anfrage-Header ausser `Host` durch (Cookie, `X-CSRF-Token`, `Idempotency-Key`), gibt
Antwort-Header per Allowlist zurück (`Content-Type`, `Set-Cookie` mehrfach, `Location`, `X-Trace-Id`, `X-Campus-Capability-Level`, Cache-
Header), folgt Weiterleitungen nicht, setzt `X-Forwarded-Proto: https`. Die Sitzung bleibt ein `HttpOnly`-, `Secure`-, `SameSite=strict`-Cookie;
CSRF und Rollen prüft weiterhin die API. Bei nicht erreichbarer API antwortet der Proxy mit 502 und einem Fehlerobjekt. Der Nachweis ist der
End-to-End-Test über HTTPS (Anmeldung, Seiten, ein Command mit CSRF und Idempotency-Key).

## Stand 12.10.2026 (Erstinstallation)

Installiert und über `https://university.kaspair.ch` geprüft: Anmeldung, alle sieben Ansichten, CSRF-Durchsetzung (403 ohne Token), ein Command
mit Token und Idempotency-Key über den Proxy, Abmeldung, Schutz ohne Sitzung. Dienste binden nur an 127.0.0.1, PostgreSQL ohne TCP.
Workspace `ws-campus`, Konto der Forschungsleitung, Pilot-Policy `pilot-policy-1`, Mock-Katalog `pilot-mock-1`, ein Mandatsentwurf.
Crontab ergänzt, Backup und Wiederherstellungsprüfung manuell bestanden. Erster Deploy mit `deploy.sh` von Hand auf Build `f5a9953`
(Backup vor Deploy, Checkout, Abhängigkeiten, Migrationen, Neustart, Gesundheit mit Build-Hash über HTTPS bestätigt).

Zwei Mängel, die der erste Deploy-Test aufdeckte und die behoben sind: (1) `install.sh` hatte Ausführungsrechte gesetzt, die git als Änderung
zählte und den Checkout blockierte; jetzt sind die Rechte im Repository gespeichert und der Server-Klon ignoriert Dateimodi. (2) Gestartete
Dienste erbten den Sperr-Deskriptor von Keepalive/Deploy und hielten die Sperre dauerhaft; jetzt schliesst der Start jeden Kindprozess davon
aus, und `deploy.sh` meldet nur Erfolg, wenn HEAD, Build-Hash der laufenden API und Worker übereinstimmen.

## Echte Anbieter in Betrieb nehmen (Stand 13.10.2026, ADR-0016)

Ziel: zwei echte Modellzugänge (Anthropic, OpenAI) über das bestehende Gateway und den konfigurierbaren Katalog, ohne Modellnamen im
Fachcode, ohne stillen Rückfall auf Mock-Ausgaben, mit Journal für jeden Aufruf. Reihenfolge einhalten; jeder Schritt sagt, was er tut.

### 1 Zugangsdaten hinterlegen (Host, einmalig)

Die Schlüssel stehen nur in `~/campus-data/secrets/campus.env` (0600) und werden vom API- und Worker-Prozess als Umgebung gelesen. Der Katalog
trägt nur die Referenz `secret://providers/<name>` → Variable `CAMPUS_SECRET_PROVIDERS_<NAME>`. Eingabe mit `read -s`, damit der Schlüssel weder
in der Shell-Historie noch in einem Log landet; nie als Argument, nie in Chat, Repository oder Browser.

```bash
umask 077
read -rsp 'Anthropic-Schlüssel: ' K && printf 'CAMPUS_SECRET_PROVIDERS_ANTHROPIC=%s\n' "$K" >> ~/campus-data/secrets/campus.env; unset K; echo
read -rsp 'OpenAI-Schlüssel: ' K && printf 'CAMPUS_SECRET_PROVIDERS_OPENAI=%s\n' "$K" >> ~/campus-data/secrets/campus.env; unset K; echo
sed -i 's/^CAMPUS_APPROVED_ADAPTERS=.*/CAMPUS_APPROVED_ADAPTERS=anthropic,openai/' ~/campus-data/secrets/campus.env
grep -c '^CAMPUS_SECRET_PROVIDERS_' ~/campus-data/secrets/campus.env   # erwartet 2; Werte nie ausgeben
bash ~/campus/ops/server/campusctl.sh restart api worker
curl -s https://university.kaspair.ch/api/v1/health   # approved_adapters: ["anthropic","openai"]
```

`mock` fehlt bewusst in `CAMPUS_APPROVED_ADAPTERS`: ein Deployment mit Mock-Adapter wird ab jetzt nicht mehr aufgerufen und nicht simuliert
(`ADAPTER_NOT_APPROVED`). TEST-01 und seine Fassungen bleiben unverändert gespeichert; bis zur Aktivierung des Live-Katalogs ist der Mock-Katalog
noch aktiv, also in dieser Zeit keinen Ablauf starten.

### 2 Discovery, Vorschlag, Freigaben, Proben, Aktivierung

Alle Schritte als Konto der Forschungsleitung mit Betriebsrolle über HTTPS (Secure-Cookie): `BASE=https://university.kaspair.ch`,
`AUTH="--workspace ws-campus --username <user> --password-file ~/campus-data/secrets/<passwortdatei> --base $BASE"`.

| Schritt | Befehl | Wer gibt frei | Schreibt |
| --- | --- | --- | --- |
| Discovery | `.venv/bin/python ops/server/live_setup.py $AUTH discover` | Betrieb (`catalog.discover`) | Vorlagenkatalog `catalog-template-1` als Entwurf (einmalig); listet native Modellkennungen |
| Vorbereiten | `… live_setup.py $AUTH prepare --anthropic-model <id> --openai-model <id> --anthropic-usd IN OUT --openai-usd IN OUT --anthropic-tokens CTX OUT --openai-tokens CTX OUT --price-source "<URLs, Datum>" --budget-chf 5.00 --yes` | Preis: Betrieb · Datenfreigabe `profile-<anbieter>-live`: **Forschungsleitung (Art. 11)** · Katalog-Entwurf: Betrieb · Policy-Entwurf: Betrieb/Leitung | Preisfassungen (CHF, aufgerundet, Quelle und Kurs im `source_note`), Datenfreigaben (nur `public`), Katalog-Entwurf `pilot-live-1` (runtime_snapshot, zwei echte Deployments, Fähigkeiten nur text_input und structured_output als supported), Policy-Entwurf `pilot-policy-2` (Selector-Pool = beide Deployments, Limits = Obergrenze) |
| Probe je Deployment | `set -a; . ~/campus-data/secrets/campus.env; set +a; cd ~/campus && .venv/bin/python -m campus_api.cli probe-invocation --workspace ws-campus --deployment-id <id> --max-cost 0.05` | Betrieb (`catalog.probe`); kostet Geld | Journaleintrag `…:probe:1` (Marker `RD-PROBE-`), Audit `ProbeStarted`/`ProbeCompleted`; Ausgabe: effektive Modellkennung, Tokens, Kosten, `structured_output_valid`, `real_provider_call` |
| Aktivieren | `… live_setup.py $AUTH activate --probed <dep-1> --probed <dep-2> --yes` | Katalog: Betrieb · Policy: **Forschungsleitung** | `pilot-live-1` aktiv (Mock-Katalog zurückgezogen), `pilot-policy-2` aktiv |

Die Probe belegt Zugang, Journal, Nutzungswerte und das technisch erzwungene Ausgabeformat gegen den Vertrag `route_proposal`. Sie ist kein
Qualifikationsnachweis; es wird keiner eingetragen. Erzeugung und Gegenprüfung mit zwei verschiedenen Modellen prüft erst der echte Ablauf
von TEST-02: der Selector (erstes zulässiges Deployment des Pools) schlägt vor, der Kern autorisiert, der Prüfer ist kanonisch verschieden
und nach Policy von einem anderen Anbieter.

**Modellvorschlag (offizielle Seiten, gelesen 13.10.2026; Kennungen gelten erst, wenn die Discovery sie meldet):**

| Anbieter | Vorschlag | Listenpreis USD je Mio. Token (ein / aus) | Kontext / Ausgabe | Günstigere Alternative |
| --- | --- | --- | --- | --- |
| Anthropic | `claude-sonnet-5-5` | 2 / 10 (platform.claude.com/docs/en/about-claude/pricing) | 1 000 000 / 128 000 | `claude-haiku-5-5` ab 0.10 / 0.50 |
| OpenAI | `gpt-6.1-sol` | 2 / 10 (developers.openai.com/api/docs/pricing) | 1 050 000 / 128 000 | `gpt-5.4-mini` 0.75 / 4.50; `gpt-6-luna` 0.10 / 0.50 |

Beide Seiten führen strukturierte Ausgabe als unterstützt. Umrechnung im Pilot 1 USD = 1.00 CHF (konservativ); Kosten eines TEST-02-Laufs
in der Grössenordnung von zehn Rappen (Selector, Erzeugung, Gegenprüfung), eine Probe unter einem Rappen; abgerechnet wird der gemeldete Verbrauch.

### 3 TEST-02: erster Live-Auftrag (TEST-01 bleibt synthetisch und unverändert)

1. `.venv/bin/python ops/server/live_order.py $AUTH mandate` legt den Mandatsentwurf mit Budget 3.00 CHF an; die Frage nennt die bereits
   gespeicherte öffentliche Quelle (Bibliothek). Entscheid der Forschungsleitung im Browser (Art. 11).
2. `… live_order.py $AUTH register` erfasst den Bedarf und registriert Skill `public-source-analysis@1` (hashgebundene Anweisungen) und Profil
   `source-analyst-public@1` als **Kandidaten** (nicht qualifiziert → Modus `sandbox_experiment`, Art. 26). Keine Nachweise werden erfunden.
3. Im Browser (`/campus/auftrag/<Mandatsfassung>`): Projekt und Aufgabe anlegen (Profil wählen; die Seite zeigt die Modelle mit «echt · kostet
   Geld» und die Reservierungen), Ablauf starten mit der Quell-URL, Stand verfolgen, Dossier lesen (Aussagen mit `passage_id`-Belegen,
   Prüfbericht des anderen Modells), Ergebnisfreigabe entscheiden. Betrieb & Kosten zeigt jeden Aufruf im Journal.

### 4 Was bleibt

Leistungsebene `development_mock` (Egress-Proxy fehlt, keine Sandbox); Temporal im Entwicklungsmodus; keine Qualifikationsnachweise für die
echten Modelle (Modus `qualified` braucht Prüfsätze und eine menschliche Bootstrap-Prüferfreigabe); Preise sind Listenpreise mit Datum, keine
Vertragspreise; Rechnungen der Anbieter laufen in USD.

## Prüfen im Betrieb

```bash
bash ~/campus/ops/server/campusctl.sh status
tail -n 20 ~/campus-data/logs/keepalive.log ~/campus-data/logs/restore_check.log
curl -s https://university.kaspair.ch/api/v1/health
```

## Wiederherstellung von Hand

`campusctl.sh stop api worker` → `pg_restore -h ~/campus-data/pg-run -U campus_owner -d campus --clean --if-exists <dump>` (Binärdateien unter
`.venv/.../pgserver/pginstall/bin`) → `campusctl.sh start`. Temporal: Datei `temporal.db` bei gestopptem Temporal ersetzen. Blobs: tar entpacken.

## Grenzen (bleiben dokumentiert)

Dev-Server-Temporal; Datenbank ohne Hoster-SLA; Prozesslimit 480 je Konto (Campus braucht etwa 15); keine Sandbox; `development_mock`
bleibt auch mit echten Modellzugängen, bis der Egress-Proxy steht; Rollback deckt den Code, nicht das Schema.
