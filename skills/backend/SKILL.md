# Skill: Backend Change (FastAPI/Services)

## Zweck

Änderungen an Routern/Services vornehmen, ohne die Datenisolation oder den
Privacy-Fluss zu gefährden.

## Wann einsetzen

Bei neuen Endpunkten, Services oder Änderungen an bestehender
Geschäftslogik.

## Vorgehensweise

1. Bestehenden Router/Service lesen, Muster übernehmen (z. B. wie andere
   Router `TemplateResponse`-Kontext aufbauen).
2. Bei sicherheitsrelevanten Änderungen: `skills/security/SKILL.md`
   parallel anwenden.
3. Unit-/Integrationstest ergänzen (siehe `skills/testing/SKILL.md`).
4. Volle Suite laufen lassen.

## Prüfungen

- Kein Blockieren des Request-Threads durch lange Operationen (Ollama,
  OCR, Netzwerk) – `asyncio.to_thread`/Background-Tasks nutzen.
- Tenant-/Akten-Isolation nicht verletzt.

## Typische Fehler

Cross-Cutting-Änderungen (z. B. neuer globaler Template-Kontext für alle
Router) unterschätzen – betrifft schnell 15–20 Dateien, vorher mit Agent A
abstimmen.

## Relevante Dateien

`app/web/*_router.py`, `app/drafting/service.py`, `app/chat/service.py`
