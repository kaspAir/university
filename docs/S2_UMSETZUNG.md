# S2 mit S1.1-Härtung – Umsetzung, Befunde, Abnahmepunkte

Stand 11.10.2026 · Baufolge Architektur Kap. 20, Stufe S2: «Mandat → Quelle → Task → Aussage → Review → Dossier, mit Temporal und
wiederaufnehmbarem Lauf.» Abnahme: «Quellenfundstellen stimmen; Unterbruch verliert keine registrierten Ergebnisse; kein erfundener
Messnachweis.» Dazu die Härtungsrunde S1.1 aus dem Review von Commit 7b73171, integriert, aber als getrennte Abnahmepunkte geführt.

**Legende der Nachweisstufen:** *implementiert* = Code vorhanden · *offline geprüft* = durch automatisierte Tests gegen PostgreSQL und
Mock-Adapter belegt · *live nachgewiesen* = gegen echte Anbieter gezeigt. **Live nachgewiesen ist in dieser Stufe nichts**; das Tor zu
zwei echten Modellen bleibt ein Betriebsentscheid (siehe `docs/S1_UMSETZUNG.md`, Abschnitt 3).

## 1 S2: Was gebaut ist

| Baustein | Umsetzung | Bezug | Stufe |
| --- | --- | --- | --- |
| Internetdienst | Kontrollierter Abruf ohne Domainliste: Normalisierung, Namensauflösung mit Adressprüfung vor jedem Hop, keine automatischen Weiterleitungen, Grössen-/Zeitlimit, Typprüfung, isolierte Extraktion (`services_sources.Fetcher`) | Kap. 12, ADR-0009 | offline geprüft (T07, T15, T16) |
| Bibliothek | `sources` je Projekt und normalisierter URL, `source_versions` mit Roh- und Text-Hash, Extraktorversion, Weiterleitungskette; `source_passages` als Zeichenbereiche, Text aus Offsets reproduzierbar (`GET /passages/{id}` meldet `locator_check`) | Kap. 14 | offline geprüft («Fundstellen stimmen») |
| Aussagen | Erzeugungslauf liefert den Vertrag `claim_set` (Repo-Ergänzung); der Kern klassifiziert: Beobachtung/Berechnung ohne echten Beleg wird als Hypothese mit Flag `EVIDENCE_MISSING` gespeichert, erfundene Passagen-IDs mit `INVALID_EVIDENCE_REF`; Evidenzlinks und Abhängigkeitskanten; Status relativ zur Bewertungsfassung | Art. 13–14, T04 | offline geprüft |
| Rückzug | `POST /sources/{id}/retract`: abhängige Aussagen `reassessment_required`, Neubewertungsaufgabe mit Frist und Zuständigkeit; nichts wird gelöscht, keine Aussage wird für falsch erklärt | Art. 23, T09 | offline geprüft |
| Dossier | `GET /projects/{id}/dossier` aus den Registern (drei Statusachsen getrennt, blockierende Einwände/Neubewertungen sichtbar); `POST …/dossier` friert eine Fassung mit Hash ein | Kap. 6, 18, 22 | offline geprüft |
| Freigaben | `POST /artifacts/{id}/approvals` (Ergebnisfreigabe, menschlich, hashgebunden, vier Optionen; uneingeschränkt nur ohne offene Einwände) und `POST /publications/{id}/release` (externe Freigabe, braucht Ergebnisfreigabe genau dieser Fassung; S2 protokolliert ohne Übertragungskanal) | Art. 11, 22; I05, T08 | offline geprüft |
| Dauerhafter Ablauf | Temporal-Workflow `ResearchTaskWorkflow` (Quellen → Route → Erzeugung → Prüfung → Dossier), idempotente Aktivitäten als Dienstprinzipale, Stopp-Signal, Stufe abfragbar, Prüfer-Queue trennbar; `POST /tasks/{id}/workflow`, `GET /workflows/{id}`, `POST /workflows/{id}/stop` | Kap. 6, 16; ADR-0008 | offline geprüft (Unterbruch, Idempotenz, typisiertes Scheitern) |

### S2-Abnahme

