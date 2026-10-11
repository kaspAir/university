# S3 – Agenten- und Skill-Fabrik: Umsetzung und Abnahmepunkte

Stand 11.10.2026 · Baufolge Architektur Kap. 20, Stufe S3: «NeedRecord, Profile/Skills als Kandidaten, Qualifikation, automatische
Aktivierung und Herkunftsansicht.» Abnahme: «Kandidat kann sich nicht selbst zertifizieren; Child-Budget, Versionsbindung und
Registervollständigkeit halten.» Grundlage: Verfassung Art. 19, 25–28, Anhang G; Architektur Kap. 7.

**Nachweisstufen:** *implementiert* · *offline geprüft* (Tests gegen PostgreSQL mit Mock-Adapter) · *live nachgewiesen* (gegen echte Anbieter).
**Live nachgewiesen ist nichts.**

## 1 Was gebaut ist

| Baustein | Umsetzung | Bezug | Stufe |
| --- | --- | --- | --- |
| Bedarfsnachweis | `POST /needs`: Lücke, verlangte Spezialisierung, Aufgabenklasse, geprüfte Alternativen, erwarteter Nutzen. Das Register entscheidet aus echten Daten zwischen `reuse` (aktives, für die Aufgabenklasse qualifiziertes Profil gleicher Spezialisierung) und `new_agent`. Profile und Skills müssen auf einen registrierten Bedarf verweisen (`NEED_NOT_FOUND`). | Art. 26, Kap. 7, Anhang G | offline geprüft |
| Kandidaten | Profile/Skills bleiben Kandidaten (`planned`/`candidate`); Tool-Scopes nur aus der Werkzeugkontrolle (A10); Skill-Paket hashgebunden (`package_sha256` = Hash der Anweisungen). | Art. 25–26, A10 | offline geprüft |
| Prüfmaterial | `POST /qualification-suites`: versionierte, feste Fälle (Eingabe, Rubrik), Schwelle, Prüfer-Aufgabenklasse; geschützte Sätze zeigen Produzenten nur Existenz, Hash und Zahl der Fälle (T05). Ein geändertes Kriterium ist eine neue Fassung; die Anwendungsrolle kann Sätze weder ändern noch löschen (T10). | Art. 19, T05, T10 | offline geprüft |
| Qualifikationslauf | `POST /evaluations`: je Fall arbeitet der Kandidat (festes Deployment) und urteilt ein kanonisch anderer, selbst für die Prüfaufgabe qualifizierter Prüfer anhand der Rubrik (Vertrag `evaluation_verdict`, Repo-Ergänzung). Jeder Aufruf hat Route, gebundene Reservierung, Journal und die gemeinsame Vorprüfung; der Lauf ist eine echte Aufgabe unter einem Projekt (Budgetbaum). Das Kontextmanifest hält fest, was der Prüfer sah: Fall, Rubrik, Kandidatenausgabe, nie eine Selbsteinschätzung (A11). | Kap. 7, Art. 17, 19 | offline geprüft |
| Bootstrap | Die Prüferqualifikation des ersten Prüfmodells trägt die Forschungsleitung von Hand ein (`evaluated_by_kind: human`); damit endet der Zertifizierungsregress. | Kap. 7 | offline geprüft |
| Automatische Aktivierung | Bestanden und ohne Rückschritt gegenüber der aktiven Fassung auf demselben Prüfsatz: der Kern setzt `active`/`task_qualified`, erweitert `qualified_task_classes` um genau diese Aufgabenklasse und erzeugt den Qualifikationsnachweis (`qr:<lauf>`, Prüfer = Prüfmodell). Nicht bestanden oder Rückschritt: Kandidat bleibt begrenzt, Vergleich dokumentiert (T17). Alte Fassungen werden nicht still abgelöst. | Art. 26, 28; T17 | offline geprüft |
| Herkunftsansicht | `GET /inventory/{ref}/rationale`: Bedarf, Alternativen, Prüfläufe mit Ergebnis und Prüfer, Nachweise, Einsätze und Kosten aus Reservierungen, Abhängigkeiten, Stilllegungen, Aktivierungsereignisse. Funktioniert ohne Sprachmodell (A09). | Anhang G, A09 | offline geprüft |
| Stilllegung | `POST /inventory/{ref}/retire`: RetirementRecord mit Grund und Nachfolger; archivierte Profile starten keine Läufe mehr; Geschichte bleibt auffindbar. | Kap. 7 | offline geprüft |

