# ADR-0003 Geld als Ganzzahl in Minor-Einheiten

Stand: 11.10.2026 · Status: angenommen · Bezug: Architektur Kap. 16 («Geldwerte sind Decimal oder Ganzzahleinheiten, keine binären Floats»), UI-Handoff §4.1

## Entscheid
Beträge werden als `BIGINT` in der kleinsten Einheit der Währung gespeichert (CHF → Rappen), zusammen mit dem ISO-Währungscode. Umrechnungskurse werden je Kostenbuchung protokolliert.

## Folgen
- Keine Rundungsdrift bei Summen über den Budgetbaum.
- Unbekannte Kosten sind ein eigener Zustand (`open`), nie `0`.
