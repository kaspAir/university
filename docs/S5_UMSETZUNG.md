# S5 Umsetzung: kontrollierter Forschungspilot und unabhängige Bewertung

Stand: 12.10.2026 · Basis: Verfassung Anhang C, D, F; Art. 11, 17, 19, 20, 27; Architektur Kap. 20 (Zeile S5: «Vergleich der Arbeitsformen aus
Anhang C, mehrere Institute, Status- und Kostenauswertung; wissenschaftliche Bewertung getrennt von Plattformtests; externe Publikationsfreigabe
bleibt menschlich»).

S5 liefert die **Maschinerie** des Piloten, nicht den Piloten. Alles ist mit Mock-Modellen offline geprüft. Ein echter Pilot braucht die
Betriebsentscheide aus Anhang F und zwei echte Anbieter (S1-Tor). Dieses Dokument unterscheidet **implementiert**, **offline geprüft** und **offen**.

## 1 Was gebaut ist

| Baustein | Umsetzung | Bezug | Stufe |
| --- | --- | --- | --- |
| Vorregistriertes Protokoll | `POST /pilots`: Frage, Stufe (`reproduction` / `follow_up`, Anschlussfrage nur nach abgeschlossener Reproduktion), genau die drei Bedingungen aus Anhang C, Wiederholungszahl, Bewertungsregel (`mean_score`, Schwelle), verdecktes Prüfmaterial (geschützter Prüfsatz im Tresor), gemeinsames Budget mit Prüfreserve, Datenzugang, erlaubte Eingriffe, verlangte Testkennungen, Fähigkeiten im Umfang. Hash `preregistration_sha256`; Änderung = neue Fassung. Der Pilot erhält ein eigenes Budgetkonto unter dem Mandat und ein Bewertungsprojekt im Institut Metawissenschaft. | Anhang C, Art. 19 | implementiert; offline geprüft |
| Plattform-Testnachweis | `POST /platform-evidence` (Betrieb): JUnit-Bericht eines Builds; Kennungen T01–T17/A01–A14 aus Testnamen, nur bestandene zählen, übersprungene nie. Daten über die Plattform, nie über Forschung. | Anhang D | implementiert; offline geprüft |
| Freigabetor | `POST /pilots/{id}/release`: vorbehaltene Entscheidung (Option, Gründe, ≥2 Alternativen, Einwände, Folgen, Auflagen). Prüft Mandat, Forschungsleitung, Integritätsfunktion, Policy-Limits, Nachweis für den Build und jede verlangte Kennung, geschütztes Material, Budget; sammelt alle Fehlgründe. Selbstkontrolle (gleiche Person) wird ausgewiesen, nicht verhindert. | Anhang F, Art. 11 | implementiert; offline geprüft |
| Arme und gemeinsames Budget | `POST /pilots/{id}/arm-projects` legt Projekte je Bedingung **unter dem Pilotbudget** an (institutsübergreifend: mehrere Institute); `POST /pilots/{id}/tasks` ordnet Aufgaben mit Wiederholungsnummer zu, nur aus Armprojekten, nur im vorregistrierten Bereich. | Anhang C | implementiert; offline geprüft |
| Verblindete unabhängige Bewertung | `POST /pilots/{id}/evaluations`: Prüfer kanonisch verschieden von allen in allen Armen eingesetzten Modellen und mit Prüferfreigabe (`role: evaluator`); sieht Aspekt, Rubrik und Aussagen, nie Bedingung/Arm/Aufgabe/Projekt/Institut/Modell (Prüfung vor dem Aufruf, sonst Abbruch); Kontextmanifest mit Hashes; Aufrufe mit Route, Reservierung, Journal und gemeinsamer Vorprüfung unter dem Bewertungsprojekt. Ohne geeignetes Modell: Bewertung **offen** (`open_no_independent_evaluator`). | Art. 17, 19; T13 | implementiert; offline geprüft |
| Bericht | `GET /pilots/{id}/report` aus Daten: je Bedingung Institute, Modelle (Erzeuger/Prüfer), Aufgaben, Wiederholungen, Läufe, Aufrufe, Tokens, Kosten gesetzt/offen, Laufzeit, Aussagen nach gespeicherter Art, markierte Aussagen, Prüfungen, offene Einwände, Experimente gemessen/ungemessen, menschliche Eingriffe, Bewertungen; **Unterschiede** zwischen Bedingungen; vorregistrierte Regel je Arm; **Plattformnachweis getrennt** von der wissenschaftlichen Bewertung; Flag «Machbarkeitsprüfung», solange Wiederholungen fehlen; nie eine Mehrwertbehauptung. `POST /pilots/{id}/report` friert ihn als Artefakt (`pilot_report`, Hash) ein. | Anhang C, Art. 20 | implementiert; offline geprüft |
| Menschliche Freigabe | Ergebnisfreigabe und externe Freigabe des Berichts über den bestehenden fassungsgebundenen Weg (`/artifacts/{id}/approvals`, `/publications/{id}/release`). Abschluss (`POST /pilots/{id}/close`) nur mit eingefrorenem Bericht. | Art. 11, 22; T08 | implementiert; offline geprüft |
| Rollen | `pilot.create/release/close` Leitung; `pilot.evaluate/report` Leitung und Integrität, nie Erzeuger; `evidence.import` Betrieb. | Art. 17 | implementiert; offline geprüft |
| Migration 0010 | `platform_evidence`, `pilot_studies`, `pilot_arms`, `pilot_arm_projects`, `pilot_arm_tasks`, `pilot_evaluations`. | – | implementiert; auf Test- und Entwicklungsdatenbank |

