# ADR-0004 Audit doppelt gesichert

Stand: 11.10.2026 · Status: angenommen · Bezug: Architektur Kap. 15 («Agenten haben keine Update-/Delete-Rechte auf diese Historie»), Kap. 16 (Audit + Outbox in derselben Transaktion)

## Entscheid
`audit_events` ist append-only: ein Datenbank-Trigger weist `UPDATE` und `DELETE` ab, **und** die Anwendungsrolle besitzt diese Rechte nicht. Jedes Command schreibt Audit und Outbox in derselben Transaktion wie die fachliche Änderung.

## Grenzen
Ein umfassend privilegierter Betreiber kann die Historie umschreiben. Verkettete Hashes und externe Checkpoints sind spätere Ergänzungen, keine S0-Behauptung.