| Kriterium | Nachweis |
| --- | --- |
| Quellenfundstellen stimmen | `test_s2_flow.py::test_t15_*` (Offsets reproduzieren den Passagentext, Skript entfernt, Hashes gesetzt) |
| Unterbruch verliert keine registrierten Ergebnisse | `test_s2_workflow.py::test_interruption_after_generation_loses_no_registered_result` (Worker stirbt nach der Erzeugung; neue Worker schliessen ab; genau ein Lauf, eine Prüfung, ein Artefakt) |
| Kein erfundener Messnachweis | `test_s2_flow.py::test_t04_claims_are_classified_by_the_core`, `tests/unit/test_s2_domain.py::test_t04_*` |
| T07 / T15 / T16 | `test_s2_flow.py::test_t07_*`, `::test_t15_*`, `::test_t16_*`, Unit-Tests der Adressklassifikation |
| T08 / T09 | `test_s2_flow.py::test_t08_*`, `::test_t09_*` |

## 2 S1.1: Review-Befunde als Abnahmepunkte

Jeder Befund trägt Umsetzung oder begründete Widerlegung, die Tests und die Nachweisstufe.

| Nr. | Befund (Review 7b73171) | Umsetzung / Widerlegung | Tests | Stufe |
| --- | --- | --- | --- | --- |
| 1 | Nicht jeder Modellaufruf durchläuft den Startschutz (Selector, Prüfung ohne Stopp-Prüfung); ungeklärte Schutzverweigerung bleibt keine Sperre | **Umgesetzt.** `check_invocation` (Stopp, Mandat, Sperre, autorisierte Route, routengebundene Reservierung) vor Selector-, Erzeugungs- und Prüfaufruf; Schutzverweigerung setzt `tasks.hold_reason`; Routen, Selector und Prüfung verweigern bei Sperre (`TASK_HELD`); Aufhebung nur durch Mensch mit Grund (`POST /tasks/{id}/hold/release`). Gilt über die Dienste auch für Workflow-Aktivitäten und Wiederaufnahme. | `test_s1_1_hardening.py::test_stopped_campus_makes_no_selector_or_review_call` (kein Journal-Eintrag), `::test_protected_refusal_holds_the_task_until_a_human_releases_it`, `test_s2_workflow.py::test_background_jobs_obey_the_common_invocation_guard`, `tests/unit/test_s2_domain.py::test_every_model_call_shares_the_same_guard` | offline geprüft |
| 2 | Aufruf- und Kostennachweise können bei späterem Inhaltsfehler zurückgerollt werden; Freigabe der Reserve pauschal «nichts verbraucht» | **Umgesetzt.** Aufrufauftrag und Reservierung werden vor dem Aufruf committet; Journal schreibt in eigenen Transaktionen; Verbrauch wird direkt nach dem Aufruf committet; Verweigerung trägt Verbrauch und wird abgerechnet; Antwort erhalten oder Ausgang unbekannt hält die Reserve offen; nur Fehler vor dem Senden gibt frei (ADR-0010) | `::test_invalid_review_content_keeps_call_and_cost_evidence` (Aufruf completed, Reserve settled, kein Bericht), `::test_selector_call_evidence_survives_rejected_proposal`, `::test_refusal_settles_usage_instead_of_releasing` | offline geprüft |
| 3 | Selector ohne Inhalte der Eignungsnachweise und Kostenvergleich; Nachweis nicht an Aufgabenklasse gebunden | **Umgesetzt.** Selector erhält je Kandidat die aufgelösten Nachweise (Aufgabenklasse, Prüfsatz, Ergebnis, Prüferart, Modellbezug), Kostenschätzung und die Angabe `evidence_basis` (verifiziert oder nicht); Systemregel verbietet behauptete Überlegenheit ohne Nachweis. Bei Betriebskatalogen und qualifizierten Aufgaben sind Deployments ohne bestandenen Nachweis **für diese Aufgabenklasse** ausgeschlossen (`QUALIFICATION_NOT_FOR_TASK_CLASS`). Die Entscheidnotiz vermerkt fehlende Nachweise je Kandidat. **Offen (begründet):** die Prüferwahl bleibt eine Startregel (anderer Anbieter bevorzugt, dann Kennung); eine fachlich begründete Prüferwahl braucht Nachweise für die Prüfaufgabe, die erst S3 erzeugt. | `::test_runtime_catalog_binds_qualification_to_task_class`, `::test_claimed_qualification_refs_must_resolve_at_registration`, `test_s1_providers_review.py::test_llm_selector_proposes_and_core_authorizes` | offline geprüft; Qualität der Begründung erst live beurteilbar |
| 4 | Widersprüchlicher Prüfbericht (no_objection + blockierender Befund) schliesst die Aufgabe ab | **Umgesetzt.** Konsistenzregel `review_consistency`; Widerspruch wird gespeichert (`consistent=false`, Ereignis `ReviewInconsistent`), nicht zugunsten des milderen Felds ausgelegt; `finished` nur bei no_objection, konsistent und ohne blockierenden Einwand; Dossier-Freigabe uneingeschränkt nur ohne offene Einwände | `::test_contradictory_review_does_not_finish_the_task`, `tests/unit/test_s2_domain.py::test_contradictory_review_report_is_detected`, `test_s2_flow.py::test_open_blocking_findings_prevent_unconditional_approval` | offline geprüft |
| 5 | Discovery nimmt Adapter, Schlüsselreferenz und freie `api_base` entgegen: Schlüsselweitergabe an falsches Ziel möglich | **Umgesetzt.** Discovery nur über registrierte Katalog-Deployments (`?deployment_id=`; CLI `probe-provider --deployment-id`); offizielle Adapter senden Schlüssel nur an den offiziellen Endpunkt, `openai_compatible` nur an Hosts aus `CAMPUS_APPROVED_API_HOSTS` (https); geprüft beim Katalogimport, im Gateway und im Adapter-Client | `::test_catalog_import_rejects_unapproved_endpoint_for_official_adapter`, `::test_discovery_goes_through_registered_deployments_only`, `test_provider_adapters.py::test_official_adapter_refuses_foreign_endpoint_before_building_a_client`, Unit `test_keys_only_travel_to_approved_endpoints` | offline geprüft |
| S0-a | Fassungsbindung der Route; Reservierungen aufgabenbezogen statt routenbezogen | **Umgesetzt.** Route trägt `catalog_revision`; Lauf und Prüfung verweigern bei anderem aktivem Katalog; Reservierungen tragen `routing_decision_id` und werden darüber gesucht; ausgeführter Entscheid ist verbraucht | `::test_route_is_bound_to_its_catalog_revision`, `test_s0_flow.py::test_mock_run_settles_cost_and_leaves_evidence` | offline geprüft |
| S0-b | Qualifikationsnachweise nur Referenzen; Agenten/Skills beanspruchen Zustände ohne Auflösung | **Umgesetzt.** Registrierung löst beanspruchte `qualification_refs` gegen bestandene Nachweise auf; Betriebskataloge prüfen Existenz, Modellbezug, Ergebnis und (bei Routing) Aufgabenklasse | `::test_claimed_qualification_refs_must_resolve_at_registration`, `test_s1_providers_review.py::test_runtime_catalog_needs_resolvable_qualification_evidence` | offline geprüft |
| S0-c | Elternaufgabe muss nicht zum Projekt gehören, Budget wird übernommen | **Umgesetzt.** Elternaufgabe muss zum selben Projekt gehören; sonst `VALIDATION_ERROR` | `::test_parent_task_must_belong_to_the_same_project` | offline geprüft |
| S0-d | Journal schreibt in der offenen Transaktion | **Umgesetzt.** Siehe Befund 2 (eigene Transaktionen je Journaleintrag) | wie Befund 2 | offline geprüft |
| + | Idempotenz (nicht ausdrücklich genannt) | Aktivitäten liefern bei Wiederholung das registrierte Objekt; ausgeführte Route verbraucht; Idempotency-Key auf API-Ebene | `test_s2_workflow.py::test_activities_are_idempotent_on_replay`, `test_s0_flow.py::test_mandate_flow_versions_and_idempotency` | offline geprüft |
| + | Projekt-/Mandats-/Budgetbindung (nicht ausdrücklich genannt) | Projekt nur mit freigegebenem Mandat; Aufgabenbudget als Kind des Projektbudgets; Reservierung an Route; Eltern im selben Projekt | `test_s0_flow.py::test_t02_*`, `::test_t03_*`, `::test_a05_*`, `::test_parent_task_must_belong_to_the_same_project` | offline geprüft |

