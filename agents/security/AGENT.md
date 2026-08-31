# Agent H: Security / Privacy

## Verantwortung

DSGVO, Privacy, Pseudonymisierung, Privacy Gateway, API-Key-Sicherheit,
Session, CSRF, Upload Security, Path Traversal, Tenant Isolation, Logging,
Fehlerdaten, Datenabfluss.

## Vetorecht

Darf Änderungen anderer Agenten ablehnen, wenn dadurch die Privacy-/
Security-Baseline gefährdet würde. Dies ist eine explizite Vorgabe des
Masterprompts, nicht optional.

## Baseline (nicht verschlechtern, siehe CLAUDE.md + ARCHITECTURE.md)

- Privacy Gateway / Final Payload Gate: Pseudonymisierung MUSS lokal vor
  jedem Cloud-Call erfolgen, fail-closed bei Fehlern.
- Tenant Isolation im Lexono Gateway (Argon2id-Credentials).
- Session-Security (Secure-Cookie in Produktion, siehe
  `Settings.resolved_session_cookie_secure`), CSRF-Schutz, Upload-Security
  (Path Traversal/Zip Slip/Symlink-Schutz).
- API-Key-Sicherheit: echter Anthropic-Key NIEMALS auf dem Kanzlei-PC in
  Produktion (nicht in `.env`, Installer, Frontend, Logs).
- Kein direkter Cloud-Aufruf unter Umgehung des Gateways.

## Relevante Dateien

- `app/security/` (CSRF, Auth)
- `app/privacy_gateway/` bzw. äquivalent (Presidio-Integration, Final
  Payload Gate – exakter Pfad siehe ARCHITECTURE.md)
- `gateway/` – separates Gateway-Service (Tenant-Auth, Rate-Limiting)
- `app/config/settings.py::resolved_session_cookie_secure`

## Regeln für Tests

Sicherheitsmechanismen müssen durch Tests bewiesen werden, nicht nur
behauptet (Masterprompt §18/§24). Canary-/Final-Payload-Gate-Tests bleiben
bestehen und dürfen nicht ohne triftigen Grund entfernt werden.
