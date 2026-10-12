# S4 Umsetzung: isolierte Ausführung und getrennt verwahrtes Prüfmaterial

Stand: 12.10.2026 · Basis: Architektur V0.1 Kap. 13, 15, 19, 20 (Zeile S4), 22; Verfassung Art. 9, 11, 14, 19; Tests T04, T05, T10, A10;
Paket CLAUDE.md («Untrusted code is never imported into the API/worker process. A missing sandbox disables that capability, not the guard.»)

Dieses Dokument unterscheidet durchgehend **implementiert**, **offline geprüft** (Mock, kein Container), **lokal gemessen** (echte Container auf
dem Entwicklungs-Host) und **offen**. Nichts davon ist ein Nachweis für den Zielhost.

## 1 Was gebaut ist

| Baustein | Umsetzung | Bezug | Stufe |
| --- | --- | --- | --- |
| Sandbox-Controller | Eigenes Paket `services/sandbox_controller/campus_sandbox`: Job-/Ergebnisvertrag (`spec.py`), Runner-Port, **Docker-Runner** (runc, Härtung je Lauf: `--network none`, `--read-only`, `--cap-drop ALL`, `no-new-privileges`, `--ipc none`, Benutzer 65534, PID-/Speicher-(ohne Swap)/CPU-/tmpfs-Limit, Eingaben `ro`, Ausgaben getrennt, nur allowlistete Umgebungsvariablen, Zeitlimit durch den Controller, Image per Digest), **Mock-Runner** (führt nichts aus, Ergebnisse `runner_kind: mock`, nie `measured`). Ausgaben werden hashbasiert in den Blob-Speicher übernommen. | Kap. 13, 19; ADR-0012 | implementiert; Docker-Runner lokal gemessen |
| Probe und Abnahme | `POST /sandbox/probe` misst auf dem Host 13 Punkte (Netz, Root-FS, Benutzer, Socket, Umgebung, Eingaben ro, Ausgaben, Capabilities via `setuid(0)`, PID-Limit, Zeitlimit, Speicherlimit, Ausgabenerfassung, «stdout ist Inhalt»). `POST /sandbox/acceptances` (nur Forschungsleitung) nimmt **nur** einen bestandenen Bericht des messenden Runners für das konfigurierte Image **und diesen Host** ab; Bericht ist prüfsummengebunden; Widerruf möglich. `GET /sandbox/status` zeigt Runner, Image, Abnahme. | Kap. 13 [T7], Art. 11 | implementiert; lokal gemessen |
| Experimente | `POST /experiments`: Programm (Text) + Eingaben (Text) → Job mit festem `argv` (`python -I -B /work/in/program.py`); Start nur mit aktivem Mandat, unge­sperrter Aufgabe, Recht `sandbox.run` in der Aufgabe, konfiguriertem Runner und (Docker) gültiger Abnahme. Der Auftrag ist **vor** dem Lauf committet und auditiert (`ExperimentStarted`/`ExperimentEnded`). Gespeichert wird, was der Runner misst: Zustand, Exitcode, Timeout, OOM, Limits, Hashes; `stdout` als Inhalt. | Kap. 13, 16; T04 | implementiert; offline geprüft (Mock), lokal gemessen (Docker) |
| Experimente als Belege | Erzeugungsläufe erhalten eingefrorene Experimente des Projekts als Kontext (Zustand, Hashes, stdout als Inhalt). `claim_set`-Belege dürfen `experiment_run_id` nennen. Der Kern zählt ein Experiment nur als Beleg, wenn `measured` und `completed`; sonst `EVIDENCE_NOT_MEASURED` → Hypothese. | Art. 13–14; T04 | implementiert; offline geprüft |
| Tresor für Prüfmaterial | Schema `vault`, Tabelle `vault.suite_cases`, eigene Rolle `campus_vault` (lesen, einfügen; nie ändern/löschen), eigene Verbindung `CAMPUS_VAULT_DATABASE_URL`. Anwendungsschema hält nur `cases_sha256` und `case_count`; vor jedem Qualifikationslauf Hash-Abgleich. Anwendungsrolle ohne Recht auf dem Schema; Sandbox ohne Verbindung. | Kap. 13, 15; T05, T10; ADR-0013 | implementiert; offline geprüft (Rollen-Grenze lokal gegen echte Rollen) |
| Grenzen im Code | Statische Tests: kein `subprocess`/`exec`/`eval`/`importlib` ausserhalb des Docker-Runners; die API importiert den Runner nie auf Modulebene. | CLAUDE.md, Kap. 13 | implementiert; offline geprüft |
| Konfiguration | `CAMPUS_SANDBOX_RUNNER` (none/mock/docker), `CAMPUS_SANDBOX_IMAGE` (Digest-Pflicht), `CAMPUS_SANDBOX_WORKROOT`, `CAMPUS_VAULT_DATABASE_URL`; `/health` und `/ready` zeigen Runner und Tresor. Leistungsebene bleibt `development_mock` (siehe 3). | Kap. 13, 19 | implementiert |
| Migration 0009 | Schema `vault` + Umzug der Fälle, `case_count`, Spalte `cases` entfernt; `sandbox_acceptances`, `experiment_runs`; `evidence_links.experiment_run_id`; Rechte je Rolle, wenn die Rolle existiert. | – | implementiert; in Testdatenbank und Entwicklungsdatenbank ausgeführt |

