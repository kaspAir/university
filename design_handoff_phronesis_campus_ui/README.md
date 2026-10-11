# Handoff: Phronesis-Forschungscampus – Oberfläche (Campusübersicht + Forschungsdossier)

Stand 11.10.2026 · Auftraggeber: Kaspar Brönnimann · Grundlage: Forschungsverfassung V0.2 [G1], Systemarchitektur V0.1 (liegt bei: `grundlagen/Phronesis_Campus_Architektur_V0.1.md`)

---

## 1 Überblick

Der Phronesis-Campus ist ein autonomer Forschungscampus mit spezialisierten Agenten, gemeinsamem Gedächtnis und begründeter Mehrmodell-Auswahl. Diese Übergabe beschreibt die **Weboberfläche** für die menschliche Forschungsleitung (Architektur Kap. 18: «Forschung steuern statt Chats beaufsichtigen»).

Ausgearbeitet sind:

1. **Campusübersicht** – Was wartet auf mich? Welche Institute arbeiten? Welche Mittel und Prüfkapazitäten bleiben?
2. **Dossierliste** – alle Forschungsvorhaben, filterbar.
3. **Forschungsdossier** – Frage, Protokoll, Aussagen, Quellen, Läufe, Fassungen, Gegenargumente, Dissens, Modellroute.
4. **Entscheiddialog** – vorbehaltene Entscheide nach Art. 11 mit vier echten Optionen.
5. **Globaler Stopp**.

Noch **nicht** ausgearbeitet (im Prototyp Platzhalter mit Zweckbeschreibung): Agenten- und Skillregister, Modellcockpit, Prüf- und Freigabezentrum, Bibliothek und Abhängigkeiten, Betrieb und Kosten. Sie müssen der hier definierten Bildsprache folgen (siehe §9 «Muster für weitere Ansichten»).

Leitprinzipien der Nutzerin/des Nutzers: **immer schnelle Übersicht, sehr intuitive Bedienung, elegant.**

---

## 2 Über die Designdateien

Die Dateien in `prototype/` sind **Designreferenzen in HTML** – Prototypen, die Aussehen und Verhalten zeigen. Sie sind **kein Produktionscode**. Aufgabe ist, diese Designs in der Zielumgebung neu umzusetzen. Die Architektur gibt vor: **React + TypeScript** im Frontend, REST/OpenAPI unter `/api/v1`, Ereignisstrom (`GET /projects/{id}/events`) mit Polling-Fallback (Kap. 17–19). Das Repository `kaspAir/university` ist derzeit leer; es gibt keine bestehenden Komponenten.

Öffnen: `prototype/Phronesis Campus.dc.html` direkt im Browser (lädt `support.js` und das Design System relativ). `prototype/Campus UI Richtungen.dc.html` zeigt die Designexploration (Varianten 1a–1e); umgesetzt wird **1b** (Kommandozentrale), ergänzt um die Aussagen-Spalten aus **1e** als Tab im Dossier.

Alle Daten im Prototyp sind **Mockdaten** (illustrativ, aus den Grundlagendokumenten abgeleitet). Architektur Kap. 20: Mock-Daten dürfen nie als reale Forschung erscheinen – im echten Build müssen Mockquellen sichtbar gekennzeichnet sein (`development_mock`-Modus, siehe §6.6).

---

## 3 Fidelity

**High-Fidelity.** Farben, Typografie, Abstände, Linienstärken und Interaktionen sind final gemeint. Umsetzung pixelgenau mit dem Design System **Modernist** (Tokens in `design-system/styles.css`, Leitfaden in `design-system/modernist-readme.md`). Die CSS-Datei kann direkt als globale Token-/Basisdatei übernommen werden (z. B. `src/styles/tokens.css`).

Harte Stilregeln des Systems: **0 px Radius überall**, **2 px Trennlinien** zwischen Hauptbereichen, **alles linksbündig** (auch Buttonlabels), **ein Akzent** (Rot), sonst Tinte auf hellem Grund. Keine Schatten ausser im Dialog. Keine Icons im Prototyp; falls Icons nötig, **Lucide**.

---

## 4 Ansichten

Grundraster aller Ansichten: Zweispaltig `grid-template-columns: 216px minmax(0,1fr)`, Höhe mind. `100vh`.

