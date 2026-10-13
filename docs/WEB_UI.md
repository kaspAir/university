# Erste Oberfläche: serverseitig gerendert, hinter derselben Sitzung

Stand: 12.10.2026 · Grundlage: UI-Handoff (`design_handoff_phronesis_campus_ui/`), Architektur Kap. 18; Vorschlag in `docs/WEB_ERSTE_OBERFLAECHE_VORSCHLAG.md`.

## Was sie ist

Sieben Ansichten unter `/campus`, gerendert im FastAPI-Prozess mit Jinja2, mit dem **unveränderten Token-CSS** des Design Systems Modernist
(`apps/api/campus_api/web/static/tokens.css`) und einem kleinen Layout-CSS nach den Massen des Handoffs (216-px-Sidebar, 2-px-Linien,
0 px Radius, ein Akzent). Kein Build-Schritt, kein Node. Jede Ansicht liest dieselben Dienste wie die JSON-Endpunkte; die zwei Handlungen
(**Stopp/Wiederaufnahme** und **Mandatsentscheid nach Art. 11**) gehen aus dem Browser über die JSON-API mit CSRF-Token und Idempotency-Key.
Es gibt genau einen Schreibpfad.

| Pfad | Ansicht | Zeigt | Handlung |
| --- | --- | --- | --- |
| `/campus/login` | Anmeldung | Workspace (vorbelegt, wenn nur einer existiert), Benutzername, Passwort | Anmelden; Abmelden über die Seitenleiste (CSRF im Formular) |
| `/campus` | Campus | Begrüssungssatz mit Anzahl offener Entscheide, Leistungsebene, Datenstand, Stopp-Banner, Entscheidkarten, fünf Institute mit laufenden/wartenden Aufgaben und Budgetanteil, Budgetbaum, Modelle mit Verfügbarkeit, letzte Ereignisse | Stopp / Wiederaufnahme (Leitung, Integrität), Entscheiden (Leitung) |
| `/campus/dossiers?institut=` | Dossiers | Projekte mit Institut, Arbeitsstand, Aussagen je Status, Freigabe, Mittel | – |
| `/campus/dossiers/{id}?tab=` | Dossier | Drei Statusachsen getrennt; Tabs Frage & Protokoll (Frage, Aufgaben, Einwände, Neubewertungen, Modellroute des letzten Laufs mit Ausschlussgründen), Aussagen (Spalten je epistemischem Status, Flags, Abhängigkeiten), Quellen, Läufe (Erzeuger, Prüfer, Endgrund), Fassungen (Hash, Freigaben) | – |
| `/campus/register`, `/campus/register/{ref}` | Register | Agenten- und Skill-Fassungen, Betriebs- und Qualifikationsstand getrennt; Herkunftsansicht | – |
| `/campus/modelle` | Modelle | Aktiver Katalog ohne Geheimnisse, Fähigkeiten, Datenklassen, Preisbezug, Nachweise | – |
| `/campus/pruefung` | Prüfung & Freigabe | Offene Mandatsentwürfe mit Entscheiddialog (vier gleichrangige Optionen, Gründe und Folgen Pflicht, Auflage mit Zuständigkeit und Frist bei begrenzter Annahme), alle Mandate, protokollierte Entscheide | Entscheiden (Leitung) |
| `/campus/bibliothek` | Bibliothek | Quellen aller Dossiers mit Status | – |
| `/campus/betrieb` | Betrieb & Kosten | Komponenten (`/ready`), Leistungsebene, Sandboxpfad, Aufrufe mit unbekanntem Ausgang, Katalogfassungen, Budgetbaum, Pilotstudien | – |

## Der Bedienweg «Forschungsauftrag» (Stand 13.10.2026)

Vom eigenen Auftrag zum lesbaren, beurteilten Ergebnis in sechs Schritten, jeder Schritt ein bestehender Command, jeder Zustand aus den
Diensten gelesen. Die Seite `/campus/auftrag/{mandatsfassung}` zeigt oben **«Nächster Schritt»** und den Arbeitsstand «n von 6».

