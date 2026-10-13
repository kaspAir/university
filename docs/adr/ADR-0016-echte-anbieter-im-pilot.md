# ADR-0016 Echte Anbieterzugänge im Pilot: Inbetriebnahme, Probe, Kennzeichnung

Stand: 13.10.2026 · Status: vorgeschlagen (Betriebsfreigaben der Forschungsleitung ausstehend) · Bezug: Architektur Kap. 8, 10, 11, 13, 16;
ADR-0007 (Adapterdisziplin), ADR-0010 (Aufrufnachweise), ADR-0015 (Pilotumgebung); `docs/S1_UMSETZUNG.md` Abschnitt 3 (S1-Tor)

## Entscheid
1. **Zwei echte Anbieter, ein Modell je Anbieter, kein Mock im Betriebskatalog.** Der Betriebskatalog `pilot-live-1` (`purpose: runtime_snapshot`)
   enthält genau die zwei freigegebenen Deployments; die Mock-Deployments bleiben nur in der zurückgezogenen Fassung `pilot-mock-1`. Der Prozess
   gibt mit `CAMPUS_APPROVED_ADAPTERS=anthropic,openai` ausschliesslich echte Adapter frei: ein stiller Rückfall auf Mock-Ausgaben ist damit
   technisch unmöglich (Deployment mit nicht freigegebenem Adapter → `ADAPTER_NOT_APPROVED`, nichts wird simuliert).
2. **Schlüssel nur im Gateway-Prozess.** Die Zugangsdaten stehen ausschliesslich in `~/campus-data/secrets/campus.env` (0600) als
   `CAMPUS_SECRET_PROVIDERS_<NAME>`; der Katalog trägt nur `secret://providers/<name>`. Sie erscheinen weder in Repository, Browser, Logs,
   Chat noch in API-Antworten (`catalog_status` entfernt `secret_ref`). Schlüssel werden über `read -s` eingetragen, nie als Kommandozeilenargument.
3. **Modellkennungen aus der Discovery, Preise aus datierten offiziellen Quellen, Fachcode ohne Modellnamen.** Kennungen kommen über
   `GET /model-catalog/discovery` durch das registrierte Vorlagen-Deployment; die Preisfassung nennt Listenpreis, Quelle, Datum und den
   Umrechnungskurs (Standard 1 USD = 1.00 CHF, bewusst konservativ, aufgerundet auf den Rappen). Kein Modul des Fachcodes kennt ein Modell;
   die Auswahl je Aufgabe trifft der Kontrollkern innerhalb des aktiven Katalogs (Selector-Vorschlag, Kernprüfung, kanonisch getrennter Prüfer).
4. **Betriebsprobe vor Aktivierung.** `campus-admin probe-invocation` macht genau einen kleinen Aufruf durch das Gateway gegen ein Deployment
   des Katalog-Entwurfs: Journal vor dem Senden, Nutzungswerte, effektive Modellkennung, technisch erzwungene strukturierte Ausgabe gegen den
   Vertrag `route_proposal`, Kostenobergrenze aus der gebundenen Preisfassung VOR dem Aufruf. Die Probe ist Betriebsnachweis, keine
   Qualifikation und keine Forschung; ihr Journaleintrag trägt den Marker `RD-PROBE-`, ihre Route wird nicht als Aufgabenroute gespeichert.
5. **Keine erfundenen Qualifikationsnachweise.** Für die echten Modelle wird kein Nachweis eingetragen. Aufträge laufen mit einem
   Kandidatenprofil im Modus `sandbox_experiment` (Art. 26); das Dossier weist das aus. Eine Bootstrap-Prüferfreigabe (`role: reviewer`,
   menschlich, Forschungsleitung) ist ein späterer, eigener Entscheid, der den Modus `qualified` erst ermöglicht.
6. **Kennzeichnung aus Daten.** Die Oberfläche leitet «echte Modellaufrufe» und «Mockbetrieb» aus dem AKTIVEN Katalog ab (Adapter der
   freigegebenen Deployments), nicht aus der Leistungsebene; `/health` leitet die verbleibenden Grenzen aus den freigegebenen Adaptern ab.
   Je Lauf und je Fassung bleibt die Kennzeichnung `synthetic` aus den Antwortblöcken massgebend.
7. **Leistungsebene bleibt `development_mock`.** `restricted_research` setzt den Egress-Proxy voraus (BETRIEB.md), `isolated_execution`
   zusätzlich eine Sandbox-Abnahme auf dem Zielhost. Beides fehlt; die Ebene wird nicht umbenannt, die Grenzen werden ausgewiesen.

## Folgen
- Jeder Aufruf kostet Geld. Die Policy `pilot-policy-2` trägt die von der Forschungsleitung bestätigte gemeinsame Obergrenze als
  `max_task_cost` und `max_project_cost`; Mandats-, Projekt- und Aufgabenbudgets liegen darunter; Reservierungen folgen den Aufgabenvorgaben,
  abgerechnet wird der gemeldete Verbrauch; unbekannter Ausgang bleibt als «offen» gebunden (ADR-0010).
- TEST-01 bleibt synthetisch und unverändert (Mandat, Projekt, Dossier, Fassungen). TEST-02 ist ein eigener Live-Auftrag mit eigenem Mandat.
- Discovery, Preisfassung, Katalogentwurf und Aktivierung laufen über dieselben Commands wie jede Betriebsänderung und sind im Audit sichtbar.

## Verworfen
- Mock und echte Adapter gleichzeitig im Betriebskatalog: hätte den stillen Rückfall auf synthetische Ausgaben möglich gemacht.
- Preise «aus dem Gedächtnis» oder von Drittseiten: nur offizielle Preisseiten mit Datum; sonst bleibt der Preis offen und die Route blockiert (A14).
- Qualifikationsnachweise «der Form halber»: ein Nachweis ohne Prüfsatz ist keiner (T05, T12).
