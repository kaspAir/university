# ADR-0010 Dauerhafte Aufrufnachweise und Fassungsbindung der Route

Stand: 11.10.2026 · Status: angenommen · Bezug: S1-Review (Commit 7b73171), Architektur Kap. 10, 15, 16

## Entscheid
1. Der Aufrufauftrag (Lauf, Route, Reservierung) wird VOR dem externen Modellaufruf committet; das Aufrufjournal schreibt jeden Eintrag in einer
   eigenen, sofort committeten Transaktion. Verbrauch und Kosten werden unmittelbar nach dem Aufruf committet, bevor der Inhalt fachlich geprüft wird.
   Ein scheiternder Inhaltscheck löscht damit nie den Nachweis eines tatsächlich erfolgten Aufrufs.
2. Reservierungen sind an die konkrete Route gebunden (`routing_decision_id`), nicht nur an die Aufgabe.
3. Eine Route ist an die Katalogfassung gebunden, gegen die sie autorisiert wurde; wechselt der aktive Katalog, ist die Route verbraucht und wird neu bestimmt.
4. Ein ausgeführter Routingentscheid ist verbraucht; ein zweiter Lauf braucht eine neue Route.
5. Nach einem Fehler gilt: bekannter Verbrauch wird abgerechnet; Antwort erhalten (Verweigerung, Zurückweisung) oder Ausgang unbekannt hält die
   Reserve offen; nur ein Fehler vor dem Senden gibt sie frei.
6. Eine Schutzverweigerung des Anbieters sperrt die Aufgabe (`hold_reason`); die Sperre hebt nur ein Mensch mit Begründung auf. Alle Modellaufrufe
   (Auswahl, Erzeugung, Prüfung) laufen durch dieselbe Vorprüfung: Stopp, Mandat, Sperre, autorisierte Route, gebundene Reservierung.
7. Zugangsschlüssel reisen nur an genehmigte Endpunkte: offizielle Adapter nur an ihren offiziellen Endpunkt, `openai_compatible` nur an Hosts aus
   `CAMPUS_APPROVED_API_HOSTS`; Discovery läuft ausschliesslich über registrierte Katalog-Deployments.

## Folgen
- Mehrere Commits je Command; der Idempotency-Key wird erst am Ende gespeichert. Eine Wiederholung eines teilweise ausgeführten Commands wird durch
  die Register abgefangen (verbrauchte Route, vorhandener Lauf), nicht durch den Key.
