# QA-Nachweis: Standard-Prompts verwalten (Modal der Chat-Startseite)

Datum: 28.10. · Umsetzung gemäß Goal „Standard-Prompt-Verwaltung“ mit zwei Referenzbildern
(Bild 1: Übersicht mit leerem rechten Bereich, Bild 2: Formular „Neuen Prompt erstellen“).

## Was geändert wurde

| Bereich | Datei |
|---|---|
| Datenmodell | `app/models/prompt_template.py` (+ `icon`, `show_on_home`, `attach_on_use`, `sort_order`) |
| Migration | `migrations/versions/schritt3_031_prompt_templates_home_management.py` – übernimmt die bisher fest verdrahteten Startseiten-Prompts, Kanzlei-Vorlagen bleiben erhalten |
| Fachlogik | `app/prompt_library/home_service.py`, `home.py` (Limit 4, Feldlängen 100/200/2000), `icons.py` |
| API | `app/web/prompt_library_router.py`: `GET/POST /dashboard/library/prompts/manage/state` (Admin/Anwalt, CSRF, atomar) |
| Startseite | `app/web/chat_router.py`, `app/web/templates/chat.html` (Kacheln aus der Datenbank, Button „Standard-Prompts verwalten“) |
| Modal | `app/web/templates/partials/prompt_manager_modal.html`, `app/web/static/js/app_prompt_manager.js`, Block „Standard-Prompts verwalten“ in `app/web/static/css/app.css` |
| Tests | `tests/test_web_prompt_home_manager.py` (neu), `tests/test_web_chat.py` und `tests/test_update_hardening.py` angepasst (neue Kachelquelle bzw. neuer Migrations-Head) |
| Doku | `.agentic/DECISIONS.md` |

## Verhalten (Kurzfassung)

- Beliebig viele Prompts speicherbar, **höchstens 4 aktivierbar** (serverseitig und im Modal). Die Startseite zeigt genau die aktivierten Prompts in der festgelegten Reihenfolge – nicht automatisch die obersten vier.
- Fünfte Aktivierung wird verhindert und erklärt („Bitte deaktivieren Sie zuerst einen anderen Prompt“); Deaktivieren löscht nichts.
- Bearbeitungsauswahl (dunkler Rahmen + Balken) ist optisch getrennt vom Aktivierungsstatus (grüner Schalter/getönte Karte).
- Drag-and-drop am Griff (mit Autoscroll) und Pfeiltasten am fokussierten Griff; Reihenfolge wird gespeichert.
- Arbeitskopie im Modal: Änderungen werden erst mit „Speichern“ übernommen (eine Transaktion). Schließen/Escape/Hintergrundklick/Abbrechen fragen bei ungespeicherten Änderungen nach. Löschen mit Bestätigung.
- Pflichtfelder (Titel, Beschreibung, Anweisung) und Limits 100/200/2000 werden client- und serverseitig geprüft.
- Mitarbeiter sehen die Kacheln, aber keinen Verwalten-Button und haben keinen Schreibzugriff (403).

## Tests und Ergebnisse

- `python3 -m pytest tests/test_web_prompt_home_manager.py` → **22 bestanden** (CRUD, Validierung inkl. Grenzwerte, Atomarität, Viererlimit, Sortierung, Wiederherstellung aus frischer Sitzung, Startseite = nur Aktivierte in Reihenfolge, HTML-Escaping, Rechte/CSRF, Migration inkl. Downgrade/Upgrade).
- Betroffene Bestandstests (`test_web_prompt_library`, `test_prompt_library_*`, `test_migrations`, `test_update_hardening`, `test_web_chat`): keine neuen Fehler.
- Gesamtsuite in dieser Umgebung: 1442 Fehlschläge **bereits vor der Änderung** (fehlende Pakete wie `presidio_analyzer`, `faster_whisper`); Vergleich gegen den unveränderten Stand: nach Anpassung der zwei Tests keine zusätzlichen Fehlschläge durch diese Änderung.
- `ui_checks.cjs` (Playwright gegen eine frisch geseedete Instanz mit echter Datenbank/Migration) → **60/60** bei **1280×720** und **960×600**: Leerzustand, Auswahl, Dirty-Dialoge, Limit, Maus-Drag mit Autoscroll, Tastatur-Sortierung, Speichern, Reload-Persistenz, Startseitenkachel → Chat-Eingabe, Neu-Formular, Validierung, Löschen, Mitarbeiter-Rolle, Modal innerhalb des Fensters.

## Screenshots (Hell/Dunkel × 1280×720/960×600)

- `00-startseite-*`: Startseite mit den aktivierten Prompts
- `01-uebersicht-leer-*`: Referenzzustand 1 (rechts leer)
- `02-neuer-prompt-*`: Referenzzustand 2 („Neuen Prompt erstellen“, 4/4 belegt → Hinweis)

Abgleich mit den Referenzen: zweispaltiges zentriertes Modal, getönte Karten (Grün/Blau/Violett/Orange), Drag-Griff, Schalter mit „Startseite/Nicht auf Startseite“, Drei-Punkte-Menü, „+ Neuen Prompt hinzufügen“, rechts Icon-Auswahl, Startseiten-Schalter mit Hinweis, Titel/Beschreibung/Anweisung mit Zeichenzählern, Fußleiste Abbrechen/Speichern.

Bewusste Abweichungen: Die Referenz zeigt „4 von 7“ bei sechs sichtbaren Einträgen (Darstellungsfehler der Vorlage) – das Modal zählt die tatsächlich vorhandenen Prompts. Das Drei-Punkte-Menü enthält nur funktionierende Aktionen (Bearbeiten, Löschen). Zusätzlich: Belegungs-Chip „4/4 Startplätze belegt“, Option „Dateidialog beim Verwenden öffnen“.

## Reproduktion

QA-Instanz: leere SQLite-Datenbank per `alembic upgrade head`, Nutzer `admin@qa.test` (Admin) und `mitarbeiter@qa.test`, sechs Prompts wie in der Referenz, Start mit `uvicorn app.main:app --port 8099`; dann `node shots.cjs` bzw. `node ui_checks.cjs` (Umgebungsvariable `BASE`, `RESEED` für das Zurücksetzen zwischen den Durchläufen).

## Offene Punkte / Grenzen

- Gesamtsuite lässt sich hier wegen fehlender optionaler Pakete nicht grün fahren (unabhängig von dieser Änderung).
- Geprüft im Chromium-Browser; WebView2/Desktop-Installation und Windows-Build wurden nicht getestet.
- Prompt-Anweisungen werden weiterhin nur als Vorbefüllung des Chat-Eingabefelds verwendet (keine Änderung an Chat-Pipeline oder Sicherheitslogik).
