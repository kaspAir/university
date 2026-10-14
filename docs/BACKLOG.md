# Backlog: Ausbauanforderungen AUS-01 bis AUS-07

Stand: 14.10.2026 · Grundlage: «Phronesis Campus – Ausbauanforderungen» V0.2 (13.10.2026, Anforderungen von Kaspar) · Abgleich mit dem
Repository auf diesem Stand. Keine Implementierungsfreigabe und keine neue Architekturphase: die Liste hält fest, was vorhanden ist, was
fehlt und in welcher Reihenfolge es kommt. Das aktuelle Ziel bleibt der Bedienweg vom eigenen Forschungsauftrag zum lesbaren Ergebnis.

## Reihenfolge

| Rang | Kennung | Umfang | Warum jetzt oder später |
| --- | --- | --- | --- |
| 1 | **AUS-04 Passwortänderung** | klein | Nächste Nutzungsverbesserung; das Pilotkonto hat ein generiertes Startpasswort aus einer Datei auf dem Server. |
| 2 | **AUS-05 E-Mail-Benachrichtigungen** | klein bis mittel | Nächste Nutzungsverbesserung; Entscheide (Art. 11) und fertige Fassungen werden heute nur sichtbar, wenn man die Seite öffnet. |
| 3 | AUS-06 Sicherheit, Grundschutz der erreichbaren Instanz | mittel | Wird nicht bis zur Mandantenfähigkeit aufgeschoben (V0.2, zeitliche Einordnung). |
| 4 | AUS-07 Institute erfassen | klein | Datenmodell ist bereits konfigurierbar; es fehlt nur der Weg ohne Codeänderung. |
| 5 | AUS-02 Mehrere Benutzer je Mandant | mittel | Voraussetzung für externe Kunden; Rollen sind vorhanden, die Verwaltung fehlt. |
| 6 | AUS-03 Mandanten- und Überadministration | mittel | Baut auf AUS-02 und AUS-01. |
| 7 | AUS-01 Mandantenfähigkeit | gross | Vor der Aufnahme externer Kunden; Workspaces trennen schon heute, der Nachweis der Trennung fehlt. |

## Abgleich je Anforderung

### AUS-01 Mandantenfähigkeit
**Vorhanden.** Der Workspace ist heute die Trenneinheit: jedes fachliche Objekt trägt `workspace_id` (Mandate, Projekte, Aufgaben, Quellen,
Aussagen, Fassungen, Budget, Audit), die Dienste prüfen die Zugehörigkeit vor dem Zugriff (`_own`, rund 50 Stellen), die Sitzung ist an
einen Workspace gebunden. Geheimnisse, Modellkatalog und Preise sind je Workspace registriert, die Schlüssel aber prozessweit.
**Fehlt.** Ein Nachweis, dass keine Abfrage den Workspace verliert (Tests, die mit zwei Workspaces jede Lese- und Schreibroute prüfen);
Trennung der Blob-Ablage, des Ablaufdienstes und künftiger Benachrichtigungen je Workspace; Regel, ob ein Mandant eigene Anbieterschlüssel
oder Datenfreigaben braucht; Entscheid, ob «Mandant» = Workspace bleibt.
**Nächster Schritt, wenn an der Reihe.** Mehr-Workspace-Testsatz über alle Routen; danach Entscheid Workspace = Mandant.

### AUS-02 Mehrere Benutzer je Mandant
**Vorhanden.** Benutzer je Workspace (`users`, eindeutig je Workspace), Rollen als Zuweisungen (`grants`: Forschungsleitung, Integrität,
Betrieb, Prüfer, Erzeuger), rollenbasierte Befehlsprüfung (`campus_domain/authz.py`), Anlage über die Betriebs-CLI (`create-user`).
**Fehlt.** Benutzer und Rollen über die Oberfläche anlegen, sperren und ändern; Rollen entziehen; Übersicht der Konten.
**Nächster Schritt.** Verwaltungsseite für die Mandantenadministration (AUS-03), gleiche Befehle wie die CLI, jeder Schritt im Audit.

### AUS-03 Mandantenadministration und Überadministration
**Vorhanden.** Die Rolle `operator` für technische Befehle; keine Rolle über den Workspaces.
**Fehlt.** Eine Mandantenadministration (Benutzer und Rollen im eigenen Workspace, ohne fachliche Freigaben) und eine
Plattformadministration für Kaspar (Workspaces anlegen, Institute für mehrere Mandanten freigeben). Übergeordneter Zugriff auf vertrauliche
Kundeninhalte nur ausdrücklich geregelt und protokolliert (V0.2).
**Nächster Schritt.** Rollenbegriffe festlegen (`tenant_admin`, `platform_admin`), getrennt von `research_director`.

