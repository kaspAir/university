# ADR-0015 Pilotumgebung im Userland des bestehenden Hosts

Stand: 12.10.2026 · Status: angenommen (Entscheid Forschungsleitung) · Bezug: Architektur Kap. 19, 24; ADR-0008 (Temporal), ADR-0012 (Sandbox); `docs/WEB_ERSTE_OBERFLAECHE_VORSCHLAG.md` 1a

## Entscheid
1. Die **erste betriebsfähige Fassung** des Campus läuft auf dem bestehenden Infomaniak-Managed-Host (`university.kaspair.ch`, derselbe
   Server wie die Schwesterprodukte) **vollständig im Userland eines Kontos**: Python 3.12 über `uv`, PostgreSQL 16 aus gebündelten
   Binärdateien (`pgserver`), Temporal als einzelne Binärdatei im Dev-Server-Modus, API und Worker als Benutzerprozesse. Kein Root, kein
   Docker, keine Änderung an Systemdiensten, keine Änderung an anderen Anwendungen.
2. **Nichts ist öffentlich** ausser Apache: PostgreSQL hört nur auf einem Unix-Socket im Home-Verzeichnis (scram-sha-256, kein TCP), Temporal
   und die API binden an 127.0.0.1. Der Eintritt ist HTTPS über Apache und den PHP-Proxy der Site (der Host hat kein mod_proxy).
3. **Kein Sandboxpfad auf diesem Host** (`CAMPUS_SANDBOX_RUNNER=none`): keine Ausführung fremden Codes; Experimente sind abgeschaltet und
   werden als abgeschaltet gemeldet.
4. **Temporal im Entwicklungsmodus** (SQLite-Persistenz, ein Prozess) ist für den Pilot zulässig und wird in `/api/v1/health` unter
   `pilot_limitations` ausgewiesen. ADR-0008 bleibt: produktiv braucht es einen persistenten Temporal-Betrieb.
5. **Betrieb als Benutzerprozesse** mit Keepalive (Cron, `flock`, Gesundheitsprüfung je Komponente, Protokoll), täglichen Backups (pg_dump,
   SQLite-Online-Backup, Blobs) und einer täglich geprüften Wiederherstellung in eine Prüfdatenbank. Die Datenbank ist selbst betrieben;
   Verfügbarkeit und Wiederherstellung liegen beim Campus-Betrieb, nicht beim Hoster.
6. **Deploy** aus Jenkins per SSH für den Job `main`: Backup vor dem Deploy, Checkout des exakten Commits, Abhängigkeiten, Migrationen,
   Neustart, Gesundheitsprüfung mit dem Build-Hash; bei Fehlern Rollback des Codes auf den vorherigen Commit. Das Schema wird nicht
   automatisch zurückgerollt (Migrationen sind vorwärtsgerichtet); der Dump vor dem Deploy steht für eine manuelle Wiederherstellung bereit.
7. **Geheimnisse** entstehen auf dem Host (`~/campus-data/secrets/campus.env`, 0600) und erscheinen weder im Repository noch in Jenkins-Logs.

## Folgen
- Diese Umgebung ist eine **Pilotumgebung**, nicht produktionsreif: Dev-Server-Temporal, selbst betriebene Datenbank, Prozesslimit des Kontos,
  keine Sandbox, Leistungsebene `development_mock` bis Egress-Proxy und echte Modellzugänge vorliegen. Das steht in jeder Gesundheitsantwort.
- Ein zusätzlicher Server wird nötig für den Sandboxpfad (S4), für hochverfügbares Temporal oder wenn Last und Prozesslimit des Kontos nicht
  mehr reichen. Beide Wege können nebeneinander bestehen.
- Die Crontab des Kontos ist mit den Schwesterprodukten geteilt; Campus-Einträge werden mit einem Marker geführt und nur ergänzt, nie überschrieben.
