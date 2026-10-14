# ADR-0017 Auftragsbezogene Quellenerschliessung durch den Campus

Stand: 14.10.2026 · Status: vorgeschlagen (Suchzugang als Betriebsfreigabe ausstehend) · Bezug: Architektur Kap. 6, 10, 12, 14; ADR-0007
(Adapterdisziplin), ADR-0009 (Internetdienst), ADR-0010 (Aufrufnachweise), ADR-0016 (echte Anbieter)

## Entscheid
1. **Der Campus erschliesst seine Quellen selbst, innerhalb des Mandats.** Eine manuell zusammengestellte URL-Liste ist keine
   Startvoraussetzung mehr; eingegebene Adressen sind optionale Startquellen.
2. **Rollenteilung wie überall im Campus.** Das Erzeugermodell der autorisierten Route leitet Teilfragen und Suchanfragen aus Frage,
   Erkenntnisziel und Methodenrahmen ab (Vertrag `search_plan`) und wählt Treffer begründet aus (Vertrag `source_selection`). Der
   Kontrollkern führt die Suche über den angebundenen Suchdienst aus, holt die gewählten Seiten über den Internetdienst (SSRF-Schutz,
   fassungsgenau) und friert die Quellenkarte als Artefakt `source_map` ein. Das Modell liest keine Quelle und behauptet keinen Befund.
3. **Ein Treffer ist kein Beleg.** Vom Modell genannte Adressen oder Literaturangaben gelten erst als erschlossen, wenn der Internetdienst sie
   tatsächlich abgerufen hat und Passagen vorliegen. Nicht abrufbare Seiten, nicht durchsuchbare Fragen (interne Unterlagen, Auskünfte) und
   Teilfragen ohne brauchbaren Treffer stehen als Lücken in der Quellenkarte und im Dossier, nicht als Quellen.
4. **Begrenzte Nachrecherche.** Offene Fragen und Auslassungen der Erzeugung, Einwände der Gegenprüfung und vorgeschlagene Folgeanfragen
   treiben höchstens eine weitere Runde (`max_rounds`, Vorgabe 2) mit neuer Route, Erzeugung und Gegenprüfung; jede Runde ist begrenzt
   (`max_queries`, `max_sources`). Eine übersprungene Erschliessung (kein Dienst, kein Budget) beendet die Schleife, nie den Ablauf.
5. **Kosten im Budget.** Plan- und Auswahlaufruf sind reservierte, geprüfte, journalierte Modellaufrufe; Suchanfragen werden zum
   registrierten Preis je Million Anfragen reserviert und je ausgeführter Anfrage abgerechnet (A14). Ohne Preisfassung verweigert der Campus
   die Erschliessung; ohne Suchdienst meldet er das und arbeitet nur mit eingegebenen Quellen.
6. **Suchdienst als Betriebsfreigabe.** Erster Dienst: Brave Web Search API (fester Endpunkt, Schlüssel nur im Prozess als
   `CAMPUS_SECRET_SEARCH_BRAVE`, Listenpreis USD 5 je 1000 Anfragen mit USD 5 Gratisguthaben je Monat, gelesen 14.10.2026). Einrichtung und
   Preisfassung sind Betriebsentscheide; der Fachcode kennt keinen Dienstnamen.

## Folgen
- Jede Suchanfrage steht im Audit mit Treffern; jede Quellenkarte nennt Teilfragen, Anfragen, Treffer, Wahl mit Begründung, Abgerufenes und
  Lücken. Das Dossier zeigt, welche Teilfragen mit der erschlossenen Basis noch nicht beantwortbar sind.
- Die Erschliessung ist kuratiert durch das Modell, nicht vollständig: eine Websuche über einen Dienst ersetzt weder Fachdatenbanken noch
  Volltexte hinter Zugangsschranken; PDF und andere nicht extrahierbare Typen bleiben Zugangslücken (ADR-0009).
- Der Erzeuger wählt seine eigenen Quellen; die Gegenprüfung sieht dieselben Passagen und die Quellenkarte und kann die Auswahl beanstanden.

## Verworfen
- Websuche über die Anbieter-Werkzeuge der Modelle: Suche und Lesen lägen dann beim Anbieter, ausserhalb von Journal, Budget und SSRF-Schutz.
- Treffer-Snippets als Belege: sie sind Anbieterauszüge, keine gelesenen Quellenfassungen.
