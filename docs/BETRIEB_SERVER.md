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

Dev-Server-Temporal; Datenbank ohne Hoster-SLA; Prozesslimit 480 je Konto (Campus braucht etwa 15); keine Sandbox; `development_mock`,
bis Egress-Proxy und zwei echte Modellzugänge freigegeben sind; Rollback deckt den Code, nicht das Schema.
