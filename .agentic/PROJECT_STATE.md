# Lexono – Projektgedächtnis: PROJECT_STATE

Kanonische Architekturquelle bleibt `ARCHITECTURE.md` (Root) – dieses
Verzeichnis dupliziert sie NICHT, sondern ergänzt sie um agentenbezogenes
Arbeitsgedächtnis (Entscheidungen, offene Punkte, Teststand, Handoffs).

## Produktidentität

- Produktname: **Lexono**. „KanzleiAI“/„Kanzlei AI“ war ausschließlich ein
  früherer interner Arbeitstitel und darf in sichtbarer Produktidentität
  nicht mehr auftauchen (Ausnahme: interne technische Pfade/Modulnamen wie
  `kanzlei_ai.exe`, `app/`-Paketstruktur – siehe DECISIONS.md, kein blindes
  globales Rename).
- Zielgruppe: Steuer-/Wirtschaftskanzleien (nicht primär Arbeitsrecht).

## Architektur-Kernprinzip (nicht verhandelbar)

Sensible Mandantendaten bleiben lokal. Lokale Verarbeitung → Pseudonymisierung
(Presidio, intern) → Final Payload Gate → NUR pseudonymisierter Payload →
Lexono Gateway → Cloud-KI (Anthropic). Der Gateway ist Infrastruktur für
Schlüssel-/Zugriffsverwaltung, NICHT die Privacy-Prüfstelle. Siehe
`ARCHITECTURE.md` §§ zur Gateway- und Local-AI-Architektur (zuletzt §71).

## Aktueller Stand (31.08., Beginn Masterprompt V2)

- Gateway-Architektur: produktiv einsatzbereit, Baseline, nicht neu
  diskutiert.
- Local AI: Pflichtkomponente, über Ollama (`qwen2.5:1.5b`, datenbasiert
  gewählt) angebunden, real per Setup-Wizard verdrahtet (`local-ai-setup`
  CLI-Subcommand).
- Chat: zentrale Startseite nach Login (`/dashboard/chat`), mit zwei
  Statusindikatoren (Lokale KI / Cloud-KI) im Chat-Header.
- Test-Baseline: **1463 passed, 1 skipped, 0 failed** (siehe TEST_STATE.md).
- Installer zuletzt real gebaut+installiert+smoke-getestet: erfolgreich
  (WebView2-Fix bestätigt stabil).

## Agentenorganisation

Siehe `agents/` (Rollen/Akten) und `skills/` (wiederverwendbares Vorgehen).
Diese Struktur wurde am 31.08. im Rahmen des Masterprompts V2 neu angelegt
(vorher nicht vorhanden) – siehe AGENT_HANDOFFS.md für den Log.

## Aktueller Live-Zustand (01.09., spät abends - autonome Weiterarbeit)

Die real installierte App (`%LOCALAPPDATA%\Lexono\kanzlei_ai.exe`) läuft
bewusst weiter im Hintergrund (windowed mode), damit die neue
Fenster-Titelleiste (Task #61) beim nächsten Blick auf den Bildschirm
sofort sichtbar ist. Automatisiert bestätigt (echter HTTP-Smoke-Test
gegen die Installation, nicht nur TestClient): Login→Chat, Titelleisten-
Markup + Skript im ausgelieferten HTML, alle Bestandsseiten erreichbar,
Dokument-Upload funktioniert. **NICHT bestätigt**: die eigentliche
visuelle/interaktive Korrektheit (sieht die Titelleiste richtig aus,
funktioniert Drag/Resize/Close-Klick tatsächlich) - das kann nur ein
Mensch am echten Fenster beurteilen. Admin-Login für Tests:
`admin@kanzlei.de` / `Lexono-Smoke-Test-Pw-2026-Neu!` (nur in dieser
lokalen Test-Installation, kein Produktivsystem).

**WICHTIG für die visuelle Prüfung**: Die gerade laufende Instanz wurde
VOR dem Logo-Fix (Commit `39a574d`) gebaut - sie zeigt in der
Titelleiste also noch ein kleines Lexono-Logo + Wortmarke links (das
inzwischen als "doppelte Logo-Darstellung" erkannte und im Quellcode
bereits behobene Problem). Das ist beim jetzigen Hinsehen normal/erwartet
und kein neuer Fehler - der Fix ist im Code, aber noch nicht in einem
Installer ausgeliefert (dritter Rebuild am selben Abend erschien
unverhältnismäßig für eine rein kosmetische Änderung). Ein weiterer
Rebuild liefert auch diesen Fix aus.

**Für den nächsten Blick auf den Bildschirm, worauf zu achten ist**:
1. Native Windows-Titelleiste weg? (sollte ja sein)
2. Eigene schmale Leiste oben mit Minimieren (−) und Schließen (✕) rechts
   sichtbar? (aktuell NOCH mit Logo links, das ist erwartet, siehe oben)
3. Lässt sich das Fenster durch Ziehen an dieser Leiste verschieben?
4. Gibt es unten rechts einen Resize-Griff, der die Fenstergröße ändert?
5. Funktioniert der Schließen-Button (✕) zuverlässig? (das war der
   ursprüngliche Fehler - unbedingt bestätigen)

## Nacht-Automode-Zyklus abgeschlossen (01.09., früher Morgen)

Zusätzlich zum Titelleisten-Fix und Logo-Fix wurde ein sichtbarer
KI-Ladezustand im Chat ergänzt (Commit `f55925b`) - echte KI-Antworten
dauern 15-30+ Sekunden, ein rein abgedunkelter Sendebutton war kein
ausreichendes Feedback. Repository-Audit gegen den neuen, breiten
"Nacht-Automode"-Auftrag ergab: Branding (kein KanzleiAI mehr in
Templates), Feedback-/Kategorisierungssystem (`app/pilot_feedback/`),
Session-Ablauf-Handling und Tesseract-Bündelung waren bereits vorhanden
und funktionsfähig - nicht erneut gebaut. Vollständiger Abschlussbericht
wurde als Chat-Nachricht geliefert (nicht in einer neuen Datei
dupliziert). Test-Baseline: 1486 passed, 1 skipped, 0 failed. Kein
Git-Push. 19 lokale Commits seit Sessionbeginn.

## Nächste größere Workstreams (noch nicht begonnen)

Siehe OPEN_ISSUES.md für die vollständige, kategorisierte Liste. Die
größten offenen Posten sind: vollständiger Dokument-Workspace mit
Pseudonymisierungs-Highlighting (Referenzbild 2), Model-Evaluation-Engine
über mehrere Runtimes/Modelle, echter Visual-QA-Screenshot-Loop, und die
Entscheidung zum nativen Fenster-Chrome (frameless vs. aktuelles
natives WinForms-Fenster).
