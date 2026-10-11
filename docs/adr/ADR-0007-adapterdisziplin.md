# ADR-0007 Adapterdisziplin im Gateway

Stand: 11.10.2026 · Status: angenommen · Bezug: Architektur Kap. 8, 10, 11; Invarianten I01, I08; Test A01, A08

## Entscheid
1. Provider-Adapter nutzen die offiziellen SDKs, aber ohne deren Wiederholungen (`max_retries=0`) und ohne
   serverseitige Fallback-Parameter. Technische Störungen gehen als typisierte Fehler an den Kontrollkern zurück,
   der unter denselben Regeln neu plant.
2. Eine Schutzverweigerung des Anbieters wird nie durch einen anderen Anbieter umgangen.
3. Strukturierte Ausgabe wird nur verlangt, wenn der Katalog sie als `supported` führt, und dann technisch erzwungen.
   Ein JSON-Textprompt ist kein Ersatz.
4. Geheimnisse werden ausschliesslich im Gateway-Prozess aus Umgebungsvariablen aufgelöst
   (`secret://providers/<name>` → `CAMPUS_SECRET_PROVIDERS_<NAME>`).
5. Nur Adapter in `CAMPUS_APPROVED_ADAPTERS` werden geladen. Ein neuer Adapter ist eine eigene Softwareänderung.
6. Modellkennungen stehen nur im Katalog; Domain und API enthalten keine Provider- oder Modellnamen (statischer Test).

## Gründe
Die SDK-Komfortfunktionen (Retries, Fallbacks) würden die protokollierte Route stillschweigend ändern oder eine
Verweigerung umgehen. Beides widerspricht Art. 27 (kein stiller Rückfall) und der Policy (`hidden_gateway_fallbacks: false`).

## Folgen
- Mehr sichtbare Fehler, dafür jede Modellbindung im Audit nachvollziehbar.
- Rohantworten werden in S1 nicht aufbewahrt; ein geschützter Speicher ist ein späterer Entscheid (Kap. 15).
