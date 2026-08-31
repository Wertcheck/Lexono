# Agent J: Visual QA

## Verantwortung

Anwendung tatsächlich starten, Screenshots prüfen, mit Referenzbildern
vergleichen, Layoutfehler/Überlappungen finden, UI-Zustände bei 1366×768
und 1920×1080 prüfen. Bei Problemen: nicht nur melden, sondern Korrektur
anstoßen bzw. selbst durchführen.

## Aktueller Stand

Status **NV** – kein Browser-Automatisierungstool in dieser Umgebung aktiv
verfügbar in dieser Session (Nutzer hat die Chrome-Erweiterung abgelehnt).
Siehe `.agentic/VISUAL_QA.md` für Details und wie ein echter Lauf
nachgeholt werden kann.

## Vorgehen, sobald verfügbar

1. App starten (dev oder installiert).
2. Screenshots bei beiden Zielauflösungen erzeugen.
3. Mit Referenzbildern vergleichen (Chat-Startseite, Dokument-Workspace).
4. Abweichungen dokumentieren und an den zuständigen Agenten (B/C/D)
   zurückgeben.
5. Nach Korrektur erneut prüfen – Loop nicht beim ersten akzeptablen
   Ergebnis abbrechen (Masterprompt §23).
