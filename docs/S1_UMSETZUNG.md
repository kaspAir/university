# S1 – Modellgateway und Routing: Umsetzung und Stand

Stand 11.10.2026 · Baufolge Architektur Kap. 20, Stufe S1: «Mindestens zwei echte zulässige Modellzugänge; konfigurierbarer
Katalog, Selector, Route und getrennte Gegenprüfung.» Abnahme: «Kein Modellname im Domaincode. Datenfreigabe, Preisbindung,
Aliasprüfung und Ausfallverhalten werden getestet.»

## 1 Was gebaut ist

| Baustein | Umsetzung | Verfassung / Architektur |
| --- | --- | --- |
| Provider-Adapter | `services/llm_gateway/campus_gateway/adapters/`: Anthropic (offizielles SDK, Messages API) und OpenAI (offizielles SDK, Chat Completions; auch `openai_compatible` mit `api_base`). Modellkennung immer aus dem Katalog. Keine SDK-Wiederholungen (`max_retries=0`), keine serverseitigen Fallbacks, strukturierte Ausgabe nur technisch erzwungen (`output_config.format` bzw. `response_format json_schema strict`). | Kap. 11, I01, I08, A08 |
| Freigabe von Adaptern | Nur Adapter in `CAMPUS_APPROVED_ADAPTERS` (Standard `mock`) werden geladen; alles andere ist `ADAPTER_NOT_APPROVED`, nichts wird simuliert. | A01 |
| Geheimnisse | `secret://providers/<name>` → `CAMPUS_SECRET_PROVIDERS_<NAME>`, aufgelöst nur im Gateway-Prozess; nie im Katalogexport, nie im Prompt. | Kap. 8, 15 |
| Ausfallverhalten | Typisierte Fehlerklassen (Timeout = Ausgang unbekannt, nicht verfügbar = neu planen, Zurückweisung = Konfiguration, Schutzverweigerung = kein Anbieterwechsel). Nach Providerfehler schliesst die Neuplanung das gescheiterte Deployment aus (`EXCLUDED:FAILED_IN_ATTEMPT:<Lauf>`) und zählt gegen `max_route_replans_per_task`. | Kap. 10, 16 |
| Selector | Kontrollkern wählt den Selector in fester Reihenfolge aus `bootstrap_selector_deployment_ids`; ein Aufruf je Versuch; Eingabe nur Metadaten und zulässige Kandidaten; Antwort muss dem Vertrag `route_proposal` genügen und wird vom Kern geprüft (A02 gilt auch für den Selector). Selector-Route und -Reservierung werden registriert. | Kap. 9–10 |
| Gegenprüfung | `POST /runs/{id}/review`: Prüfmodell kanonisch verschieden vom Erzeuger und von allen Mitwirkenden; erhält das eingefrorene Artefakt (Hash) und den Aufgabenkontext, keine Erzeugerbewertung; Vertrag `review_report` (neu, Repo-Ergänzung); Einwände als `findings` mit Status `open`; Mehrheit löscht nichts; zweite Prüfung desselben Artefakts verweigert. | Art. 17–18, 27; I04, I05, T14 |
| Aktivierungstor Katalog | Aktivieren scheitert, wenn ein aktives Deployment keine registrierte Preisfassung oder keine genehmigte Datenfreigabe (Provider, Datenklassen) hat; `runtime_snapshot`-Kataloge brauchen auflösbare, bestandene Qualifikationsnachweise des richtigen kanonischen Modells. | Kap. 8, 12 |
| Datenfreigabe | `POST /processing-profiles` durch die Forschungsleitung (Schutzstandard, Art. 11): Provider, Region, erlaubte Datenklassen, Begründung. | Kap. 12 |
| Qualifikationsnachweise | `POST /qualifications`: Modell, Aufgabenklasse, Prüfsatz, Ergebnis, Prüfer; ein Modell kann sich nicht selbst qualifizieren. | Art. 19, 26; T12 |
| Discovery | `GET /model-catalog/discovery` und CLI `probe-provider`: native Modellkennungen, keine Freigabe, keine Qualifikation. | Kap. 8 |
| Artefakte | Jeder beendete Lauf friert seine Ausgabe als `artifact_versions` mit SHA-256 ein; Mock-Ausgaben sind als synthetisch markiert. | Kap. 5, 13 |

## 2 Nachweise