### 4.0 Sidebar (alle Ansichten)

- Breite 216 px, `position: sticky; top: 0; height: 100vh`, rechte Kante `2px solid var(--color-divider)`, Padding oben/unten 18 px, Flex-Spalte.
- **Marke**: «PHRONESIS» + roter Punkt «.» (`--color-accent`). Archivo 800, 18 px, Padding `0 20px 18px`, darunter `2px solid var(--color-divider)`.
- **Navigationseinträge** (Reihenfolge und Zähler):
  | Key | Label | Zähler (Mock) |
  |---|---|---|
  | campus | Campus | – |
  | dossiers | Dossiers | 11 |
  | register | Register | 23 |
  | modelle | Modelle | 3 |
  | pruefung | Prüfung & Freigabe | 3 |
  | bibliothek | Bibliothek | 412 |
  | betrieb | Betrieb & Kosten | – |
  - Jeder Eintrag: Button, volle Breite, Padding `11px 20px`, 14 px, linksbündig, Label links / Zähler rechts (11 px, `--color-neutral-700`), Trennlinie unten `1px solid var(--color-neutral-300)`.
  - Aktiv: Gewicht 800, Text `--color-accent`, Hintergrund `--color-accent-100`. «Dossier»-Detail markiert «Dossiers» aktiv.
  - Hover: Hintergrund `--color-neutral-200`. Fokus: 2px Akzent-Outline (aus Systemstyles).
  - Zähler kommen aus dem Backend (Anzahl offener Einträge je Bereich; bei «Prüfung & Freigabe» = offene vorbehaltene Entscheide).
- **Fuss** (`margin-top:auto`, Padding `16px 20px`, Gap 10 px):
  - «Forschungsleitung» (11 px, `--color-neutral-700`) / Name fett in `--color-text`.
  - Button `btn btn-secondary btn-block`, linksbündig: Label «Stopp – alle Läufe» bzw. «Stopp aktiv».

### 4.1 Campusübersicht (`view = campus`)

Hauptbereich: Padding `26px 32px 48px`, `max-width: 1240px`.

1. **Kopfzeile** – Flex, `space-between`, baseline, wrap.
   - H1 36 px Archivo 800: dynamischer Satz nach Anzahl offener Entscheide:
     - 3 → «Guten Tag. Drei Entscheide warten.» · 2 → «Zwei …» · 1 → «Guten Tag. Ein Entscheid wartet.» · 0 → «Guten Tag. Keine Entscheide offen.»
   - Rechts 13 px `--color-neutral-700`: Datum · Uhrzeit Datenstand · Leistungsebene (z. B. «Sa 11.10.2026 · 14:20 · restricted_research»). Die Leistungsebene (`development_mock` / `restricted_research` / `isolated_execution`, Kap. 13) muss immer sichtbar sein.

2. **Entscheidkarten «Wartet auf mich»** – Grid `repeat(auto-fit, minmax(280px,1fr))`, Gap 16 px, Abstand oben 22 px.
   - Karte: Hintergrund `--color-surface`, Padding 18 px, Flex-Spalte Gap 8 px, **oben 2 px Akzentlinie**.
   - Zeile 1 (11 px): Art in Versalien, `letter-spacing .1em`, `--color-accent` · rechts Fälligkeit `--color-neutral-700`.
   - Titel: Archivo 800, 18 px, `line-height 1.15`, `min-height 42px`.
   - Meta: 12 px, `--color-neutral-700`, lh 1.5 (Institut · Linie · Budget · Laufzeit · Datenklasse / Ziel).
   - Offene Punkte: 12 px `--color-neutral-800`, oben `1px solid divider`, Padding-top 8 px, `flex:1` (damit Buttons unten bündig).
   - Aktionen (offen): `btn btn-primary` «Entscheiden» (flex:1, linksbündig) + `btn btn-secondary` «Dossier».
   - Nach Entscheid: Buttons ersetzt durch Zeile 13 px/600 mit oberer 2 px Tintenlinie: «Entschieden: ‹Option› · Audit AE-‹nr›».
   - Arten: **Mandat** (Mandatsfreigabe, `POST /mandates/{id}/approve`), **Publikation** (externe Freigabe, `POST /publications/{id}/release`), **Auflage fällig** (strukturierte Auflage mit Frist und Zuständigkeit). Sortierung: überfällig zuerst, dann nach Frist.
   - Nur vorbehaltene Entscheide nach Art. 11 erscheinen hier – **keine** Routine-Genehmigungen (Kap. 18: «Normale Recherche und Taskplanung belasten dieses Zentrum nicht»).

