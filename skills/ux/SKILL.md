# Skill: UX / Referenzbild-Abgleich

## Zweck

Ein bereitgestelltes Referenzbild in konkrete, umsetzbare UI-Diffs
übersetzen, ohne blind zu kopieren.

## Vorgehensweise

1. Referenzbild analysieren: Layout-Struktur, Hierarchie, welche Elemente
   sind funktional (Buttons/Links) vs. dekorativ.
2. Aktuellen Zustand der entsprechenden Seite lesen (Template + CSS).
3. Diffs auflisten: was fehlt, was weicht ab, was ist bereits vorhanden.
4. Für jeden Diff bewerten: klein/lokal (sofort umsetzbar) vs. groß/
   strukturell (→ `.agentic/OPEN_ISSUES.md`, mit Agent A abstimmen).
5. Kleine Diffs umsetzen, große dokumentieren statt überstürzt umzusetzen.

## Prüfungen

Passt die Umsetzung zum bestehenden Designsystem (Farben, Radius,
Typografie aus `app.css`)? Keine neue Farbpalette „nebenbei“ einführen.

## Typische Fehler

Ein Referenzbild 1:1 nachbauen wollen, obwohl die zugrunde liegenden
Daten/Routen dafür noch nicht existieren (z. B. inline PDF-Highlighting
ohne vorhandene Span-Koordinaten aus der Pseudonymisierung).

## Relevante Dateien

`.agentic/VISUAL_QA.md`, `agents/ux/AGENT.md`