| Abnahmepunkt | Nachweis |
| --- | --- |
| Kein Modellname im Domain- und API-Code | `tests/security/test_static_boundaries.py` (Provider- und Modellnamen nur unter `services/llm_gateway/adapters`) |
| Datenfreigabe | `test_s1_providers_review.py::test_catalog_activation_refuses_without_data_authorization`, `::test_operator_cannot_approve_data_processing` |
| Preisbindung | dieselbe Aktivierungsprüfung; `test_s0_flow.py::test_a14_*` |
| Aliasprüfung | `tests/unit/test_routing.py::test_a03_*` (Alias desselben kanonischen Modells ist keine Prüfvielfalt) |
| Ausfallverhalten | `test_gateway_failures.py` (Mapping), `test_s1_providers_review.py::test_failed_deployment_is_excluded_on_replan`, `::test_replan_limit_is_enforced`, `::test_refusal_ends_run_without_rerouting` |
| Selector durch Kern geprüft | `::test_llm_selector_proposes_and_core_authorizes`, `::test_selector_pool_empty_means_wait` |
| Getrennte Gegenprüfung | `::test_review_by_distinct_model_closes_or_contests`, `::test_review_requires_a_finished_run` |
| Adapterverträge ohne Netz | `tests/adapter_contracts/test_provider_adapters.py` (SDK-Clients durch Fakes ersetzt: Anfrageform, Antwort-Mapping, Fehlerklassen, Verweigerung) |

Testlauf: 113 Tests grün, 5 übersprungen (Live-Tests ohne Schlüssel), Linter ohne Befund.

## 3 Das Tor zu S1 ist noch nicht durchschritten

Das Paket sagt: «Two real approved models are a gate, not merely two mock labels.» Dieses Tor ist ein **Betriebsentscheid**
der Forschungsleitung und wurde hier bewusst nicht durch Code ersetzt. Alle Tests oben laufen mit Mock-Deployments.

Für das Tor sind nötig, je Anbieter (zwei verschiedene):
1. Vertrag und Schlüssel; Schlüssel als `CAMPUS_SECRET_PROVIDERS_<NAME>` auf dem Gateway-Host, nie im Repo.
2. Datenfreigabe: `POST /processing-profiles` mit Region und erlaubten Datenklassen (Forschungsleitung).
3. Preisfassung: `POST /pricing` mit datierter Quellenangabe (Betrieb).
4. Discovery: `campus-admin probe-provider --adapter <name> --secret-ref secret://providers/<name>` liefert die nativen
   Modellkennungen. Eine davon kommt als `provider_model_id` in den Katalog; `canonical_model_id` und `shared_base_id`
   sind von Hand zu setzen (Herkunft, Aliase).
5. Katalog mit `purpose: runtime_snapshot`, Adapter in `CAMPUS_APPROVED_ADAPTERS`, Fähigkeiten als
   `supported/unsupported/unknown` (unbekannt = ausgeschlossen), Qualifikationsnachweise für die Aufgabenklasse,
   dann `activate`.
6. Live-Nachweis: `CAMPUS_LIVE_TESTS=1` plus Modellkennungen → `pytest tests/live` (kostet Geld; prüft Discovery,
   strukturierte Ausgabe, Nutzungswerte, effektive Modellkennung, kanonische Verschiedenheit).

Erst wenn 1–6 für zwei Anbieter dokumentiert sind, gilt S1 als abgenommen. Bis dahin steht die Leistungsebene auf
`development_mock`.

**Stand 13.10.2026:** Der Weg durch das Tor ist gebaut und dokumentiert in `docs/BETRIEB_SERVER.md`, Abschnitt «Echte Anbieter in Betrieb
nehmen», mit ADR-0016: Schlüssel nur im Gateway-Prozess, Discovery über das Vorlagen-Deployment (`ops/server/live_setup.py discover`),
Preis- und Datenfreigaben plus Katalog-/Policy-Entwurf (`prepare`), Betriebsprobe je Deployment mit Journal und Kostenobergrenze
(`campus-admin probe-invocation`), Aktivierung (`activate`). Die Punkte 1–6 sind erst erfüllt, wenn die Proben auf dem Zielserver im
Aufrufjournal stehen; die Leistungsebene bleibt auch dann `development_mock` (Egress-Proxy und Sandbox fehlen).

## 4 Entscheide

- **ADR-0007** Adapterdisziplin: keine SDK-Wiederholungen, keine serverseitigen Fallbacks, Geheimnisse nur im Gateway,
  freigegebene Adapter per Liste.
- Der Prüfvertrag `review_report` ist eine Repo-Ergänzung zu den sechs Paketschemas (Datei trägt den Hinweis).
- Ein Routingentscheid ist nach einem Lauf verbraucht; ein zweiter Lauf braucht eine neue Route (kein versteckter Retry).
- Die S0-Basis-Migration ist auf ihre 24 Tabellen festgenagelt; neue Tabellen kommen nur über neue Revisionen.

## 5 Offen für S2

Temporal-Workflows (Lauf und Prüfung asynchron statt synchron), Quellenabruf über den Internetdienst, Aussagen und Evidenz,
Dossier. Die Weboberfläche bleibt an Node 20+ gebunden (Lücke L1 aus dem S0-Vorschlag).