3. **Institute** – H6-Label «INSTITUTE» (11–13 px Versalien, 600, `--color-neutral-700`), Abstand oben 30 px.
   - Leiste mit oberer und unterer 2 px Linie; Grid `repeat(auto-fit, minmax(180px,1fr))`; `lang="de"`, `hyphens:auto`, `overflow-wrap:anywhere`.
   - Zelle (klickbar, Button): Padding `14px 14px 14px 0`, `margin-right 14px`, rechte Linie 1 px, `min-height 150px`, Gap 6 px. Hover `--color-neutral-200`.
     - Name 12 px/600, `min-height 32px` · grosse Zahl laufender Tasks 40 px Archivo 800 · «laufend · N warten» 11 px · Budgetbalken (4 px, Grund `--color-neutral-300`, Füllung `--color-text`, an Unterkante) · «Budget X % genutzt» 11 px.
   - Die fünf Institute (Verfassung Anhang A): Grundlagen und formale Modelle · Angewandte Forschung und Entscheidungsprozesse · Recht, Institutionen und Gesellschaft · Ingenieurswesen und experimentelle Systeme · Metawissenschaft, Methoden und Replikation. Institute sind Stammdaten, keine festen Komponenten.
   - Klick → Dossierliste, gefiltert auf dieses Institut.

4. **Zweispaltiger Unterteil** – Grid `repeat(auto-fit, minmax(380px,1fr))`, Gap 32 px, Abstand oben 26 px.
   - **Mittel · Budgetbaum** (Kap. 16): Zeilen Grid `1fr 100px 90px`, 13 px, Padding 8 px, untere 1 px Linie; Einrückung je Ebene 16 px; Gewicht Ebene 0 = 800, 1 = 600, 2 = 400. Balken 4 px; Betrag rechtsbündig, tabellarische Ziffern. Geschützte Anteile (Grundlagen + Replikation, Art. 8) mit **roter** Balkenfüllung. Beträge in CHF, Tausender mit schmalem Leerzeichen («CHF 6 800»). Geldwerte im Backend als Decimal/Ganzzahl.
   - **Prüfkapazität · Modelle**: Zeilen Grid `14px 1fr 1.4fr auto`: Quadrat 10×10 (Tinte = verfügbar, `--color-accent-400` = gedrosselt/gestört, `--color-neutral-400` = nicht nutzbar) · Modellname 600 · Rolle/Eignung 12 px · Statustag `tag tag-neutral` (verfügbar / gedrosselt / veraltet / gesperrt).
   - **Ereignisse heute**: Zeilen Grid `48px 1fr`, 13 px; Uhrzeit tabellarisch grau · Ereignistyp fett (Tinte; **Rot** für ClaimContested, SourceRetracted, IntegrityHold; grau für Budget-/Betriebsereignisse) + « — » + Text. Quelle: Ereignisstrom (`AgentActivated`, `RoutingDecided`, `ClaimContested`, `SourceRetracted` …). Ereignistexte enthalten IDs, nie ungeschützte Volltexte.

### 4.2 Dossierliste (`view = dossiers`)

- H1 «Dossiers» 36 px; rechts Statuszeile 13 px: «N Dossiers · alle Institute» bzw. «Filter: ‹Institut› · N Dossiers».
- Werkzeugzeile (Gap 10 px, Abstand 18 px, wrap): Suchfeld `.input` (max 380 px, Platzhalter «Suche in Titel, Mandat, Aussagen-ID …») + Segmentsteuerung `.seg` «Alle / Laufend / Wartet / Beendet» (filtert Arbeitsstand). *Im Prototyp nur visuell – im Build funktional umsetzen.*
- Tabelle `.table`, Spalten: **ID** (grau, nowrap) · **Titel** (600) · **Institut** (12 px) · **Arbeitsstand** (`tag-neutral`) · **Aussagen** (Kurzform «2 gestützt · 1 bestritten · 1 Hyp.») · **Freigabe** (`tag-neutral` Entwurf/–; `tag-accent` in Prüfung; `tag-outline` freigegeben) · **Mittel** (nowrap, tabellarisch).
- Ganze Zeile klickbar → Dossierdetail. Hover-Tönung aus `.table`.
- Tabellen müssen filter- und exportierbar sein (Kap. 18) und **ohne LLM** funktionieren (A09).

