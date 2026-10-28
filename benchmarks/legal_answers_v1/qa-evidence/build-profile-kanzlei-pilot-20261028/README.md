# Build-Profil „kanzlei-pilot" – Trennung Kanzlei / Finance / Steuern (28.10.2026)

**Status:** Umgesetzt und geprüft auf Quelltext-, Bundle- und EXE-Laufzeitebene.

- **NICHT installiert:** Es wurden nur Test-Builds ohne Installer gebaut; beide wurden nach der Prüfung wieder gelöscht.
- **NICHT freigegeben:** keine Datenschutz-, P0-08-, C-14- oder Pilotfreigabe.
- **Daten:** ausschließlich synthetisch.
- **Unberührt:** produktive DB und laufende Prozesse; es wurde kein Lizenzschlüssel verwendet.

## Änderungen

| Datei | Änderung |
|---|---|
| `app/modules/profile.py` (neu) | Profile `kanzlei-pilot` = {kanzlei} und `full` = {kanzlei, finanzen, steuern}. EXE: nur Bundle-Datei `lexono_build_profile.txt`, fail-closed Pilot. Quelltext: `LEXONO_BUILD_PROFILE`, Standard `full` |
| `app/main.py` | Finance-Router nur importieren/registrieren, wenn `finanzen` im Profil ist |
| `app/modules/licensing.py` | `enabled_modules()` ∩ Profil; `require_module` liefert 404 für Module außerhalb des Profils |
| `app/web/settings_router.py` | Lizenz-Tab zeigt nur Module des Profils |
| `app/web/templates/base.html`, `fibu.html` | Block `extra_head`; `fibu.css` wird nur von der Finance-Seite geladen |
| `app/web/static/css/app.css` → `fibu.css` (neu) | FiBu-Stile herausgelöst (304 Zeilen); das Sidebar-Badge bleibt in `app.css` |
| `windows/lexono.spec` | Profil per `LEXONO_BUILD_PROFILE`, Standard **kanzlei-pilot**. Pilot: `app.fibu`, `app.fibu.service`, `app.web.fibu_router` ausgeschlossen; `fibu.html`/`fibu.css` nicht im Paket. Migrationen vollständig |
| `tests/test_build_profile.py` (neu) | 11 Profiltests |
| `agents/finance/AGENT.md`, `agents/tax/AGENT.md` (neu), `agents/lead/AGENT.md` | Rollen M (Finance) und N (Steuern), Strangregeln |
| `MODULARCHITEKTUR.md` §12, `ARCHITECTURE.md` §76.1 | Entscheidung dokumentiert |
| `app/auth/permissions.py` | Veralteter Kommentar korrigiert („Mitarbeiter lesen nur" → Erfassungsrecht seit 28.10.) |
| `tests/test_no_ai_on_open.py` | Testwert für `receipt_id` (bestehender Fehler seit ece9673) |

**Bewusst NICHT entfernt** (Abhängigkeiten nachgewiesen):

- **Migrationen 040–042:** Bestehende Datenbanken stehen auf 042.
- **`app.models.fibu` / `app.models.tax`:** `app/models/__init__.py` importiert sie für Metadaten und Migrationen.
- **Lizenzmodul und Modulkatalog.**

## Bundle-Inhalt beider Profile (PyInstaller, je ohne Installer)

| Prüfpunkt | kanzlei-pilot | full |
|---|---|---|
| `lexono_build_profile.txt` | `kanzlei-pilot` | `full` |
| `fibu.html` / `fibu.css` | fehlt / fehlt | vorhanden / vorhanden |
| Migrationen `schritt3_040–042` | 3/3 | 3/3 |
| `module_locked.html` | vorhanden | vorhanden |
| PYZ `app.fibu`, `app.fibu.service`, `app.web.fibu_router` | fehlen | vorhanden |
| PYZ `app.models.fibu`, `app.modules.profile`, `app.modules.licensing`, `app.web.chat_router`, `app.privacy.gateway` | vorhanden | vorhanden |
| PYZ `app.modules.client_access` | fehlt (nur von fibu/tax importiert) | vorhanden |
| `app`-Module gesamt | 313 | 317 |

## Laufzeitprobe der gebauten EXEs (`messwerte_exe_laufzeit.json`)

Der Ablauf hatte zwei Schritte.

1. Die Voll-EXE migrierte eine frische, isolierte Test-DB auf `schritt3_042`.
2. Danach startete die **Pilot-EXE** auf derselben DB. Gesetzt war dabei `LEXONO_BUILD_PROFILE=full`, um zu prüfen, dass
   die Umgebung das Paket nicht erweitert. Cloud-Endpunkte waren tot gesetzt.

| Prüfung | Ergebnis |
|---|---|
| Start auf DB mit Stand 042, Revision danach | 200, weiterhin `schritt3_042`, `quick_check` ok |
| Chat, Mandanten, Akten, Aufgaben, Posteingang, Kanzleiwissen | je 200 |
| Navigation ohne Finanzbuchhaltung | ja |
| `/dashboard/fibu` GET, Konten-POST, DATEV-Export, `fibu.css` | 404 / 404 / 404 / 404 (identisch mit jeder unbekannten URL) |
| Lizenz-Tab | nur „Lexono Kanzlei" |
| Tabellen `fibu_*` | bleiben in der DB unverändert vorhanden (5 Tabellen) |
| Tracebacks im Log | 0 |

Die Voll-EXE auf derselben DB lieferte ohne Lizenz für `/dashboard/fibu` 403: Die Seite „Lexono Finanzen ist nicht
freigeschaltet" erscheint, und der Lizenz-Tab zeigt kanzlei, finanzen und steuern. Die Freigabe mit gültiger Lizenz ist
in den Quelltexttests nachgewiesen (`test_full_app_still_provides_finance`: GET 200, POST 303). Die EXEs enthalten keinen
Herstellerschlüssel.

## Aufnahmen (1280×720, Headless Edge, gebaute EXEs)

- `pilot-1-navigation.png`: Navigation ohne Finanzbuchhaltung.
- `pilot-2-fibu-url-direkt.png`: Direktaufruf der Finance-URL liefert die generische 404-Antwort.
- `pilot-3-lizenz-tab.png`: Lizenz-Tab nur mit „Lexono Kanzlei".
- `voll-1-navigation.png`, `voll-2-fibu-url-direkt.png`, `voll-3-lizenz-tab.png`: Vollprofil ohne Lizenz zeigt das
  gesperrte Modul und den Lizenz-Tab mit allen drei Modulen.
- `messwerte_aufnahmen.json`: Zustand jeder Aufnahme; JS-Fehler: keine.

## Tests

**Gezielt:** `test_build_profile`, `test_fibu`, `test_modules`, `test_staff_rights`, `test_migrations`, `test_design_refresh`:
126 passed, 4 failed. Die 4 Fehler sind die bekannten Logo-Tests (siehe unten).

**Volle Regression:** alle **327** Testdateien unter `tests/`, seriell im Vordergrund, Quelltextprofil `full`, in Blöcken
zu je 12 Dateien.

Bisherige Läufe deckten nur eine Liste von 177 Dateien ab. Die übrigen 149 Dateien liefen hier erstmals mit.

- **Gesamt:** 7116 passed, 8 xfailed, 10 skipped, **6 failed**. Die 6 Fehler schlüsseln sich so auf:
  - **4:** bekannte Logo-Tests (siehe unten).
  - **1:** `test_s81_benchmark_hardening.py::test_crash_before_the_call_resumes_normally` („DID NOT RAISE _Crash")
    trat nur im Block unter Last auf. Die Datei allein: 56/56 passed. Sie ist nicht deterministisch (Thread/Timing),
    steht in keinem Zusammenhang mit dem Build-Profil und wird nicht als behoben gemeldet.
  - **1:** `test_no_ai_on_open.py::test_opening_any_get_route_triggers_no_ai_and_no_network` mit „neue
    Pfadparameter ohne Testwert: ['receipt_id']". Der Fehler bestand **bereits seit dem FiBu-Commit ece9673**, blieb
    aber unentdeckt, weil die Datei nicht in der 177er-Liste stand. Behoben durch einen Testwert für `receipt_id`.
    Danach ist die Datei grün: 2/2 im Profil `full` und 2/2 im Profil `kanzlei-pilot`.

**Kanzlei-Tests unter Pilotprofil** (`LEXONO_BUILD_PROFILE=kanzlei-pilot`):

- **Grün:** `test_web_chat`, `test_auth_web`, `test_migrations` und `test_web_clients`, also die Kanzlei-Funktionen ohne
  Finance-Abhängigkeit.
- **Erwartete Fehler:** In `test_modules`/`test_staff_rights` scheitern 27 Finance-/Steuern-Fälle wie vorgesehen,
  weil diese Funktionen im Pilot nicht existieren (404 bzw. `ModuleLockedError`).
- **Folgerung:** Die Gesamtsuite ist eine Vollprofil-Suite. Die Pilot-spezifische Absicherung liegt in
  `tests/test_build_profile.py`.

### Bekannte Logo-Testfehler (separat, nicht durch diese Änderung verursacht)

Die vier Fehler in `tests/test_design_refresh.py` gehen auf die im Arbeitsbaum gelöschten, fremden Logo-Dateien zurück
(`assets/branding/lexono-logo.png`, `Lexono-Logo Dark Mode.png`). Die Logos werden gemäß Vorgabe nicht
wiederhergestellt.

- `test_full_logo_lockup_assets_have_a_real_alpha_channel`
- `test_dark_logo_icon_is_byte_identical_to_the_light_logo_icon`
- `test_dark_logo_has_identical_overall_geometry_to_light_logo`
- `test_dark_logo_wordmark_is_recolored_to_white`

## Risiken / offene Punkte

1. **Lizenzimport im Pilot:** Der Import bleibt möglich (Lizenzprüfung erhalten), schaltet aber nichts frei. Der Text
   „Zusatzmodule werden … freigeschaltet" ist im Pilot womöglich missverständlich; eine Umformulierung ist eine
   UI-Entscheidung.
2. **Installer:** `windows/installer.iss` unterscheidet die Profile nicht (Name/Version gleich). Ein Vollpaket-Build
   braucht `LEXONO_BUILD_PROFILE=full`. Eine eigene Kennzeichnung des Installers ist noch offen.
3. **Wechsel Pilot → Voll:** Ein Wechsel per Neuinstallation ist datenseitig möglich, weil die Tabellen erhalten
   bleiben. Bestehende `fibu_*`-Daten bleiben im Pilot unsichtbar, sind aber noch in DB und Backup.
4. **Finance-Befunde der Agentenanalyse** (P1: Belegablage nicht im Backup, keine Festschreibung/Periodensperre,
   `content_hash` ungeprüft, offene Buchungen in USt-KPIs) betreffen nur das Vollprofil und gehen in den
   Finance-Strang.
5. Die volle Regression lief einmal im Arbeitsbaum, nicht auf einer frischen Kopie. Ein Test (s81) ist unter Last nicht deterministisch.

## Finance-Worktree (Abschnitt 5, 29.10.2026)

Vor dem Anlegen wurde auf Konflikte geprüft: Pfad und Branch existierten nicht, die Ports 8100–8149 waren frei, und
der einzige weitere Worktree `lexono_build_07d5fe0` ist unberührt.

| Punkt | Stand |
|---|---|
| Worktree | `C:\Users\Bonit\Lexono-wt-finance`, Branch `feature/finance-pilot` ab **33dbd7b** (wie vorgegeben; enthält das Build-Profil 3f13bf4 noch nicht) |
| Python | eigene `.venv` im Worktree (Python 3.13.15, gleiche Paketversionen wie das Haupt-venv). Editable install `lexono` zeigt auf den Worktree, `app` wird aus dem Worktree importiert |
| Daten | `C:\Users\Bonit\Lexono-wt-finance-data` (eigenes `LEXONO_DATA_DIR`, `.env` mit Port 8100, Cloud/Mail/Websuche aus, tote Gateway-URL), außerhalb beider Checkouts |
| Smoke-Test | frische DB → `schritt3_042`, synthetischer Admin; Server auf 8100: Chat/Mandanten/Lizenz-Tab 200, `/dashboard/fibu` 403 (ohne Lizenz gesperrt), 0 Tracebacks; Server danach beendet |
| Tests im Worktree | `test_fibu`, `test_modules`, `test_staff_rights`, `test_migrations`: 95 passed |
| Steuern-Worktree | nicht angelegt (erst bei Beginn der Implementierung) |
| Branch-Push | `feature/finance-pilot` nur lokal, nicht gepusht |

**Risiken des Worktrees:**

- **Doppelte Bearbeitung:** Der Finance-Strang arbeitet ohne `fibu.css`-Trennung. Ändert er FiBu-Stile in `app.css`,
  kollidiert das mit 3f13bf4. Deshalb vor solchen Änderungen 3f13bf4 kontrolliert per Merge übernehmen (kein Rebase).
- **Gemeinsame Ressourcen:** CPU/RAM sind geteilt. Volle Regressionen, Builds und native Prüfungen laufen nur seriell
  und nach Absprache.
