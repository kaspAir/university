# ADR-0008 Temporal als Ablaufdienst, Aktivitäten idempotent

Stand: 11.10.2026 · Status: angenommen · Bezug: Architektur Kap. 6, 16, 19 (ADR-02 der Architektur), Abnahme S2 («Unterbruch verliert keine registrierten Ergebnisse»)

## Entscheid
1. Der Forschungsablauf einer Aufgabe (Quellen → Route → Erzeugung → Gegenprüfung → Dossier) läuft als Temporal-Workflow
   (`workers/research/campus_workers`). Der Workflow hält nur Reihenfolge, Stopp-Signal und Stufe; jeder externe Effekt ist eine Aktivität.
2. Aktivitäten sind idempotent gegen die Register: eine wiederholte Aktivität liefert das registrierte Ergebnis (bestehender Lauf, bestehender
   Prüfbericht) statt eines zweiten. Nicht wiederholbare Campus-Fehler werden als nicht wiederholbare Anwendungsfehler mit dem Fehlercode als Typ
   gemeldet; der Workflow scheitert typisiert statt still weiterzulaufen.
3. Aktivitäten laufen als Dienstprinzipale mit getrennten Rollen (`sp:workflow-producer`, `sp:workflow-reviewer`); die Gegenprüfung kann über
   eine eigene Task-Queue von einer getrennten Worker-Instanz ausgeführt werden (Art. 17).
4. Die gemeinsame Aufrufprüfung (Stopp, Mandat, Sperre, Route, Reservierung) sitzt in den Diensten selbst und gilt damit für API-Aufrufe,
   Hintergrundaufträge und Wiederaufnahme gleichermaßen.
5. Entwicklung und Tests nutzen den Temporal-Entwicklungsserver (`docker compose up -d temporal` bzw. der vom SDK gestartete lokale Server in
   den Tests). Produktion braucht eine persistente Temporal-Installation; Zielhost offen (Lücke L2).

## Folgen
- Ein Workerausfall zwischen zwei Aktivitäten verliert nichts: Temporal spielt abgeschlossene Aktivitätsergebnisse wieder ein, der Kern liefert
  bei Wiederholung dasselbe Objekt (Test `test_interruption_after_generation_loses_no_registered_result`).
- Die Tests laden den Temporal-Entwicklungsserver beim ersten Lauf herunter; die CI braucht dafür Internetzugang.
