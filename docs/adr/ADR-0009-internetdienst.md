# ADR-0009 Internetdienst: offene Quellen, geschlossene Netze

Stand: 11.10.2026 · Status: angenommen · Bezug: Architektur Kap. 12 (ADR-06 der Architektur), OWASP SSRF Prevention, Tests T07, T15, T16

## Entscheid
1. Jede öffentliche Quelle darf ohne Einzelgenehmigung gelesen werden (T15). Es gibt keine Domain-Allowlist; die Policy ist handlungs- und
   datenklassenbasiert.
2. Vor jedem Abruf und vor jeder Weiterleitung: URL-Normalisierung (nur http/https, Ports 80/443, keine Zugangsdaten), Namensauflösung
   und Prüfung aller aufgelösten Adressen; Loopback, private, link-lokale und Metadaten-Adressen, reservierte, Multicast- und
   Carrier-NAT-Bereiche sowie interne Namen werden abgewiesen (T07). Weiterleitungen folgen nicht automatisch.
3. Suchparameter in der URL gelten als Weitergabe: trägt die Aufgabe nicht-öffentliche Datenklassen, ist ein Abruf mit Query verboten (T16).
4. Grössen- und Zeitlimits; nur Text, HTML und JSON werden in S2 extrahiert. Andere Typen sind eine dokumentierte Zugangslücke, keine Quelle.
5. Quellen sind pro Projekt und normalisierter URL identifiziert; jede Fassung trägt Roh- und Text-Hash, Extraktorversion und Weiterleitungskette.
   Passagen sind Zeichenbereiche in der extrahierten Fassung; der Text einer Passage ist aus den Offsets reproduzierbar («Fundstellen stimmen»).
6. Rückzug einer Quelle markiert abhängige Aussagen als neu zu bewerten und erzeugt eine Neubewertungsaufgabe mit Frist und Zuständigkeit
   (Art. 23, T09). Die Software erklärt keine Aussage für falsch.

## Grenzen
- Die Namensauflösung wird vor dem Abruf geprüft; ein DNS-Rebinding zwischen Prüfung und Verbindung bleibt eine Restlücke, bis der Abruf über
  einen eigenen Egress-Proxy mit Verbindungsziel-Prüfung läuft (S4).
- Browser-Unteranfragen, Paketdownloads und Dateikonverter sind nicht Teil von S2.