## 2 Abnahmepunkte

| Kriterium | Was der Test tatsächlich belegt | Tests | Stufe |
| --- | --- | --- | --- |
| Protokoll vollständig und eingefroren | Jede Lücke wird benannt; Hash ändert sich mit dem Inhalt; zweite Fassung statt Änderung | `tests/unit/test_pilot_rules.py`, `test_s5_pilot.py::test_pilot_end_to_end_with_mock_models` | offline geprüft |
| Tor sammelt alle Fehlgründe (Anhang F) | Ohne Integritätsfunktion und ohne Nachweis: beide Gründe; Nachweis mit gescheitertem T05: benannt; Build-Abweichung: benannt; mit vollständigem Nachweis freigegeben | Unit `test_release_gate_collects_all_failures`, Integration (Schritte 2–4) | offline geprüft |
| Selbstkontrolle sichtbar | Gleiche Person in Leitung und Integrität blockiert nicht, wird als `self_control` gespeichert | Unit `test_self_control_is_shown_not_hidden` | offline geprüft |
| Plattformnachweis ≠ Forschungsnachweis | JUnit: nur bestandene Fälle decken Kennungen, übersprungene nie; DOCTYPE abgewiesen; im Bericht eigener Abschnitt mit eigener Notiz | Unit `test_junit_parsing_covers_only_passed_ids`, Integration (Bericht) | offline geprüft |
| Gemeinsames Budget | Alle Armprojekte hängen unter dem Pilotkonto; Aufgabe ausserhalb nicht zuordenbar | Integration (Budgetbaum, `gemeinsamen Budgets`) | offline geprüft |
| Kein stilles Prüfsiegel (T13) | Nur A und B im Einsatz → Bewertung offen mit Ausschlussliste; mit drittem Modell F verblindet und abgeschlossen | Integration (Schritte 7–8) | offline geprüft |
| Verblindung | Bedingungen, Kennungen, Modell- und Anbieternamen werden vor dem Aufruf erkannt; kurze Tokens ignoriert; Manifest hält Hashes je Block | Unit `test_blinding_detects_condition_and_identifier_leaks`, Integration (`blinded`, Manifest) | offline geprüft |
| Unterschiede statt Verstecken | Einzelagent mit Gegenprüfung, Paar ohne Gegenprüfung, institutsübergreifend mit einem Institut, Eingriffe trotz leerer Erlaubnisliste werden benannt | Unit `test_differences_name_what_the_protocol_wanted_controlled` | offline geprüft |
| Keine Mehrwertbehauptung; Machbarkeit markiert | `added_value_claim` immer; `feasibility_only`, wenn Wiederholungen fehlen; Regel je Arm nur aus abgeschlossenen Bewertungen | Unit `test_report_never_claims_added_value_and_flags_feasibility`, Integration (Bericht) | offline geprüft |
| Erzeuger prüft und gibt nicht frei (Art. 17) | Erzeugerrolle: 403 bei Freigabe, Bewertung, Nachweisimport, Bericht | `::test_producer_role_cannot_evaluate_or_release` | offline geprüft |
| Stufe 1 vor Stufe 2 | Anschlussfrage vor Abschluss der Reproduktion abgewiesen; nach Bericht, Freigabe und Abschluss zulässig | Integration (Schluss) | offline geprüft |