## 2 Abnahmepunkte

| Kriterium | Was der Test tatsächlich belegt | Tests | Stufe |
| --- | --- | --- | --- |
| Härtung jedes Laufs (A10: Sandbox blockiert Ausbruchspfade) | Jeder `docker run` trägt die Härtungsflags, genau die allowlistete Umgebung, keine privilegierten Optionen; Image nur per Digest; Eingabenamen ohne Pfade | `tests/unit/test_sandbox_spec.py` | offline geprüft |
| Härtung greift auf dem Host | Probe mit echten Containern: Netz blockiert, Root-FS schreibgeschützt, uid 65534, kein Docker-Socket, keine Host-Umgebung (ein im Controller gesetztes Geheimnis ist im Container unsichtbar), Eingaben ro, Ausgaben erfasst und gehasht, `setuid(0)` verweigert, PID-Limit, Zeitlimit tötet, Speicherlimit verhindert Abschluss, Ausgabegrenze erzwungen, fremdes Image verweigert | `tests/sandbox/test_docker_sandbox.py` (nur mit `CAMPUS_TEST_SANDBOX=1`) | lokal gemessen (Entwicklungs-Host, Docker Desktop/WSL2, runc) |
| Nachweis der Durchführung ≠ Richtigkeit (T04) | Exit 1 mit «alle Tests bestanden» im stdout ist kein `completed`; Mock-Ergebnisse sind nie `measured`; ein Mock-Experiment als Beleg ergibt eine Hypothese mit `EVIDENCE_NOT_MEASURED`, ohne Beleglink | Unit `test_completion_is_measured_never_read_from_stdout`, Integration `test_unmeasured_experiment_evidence_yields_a_flagged_hypothesis` | offline geprüft |
| Fehlende Sandbox schaltet ab, ersetzt nicht | Ohne Runner, ohne Abnahme, mit widerrufener Abnahme, mit fremdem Host/Image: `CAPABILITY_UNSUPPORTED`; Mock-Runner ausserhalb `development_mock` unzulässig | Unit `test_experiment_rules.py`, Integration `test_docker_path_needs_probe_and_acceptance_then_measures` | offline geprüft; Docker-Pfad lokal gemessen |
| Abnahme bindet Runner, Image, Host | Veränderter Bericht (Prüfsumme) und Bericht eines anderen Hosts werden abgewiesen; Mock-Bericht ist nicht abnehmbar | Unit `test_only_a_passed_docker_report_of_this_host_is_acceptable`, Integration `test_mock_probe_cannot_be_accepted`, Docker-Pfad-Test | offline geprüft; lokal gemessen |
| Rechenlauf braucht das Recht `sandbox.run` | Aufgabe ohne das Recht: `FORBIDDEN` vor jeder Vorbereitung | `test_task_without_sandbox_scope_cannot_run_experiments` | offline geprüft |
| Gemessener Lauf stützt eine Aussage | Mit abgenommenem Docker-Pfad: Experiment `measured` + `completed`, Erzeugungslauf speichert `computation` mit Beleglink statt Hypothese | `test_docker_path_needs_probe_and_acceptance_then_measures` | lokal gemessen |
| Prüfmaterial getrennt verwahrt (T05) | Anwendungstabelle ohne `cases`; Tresorzeile mit registriertem Hash; Anwendungsrolle `permission denied` auf `vault.suite_cases`; Tresorrolle kann weder `UPDATE` noch `DELETE`, liest das Anwendungsschema nicht | `tests/integration/test_s4_vault.py` (Rollen-Tests mit echten Rollen, sonst Skip) | offline geprüft (lokal gegen echte Rollen) |
| Manipuliertes Material stoppt den Lauf (T10) | Vom Eigentümer verändertes Tresormaterial ≠ Hash → kein Qualifikationslauf | `test_tampered_vault_material_stops_the_evaluation` | offline geprüft |
| Keine dynamische Ausführung im Kern | Statische Prüfung aller Pakete ausser dem Docker-Runner | `tests/security/test_static_boundaries.py` | offline geprüft |

## 3 Begründete Grenzen und Interpretationen

