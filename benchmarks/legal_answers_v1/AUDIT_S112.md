# §112 Behebung A21 (pytest-Plugin-Autoload) + konsolidierter Abschlussplan

Stand 21.10.2026. Branch `feature/feedback-diagnose`, HEAD `c9cecc3` (geprüft; Zwischencommit, NICHT
SICHERHEITSABGENOMMEN). Die §110/§112-Änderungen sind **uncommittet**. **Keine Sicherheitsfreigabe.** D1, Signierung
und C-14 bleiben **GESPERRT**.

**Grenzen:** Nur lokale Code-, Test- und Dokumentänderungen. Keine System-/venv-/Rechte-/VM-/Netz-Änderung, keine
echten Schlüssel/Signaturen, keine Produktivdatenbank. Keine echte venv verändert, keine Plugins installiert. Unit-Tests
und Simulation sind **kein Nachweis realer Isolation**. Die vier fremden Arbeitsbaum-Änderungen blieben unangetastet.

## 1 A21 – Plugin-Autoload → BEHOBEN (lokal)

**Ursprung:** AUDIT_S111 §2 A21. `isolation_real.run_pytest` setzte Bytecode-Isolation, aber die VM-pytest-Kinder
luden installierte Drittanbieter-Plugins weiter automatisch (Code außerhalb des Fingerabdrucks).

**Codepfad:** `isolation_real.run_pytest` (einziger produktiver pytest-Startpfad; der VM-Teil ruft ihn über `vm_teil`
→ `pytest_runner`).

**Änderung [Code]:**
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` wird für jeden Kindprozess gesetzt (kein Auto-Laden über Entry Points).
- Bypass-Umgebungsvariablen (`PYTEST_ADDOPTS`, `PYTEST_PLUGINS`, ein von außen gesetztes
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD`) werden aus der Kind-Umgebung entfernt.
- Nur ausdrücklich freigegebene Plugins werden über `-p <name>` geladen (Parameter `plugins`, Default leer); ungültige
  Namen → `AcceptanceRefused`.
- Ein freigegebenes, aber nicht ladbares Plugin lässt pytest scheitern → kein JUnit → `RuntimeError` (BLOCKIERT).

**Belege (Unit, injizierter Runner – kein echter pytest-Start):**
- `test_autoload_is_disabled_for_the_child`, `test_bypass_env_is_stripped` (3 Fälle),
  `test_only_explicit_plugins_are_loaded`, `test_invalid_plugin_name_is_refused`,
  `test_missing_plugin_blocks_via_no_junit`, `test_run_pytest_requires_real_mode`, `test_bytecode_isolation_still_set`
  (`tests/test_s112_review_fixes.py`, 10 Tests); §102 `test_pytest_children…` um Autoload/Bypass erweitert.

**Restrisiko / Voraussetzung:** Die Autoload-Sperre verhindert das Laden per Entry Point. Sie ersetzt NICHT den
venv-Schreibschutz: Nur wenn die venv einem **getrennten Eigentümerkonto** gehört und das Benchmark-Konto sie nicht
verändern kann (A16), ist ausgeschlossen, dass ein installiertes Plugin über `-p`/conftest geänderten Code einschleust.
Der **Inhalt** installierter Pakete bleibt ungehasht (A1). Reale Wirkung in der VM **NICHT VERIFIZIERT**.

**Urteil A21: BEHOBEN (lokal, Unit).**

## 2 Tests

| Lauf | Ergebnis | Klasse |
|---|---|---|
| `tests/test_s112_review_fixes.py` (neu) | 10 bestanden | Unit |
| Isolationssuiten Teil 1 (§86,§87,§90,§92,§102,§104,§105,§106,§110,§112) | 342 bestanden, 8 übersprungen | Unit/Sim |
| Isolationssuiten Teil 2 (§91,§94,§96,§98) | 198 bestanden, 1 übersprungen | Unit/Sim |
| Gesamtsuite, Lint, echte Signatur, reale Abnahme V1–V13, Schreibtest echte venv | — | NICHT DURCHGEFÜHRT |

Alle Läufe einzeln im Vordergrund mit Speicherwächter, `PYTHONDONTWRITEBYTECODE=1`, nie unter 5,9 GB frei.

Der konsolidierte Abschlussplan liegt in `SECURITY_EXIT_PLAN.md`.
