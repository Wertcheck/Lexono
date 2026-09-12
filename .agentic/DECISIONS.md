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

DECISION: Die Titelleiste (Task #61) wird als gemeinsames Partial
(`partials/app_titlebar.html`) + gemeinsame statische Datei
(`static/js/app_titlebar.js`) implementiert und in DREI Templates
eingebunden: `base.html`, `login.html`, `unlock.html`.
REASON: **Realer Bug, gefunden durch tatsächlichen Nutzertest am echten
installierten Fenster** (01.09.): Nach dem ersten Rebuild+Install zeigte
das Fenster keinerlei Bedienelemente - der Nutzer musste die App über den
Task-Manager beenden ("Task Manager, there was noch clone Button" = kein
Close-Button vorhanden). Ursache: `login.html` (die vom Fenster beim Start
zuerst geladene Seite, `run.py::_serve_with_window` lädt
`{base_url}/dashboard/login`) und `unlock.html` (PIN-Sperrbildschirm) sind
BEIDE eigenständige Templates, die `base.html` NICHT erben (kein
Sidebar-/Nav-Layout vor dem Login nötig) - die Titelleiste existierte
ursprünglich ausschließlich in `base.html`. Mit `frameless=True` bereits
ohne native Titelleiste UND ohne die eigene (weil nur in `base.html`)
ergab das ein komplett unbedienbares Fenster auf der allerersten Seite -
in JEDEM Fall, nicht nur in einem Edge-Case. Ein erster Zwischenfix (das
Aktivierungsskript aus `{% if current_user %}` in `base.html` herausholen)
war zwar für sich genommen ein echter, separater Bug (siehe unten), löste
aber nicht das eigentliche Problem, weil `login.html` `base.html` gar
nicht verwendet.
LEHRE: Ein Fenster-weites UI-Element (hier: Fenster-Chrome-Ersatz) darf
nicht an EIN Template gebunden werden, wenn mehrere unabhängige
Root-Templates existieren, die dasselbe Fenster füllen können - IMMER
zuerst prüfen, welche Templates tatsächlich NICHT von der gemeinsamen
Basis erben (`grep -L 'extends "base.html"' app/web/templates/*.html`),
bevor ein Feature als "auf jeder Seite vorhanden" angenommen wird.
Regressionstest: `tests/test_auth_web.py::
test_login_page_activates_custom_titlebar_script_before_login`.
DATE: 01.09.

---

DECISION: `ARCHITECTURE.md` bekommt einen neuen, prominenten Block
"AKTUELLER VERBINDLICHER ARCHITEKTURSTAND" direkt nach dem Titel, der die
Architektur-Fragen mit dokumentierter Historie von Kehrtwenden (lokale
KI: Pflicht → entfernt → wieder Pflicht; zentraler Proxy: abgelehnt →
als Lexono-Gateway eingeführt) eindeutig und ohne Notwendigkeit,
71 Abschnitte chronologisch zu lesen, klärt. Zusätzlich bekommen die drei
konkret überholten Abschnitte (§57, §60, §63) je eine kurze
"> ÜBERHOLT/TEILWEISE ÜBERHOLT"-Markierung direkt am Abschnittsanfang.
REASON: Expliziter Nutzerauftrag (01.09.): "frühere Überlegungen,
alternative Architekturen... sind als historische Stände zu behandeln,
sofern sie dem aktuellen finalen Architekturentscheid widersprechen...
sicherstellen, dass ein zukünftiger Agent beim Lesen der Dokumentation
sofort erkennt, welche Architektur verbindlich ist." Alte Abschnitte
wurden NICHT gelöscht oder inhaltlich verändert (nur eine kurze Notiz
vorangestellt) - der Auftrag verlangt ausdrücklich Kennzeichnung statt
Löschen, Nachvollziehbarkeit bleibt erhalten.
DATE: 01.09.

---

DECISION: `.agentic/PROJECT_STATE.md` wird von einem chronologisch
wachsenden Log zu einem reinen Ist-Zustands-Dokument ohne datierte
Verlaufsabschnitte umgebaut; die bisherige Verlaufserzählung wandert
unverändert in eine neue `.agentic/SESSION_LOG.md`.
REASON: Expliziter Nutzerauftrag (01.09.) zur Dokumentationskonsolidierung
- nach einer sehr langen Sitzung (24+ Commits über eine Nacht) war
`PROJECT_STATE.md` an mehreren Stellen widersprüchlich geworden (z. B.
`TEST_STATE.md` noch mit dem Stand vom 31.08., mehrere überlappende
datierte "Aktueller Stand"-Abschnitte in PROJECT_STATE.md selbst). Der
Auftrag verlangt ausdrücklich "eine klare aktuelle Single Source of
Truth" bei gleichzeitigem Erhalt der Historie zur Nachvollziehbarkeit -
Archivierung statt Löschen.
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

---

DECISION: `app/search/service.py`'s `search_within_matter`/
`search_knowledge_base`/`search_sources` only call the real embedding
provider (`FastEmbedProvider.embed`) when the candidate list (documents/
approved knowledge items/approved sources) is non-empty, instead of
unconditionally embedding the query.
REASON: P1 memory-pressure root-cause investigation (12.09.) measured
FastEmbed's real model load at ~1.71GB, invoked on every single
`create_draft()`/chat call regardless of whether there was anything to
search - for a new firm/matter with an empty knowledge base (the common
pilot case), this was pure waste and, combined with Presidio (~0.94GB)
and a loaded local-AI model (`qwen3:8b`, ~5.6GB in Ollama's separate
`llama-server` process), drove free RAM on the 16GB-class reference
machine below 1GB, correlating with a real, reproducible Ollama request
failure (HTTP 404 despite the model showing as loaded). The fix is
behavior-preserving when candidates exist (identical embedding still
happens) and does not touch Presidio, pseudonymization, Local AI, or the
Claude call. Real Local AI/Claude direct pilot path (see
LEXONO_MASTER_PRODUCT.md P0-07/P0-09) remains unaffected/unchanged.
DATE: 12.09.

---

DECISION: First-run detection (`run.py::main()`) now requires BOTH `.env`
presence AND at least one real user in the database before skipping
`cmd_setup()`; a partial setup (`.env` written but no admin created)
automatically re-triggers the setup assistant with `force=True`.
REASON: Real user-reported P0 defect - a genuine end user reached the
login page with no way to know credentials. Root-caused to `main()`
checking only `.env` existence, which `run_setup_wizard()` writes as its
very first step, before migration/admin-creation (the actually
load-bearing steps) even run. Any single failure in those later steps
(a subprocess hiccup, antivirus interference, disk issue) left `.env`
behind with zero users, and every subsequent launch treated that as
"setup already done." Confirmed real via a faithful reproduction (a
genuinely failed `create-admin` step, no synthetic shortcuts) against
the actual installed release candidate, both before the fix (silent
login dead-end) and after (correct re-invocation of setup, printed
console message). A pre-existing test
(`test_main_serve_skips_setup_when_env_already_exists`) had encoded the
broken behavior as expected - corrected as part of this fix, alongside
3 new regression tests covering the real failure condition.
DATE: 12.09.

---

DECISION: `Start.vbs` decides console visibility by checking a new
`.setup_complete` marker file instead of `.env` presence.
`app/setup/wizard.py::run_setup_wizard()` writes this marker only after
`create_admin()` actually succeeds (not at the same time as `.env`,
which is written first, before migration/admin-creation even run).
REASON: `Start.vbs` had its own, separate, unfixed copy of the exact
same flawed logic just corrected in `run.py::main()` (checking `.env`
presence as a proxy for "setup complete"). Discovered while
investigating a real end user's report: they had genuinely completed
setup once (a real admin, `bonitzki@live.de`, existed in the database
with a real `created_at` timestamp) but had lost access to the
one-time-shown initial password, most likely because the native window
opened immediately after and covered the console where it was printed
- a separate finding from the original P0. The `Start.vbs` gap itself
is real and forward-looking: on a FUTURE failed setup attempt (`.env`
written, admin creation fails), `Start.vbs` would launch the resulting
setup retry silently/hidden, indistinguishable from a hang, even though
`run.py::main()` correctly triggers the retry. VBScript has no SQLite
driver and cannot query the database directly the way `main()` now
does; the marker file is the closest safe approximation, deliberately
conservative (only ever written on genuine success). Verified against
the actual newly-built installed `.exe` via the real production
subprocess calls (`_run_migrate_subprocess`/`_run_create_admin_subprocess`,
exactly what `cmd_setup()` uses) — marker correctly absent after a real
failed `create-admin`, correctly present after a real successful one.
The affected real user was recovered via `reset-admin-password`
(legitimate account recovery for their own real account, not used as
clean-room evidence); the new password was relayed only in
conversation, never written to a file, log, or report.
DATE: 12.09.

---

DECISION: **Supersedes** the earlier "Klasse A / bewusst stabile interne
Bezeichner" classification of `kanzlei_ai.exe`/`kanzlei_ai.spec`/
`KANZLEI_AI_DATA_DIR`/`%ProgramData%\KanzleiAI` recorded above and in
`PROJECT_STATE.md`/`OPEN_ISSUES.md` (12.09., earlier entries this same
day). The user explicitly authorized and demanded a full rename of
these previously-accepted internal identifiers in a later, more
expansive mega-prompt this same day. Executed: `windows/kanzlei_ai.spec`
→ `windows/lexono.spec` (`EXE`/`COLLECT` name "Lexono"), `pyproject.toml`
package name `kanzlei-ai` → `lexono`, session cookie name/salt, backup
archive filename prefix, temp-export directory names, log download
filename, uvicorn thread name, `Start.vbs` exe lookup and data-dir
resolution, and the two remaining user-visible CLI-help mentions in
`backup.html`/`settings.html` (`kanzlei_ai.exe` → `Lexono.exe`).
REASON: user's explicit, repeated instruction that the end user must
never be confronted with "KanzleiAI" anywhere in the active product
identity, now extended (beyond the previously-agreed "UI/templates
only" scope) to build artifacts, package metadata, and internal thread
naming.
NOT renamed (deliberately, unchanged from the earlier decision): the
actual SQLite DB filename `kanzlei_ai.db` and log filename
`kanzlei_ai.log` inside the data directory, and the legacy
`KANZLEI_AI_DATA_DIR` env var and `%ProgramData%\KanzleiAI` directory
name, which are preserved as fallback/migration-source identifiers only
- `app/setup/paths.py::_migrate_legacy_dir_if_needed()` performs a safe,
atomic, data-preserving one-time rename of an existing legacy directory
to `%ProgramData%\Lexono` on first resolution after upgrade (existing
data is migrated, never deleted; falls back to the legacy path if the
rename fails for any reason). `windows/installer.iss`'s `AppId` and
`AppMutex` were deliberately left unchanged (upgrade-detection
continuity). Verified: full regression suite (1538 passed, 1 skipped,
0 failed) after all changes; `resolve_data_dir()` migration logic
covered by 11 dedicated tests including a real-SQLite-file fixture
proving the full rename+migrate chain; PyInstaller + Inno Setup
rebuilt and the resulting bundled templates inspected directly to
confirm the fixed `Lexono.exe` text is what actually ships.
DATE: 12.09.
