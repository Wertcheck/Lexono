# DECISIONS – Architektur- und Produktentscheidungen

Format: DECISION / REASON / DATE. Neue Einträge unten anfügen, bestehende
nicht rückwirkend umschreiben (Historie bleibt nachvollziehbar).

---

DECISION: Gateway-Relay-Architektur (Lexono Gateway hält den echten
Anthropic-Key, Kanzlei-PC kennt nur Gateway-Adresse + Tenant-Credential)
bleibt Baseline und wird nicht zurückgebaut.
REASON: Bewusste Umkehrung einer früheren „kein zentraler Proxy“-Entscheidung
aus einer vorherigen Session; siehe ARCHITECTURE.md Gateway-Kapitel.
DATE: vor 31.08. (Vorsession)

---

DECISION: Lokales Standardmodell ist `qwen2.5:1.5b` statt `qwen3:4b`.
REASON: Echter Benchmark auf realer CPU-only-Entwicklungshardware zeigte
qwen3:4b >20 Min. Latenz durch „Thinking“-Modus; qwen2.5:1.5b liefert in
10–11s (warm) korrekte, nicht-halluzinierte Ergebnisse. Datenbasierte
Entscheidung gemäß Masterprompt-Anforderung „nicht einfach abschalten,
sondern testen“. Siehe ARCHITECTURE.md §71, `.agentic/MODEL_EVALUATION.md`.
DATE: 31.08.

---

DECISION: Local-AI-Setup wird über den interaktiven Setup-Wizard verdrahtet
(`local-ai-setup` CLI-Subcommand, Opt-out, NICHT load-bearing für die
Kernanwendung).
REASON: Hardware-Detection/Model-Recommendation/Ollama-Installer existierten
bereits vollständig, wurden aber nie aufgerufen – größte reine
Verdrahtungslücke der Local-AI-Architektur. Bewusst nicht load-bearing, da
ein fehlgeschlagenes Local-AI-Setup die App-Installation nicht blockieren
darf (Kernanwendung muss auch ohne lokale KI startbar bleiben).
DATE: 31.08.

---

DECISION: „Presidio“ wird aus der normalen Produkt-UI entfernt (nur noch
„Pseudonymisierung“); auf technischen/administrativen Seiten
(`account_privacy.html`, `settings.html`), die sich explizit als
„Technische Transparenz“ ausweisen, bleibt die Nennung bestehen.
REASON: Masterprompt V2 §13/§21 verlangt Entfernung des Bibliotheksnamens
aus der normalen Oberfläche, erlaubt ihn aber ausdrücklich in technischer
Dokumentation. Die beiden genannten Seiten sind bewusst als technische
Transparenzseiten gerahmt, nicht Teil des Kern-Workflows – dort bleibt der
Begriff bewusst stehen statt eine riskante flächendeckende Änderung
vorzunehmen.
DATE: 31.08.

---

DECISION: „Strg+K“-Tastenkürzel-Badge wird aus der sichtbaren UI entfernt,
die Tastenkombination (Strg+K/⌘K) selbst bleibt funktional erhalten.
REASON: Masterprompt V2 §21 explizit: „Suchfeld ohne 'Strg+K'“ (visuell).
DATE: 31.08.

---

DECISION (ÜBERHOLT, siehe Eintrag 01.09. unten): Kein Wechsel auf ein
frameless pywebview-Fenster in dieser Iteration (siehe OPEN_ISSUES.md,
Kategorie MEDIUM).
REASON: Das native WinForms-Fenster (`run.py::webview.create_window`) ist
Teil einer in Vorsessions mühsam stabilisierten Desktop-Shell (u. a.
WebView2-Bootstrapper-Fix). Ein Wechsel auf `frameless=True` würde native
Resize-Fähigkeit riskieren (pywebview bietet für frameless Fenster kein
eingebautes Rand-Resize) und erfordert eigene Minimieren/Schließen-Buttons
über die JS-Bridge. Substanzielle, aber risikoreiche Änderung – bewusst als
eigener, separat zu entscheidender Workstream zurückgestellt statt
nebenbei im selben Durchgang wie Textänderungen umgesetzt.
DATE: 31.08.

---

