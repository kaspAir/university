# ADR-0005 Idempotenz und Versionsprüfung

Stand: 11.10.2026 · Status: angenommen · Bezug: Architektur Kap. 16–17

## Entscheid
Schreibende Aufrufe verlangen `Idempotency-Key`. Je `(Akteur, Command, Key)` wird der Hash der Anfrage und die Antwort gespeichert. Wiederholung mit gleichem Hash liefert die gespeicherte Antwort; abweichender Hash unter gleichem Key ergibt `IDEMPOTENCY_MISMATCH` (409). Zustandsändernde Aufrufe auf versionierte Objekte tragen `expected_version`; Abweichung ergibt `VERSION_CONFLICT` (409).

## Folgen
- Technische Wiederholungen erzeugen keine zweiten Fachobjekte.
- Ein unbekannter Ausgang bleibt `OUTCOME_UNKNOWN` und wird nicht als «nie ausgeführt» behandelt.