### 4.3 Forschungsdossier (`view = dossier`)

1. **Breadcrumb** 12 px grau: «Dossiers › ‹Institut› › ‹ID›» – «Dossiers» ist Link zurück zur Liste.
2. **Titel** H1 32 px, lh 1.1, max 900 px.
3. **Kontextzeile** 13 px `--color-neutral-800`: Mandat · Linie · federführendes Institut · Kooperation · verantwortliche Person.
4. **Drei Statusachsen** – Grid 3 Spalten mit oberer/unterer 2 px Linie, 1 px Spaltentrenner. Je Zelle: Label 10 px Versalien grau · Wert Archivo 800 `clamp(15px,1.6vw,20px)` · Erläuterung 12 px grau.
   - **Arbeitsstand** (geplant/laufend/wartet/beendet/abgebrochen) + Ablaufschritt (1–8, Kap. 6) + Anzahl Läufe + nächster Berichtspunkt.
   - **Aussagen · epistemisch** (Zählung je Status) + «relativ zu Bewertungsfassung R-x».
   - **Freigabe** (Entwurf/in Prüfung/freigegeben/gesperrt/ersetzt) + Fassung + was blockiert.
   - ⚠ **Diese drei Achsen dürfen nie zu einem Gesamtstatus, einer Ampel oder einem Score verschmolzen werden** (Art. 13, Kap. 5, Kap. 18).
5. **Tabs** – Leiste mit unterer 2 px Linie; Tab-Buttons 14 px, Padding `10px 16px`. Aktiv: Hintergrund `--color-text`, Schrift `--color-bg`, 600. Hover `--color-neutral-200`. Labels mit Zählern: «Frage & Protokoll» · «Aussagen 4» · «Quellen 12» · «Läufe 7» · «Fassungen 2».

