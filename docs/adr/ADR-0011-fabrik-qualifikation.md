# ADR-0011 Fabrik: Vorschlagen, prüfen, aktivieren sind drei Stellen

Stand: 11.10.2026 · Status: angenommen · Bezug: Architektur Kap. 7 (ADR-04 der Architektur), Art. 19, 26, 28, Anhang G; Tests T05, T10, T12, T17, A10, A11

## Entscheid
1. Eine Einheit entsteht nur aus einem registrierten Bedarf (NeedRecord). Das Register entscheidet aus echten Daten über Wiederverwendung.
2. Kandidaten tragen nur Rechte aus der Werkzeugkontrolle; alles andere ist eine Softwareänderung (A10).
3. Prüfmaterial ist versioniert, geschützt und unveränderlich; ein geändertes Kriterium ist eine neue Fassung (T05, T10).
4. Qualifiziert wird je Kombination (Profil **und** exakte Skillmenge, kanonisches Modell, Aufgabenklasse) durch einen kanonisch verschiedenen,
   selbst für die Prüfaufgabe qualifizierten Prüfer auf festem Material. Ein Nachweis trägt die Kombination als Ganzes (`kind=combination`);
   eine andere Skillmenge oder -fassung ist eine andere Kombination. Fehlt ein referenzierter Skill, stoppt der Aufbau vor dem Modellaufruf. Der Prüfer sieht Fall, Rubrik und Ausgabe, nie eine Selbsteinschätzung (A11).
   Der Zertifizierungsregress endet bei einer menschlich eingetragenen Bootstrap-Freigabe (`kind=bootstrap`, nur Forschungsleitung); sie bindet
   ein kanonisches Modell an **eine Prüfrolle** (`reviewer` oder `evaluator`) und eine Aufgabenklasse, weder Profil noch Skills. Sie qualifiziert
   nie einen Erzeuger und nie die andere Prüfrolle; ein Kombinationsnachweis qualifiziert nie einen Prüfer. Die Arten werden nie gemischt (Kap. 7,
   Art. 17). Altbestand ohne Bindung wird bei der Migration als `legacy_unbound` geführt und nie zur Freigabe befördert.
5. Aktiviert wird automatisch durch den Kern, nur bei bestandenem Satz und ohne Rückschritt gegenüber der aktiven Fassung; Aktivierung
   erweitert den Geltungsbereich um genau die geprüfte Aufgabenklasse. Alte Fassungen werden nicht still abgelöst (T17).
6. Jeder Prüfaufruf ist ein regulärer Modellaufruf: Route, gebundene Reservierung, Journal, gemeinsame Vorprüfung, Budget unter einem Projekt.
7. Stilllegung ist ein Datensatz mit Grund und Nachfolger; die Geschichte bleibt auffindbar.

## Folgen
- Ein Kandidat kann sich auf keinem Weg selbst zertifizieren (T12): weder als Prüfer, noch per Nachweisdatensatz, noch per beanspruchtem Zustand.
- Die Herkunftsansicht entsteht aus Registern und Läufen; eine nachträglich erfundene Begründung ist technisch nicht vorgesehen (Art. 26).