| Schritt | Seite | Was der Browser tut | Command |
| --- | --- | --- | --- |
| 1 Mandat | `/campus/auftrag/neu` | Formular (Titel, Frage, Ziel, Methode, Regeln, Budget, Dauer, Berichtspunkte); Datenklasse `public`; verantwortlich = angemeldete Person | `POST /mandates` |
| 2 Entscheid | Auftragsseite | Dialog mit vier gleichrangigen Optionen, Gründe und Folgen Pflicht, Auflage bei begrenzter Annahme | `POST /mandates/{id}/approve` |
| 3 Projekt und Aufgabe | Auftragsseite | Institut, Teilbudget, registriertes Forscherprofil; die Aufgabe erhält die sichtbaren Pilotvorgaben (`web/steps.py`); nicht qualifizierte Profile laufen als `sandbox_experiment` | `POST /projects`, `POST /projects/{id}/tasks` |
| 4 Ablauf | Auftragsseite | Optional öffentliche Quellen; der dauerhafte Ablauf macht Quellen → Route → Erzeugung → Gegenprüfung → Dossier; die Seite liest Status und Stufe aus dem Ablaufdienst | `POST /tasks/{id}/workflow`, `GET /workflows/{id}` |
| 5 Dossier | Auftragsseite | Falls der Ablauf ohne Dossier endete: «Dossier einfrieren» | `POST /projects/{id}/dossier` |
| 6 Ergebnis | `/campus/artefakte/{id}` | Lesbares Dossier (Frage, Aussagen mit Status und Flags, Quellen, Prüfberichte, Einwände, Läufe, KI-Erklärung) und die **Ergebnisfreigabe** an den Hash gebunden | `POST /artifacts/{id}/approvals` |

`/campus/ergebnisse` listet alle eingefrorenen Fassungen mit Herkunft (synthetisch oder Erzeugermodell) und Freigabestand (offen, gilt für
ältere Fassung, Grundlage zurückgezogen, angenommen). Externe Freigabe und Einreichung bleiben ein gesonderter Entscheid über die API.

Was die Seiten **nicht** tun: keinen Schritt auslösen, keinen Zustand erfinden. Fehlt eine Gegenprüfung, steht «Gegenprüfung offen», nie
«bestanden». Im Mockbetrieb trägt jeder Auftrag den Hinweis «synthetischer Funktionsnachweis, keine Forschung».

## Regeln, die die Oberfläche durchsetzt

- Leistungsebene und Datenstand in jeder Ansicht; in `development_mock` zusätzlich der Tag **Mockbetrieb**.
- Läufe und Deployments über den Mock-Adapter und Fassungen mit `synthetic` tragen den Tag **synthetisch**. Mock-Daten erscheinen nie als Forschung.
- Die drei Statusachsen (Arbeitsstand, epistemischer Status, Freigabe) stehen nebeneinander und werden nie zu einem Gesamtstatus verschmolzen.
- Fehlende Gegenprüfung heisst **offen**, nie bestanden. Einwände bleiben sichtbar.
- Kosten «unbekannt» erscheinen als **offen**, nie als 0.
- Rollen: der Entscheid-Knopf erscheint nur für die Forschungsleitung, Stopp nur für Leitung und Integrität; die API prüft dasselbe noch einmal.
- Tabellen, Register, Stopp und Entscheide funktionieren ohne Sprachmodell (A09).
- Keine Fixture-Daten: eine frische Instanz zeigt fünf Institute, dein Konto und leere Zustände.

## Abweichungen vom Handoff und warum

| Handoff | Hier | Grund |
| --- | --- | --- |
| React + TypeScript | Jinja2-Templates im API-Prozess | Node 20 weder lokal noch auf dem Host; kleinster Weg zu einer lauffähigen, geschützten Fassung. Endpunkte sind dieselben; React kann Ansicht für Ansicht übernehmen. |
| Archivo über Google Fonts | kein externer Font-Abruf | Keine Drittanfrage aus dem Browser der Forschungsleitung; Archivo wird genutzt, wenn installiert, sonst Systemschrift. Self-Hosting ist ein Betriebsentscheid. |
| Entscheidkarten für Publikation und Auflagen | nur Mandatsentscheide | Publikationsfreigabe und Auflagen laufen über die API; die Karten folgen, wenn die Fälle entstehen. |
| Ereignisstrom mit Polling | Seitenaufruf | Datenstand steht in jeder Ansicht; Polling ist ein späterer Zusatz. |
| Suche und Segmentfilter in der Dossierliste | Institutsfilter | Suche folgt mit der ersten echten Datenmenge. |

## Prüfen

`tests/integration/test_web_journey.py`: der ganze Bedienweg mit dem nächsten Schritt je Zustand, Leser, Übersicht, Freigabe; Erzeugerrolle ohne Formular. `tests/unit/test_web_steps.py`: die Schrittableitung. `tests/integration/test_web_ui.py`: Seiten ohne Sitzung leiten zur Anmeldung; Anmeldung und Abmeldung (CSRF) funktionieren; eine leere Instanz zeigt
fünf Institute und keine erfundenen Daten; Seiten zeigen reale Daten der Instanz, markieren Mock-Läufe als synthetisch, zeigen T04-Flags und
Prüfer; die Erzeugerrolle sieht weder Entscheid- noch Stopp-Knopf. Was nicht geprüft ist: das Aussehen im Browser (Sichtkontrolle von Hand).

## Starten (lokal)

```bash
set -a; source .env; set +a
.venv/Scripts/python -m uvicorn campus_api.main:app --app-dir apps/api --port 8000
```

Dann `http://localhost:8000/campus`. Workspace und Konto wie in `docs/BETRIEB.md` angelegt.