## 2 Abnahmepunkte

| Kriterium | Umsetzung | Tests | Stufe |
| --- | --- | --- | --- |
| Kandidat kann sich nicht selbst zertifizieren (T12) | Prüfer muss kanonisch verschieden UND selbst für die Prüfaufgabe qualifiziert sein; ohne qualifizierten Prüfer wird kein ungeprüfter Prüfer «zum Selbstzertifikat»; selbst erzeugte Nachweise werden an der Quelle abgewiesen; beanspruchte Zustände ohne auflösbare Nachweise ebenso | `test_s3_factory.py::test_t12_*`, `tests/unit/test_qualification_rules.py::test_t12_*` | offline geprüft |
| Child-Budget hält | Qualifikationslauf = Aufgabe mit Kindbudget unter dem Projekt; alle Reservierungen routengebunden und abgerechnet | `::test_need_candidate_evaluation_activation_and_provenance` (4 Reservierungen, alle settled) | offline geprüft |
| Versionsbindung | Aktivierung von Fassung 2 ändert Fassung 1 nicht; Aufgaben referenzieren exakte Fassungen; Rückschritt blockiert Ablösung | `::test_need_candidate_*` (v1 bleibt `not_evaluated`), `::test_t17_regression_blocks_silent_replacement` | offline geprüft |
| Registervollständigkeit | Bedarf, Alternativen, Prüfer, Rechte (Tool-Scopes), Modelle, Qualifikationsstand, Einsätze, Abhängigkeiten, Stilllegung aus Konfigurations- und Laufdaten | `::test_need_candidate_*` (Rationale), `::test_retirement_keeps_history_and_blocks_new_runs` | offline geprüft |
| T05 geschütztes Prüfmaterial | Fälle für Produzenten verborgen, Erstellung nur Leitung/Integrität | `::test_t05_*` | offline geprüft |
| T10 Kriterium rückwirkend | Neue Prüfsatzfassung; gescheiterter Lauf bleibt mit Fassung 1 | `::test_t10_*` | offline geprüft |
| A10 neue Rechte | Tool-Scopes ausserhalb der Werkzeugkontrolle werden abgewiesen | `::test_a10_*`, Unit `test_a10_tool_scopes` | offline geprüft |
| A11 Kontextmanifest | Je Prüferaufruf: Hashes von Fall, Rubrik, Ausgabe; keine Selbsteinschätzung | `::test_need_candidate_*` | offline geprüft |

## 2a Befunde der S3-Prüfung (gegen Commit cf4eef4)

| Befund | Umsetzung | Was der Test tatsächlich belegt | Stufe |
| --- | --- | --- | --- |
| Qualifikationsläufe prüften die betriebliche Zulassung des Kandidatenmodells nicht | Vor dem Lauf laufen dieselben harten Regeln wie im Routing (aktiv, erreichbar, Datenklassen, Fähigkeiten, Kontext, Preis, Endpunkt) im Modus `sandbox_experiment`; einzig die fehlende fachliche Qualifikation ist ausgenommen | `test_s3_factory.py::test_candidate_model_needs_operational_admission_even_in_evaluation`: ein deaktiviertes Deployment wird mit `NOT_ENABLED` abgewiesen, `QUALIFICATION_MISSING` erscheint nicht als Grund. Belegt die Regelanwendung, nicht die Richtigkeit der Katalogdaten | offline geprüft |
| Evaluation und Ausführung bauten den Kandidaten unterschiedlich auf | Eine Komposition (`campus_domain/composition.py`) aus registriertem Profil, registrierten Skills mit Anweisungen und Aufgabe; `start_run` und der Qualifikationslauf rufen dieselbe Funktion; der Lauf speichert den `composition_key` | `::test_qualification_binds_profile_skills_model_and_task_class`: Kompositionsschlüssel des Laufs = Profil + Skills + Aufgabenklasse. Belegt, dass dieselbe Funktion mit denselben Dokumenten gerufen wird; nicht, dass echte Modelle sich bei gleicher Komposition gleich verhalten | offline geprüft |
| Einsatzqualifikation nicht an die Kombination gebunden | `QualificationRecord` trägt `profile_ref` und `skill_refs`; der Fabriklauf schreibt die geprüfte Kombination; Routing wertet den Nachweis nur für passende Kombination und Aufgabenklasse (`applies_to_combination`); Betriebskataloge vertrauen allein dem Register, zitierbare Nachweise kommen aus dem Register | derselbe Test: Profil v2 mit Modell A zugelassen, Profil v1 mit Modell A ausgeschlossen, ein profil-ungebundener menschlicher Nachweis gilt für jedes Profil; erfundener Nachweis weiterhin `PROPOSAL_INVALID` | offline geprüft |
| Regressionsvergleich über nicht vergleichbare Prüfsätze | Baseline nur bei gleichem Prüfsatz-Ref **und** identischem `cases_sha256`; sonst keine Baseline (`baseline_comparable=false`) | `::test_t17_regression_blocks_silent_replacement_on_comparable_material` (gleiches Material, schwächere Fassung mit Marker wird nicht aktiviert) und `::test_regression_is_not_judged_across_non_comparable_suites` (neue Fallversion, kein Rückschritt-Urteil). Der Mock-Prüfer erkennt die Schwäche nur über einen Marker; belegt ist der Vergleichsmechanismus, nicht die Urteilsqualität | offline geprüft |

