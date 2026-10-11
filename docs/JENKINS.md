# Jenkins für den Forschungscampus einrichten

Jenkins läuft lokal in Docker, eine eigene Instanz je Produkt (wie bei den übrigen Werkzeugen).
Port **8083**, Container `university-jenkins`, Daten im Volume `university_jenkins_data`.

## 1. Repository auf GitHub bringen

Das GitHub-Repository `kaspAir/university` ist noch leer. Jenkins holt die Pipeline aus dem Repo,
deshalb muss zuerst gepusht werden:

```bash
cd /c/Projekte/university
git add .
git commit -m "chore: Grundlagen und Jenkins-Gerüst"
git push -u origin main
git checkout -b develop && git push -u origin develop
```

## 2. Jenkins starten

```bash
docker compose -f docker-compose.jenkins.yml up -d --build
```

Erstes Admin-Passwort auslesen (erscheint erst, wenn der Container hochgefahren ist):

```bash
docker exec university-jenkins cat /var/jenkins_home/secrets/initialAdminPassword
```

Dann im Browser `http://localhost:8083` öffnen, das Passwort eingeben und
**«Install suggested plugins»** wählen. Danach einen Admin-Benutzer anlegen (kein Standardpasswort).

## 3. Plugins ergänzen

Unter *Manage Jenkins → Plugins → Available* zusätzlich installieren:

- **Docker Pipeline** (`docker-workflow`): Test-Stage läuft im `python:3.12-slim`-Container
- **SSH Agent** (`ssh-agent`): für den späteren Deploy per SSH
- **JUnit** und **Git** sind in den «suggested plugins» enthalten

Das entspricht dem Plugin-Satz des HERMES-PIA-Jenkins.

## 4. Credentials

| ID | Art | Zweck | Stand |
| --- | --- | --- | --- |
| `github-kaspair` | Username + Token | Checkout, falls das Repo privat ist | nur nötig bei privatem Repo |
| `university-deploy` | SSH Username with private key | Deploy auf den Zielhost | **erst nach Host-Entscheid** |

Der Deploy-Key des HERMES-PIA-Jenkins (`hermespia-deploy`) liegt nur im Volume dieser anderen
Instanz und auf dem Server, nicht lokal. Wenn der Campus auf denselben Host soll, denselben Key
exportieren oder einen neuen auf dem Server erzeugen.

## 5. Jobs anlegen

Pro Branch ein Job vom Typ **Pipeline**, Name mit dem Branch im Namen (die Pipeline liest den
Jobnamen aus):

| Job | Branch | Macht |
| --- | --- | --- |
| `university develop` | `develop` | Prüfung + Tests, kein Deploy |
| `university test` | `test` | Prüfung + Tests + Deploy test |
| `university integration` | `integration` | Prüfung + Tests + Deploy int |
| `university main` | `main` | Prüfung + Tests + Deploy prod |

Einstellungen je Job:

- *Definition*: Pipeline script from SCM, SCM = Git
- *Repository URL*: `https://github.com/kaspAir/university`
- *Branch Specifier*: `*/develop` usw.
- *Script Path*: `Jenkinsfile`
- *Build Triggers*: **Poll SCM** mit `H/5 * * * *`. GitHub-Webhooks erreichen den lokalen
  Rechner nicht; Polling ersetzt sie. Alternativ manuell starten.

Zunächst reicht der Job `university develop`. Die Deploy-Stage bricht in den anderen Jobs
absichtlich mit einer klaren Meldung ab, bis Zielhost und Betriebsform entschieden sind.

### Was die Test-Stage tut

Sie legt je Build ein Docker-Netz an, startet darin `postgres:16` als Sidecar und führt im Container
`python:3.12-slim` die Abhängigkeiten aus `requirements-dev.txt`, `pytest` (Unit, Integration gegen das
Sidecar-PostgreSQL, Adapterverträge, statische Grenzen) und `ruff` aus. Die Integrationstests setzen das
Schema per Alembic auf und ab. Ohne erreichbare Datenbank würden sie **übersprungen** gemeldet, nie
durch SQLite ersetzt. Im CI gibt es keine Rolle `campus_app`; die Migration überspringt dann nur die
Rechtevergabe, der Append-only-Trigger auf `audit_events` wird trotzdem angelegt und getestet.

## 6. Promotion

Wie bei den übrigen Produkten strikt sequenziell und ohne Stufen zu überspringen:

```
feature → develop → test → integration → main
```

## 7. Betrieb

```bash
docker compose -f docker-compose.jenkins.yml logs -f jenkins   # Log
docker compose -f docker-compose.jenkins.yml restart            # Neustart
docker compose -f docker-compose.jenkins.yml down               # Stopp, Daten bleiben
```

## Offen

- **Zielhost.** `university.kaspair.ch` zeigt auf den Infomaniak-Managed-Host (gleiche Adresse wie
  hermespia.ch): kein Docker, kein Root, Python 3.9. Die Architektur (Kap. 19) setzt Docker Compose,
  PostgreSQL und Temporal voraus. Entweder ein anderer Host (VPS/Public Cloud mit Docker) oder eine
  angepasste Betriebsform. Erst danach lässt sich die Deploy-Stage bauen.
- **Entwicklungspaket.** Die Architektur verweist auf CLAUDE.md, JSON-Schemas, `diagrams/*.mmd`,
  Abnahmespezifikation und `validate_contracts.py`. Diese Dateien liegen noch nicht im Repo.