**Tab Frage & Protokoll** – Grid `minmax(0,1fr) 300px`, Gap 32 px.
- Links: Label «FRAGE» (rot) · Forschungsfrage 17 px lh 1.45, max 640 px · Protokollzeile 12 px (Protokoll-ID · explorativ/**bestätigend** fett · Rechtsordnung + Stand · «Kriterien fixiert ‹Datum›») mit unterer 2 px Linie · Abschnitt «Gegenargumente und Dissens»: Einwände (Finding) mit ID, Bezug, Urheber-Modell, Argument, dann grau: Tragweite · Behandlung (beheben/zurückweisen/einschränken/eskalieren) · Zuständig · Termin. Dissens wird als **Streitfrage** geführt, nicht aufgelöst.
- Rechts (linke 1 px Linie, Padding-left 24 px): **Letzter Lauf / Routing-Entscheid** (Kap. 9 + 18): Aufgabe · gewähltes Modell (Quadrat + Name + Anbieter) · Eignungsnachweis-ID und Trennungsbegründung · **ausgeschlossene Modelle mit Regelgrund** (z. B. «Erzeuger (I04)», Fehlercode `QUALIFICATION_MISSING`) · Raster: Reserve / Ist-Kosten / Datenklasse / Route-ID. Datenklasse und Qualifikation nie hinter einem grünen Pauschalsymbol verstecken.

**Tab Aussagen** – Spalten nach epistemischem Status: Grid `repeat(auto-fit, minmax(200px,1fr))`, `hyphens:auto`. Spaltenkopf 11 px Versalien in Statusfarbe mit 2 px Unterlinie und Zähler. Karten: `--color-surface`, Padding 12 px, linke 2 px Linie in Statusfarbe; Zeile ID/Aussageart 11 px grau · Aussage 14 px/600 · «Belege · Prüfung» 11 px grau.
- Statusfarben: Hypothese = `--color-neutral-500`, Beobachtung/Ableitung/gestützt = `--color-text`, bestritten/widerlegt = `--color-accent`, ungeklärt = `--color-neutral-500`. Im Build alle Status aus Kap. 5 als Spalten (leere ausblendbar).
- Darunter **Abhängigkeitskette** (obere 2 px Linie): Kästen mit 1 px Rahmen, durch «→» verbunden: Quellen → Aussage → Aussage → Manuskript. Betroffene (bestrittene) Knoten: Rahmen Akzent, Grund `--color-accent-100`, Text `--color-accent-800`. Gesperrte Folgeobjekte: gestrichelter Rahmen. Hinweiszeile 12 px auf **gemeinsame Ursprünge** («… zählen nicht als unabhängige Bestätigung», Kap. 14). Im Build: Graph pro ausgewählter Aussage, nie nur eine unlesbare Gesamtwolke.

**Tab Quellen** – `.table`: ID · Quelle · Rang (Höchstgericht/Kantonal/Materialien/Lehre/Eigene Untersuchung) · Fassung/Zugriff · Status (`tag-neutral` geprüft/reproduzierbar; `tag-accent` Zugangslücke/zurückgezogen) · Stützt (Aussage-IDs). Sekundärzitate und fehlender Volltext bleiben sichtbar.

**Tab Läufe** – `.table`: Lauf · Aufgabe · Agent · Skill-Version · Modell · Ende (Endgrund; `tag-accent` bei Einwand/Fehler) · Kosten. **Alle** Versuche inkl. abgebrochener erscheinen (Art. 16). Endgründe sind getrennt: beendet, Stopp, Suspend, Deadline, Providerfehler, Budgetende, `outcome_unknown` (Kap. 16).

**Tab Fassungen** – Karten-Grid `auto-fit minmax(320px,1fr)`: je Fassung Kopf (Versalien: Version · Hash) · Titel (`.card-title`: Status · Herkunft) · Text zu Sperren/Auflagen/KI-Deklaration · Aktion. «Ergebnisfreigabe» ist `disabled`, solange Auflagen/Einwände blockieren; Label nennt «(gesperrt)». Ersetzte Fassungen bleiben lesbar.

### 4.4 Entscheiddialog

- `.dialog-backdrop` (z-index 10) + `.dialog` mit `width:min(720px,100%)`. Klick auf Backdrop schliesst; Klick im Dialog nicht.
- Kopfzeile 11 px: «‹Art› · VORBEHALTENER ENTSCHEID (ART. 11)» rot · Fälligkeit grau.
- Titel 22 px · Meta 13 px grau.
- Zweispaltig (obere 2 px Linie): **Tragende Gründe** (Liste) | **Restbefunde und Auflagen** (Label `--color-accent-700`, Liste).
- Fusshinweis 12 px grau: «Bindet Fassung ‹version›. Eine geänderte Fassung erzeugt eine neue Freigabefrage (I05). Keine Zustimmung durch Schweigen oder Fristablauf.»
- **Vier gleichrangige Optionen** (Grid 4 Spalten, Gap 8 px, alle linksbündig): `btn-primary` **Annehmen** · `btn-secondary` **Begrenzt annehmen** · **Überarbeiten** · **Ablehnen**. Keine Option vorausgewählt.
- Im Build zusätzlich: «Begrenzt annehmen» und «Überarbeiten» öffnen Pflichtfelder (Auflage mit Zuständigkeit + Frist bzw. Begründung); jede Entscheidung erfordert eine kurze tragende Begründung. Bei Entscheid → `POST` mit Idempotency-Key + erwarteter Objektversion; Erfolg zeigt Audit-ID.

### 4.5 Stopp

- Klick auf Sidebar-Button → `stopped = true`. Oben im Hauptbereich erscheint ein **rotes Vollflächenbanner** (`--color-accent`, Schrift `--color-bg`, Padding `14px 18px`, Abstand unten 22 px): fetter Text «Alle Läufe angehalten. 14 Tasks warten mit Unterbruchgrund «Stopp Forschungsleitung». Register, Suche und Freigaben bleiben bedienbar.» + rechts Button «Wiederaufnahme» (Grund `--color-bg`, Schrift Akzent).
- Im Build: Bestätigung vor dem Stopp (ein Schritt), `POST /runs/{id}/cancel` bzw. ein Workspace-Stop-Command; Stopp muss **ohne LLM** funktionieren (A09) und überall erreichbar bleiben. Wiederaufnahme verlangt dokumentierten Grund (Art. 12).

---

## 5 Interaktionen & Navigation

| Auslöser | Ergebnis |
|---|---|
| Sidebar-Eintrag | Wechsel der Ansicht; Institutsfilter zurückgesetzt |
| Institutszelle (Campus) | Dossierliste mit Filter auf dieses Institut |
| Entscheidkarte «Entscheiden» | Entscheiddialog für diese Karte |
| Entscheidkarte «Dossier» | Dossierdetail |
| Dialog: Option | Entscheid gespeichert, Dialog zu, Karte zeigt «Entschieden: …», H1-Satz zählt neu |
| Dialog: Backdrop | schliesst ohne Entscheid |
| Dossierzeile | Dossierdetail (Tab «Frage & Protokoll») |
| Breadcrumb «Dossiers» | zurück zur Liste |
| Tab | Tabinhalt wechseln |
| Stopp / Wiederaufnahme | Banner ein/aus, Sidebar-Label wechselt |

- Keine Animationen im Prototyp; im Build höchstens kurze Fades (≤150 ms) bei Dialog/Banner. Nichts «schwebt».
- Routing im Build mit echten URLs: `/campus`, `/dossiers?institut=…`, `/dossiers/:id/:tab`, damit Ansichten verlinkbar sind.
- Ladezustände: Skelettzeilen in Tabellen/Karten in `--color-neutral-200`, keine Spinner über ganzen Seiten. Datenstand («Stand 14:20») immer anzeigen.
- Fehlerzustände: Fehlerobjekt der API (Kap. 17: `code, message, trace_id, blocked_action, retryable, required_resolution`) als Block mit oberer 2 px Akzentlinie, Code fett (z. B. `NO_ELIGIBLE_MODEL`), Text, erforderliche Behebung. Kein stilles Fallback.
- Kosten «unbekannt» nie als 0 anzeigen, sondern «offen» (A14).

### Responsives Verhalten
Fluid. Getestet bis ca. 920 px Inhaltsbreite: Karten-, Instituts- und Spalten-Grids brechen per `auto-fit/minmax` um; IDs und Beträge `nowrap`; Tabellen in `overflow-x:auto`-Container; Fliesstext `lang="de"` + `hyphens:auto`. Unter ~760 px Sidebar zu einer oberen Leiste einklappen (noch nicht gestaltet; Stil wie `.nav` aus dem Design System).

---

## 6 Zustand & Daten

### 6.1 UI-Zustand (Prototyp)
```ts
view: 'campus' | 'dossiers' | 'dossier' | 'register' | 'modelle' | 'pruefung' | 'bibliothek' | 'betrieb'
tab: 'frage' | 'aussagen' | 'quellen' | 'laeufe' | 'fassungen'
dialog: number | null          // Index des offenen Entscheids
decided: Record<number, 'accept'|'limited'|'revise'|'reject'>
stopped: boolean
filterInst: string | null
```
Im Build: View/Tab/Filter in die URL; Entscheide und Stopp sind Serverzustand.

### 6.2 Datenquellen je Ansicht (Architektur Kap. 5, 17)
| UI-Element | Objekte | Endpunkt (Vorschlag) |
|---|---|---|
| Entscheidkarten | HumanDecision-Bedarf aus MandateVersion, PublicationPackage, Approval-Auflagen | `GET /decisions?state=open` (neu, aggregiert) |
| Institute | Institute + Task-Zähler + BudgetAccount | `GET /institutes?include=stats` |
| Budgetbaum | BudgetAccount, Reservation, CostEntry | `GET /budget/tree` |
| Prüfkapazität | ModelIdentity, DeploymentVersion (Betriebsstatus) | `GET /model-catalog/status` |
| Ereignisse | AuditEvent / OutboxEvent | `GET /projects/{id}/events` bzw. workspace-weit |
| Dossierliste | ResearchProject + Status je Achse | `GET /projects` |
| Dossier | ResearchProject, PlanRevision, Protocol | `GET /projects/{id}` |
| Aussagen + Kette | ClaimVersion, EvidenceLink, DependencyEdge, Contradiction | `GET /projects/{id}/claims?include=graph` |
| Quellen | SourceVersion, SourcePassage | `GET /projects/{id}/sources` |
| Läufe | AgentRun, RunAttempt, RoutingDecision, LLMInvocation | `GET /projects/{id}/runs` |
| Routing-Panel | RoutingDecision (gewählt, Alternativen, Ausschlussgründe, Belege) | `GET /routing-decisions/{id}` |
| Fassungen | ArtifactVersion, Approval | `GET /projects/{id}/artifacts` |
| Entscheid | — | `POST /mandates/{id}/approve`, `POST /artifacts/{id}/approvals`, `POST /publications/{id}/release` |

Alle schreibenden Aufrufe: `Idempotency-Key` + erwartete Objektversion. Kein generisches `set_status`.

### 6.3 Benennung und IDs
Sichtbare Kurz-IDs: Mandat `M-04`, Projekt `P-07`, Aussage `C-014`, Einwand `F-07`, Dissens `D-02`, Auflage `A-11`, Quelle `Q-03`, Lauf `R-07`, Route `RD-0931`, Audit `AE-1042`. Überall dieselbe Schreibweise; IDs sind klick-/kopierbar.

### 6.4 Sprache und Formate
Deutsch (CH, ohne ß: «gemäss», «Massnahme»), Englisch als zweite Sprache vorbereiten (i18n-Keys). Datum `Sa 11.10.2026`, Uhrzeit 24 h, Währung «CHF 1 200» mit schmalem Leerzeichen, Prozent ohne Leerzeichen «62%». Fachliche Quelltexte bleiben im Original.

### 6.5 Fachliche UI-Regeln (nicht verhandelbar)
1. Drei Statusachsen nie vermischen (§4.3.4).
2. Kein Gesamtscore, kein «Vertrauens-Häkchen».
3. Fehlende Gegenprüfung = «offen», nie «bestanden».
4. Dissens bleibt sichtbar; Mehrheit löscht keinen Einwand.
5. Simuliert/synthetisch/Mock immer als solches kennzeichnen.
6. Freigabe gilt nur für die angezeigte Fassung (Hash sichtbar).
7. Stopp, Tabellen, Suche und Freigaben funktionieren ohne LLM.
8. Personelle Rollenzusammenlegung (Forschung + Kontrolle) wird ausgewiesen.
9. Keine Chat-only-Steuerung; eine spätere Gesprächsansicht muss auf konkrete Registereinträge mit Stichtag verweisen.

### 6.6 Mock-Modus
Solange `development_mock` aktiv ist: Leistungsebene in der Kopfzeile prominent (Tag `tag-accent` «Mockdaten») und jede Mockquelle markiert.

---

## 7 Design Tokens (Modernist)

Quelle der Wahrheit: `design-system/styles.css`. Wichtigste Werte:

**Farben**
- `--color-bg` #f3f2f2 · `--color-surface` #eae9e9 · `--color-text` #201e1d · `--color-accent` #ec3013 · `--color-divider` = #201e1d @ 40 %
- Neutral: 100 #f8f4f4 · 200 #eae7e7 · 300 #d7d3d3 · 400 #bab6b6 · 500 #9b9797 · 600 #7d7979 · 700 #605d5d · 800 #444141 · 900 #2d2b2b
- Akzent: 100 #fff2ef · 200 #ffe0d9 · 300 #ffc4b8 · 400 #ff9783 · 500 #ff563c · 600 #dd2b0f · 700 #ae1800 · 800 #7c1405 · 900 #4d170e
- Rot nur für: primäre Aktion, aktive Navigation, «Wartet auf mich»-Linien, bestrittene/gesperrte Zustände, Stopp-Banner. Fliesstext in Rot immer `--color-accent-700` oder dunkler.

**Typografie** – Archivo (Google Fonts, 400/600/800) für alles.
- H1 36 px (Campus/Listen), 32 px (Dossier) · H6-Labels 13 px Versalien, `letter-spacing .08em`, 600
- Kicker 10–11 px Versalien `letter-spacing .1em`
- Kartentitel 18 px 800 · Kennzahl gross 40 px 800 · Fliesstext 13–15 px, lh 1.5–1.55 · Forschungsfrage 17 px
- Ziffern in Tabellen/Beträgen: `font-variant-numeric: tabular-nums`
- Überschriften `letter-spacing -0.015em`, lh 1.12

**Abstände** – 4 · 8 · 12 · 16 · 24 · 32 px (`--space-1…8`). Seitenrand 32 px, Sektionen 22–30 px, Karten-Padding 18 px.

**Radius** – 0 überall. **Linien** – Hauptbereiche 2 px divider, Zeilen 1 px divider, feine Listen 1 px `--color-neutral-300`. **Schatten** – nur Dialog (`--shadow-lg`).

**Komponentenklassen** (aus styles.css übernehmen oder als React-Komponenten nachbauen): `.btn` (+ `-primary/-secondary/-ghost/-block`), `.tag` (+ `-accent/-neutral/-outline`), `.input`, `.seg`/`.seg-opt`, `.table`, `.dialog-backdrop`/`.dialog`, `.card-title`.

---

## 8 Assets
Keine Bilder, keine Icons im Prototyp. Schrift Archivo via Google Fonts. Falls Icons ergänzt werden: Lucide, 16 px, `currentColor`.

---

## 9 Muster für die noch offenen Ansichten
Gleiches Gerüst (Sidebar + Hauptbereich, H1 36 px, 2 px Sektionslinien), Inhalte nach Architektur Kap. 18:
- **Register**: Tabelle der Agenten/Skills (Status aktiv/ruhend getrennt von Qualifikation ungeprüft/experimentell/qualifiziert) → Detail mit «Warum existiert diese Einheit?» (NeedRecord, Alternativen damals, Prüfer, Einsätze, Kosten, Abhängigkeiten, ggf. RetirementRecord). `GET /inventory`, `/inventory/{id}/rationale`.
- **Modellcockpit**: Katalog (Identität, Zugang, Fähigkeiten unterstützt/nicht/unbekannt, Nachweise, Preisfassung, Betrieb); Ist-Verteilung nach Aufgabenklassen; Konfiguration als Entwurf → Aktivieren (versioniert). Secrets nie anzeigen.
- **Prüfung & Freigabe**: alle vorbehaltenen Entscheide + Prüfaufträge, eingefrorener Prüfgegenstand, Einwände, Auflagen mit Frist.
- **Bibliothek**: Quellen/Passagen mit Status und Datenrechten, ImpactAnalysis bei Rückzug, fällige ReassessmentTasks.
- **Betrieb & Kosten**: Queue, Warte-/Fehlergründe, `outcome_unknown`-Aufrufe, offene Abrechnungen, Modellstörungen.
Wiederverwendbare Bausteine: Entscheidkarte, Kennzahlzelle, Budgetzeile, Modellstatuszeile, Ereigniszeile, Statusachsen-Leiste, Aussagenkarte, Abhängigkeitskette, Routing-Panel.

---

## 10 Dateien
- `prototype/Phronesis Campus.dc.html` – **umzusetzender** klickbarer Prototyp (Markup im Template, Mockdaten + Zustand in der Klasse `Component` am Dateiende).
- `prototype/Campus UI Richtungen.dc.html` – Designexploration 1a–1e (Referenz; 1b gewählt, 1e als Aussagen-Tab übernommen).
- `prototype/support.js`, `prototype/_ds/…` – Laufzeit nur zum Anzeigen der Prototypen; nicht übernehmen.
- `design-system/styles.css`, `design-system/modernist-readme.md` – Tokens und Komponentenregeln.
- `grundlagen/Phronesis_Campus_Architektur_V0.1.md` – Systemarchitektur (Kap. 5 Datenmodell, 17 API, 18 Oberfläche sind für das UI massgeblich).

## 11 Empfohlene Reihenfolge (passend zu Architektur Kap. 20)
1. Token-CSS + Basis-Komponenten (Button, Tag, Table, Dialog, Seg, Input).
2. App-Shell (Sidebar, Routing, Stopp, Leistungsebene).
3. Campusübersicht gegen Mock-API (S0), dann echte Endpunkte.
4. Dossierliste + Dossier mit Tabs (für S2 «ein Forschungsdurchlauf» benötigt).
5. Entscheiddialog mit Begründungs-/Auflagenpflicht und Idempotenz.
6. Offene Ansichten nach §9.
Abnahme UI: A09 (Register + Stopp ohne LLM), Statusachsen getrennt, keine Mockdaten ohne Kennzeichnung.
