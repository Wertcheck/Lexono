# VISUAL_QA – Screenshot-/Referenzabgleich

Status: **NV (nicht verifiziert)** – in dieser Umgebung steht aktuell kein
Browser-Automatisierungstool zur Verfügung, mit dem eigenständig
Screenshots der laufenden Anwendung erzeugt und mit den bereitgestellten
Referenzbildern verglichen werden könnten. Der Nutzer hat die
Chrome-Erweiterung für diese Session abgelehnt.

## Was stattdessen gemacht wurde

- Referenzbilder (Chat-Startseite, Dokument-Workspace) wurden visuell
  analysiert (nicht kopiert) und daraus konkrete, verifizierbare
  UI-Diffs abgeleitet: vierte Quick-Action „Akte öffnen“, Entfernen des
  sichtbaren „Strg+K“-Badges, Branding-Korrekturen. Siehe DECISIONS.md
  und den Git-Commit-Log für die tatsächlich umgesetzten Änderungen.
- Der große Dokument-Workspace mit Pseudonymisierungs-Highlighting
  (Referenzbild 2) und die mögliche Sidebar-Statusanzeige (Referenzbild 1)
  wurden bewusst NICHT blind nachgebaut, da sie größere strukturelle
  Änderungen erfordern (siehe OPEN_ISSUES.md).

## Wie ein echter Visual-QA-Lauf nachgeholt werden kann

1. App im Dev-Modus starten (`python run.py serve --no-window` o. ä.) oder
   die installierte Version verwenden.
2. Mit einem Browser-Tool (z. B. Claude-in-Chrome-Erweiterung, wenn vom
   Nutzer aktiviert) Screenshots bei 1366×768 und 1920×1080 erzeugen.
3. Mit den Referenzbildern vergleichen, Abweichungen dokumentieren,
   Korrekturen vornehmen, erneut prüfen (Loop gemäß Masterprompt §23).
4. Ergebnis hier mit Datum und Status (V/NV) nachtragen.
