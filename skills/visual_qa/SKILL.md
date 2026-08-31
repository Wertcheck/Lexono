# Skill: Visual QA Loop

## Zweck

UI-Änderungen tatsächlich visuell verifizieren statt nur den Code zu
lesen.

## Voraussetzungen

Ein Browser-Automatisierungstool (z. B. Claude-in-Chrome, sofern vom
Nutzer aktiviert) oder manuelles Feedback vom Nutzer mit Screenshots.

## Vorgehensweise

1. Anwendung starten (dev oder installiert).
2. Screenshot bei 1366×768 UND 1920×1080 erzeugen.
3. Mit Referenzbild/Vorgabe vergleichen.
4. Abweichungen konkret benennen (nicht nur „sieht komisch aus“).
5. Korrigieren, erneut prüfen – nicht beim ersten akzeptablen Ergebnis
   stoppen.

## Fallback ohne Browser-Tool

Wenn kein Browser-Tool verfügbar ist: Status als NV dokumentieren (siehe
`.agentic/VISUAL_QA.md`), keine unbelegte „sieht gut aus“-Behauptung
aufstellen.

## Relevante Dateien

`.agentic/VISUAL_QA.md`