## 2b Weiterhin offene Nachweise

| Nachweis | Was bisher belegt ist | Was offen ist |
| --- | --- | --- |
| Belegdeckungsprüfung (trägt die Passage die Aussage inhaltlich?) | Strukturell: jeder Beleg ist eine existierende, auffindbare Passage; Inhaltlich: ein getrennter Prüfer kann Einwände erheben, die Aussagen werden bestritten (`test_s2_depth.py::test_valid_passage_that_does_not_support_the_claim`). Der Mock-Prüfer erhebt Einwände nur auf Testschalter | Ob ein echtes Prüfmodell eine sachlich unpassende Belegstelle zuverlässig erkennt, ist nicht gezeigt; eine eigene Deckungsprüfung je Evidenzlink (Passage ↔ Aussage) als Prüfaufgabe mit Prüfsatz fehlt |
| Wiederaufnahme nach abruptem Prozessabbruch | Ein Worker wird nach abgeschlossener Aktivität geordnet beendet, neue Worker setzen fort, nichts doppelt (`test_s2_workflow.py::test_interruption_after_generation_loses_no_registered_result`); Aktivitäten liefern bei Wiederholung das registrierte Objekt (`::test_activities_are_idempotent_on_replay`) | Ein harter Abbruch **innerhalb** einer Aktivität (Prozess stirbt zwischen externem Aufruf und Commit) ist nicht getestet; hier greift die Journal-Durabilität (Aufruf vor dem Aufruf committet, Ausgang `pending` bleibt sichtbar), ein Abgleich-Lauf für hängende `pending`-Einträge fehlt |

## 3 Begründete Grenzen

- **Prüferwahl** (offener Punkt aus S1.1, Befund 3): Der Prüfer wird aus den qualifizierten, kanonisch getrennten Deployments nach Anbieterpräferenz und Kennung gewählt. Eine fachlich begründete Wahl des «besten» Prüfers bräuchte vergleichende Nachweise für die Prüfaufgabe selbst; dieser Vergleich ist mit den Prüfsätzen jetzt möglich, aber nicht automatisiert.
- **Keine Builder-Erzeugung durch Modelle**: Kandidaten werden über die API eingereicht (durch Menschen oder Agenten). Ein Builder-Lauf, der Anweisungen und Schemas selbst entwirft, ist ein normaler Erzeugungslauf mit eigenem Vertrag; er fehlt noch.
- **Qualifikationslauf synchron**: Der Lauf ist idempotent protokolliert, aber noch keine Temporal-Aktivität; grosse Prüfsätze gehören in den Ablaufdienst.
- **Qualifikation gilt je Kombination** Profil + Modell + Aufgabenklasse. Ein Modellwechsel überträgt den Nachweis nicht (Kap. 5); das Routing prüft das über die Aufgabenklasse, die Kombination mit dem konkreten Deployment wird noch nicht hart erzwungen.

## 4 Nachweise dieses Stands

Testlauf: 189 Tests grün, 5 Live-Tests übersprungen; Linter ohne Befund; Migrationskette 0001–0006 (0006 bindet Nachweise an die
Kombination); CI-Konstellation mit PostgreSQL- und Temporal-Sidecar. Geprüft gegen Commit cf4eef4; Arbeitsstand ohne lokale Abweichungen
vor den Änderungen dieses Abschnitts.