### AUS-04 Passwortänderung · als nächste kleine Verbesserung eingeplant
**Vorhanden.** Argon2-Hashes, Mindestlänge 12 (`security.py`), serverseitige Sitzungen mit CSRF; Anlage mit Startpasswort über die CLI.
**Fehlt.** Selbstbedienung in der Oberfläche.
**Geplanter Umfang.** Seite «Konto» mit aktuellem und neuem Passwort; Befehl `auth.change_password` über die JSON-API mit CSRF; prüft das
aktuelle Passwort, Mindestlänge, nicht gleich dem alten; beendet alle anderen Sitzungen des Kontos; Audit-Eintrag ohne Passwortinhalt.
Danach die Startpasswort-Datei auf dem Server löschen. Wiederherstellungsweg und Mehrfaktor-Anmeldung (V0.2) folgen getrennt, zuerst für
Administrationskonten.
**Abnahme.** Test: falsches altes Passwort abgewiesen, zu kurzes abgewiesen, andere Sitzungen ungültig, Audit ohne Geheimnis.

### AUS-05 E-Mail-Benachrichtigungen · als nächste kleine Verbesserung eingeplant
**Vorhanden.** Ereignisse liegen bereits als Outbox (`outbox_events`) vor: Mandatsentwurf, Dossier eingefroren, Ablauf beendet, Einwand.
**Fehlt.** Versand, E-Mail-Adresse je Konto, Einstellungen je Person.
**Geplanter Umfang.** Feld E-Mail-Adresse am Konto; Versand über einen konfigurierten SMTP-Zugang (Schlüssel nur in `campus.env`, wie die
Modellschlüssel); ein Zustellprozess liest die Outbox und verschickt für drei Anlässe: Entscheid wartet (Mandat, Ergebnisfreigabe), Fassung
bereit, Ablauf ohne Ergebnis beendet. Inhalt nur eine knappe Nachricht und ein Link zur geschützten Ansicht, keine Forschungsinhalte
(V0.2); der Link ersetzt keine Anmeldung. Zustellung je Ereignis höchstens einmal, im Audit festgehalten.
**Voraussetzung (Betrieb).** Ein SMTP-Zugang mit Absenderadresse, z. B. über das Infomaniak-Mailkonto der Domain; ohne Zugang wird nichts
verschickt und die Oberfläche sagt das.
**Abnahme.** Test mit einem Prüf-Postfach-Adapter: genau eine Nachricht je Anlass, kein Forschungsinhalt in der Nachricht, Link führt zur
Anmeldung.

### AUS-06 Sicherheit
**Vorhanden.** HTTPS, Argon2, serverseitige Sitzungen mit `HttpOnly`/`Secure`/`SameSite=strict`, CSRF auf jedem Befehl, Idempotenz,
rollenbasierte Befehlsprüfung, unveränderliches Audit (Datenbank-Trigger), Datenbank nur über Unix-Socket, Dienste nur auf 127.0.0.1,
Internetdienst mit SSRF-Schutz, Geheimnisse nur in einer 0600-Datei, tägliche Sicherung mit geprüfter Wiederherstellung, Proxy setzt
`X-Content-Type-Options` und `Referrer-Policy`.
**Fehlt.** Begrenzung von Anmeldeversuchen (heute unbegrenzt), Content-Security-Policy, `X-Frame-Options`/`frame-ancestors`, HSTS-Prüfung,
Abhängigkeitsprüfung in CI (bekannte Schwachstellen), Protokollierung fehlgeschlagener Anmeldungen mit Alarm, Mehrfaktor für
Administrationskonten (mit AUS-04), eine unabhängige Prüfung vor externen Kunden. Egress-Proxy (Leistungsebene `restricted_research`).
**Nächster Schritt.** Grundschutz der erreichbaren Instanz: Anmeldebegrenzung und Sicherheits-Header zuerst; danach Abhängigkeitsprüfung in CI.
Ein absolutes «nie hackbar» ist kein Abnahmekriterium (V0.2); Restrisiken bleiben sichtbar.

### AUS-07 Neue Institute erfassen
**Vorhanden.** Institute sind Datensätze je Workspace (`institutes`: Name, Auftrag, Reihenfolge), nicht Code; Projekte und Profile verweisen
darauf; die Campus-Übersicht liest sie aus der Datenbank. Die fünf Institute entstehen bei der Workspace-Anlage (`INSTITUTES`).
**Fehlt.** Anlegen, Umbenennen und Stilllegen über die Oberfläche durch die Plattformadministration; ein Status je Institut; die Freigabe
eines Instituts für mehrere Mandanten als gesonderte Berechtigung (ein gemeinsames Institut führt keine Kundendaten zusammen, V0.2).
**Nächster Schritt.** Formular «Institut anlegen» (Name, Auftrag, Status), vorerst für die Forschungsleitung des eigenen Workspace, bis
AUS-03 die Plattformrolle bringt. Keine automatische Institutsgründung durch Agenten.

## Was diese Liste nicht ist
Kein Sicherheitsnachweis und keine Zusage eines Termins. Jeder Punkt wird erst nach einem eigenen, kleinen Auftrag umgesetzt.