## 3 Begründete Grenzen

- **Kein echter Pilot.** Mock-Modelle liefern synthetische Aussagen und Urteile (Score 100 ohne Marker). Belegt ist die Maschinerie, nicht Forschung.
- **Einzelagent mit Selbstprüfung** ist auf dieser Plattform nicht nativ: Art. 27 verlangt für tragende Erzeugung eine Gegenprüfung durch ein
  anderes Modell, und die Policy erzwingt sie. Die Bedingung braucht eine Policy ohne Prüfpflicht für diesen Arm; das ist eine vorbehaltene
  Entscheidung. Der Bericht weist das als Unterschied aus, statt die Bedingung still umzudeuten.
- **Testkennungen aus Namen.** Nur Tests, deren Name eine Kennung trägt, zählen. Im Repo tragen heute A02–A06, A08, A10, A14, T02–T05, T07–T12,
  T15–T17 eine Kennung; T01, T06, T13, T14, A01, A07, A09, A11–A13 fehlen im Namen (einige sind geprüft, aber nicht so benannt). Ein echter
  Pilot muss diese Kennungen liefern oder die betroffene Fähigkeit aus dem Umfang nehmen; das Tor zeigt die Lücke.
- **Menschliche Eingriffe** werden als Zahl von Freigaben gesperrter Aufgaben und Abbrüchen gezählt; andere Eingriffe (z. B. Mandatsänderung)
  erscheinen nicht je Arm.
- **Blinding ist eine Token-Prüfung** über bekannte Kennungen; eine Aussage, die ihre Bedingung umschreibt («wurde institutsübergreifend
  geprüft»), erkennt sie nicht. Das ist eine Grenze des Verfahrens und steht im Bericht.
- **Externe Bewertung** (Art. 19: «externe Bewertungen oder unabhängig verwahrtes Prüfmaterial») ist als Rolle vorgesehen (Integrität, dritter
  Prüfer), aber keine externe Person oder Organisation ist besetzt (Verfassung: «Eine externe Rolle ist erst besetzt, wenn eine reale Person
  oder Organisation den Auftrag angenommen hat»).

## 4 Nachweise dieses Stands

| Lauf | Ergebnis |
| --- | --- |
| Gesamtsuite lokal mit `CAMPUS_TEST_SANDBOX=1` (Unit, Sicherheit, Verträge, Integration gegen PostgreSQL und Temporal, Sandbox mit echten Containern) | 247 Tests grün, 5 Live-Tests übersprungen |
| CI-Konstellation (python:3.12-slim, PostgreSQL- und Temporal-Sidecar, ohne Docker-Zugriff) | 237 Tests grün, 15 übersprungen (5 Live, 8 Sandbox, 2 Rollen-Grenzen); Linter ohne Befund |
| Migrationskette | 0001–0010 auf Test- und Entwicklungsdatenbank |

Was diese Zahlen **nicht** belegen: wissenschaftliche Aussagekraft, Verhalten echter Modelle, einen Mehrwert einer Bedingung. Der erste echte
Pilot ist ein Betriebsentscheid (Abschnitt 5).

## 5 Nächster Entscheid (Anhang F)

Für den ersten echten Pilot sind zu entscheiden: F03 Personen für Integrität, Betrieb, Stellvertretung, Beschwerdestelle; F04 Geld-, Zeit- und
Parallelitätsgrenzen und der Anteil für Grundlagen und Replikation; F05 konkrete Vorlage, Material, Kriterien, Wiederholungen; F06 lizenzierte
Zugänge und Exportregeln; F07 Startprofile und Qualifikationsfälle; F08 Anbieter, Modelle, Schlüssel, Budgets, Datenfreigaben. Dazu die
Policy-Frage zur Einzelagent-Bedingung, der Zielhost mit Sandbox-Abnahme (S4) und der Egress-Proxy (S2/S4).