## 2a Vertiefungstests (Nachfrage nach dem Review)

Jeder Test trennt drei Fragen, die nie verschmolzen werden dürfen (Art. 13): **struktureller Nachweis** (existiert ein echter,
auffindbarer Beleg?), **inhaltliche Bewertung** (was sagt der getrennte Prüfer, und bleibt Dissens als Dissens stehen?) und
**aktuelle Freigabefähigkeit** (darf genau diese Dossierfassung jetzt hinaus?).

| Fall | Struktur | Inhalt | Freigabe | Test |
| --- | --- | --- | --- | --- |
| Gültige, aber sachlich unpassende Belegstelle | Evidenzlink echt und auffindbar, keine strukturellen Flags | Prüfer erhebt Einwand; die Aussagen des Laufs werden «bestritten» relativ zur Prüfung, nichts wird widerlegt | Freigabeachse blockiert, uneingeschränkte Freigabe verweigert | `test_s2_depth.py::test_valid_passage_that_does_not_support_the_claim` |
| Ausfall innerhalb der Erzeugungsaktivität | Gescheiterter Versuch bleibt mit Endgrund `provider_error`; Neuplanung schliesst das Deployment aus | Prüfung des gelungenen Versuchs ohne Einwand | Entwurf freigebbar | `::test_provider_failure_inside_generation_is_replanned_by_the_workflow` |
| Quelle → Aussage A → Aussage B | A mit Passagenbeleg, B ohne eigenen Beleg als markierte Hypothese, Abhängigkeitskante A→B | Saubere Prüfung lässt A beobachtet und B Hypothese; kein Konsens macht «gestützt» | Nach Rückzug der Quelle sind A und (über die Kante) B neu zu bewerten, Freigabe blockiert | `::test_chain_source_claim_a_claim_b` |
| Rückzug nach erteilter Dossierfreigabe | Fassung 1 bleibt mit Hash und Freigabedatensatz erhalten | Aussagen zur Neubewertung markiert, nicht für falsch erklärt | Fassung 1 darf nicht mehr hinaus (`basis_withdrawn_after_approval`), Fassung 2 ist blockiert und unfreigegeben | `::test_retraction_after_dossier_approval_invalidates_release` |

