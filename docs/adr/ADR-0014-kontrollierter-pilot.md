# ADR-0014 Kontrollierter Pilot: vorregistriert, verblindet bewertet, nichts behauptet

Stand: 12.10.2026 · Status: angenommen · Bezug: Verfassung Anhang C (Pilotvorschlag), D (Abnahmeplan), F (vor Aufnahme des Pilotbetriebs), Art. 11, 17, 19, 20; Architektur Kap. 20 (S5)

## Entscheid
1. **Vorregistrierung.** Ein Pilot ist ein Protokoll mit Frage, Stufe (Reproduktion vor Anschlussfrage), den drei Bedingungen aus Anhang C,
   Wiederholungszahl, Bewertungsregel, verdecktem Prüfmaterial, gemeinsamem Budget, zugelassenen Eingriffen und den verlangten Plattformtests.
   Sein Hash wird durch eine **vorbehaltene menschliche Entscheidung** (Alternativen, Gründe, Einwände, Folgen) eingefroren. Eine Änderung ist
   eine neue Fassung; nichts wird nachträglich an die Ergebnisse angepasst.
2. **Freigabetor nach Anhang F.** Freigegeben wird nur mit genehmigtem Mandat, benannter Forschungsleitung und Integritätsfunktion, gesetzten
   Zahlenlimits, geschütztem Prüfmaterial, gemeinsamem Budget und einem **importierten Plattform-Testnachweis** (JUnit) für den freigegebenen
   Build, der jede verlangte Testkennung als bestanden zeigt. Das Tor sammelt alle Fehlgründe statt beim ersten zu stoppen. Rollenzusammenlegung
   (Leitung = Integrität) blockiert nicht, wird aber als Selbstkontrolle ausgewiesen.
3. **Gemeinsames Budget.** Alle Projekte aller Arme hängen unter dem Pilotbudget; eine Aufgabe ausserhalb kann keinem Arm zugeordnet werden.
   Das verhindert den versteckten Ressourcenvergleich.
4. **Unabhängige, verblindete Bewertung.** Der Prüfer ist kanonisch verschieden von **jedem** in **irgendeinem** Arm eingesetzten Modell und für
   die Prüfrolle freigegeben. Er sieht Aspekt, Rubrik und die Aussagen, nie Bedingung, Arm, Aufgabe, Projekt, Institut oder Modell; der Kern prüft
   das vor dem Aufruf und bricht sonst ab. Ohne geeignetes Modell bleibt die Bewertung **offen** (kein stilles Siegel, T13).
5. **Bericht aus Daten.** Je Bedingung Fakten (Modelle, Institute, Aufrufe, Tokens, Kosten gesetzt und offen, Laufzeit, Aussagen nach Art,
   Prüfungen, offene Einwände, Experimente, Eingriffe, Bewertungen), dazu die **Unterschiede** zwischen den Bedingungen, die vorregistrierte
   Regel je Arm, der Plattformnachweis **getrennt** von der wissenschaftlichen Bewertung und die Kennzeichnung als Machbarkeitsprüfung, solange
   nicht alle Wiederholungen vorliegen. Der Bericht behauptet nie einen Mehrwert einer Bedingung.
6. **Freigabe bleibt menschlich.** Der eingefrorene Bericht ist ein Artefakt mit Hash; Ergebnisfreigabe und externe Freigabe laufen über den
   bestehenden, fassungsgebundenen Weg (T08). Ein Bericht ohne tragfähige Erkenntnis ist ein zulässiger Abschluss.

## Folgen
- Die Bedingung «Einzelagent mit Selbstprüfung» ist auf dieser Plattform nur mit einer Policy ohne Prüfpflicht herstellbar (Art. 27 wird sonst
  durchgesetzt). Der Bericht weist das als Unterschied aus; die Policy-Entscheidung ist vorbehalten.
- Mock-Modelle erzeugen keine wissenschaftliche Aussagekraft. Alles hier ist Maschinerie; der erste echte Pilot braucht die offenen
  Betriebsentscheide (Anhang F, F03–F08) und zwei echte Anbieter.
- Die Kennungen T01–T17/A01–A14 werden aus Testnamen gelesen; Tests ohne Kennung im Namen zählen nicht als Nachweis. Fehlende Kennungen sind im
  Tor sichtbar und müssen geliefert oder aus dem Umfang genommen werden.
