# Agent B: Frontend Engineer

## Verantwortung

HTML/CSS/JavaScript, Komponenten, Navigation, Responsive-Verhalten,
Accessibility, UI-State, Interaktionen. Kein Build-Schritt/Node.js im
Projekt (Vanilla JS + Jinja2 + HTMX, siehe ARCHITECTURE.md).

## Wichtige Dateien

- `app/web/templates/base.html` – Shell/Sidebar/Header, gemeinsam für
  alle Seiten
- `app/web/templates/chat.html` – zentrale Chat-Seite
- `app/web/templates/_icons.html` – Jinja-Icon-Makros (SVG inline)
- `app/web/static/css/app.css` – ein einziges CSS-File, kein Framework
- `app/web/static/img/logo.svg` / `logo-white.svg`

## Bekannte Probleme / offene Punkte

- Kein globaler Statuskontext (siehe OPEN_ISSUES.md, MEDIUM).
- Fenster-Chrome-Frage (frameless vs. nativ) noch offen, siehe
  DECISIONS.md und `agents/release/AGENT.md`.
- Dokument-Workspace mit Pseudonymisierungs-Highlighting nicht gebaut
  (OPEN_ISSUES.md, HIGH) – größte fehlende UI-Komponente.

## Regeln

- Keine blinden globalen Suchen/Ersetzen (Masterprompt §33), insbesondere
  bei „KanzleiAI“/„Lexono“ – interne technische Pfade dürfen bestehen
  bleiben.
- Vor jeder UI-Änderung: bestehende Tests in `tests/test_web_*.py` prüfen,
  ob Template-Strukturen/Texte dort erwartet werden.
- Jede Text-/Struktur-Änderung an gemeinsam genutzten Templates
  (`base.html`, `_icons.html`) betrifft ALLE Seiten – entsprechend
  vorsichtig testen.

## Letzte Arbeit (31.08.)

- `chat.html`: „KanzleiAI“ → „Lexono“ (Sender-Label, Composer-Placeholder),
  „Presidio“ aus Composer-Hinweistext entfernt, vierte Quick-Action „Akte
  öffnen“ ergänzt.
- `base.html`: sichtbares „Strg+K“-Badge entfernt (Funktion bleibt über
  Tastatur-Listener erhalten), zugehörige tote CSS-Regel entfernt.

## Nächste Arbeit

Siehe OPEN_ISSUES.md, priorisiert nach HIGH zuerst.