Dafür ergänzt: Einwände markieren die Aussagen des Laufs als bestritten; der Rückzug folgt den Abhängigkeitskanten; eine externe
Freigabe scheitert, wenn nach der Ergebnisfreigabe eine Grundlage zurückgezogen wurde; der Workflow plant bei Providerausfall
innerhalb der Erzeugung bis zu dreimal neu statt blind zu wiederholen.

**Betriebsbeschränkung DNS-Rebinding:** siehe `docs/BETRIEB.md`, Abschnitt «Betriebsbeschränkung Internetdienst» (Ausgangsfilter im Netz,
Leistungsebene bleibt `development_mock`, Inhalte sind Daten).

## 3 Nicht behauptet

- Nichts in S0–S2 ist **live nachgewiesen**: alle Modellaufrufe liefen über den Mock-Adapter. Die Live-Tests (`tests/live`) bleiben übersprungen,
  bis zwei echte Anbieter mit Datenfreigabe, Preis und Nachweis registriert sind.
- Temporal läuft in Entwicklung und Test als In-Memory-Entwicklungsserver; Produktion braucht eine persistente Installation (ADR-0008).
- Der Internetdienst prüft die Namensauflösung vor der Verbindung; ein DNS-Rebinding zwischen Prüfung und Verbindung bleibt eine Restlücke bis zum
  Egress-Proxy (ADR-0009). PDF und andere Typen sind Zugangslücken.
- Die externe Freigabe protokolliert; kein Kanal überträgt etwas.
- Weboberfläche und Deploy: unverändert offen (Lücken L1, L2 aus `docs/S0_VORSCHLAG.md`).

## 4 Nachweise dieses Stands

Testlauf: 167 Tests grün, 5 Live-Tests übersprungen; Linter ohne Befund; Migrationskette 0001–0004 auf, ab und wieder auf geprüft;
CI-Konstellation (PostgreSQL-Sidecar, Python-3.12-Container, Temporal-Entwicklungsserver) lokal durchgespielt.

## 5 Nächster Schritt

Vor S3 (Fabrik, Qualifikationsläufe, automatische Aktivierung) steht der Betriebsentscheid: zwei echte Anbieter registrieren und die Live-Tests
fahren. Erst dann wird aus «offline geprüft» ein «live nachgewiesen» für S1 und S2.
