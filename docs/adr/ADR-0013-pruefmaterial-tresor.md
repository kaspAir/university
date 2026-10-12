# ADR-0013 Prüfmaterial im Tresor: getrennt verwahrt, nicht nur verborgen

Stand: 12.10.2026 · Status: angenommen · Bezug: Architektur Kap. 7, 13 («Prüfdaten liegen nicht im erzeugenden Arbeitsbereich»), Kap. 15; Verfassung Art. 19; Tests T05, T10

## Entscheid
1. Die Fälle eines Prüfsatzes liegen im Schema **`vault`** (`vault.suite_cases`), erreichbar nur über eine **eigene Verbindung mit der
   Rolle `campus_vault`**. Die Anwendungsrolle `campus_app` hat auf dem Schema kein Recht; die Sandbox erhält gar keine Verbindung.
2. Im Anwendungsschema bleiben nur **Hash und Anzahl** der Fälle (`cases_sha256`, `case_count`). Vor jedem Qualifikationslauf wird das
   Material aus dem Tresor gegen den registrierten Hash geprüft; stimmt es nicht, startet kein Lauf.
3. Das Material ist **append-only**: die Tresorrolle darf lesen und einfügen, nie ändern oder löschen (T10: ein geändertes Kriterium ist
   eine neue Fassung).
4. Ein fehlender Tresor schaltet Anlage und Lauf von Prüfsätzen ab (`CAPABILITY_UNSUPPORTED`); es gibt keinen stillen Rückfall ins
   Anwendungsschema.

## Folgen
- Die Sichtbarkeitsregel T05 (Produzenten sehen nur Existenz, Hash und Anzahl) bleibt; sie ist jetzt zusätzlich durch die Datenbankgrenze
  gestützt (Kap. 15: «Datenbank als zusätzliche Grenze»). Der Eigentümer der Datenbank kann weiterhin alles; die Grenze schützt gegen
  die Anwendungsrolle und gegen Code in der Sandbox, nicht gegen einen privilegierten Betreiber.
- Betrieb: Rolle `campus_vault` anlegen (`docker/postgres-init.sql` für neue Instanzen; bestehende Instanzen von Hand), Migration 0009
  verschiebt den Bestand und vergibt die Rechte, `CAMPUS_VAULT_DATABASE_URL` setzen. In der CI-Konstellation ohne Rollen steht die
  Eigentümerverbindung für den Tresor ein; die Rollen-Grenze wird dort nicht geprüft und der Test weist das als Skip aus.