- **Leistungsebene.** Die Architektur definiert `isolated_execution` als `restricted_research` plus abgenommener Sandboxpfad. `restricted_research`
  ist wegen der DNS-Rebinding-Lücke (Egress-Proxy fehlt, BETRIEB.md) nicht freigegeben. Darum bleibt die Ebene `development_mock`, und der
  Sandboxpfad hat sein eigenes Tor: die Abnahme. **Interpretation:** «development_mock: keine echte autonome Codeausführung» verstehen wir als
  Verbot autonomer, nicht abgenommener Ausführung; ein von Menschen abgenommener Pfad mit Mandat und Recht `sandbox.run` ist zulässig und als
  solcher ausgewiesen (`measured`, `acceptance_id`). Das ist ein Betriebsentscheid des Nutzers (siehe 5).
- **Isolationsstärke.** runc unter Docker Desktop/WSL2 ist der Entwicklungs-Host. Für autonom erzeugten Code verlangt Kap. 13 stärkere Isolation
  (eigene VM, gVisor); auf diesem Host ist kein `runsc` vorhanden. Der Runner behauptet keine Isolationsklasse; die Abnahme bindet den Host.
- **Experiment synchron.** Der Lauf läuft im API-Prozess synchron (Zeitlimit bis zur Runner-Obergrenze 300 s). Als Temporal-Aktivität mit
  Wiederaufnahme ist er noch nicht umgesetzt; der Auftrag ist vorher committet, ein Abbruch hinterlässt einen sichtbaren `running`-Eintrag
  (kein Abgleich-Lauf; dieselbe Lücke wie bei S2, siehe S3_UMSETZUNG 2b).
- **Eingaben.** Nur Text (Programm, Dateien) bis 4 MB; Artefakt- oder Quellenfassungen als Eingaben sind noch nicht angebunden.
- **Paketinstallation** (Kap. 13 «Netz und Pakete») fehlt: das Image ist fest, Programme laufen mit der Standardbibliothek.
- **Kein Replikationslauf** (Rolle `replicator`) und keine Evaluation von Skills *in* der Sandbox; die Fabrik ruft weiterhin Modelle, nicht Code.
- **Tresor-Grenze** schützt gegen Anwendungsrolle und Sandbox, nicht gegen den Datenbankeigentümer (Kap. 15). CI ohne Rollen prüft die Grenze
  nicht (Skip, ausgewiesen).
- **CI** führt die Sandboxtests nicht aus (Testcontainer ohne Docker). Sie laufen lokal mit `CAMPUS_TEST_SANDBOX=1`; eine Jenkins-Stage mit
  Docker-Zugriff ist ein Folgeentscheid.

## 4 Nachweise dieses Stands

| Lauf | Ergebnis |
| --- | --- |
| Gesamtsuite lokal **mit** `CAMPUS_TEST_SANDBOX=1` (Unit, Sicherheit, Verträge, Integration gegen PostgreSQL und Temporal, Sandbox mit echten Containern) | 237 Tests grün, 5 Live-Tests übersprungen |
| Sandboxtests einzeln (`tests/sandbox`, Docker Desktop/WSL2, runc, Image `python@sha256:401f6e…`) | 8 grün; ein ursprünglicher Probe-Punkt «Port 80 binden» entfiel, weil Docker in Containern `ip_unprivileged_port_start=0` setzt und der Punkt nichts über Capabilities aussagt |
| Linter | ohne Befund |
| Migrationskette | 0001–0009 auf Test- und Entwicklungsdatenbank; Tresorrechte für `campus_vault` (SELECT, INSERT) vergeben, Anwendungsrolle ohne Recht auf `vault` |
| CI-Konstellation (python:3.12-slim, PostgreSQL- und Temporal-Sidecar, ohne Docker-Zugriff) | 227 Tests grün, 15 übersprungen (5 Live, 8 Sandbox ohne Docker, 2 Rollen-Grenzen ohne Rollen im Sidecar); Linter ohne Befund |

Was diese Zahlen **nicht** belegen: Isolation auf dem Zielhost, Verhalten echter Modelle, Richtigkeit von Experimentergebnissen.

## 5 Nächster Entscheid

1. **Zielhost und Isolationsklasse:** Wo läuft der Sandboxpfad produktiv, und genügt runc, oder braucht es VM/gVisor? Die Probe ist dort zu
   fahren und abzunehmen; der Entwicklungs-Host zählt nicht.
2. **Leistungsebene:** Bestätigung der Interpretation in Abschnitt 3 (abgenommener Sandboxpfad in `development_mock` zulässig) oder
   Verschiebung aller Experimente bis `isolated_execution`.
3. **Egress-Proxy** (schliesst DNS-Rebinding) als Voraussetzung für `restricted_research`.
4. S5: kontrollierter Pilot mit unabhängiger wissenschaftlicher Bewertung.
