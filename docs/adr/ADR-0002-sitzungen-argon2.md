# ADR-0002 Serverseitige Sitzungen und Argon2

Stand: 11.10.2026 · Status: angenommen · Bezug: Architektur Kap. 15, 19 («echter lokaler Administratorlogin mit sicherem Passworthash, serverseitiger Sitzung und CSRF-Schutz; kein vorgegebenes Standardpasswort»)

## Entscheid
Passwörter werden mit Argon2id gehasht. Sitzungen liegen in der Datenbank (Token-Hash, Ablauf, Widerruf); der Browser erhält ein zufälliges Token als HttpOnly-Cookie mit `SameSite=Strict`. Zustandsändernde Aufrufe tragen zusätzlich den CSRF-Wert der Sitzung im Header `X-CSRF-Token`.

## Gründe
- Widerruf einer Sitzung ist sofort wirksam (anders als bei JWT).
- Kein Signaturgeheimnis, das im Betrieb rotiert werden müsste.

## Folgen
- Ein Auth-Port erlaubt später OIDC; die Sitzung bleibt serverseitig.
- Der erste Administrator wird per CLI angelegt; das Passwort kommt aus einer Umgebungsvariable oder einer Eingabe, nie aus dem Code.
