# Skill: Security Review

## Zweck

Vor Integration einer Änderung prüfen, ob sie die Sicherheitsbaseline
verschlechtert.

## Checkliste (Masterprompt §18, verbindlich)

Secrets/API-Keys/`.env`, Logs, Frontend, HTTP-Responses, Sessions, CSRF,
Auth, Uploads, Path Traversal, Symlinks, Hardlinks, Zip Slip, Backup,
Export, Temp-Dateien, Rate Limiting, Gateway-Auth,
Credential-Rotation, Tenant-Isolation, SSRF, ungewollte externe Requests,
Debug-Modus, Tracebacks, Fehlermeldungen.

## Vorgehensweise

1. Diff auf die obige Checkliste anwenden – welche Punkte sind
   überhaupt betroffen?
2. Bei jedem betroffenen Punkt: Test schreiben/prüfen, der den
   Sicherheitsmechanismus BEWEIST (nicht nur behauptet).
3. Bei Unsicherheit: Agent H hat Vetorecht – Änderung nicht integrieren,
   bis geklärt.

## Typische Fehler

Sicherheitsmechanismen als „offensichtlich korrekt“ annehmen statt zu
testen (z. B. dass ein Secure-Cookie tatsächlich über die relevante
Origin gesendet wird – siehe bekannter Testfallstrick mit
`http://127.0.0.1` in `.agentic/`-Historie).

## Relevante Dateien

`agents/security/AGENT.md`, `CLAUDE.md` (Grundregeln)