DECISION: `run.py` nutzt jetzt `frameless=True` mit einer eigenen, in
`base.html` gerenderten Titelleiste (Drag-Region + Minimieren/Schließen)
und einem hand-gerollten Resize-Griff unten rechts.
REASON: Explizite Nutzerentscheidung (01.09.): natives Fenster-Chrome soll
verschwinden, "wie eine zusammenhängende moderne Desktop-Anwendung", X muss
zuverlässig bleiben, Resize darf nicht kaputtgehen, sonst sicherste
Variante wählen. Vor der Umsetzung wurde das installierte pywebview 6.2.1
(`.venv/Lib/site-packages/webview/`) tatsächlich gelesen (nicht aus
Trainingswissen angenommen): `frameless=True` liefert auf Windows NUR
`FormBorderStyle.None`, keinerlei Drag-/Resize-Hit-Testing;`easy_drag` ist
auf dem WinForms-Backdend in dieser Version funktionslos (toter Code).
Gewählte, bewusst SCHMAL geschnittene Umsetzung (kein vollflächiges
easy_drag, keine 8-seitige Rand-Hit-Test-Zone): Drag nur über eine
dedizierte Titelleisten-Drag-Region, Resize nur über EINEN Eck-Griff unten
rechts, beide über `window.move()`/`window.resize()` (bereits vorhandene,
live x/y/width/height lesende `webview.Window`-Methoden) - das begrenzt die
Fragilität auf zwei klar abgegrenzte, einzeln testbare Interaktionsflächen
statt eines vollflächigen Hit-Test-Systems. `close_window()`/
`minimize_window()` sind duenne Wrapper um bereits vorhandene, stabile
`webview.Window.destroy()`/`.minimize()`.
WICHTIGE EINSCHRÄNKUNG: Die eigentliche VISUELLE Korrektheit (sieht das
Fenster wirklich gut aus, fühlt sich das Draggen richtig an, funktioniert
der Resize-Griff bei verschiedenen DPI-Einstellungen) ist NUR per echtem
Fenster + menschlichem Auge überprüfbar - in dieser Umgebung stand kein
Screenshot-/Browser-Tool zur Verfügung (siehe VISUAL_QA.md). Verifiziert
wurde ausschließlich die Python-seitige Logik (9 neue Unit-Tests gegen ein
Fake-Window-Objekt, `tests/test_native_api.py`) sowie, dass der reine
Browser-/`--no-window`-Modus durch Feature-Detection unverändert bleibt
(volle Testsuite grün). Ein echter Installer-Neubau + realer Fenstertest
mit menschlicher Sichtprüfung ist der zwingende nächste Schritt, bevor
dieser Punkt als vollständig VERIFIZIERT gelten darf.
DATE: 01.09.

---

DECISION: Der Jinja-Dict-Literal-Trick für Statustexte
(`{'ready': ..., 'disabled': ...}[state]`) darf NIE einen direkten
verketteten Attributzugriff auf einen potenziell undefinierten Wert
innerhalb eines der NICHT ausgewählten Zweige enthalten (z. B.
`undefined_var.attribut`).
REASON: Realer, in dieser Session gefundener Bug: beim ersten Versuch, die
globale Sidebar-Statusanzeige direkt aus `request.app.state` zu lesen
(ohne Router-Änderung, siehe unten), warf Jinja einen `UndefinedError`,
weil Dict-Literale in Jinja ALLE Werte eager auswerten - auch den gerade
nicht ausgewählten `'ready'`-Zweig, der `_lai_status.configured_model`
referenzierte, obwohl `_lai_status` (mangels gesetztem `app.state` in den
meisten Tests ohne durchlaufenen Lifespan) ein Jinja-`Undefined`-Sentinel
war. Jinjas Standard-`Undefined` wirft bei JEDEM verketteten
Attributzugriff sofort (`Undefined.__getattr__` -> `UndefinedError`,
KEIN stiller Fallback wie bei einem einfachen `if undefined_var`-Check,
der stattdessen `__bool__` nutzt und sicher False liefert). Betraf beim
ersten Anlauf 5 von ~320 Web-Tests (alle Seiten außer Chat, da dort
`app.state.local_ai_status` bereits Router-seitig gesetzt wird).
FIX: den riskanten Attributzugriff IMMER vorher in eine eigene,
kurzschluss-sichere `{% set %}`-Zeile mit `if/else`-Ternary auslagern
(nutzt `__bool__`, nicht `__getattr__`), erst DANACH die fertige Variable
im Dict-Literal referenzieren.
DATE: 01.09.

---

DECISION: `request.app.state.<beliebiges Feld>` ist in Jinja-Templates
sicher lesbar (liefert `Undefined`, wirft nicht), WENN nur eine einzige
Ebene tief zugegriffen und das Ergebnis sofort per `if`/Ternary geprüft
wird, bevor eine zweite Ebene folgt.
REASON: Starlettes `State.__getattr__` wirft bei fehlendem Attribut ein
normales `AttributeError` (nicht `KeyError`), das Jinjas
`Environment.getattr` explizit abfängt und in ein `Undefined` umwandelt -
das macht globale, router-unabhängige Statusanzeigen in `base.html` (siehe
"globaler Statuskontext", OPEN_ISSUES.md MEDIUM) möglich, OHNE jeden
einzelnen der ~20 Router anzufassen. Genutzt für die neue Sidebar-
Statusanzeige (Lokale KI/Cloud-KI) auf allen Seiten außer Chat (dort
bewusst nicht dupliziert, siehe "ohne die Chat-Oberfläche zu
überladen"-Vorgabe).
DATE: 01.09.
