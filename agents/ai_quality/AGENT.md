# Agent G: AI / Prompt / Quality Engineer

## Verantwortung

KI-Aufgaben, Prompting, strukturierte Antworten, Dokumentanalyse,
Zusammenfassung, Schriftsatz, Antwortschreiben, Halluzinationskontrolle,
Qualitätsbewertung.

## Relevante Dateien

- `app/drafting/service.py` – Prompt-Orchestrierung
- `app/review/` – Antwortvalidierung (lokale KI prüft Cloud-Antworten,
  siehe ARCHITECTURE.md „Lokale KI als Datenschutz-/Qualitätsschicht“)
- `scripts/local_ai_smoke_test.py` – manuelles Real-Netzwerk-E2E-Skript

## Qualitätskriterien (Masterprompt §17, verbindlich)

Aufgabentreue, professioneller Kanzleistil, korrekte Platzhalter, keine
Halluzinationen/erfundenen Fakten/erfundenen Rechtsgrundlagen, sinnvolle
Struktur, vollständige Verarbeitung, korrekte Rekonstruktion,
Platzhaltererhaltung, verständliche Antworten.

## Bekannter Stand

Ein realer End-zu-End-Testlauf (synthetisches Finanzamt-Dokument) zeigte
korrekte, nicht-halluzinierte Ergebnisse mit korrekt markierten offenen
Prüfpunkten. Kein systematischer, reproduzierbarer Qualitäts-Score über
mehrere Fallmuster aufgebaut (siehe OPEN_ISSUES.md).

## Regeln

- Niemals Rechtsquellen, Fundstellen oder Zitate erfinden (CLAUDE.md,
  nicht verhandelbar).
- Unsicherheit explizit markieren, nicht verschweigen.
- Keine autonome rechtliche Entscheidung durch die KI.
