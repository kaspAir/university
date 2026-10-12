# ADR-0012 Sandbox-Controller: ein Rechenlauf ist kein Shell-Zugang

Stand: 12.10.2026 · Status: angenommen · Bezug: Architektur Kap. 13 (Isolierte Experimente), Kap. 19; Verfassung Art. 9, 14; Tests T04, A10; Hinweis [T7]

## Entscheid
1. Untrusted Code wird ausschliesslich vom **Sandbox-Controller** (`services/sandbox_controller/campus_sandbox`) gestartet. API, Worker,
   Domäne, Infrastruktur und Gateway starten keine Prozesse und werten keinen Code aus; ein statischer Test erzwingt das.
2. Der Runner im Pilot ist **Docker/runc** mit fester Härtung je Lauf: kein Netz, Root-Dateisystem schreibgeschützt, alle Capabilities
   entfernt, `no-new-privileges`, Benutzer 65534, PID-, Speicher- (ohne Swap), CPU- und tmpfs-Limit, Eingaben schreibgeschützt unter
   `/work/in`, Ausgaben nur unter `/work/out`, keine Host-Umgebung, kein Docker-Socket, Zeitlimit durch den Controller-Prozess.
   Das Image ist per Digest festgeschrieben (`name@sha256:…`); ein anderes Image ist ein anderer Sandboxpfad.
3. **Abnahme statt Behauptung.** Der Controller misst mit einer Probe auf dem konkreten Host, ob die Härtung greift (Netz, Dateisystem,
   Benutzer, Socket, Umgebung, Capabilities, PID-Limit, Zeitlimit, Speicherlimit, Ausgabenerfassung, «stdout ist Inhalt»). Nur ein
   bestandener Bericht des messenden Runners für Image und Host kann von der Forschungsleitung abgenommen werden (Art. 11). Ohne gültige
   Abnahme ist die Fähigkeit abgeschaltet: `CAPABILITY_UNSUPPORTED`, kein Ersatz im Prozess, keine Ermahnung zur Vorsicht.
4. **Nachweis der Durchführung ist nicht Nachweis der Richtigkeit.** Der Controller erfasst Start, Ende, Exitcode, Kill-Gründe, Limits,
   Hashes von Eingaben und Ausgaben. `stdout` wird als Inhalt gespeichert und nie als Messwert gelesen. Nur ein **messender** Runner
   (docker) erzeugt `measured=true`; der Mock-Runner führt nichts aus und liefert gekennzeichnete Fixtures.
5. Ein Experiment stützt eine Aussage der Art `computation` nur, wenn der Lauf gemessen und innerhalb aller Limits mit Exit 0 beendet
   wurde. Alles andere wird als `EVIDENCE_NOT_MEASURED` markiert und als Hypothese gespeichert (T04).
6. Die Leistungsebene bleibt `development_mock`: sie beschreibt Internet- und Modellpfad (Egress-Proxy fehlt). Der Sandboxpfad hat sein
   eigenes Tor, die Abnahme. `isolated_execution` wird erst beansprucht, wenn beides auf dem Zielhost steht.

## Folgen
- Auf dem Entwicklungs-Host (Docker Desktop/WSL2, runc) ist die Härtung mit echten Containern gemessen (`tests/sandbox`). Das ist kein
  Nachweis für den Zielhost; dort ist die Probe erneut zu fahren und abzunehmen. Stärkere Isolation (eigene VM, gVisor) ist ein
  Betriebsentscheid; dieser Runner behauptet sie nicht.
- Docker setzt in Containern `ip_unprivileged_port_start=0`; ein «Port 80 binden»-Test sagt nichts über Capabilities. Die Probe prüft
  Capabilities über `setuid(0)`.
- Ein fehlendes Sandbox-Image, ein nicht erreichbarer Daemon oder ein Host-Wechsel schalten die Fähigkeit ab und werden auditiert.
