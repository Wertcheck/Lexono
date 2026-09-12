# TEST_STATE – Regressions-Baseline

## Aktuelle Baseline (verbindlich, darf sich nicht verschlechtern)

```
1538 passed, 1 skipped, 0 failed  (119.05s, voller Lauf via pytest)
```

Letzter voller, bestätigter Lauf: 12.09. (vollständiger KanzleiAI→Lexono-
Rename über das gesamte aktive Produkt - Spec/Installer/Paketname/
Session-Cookie/Backup-/Log-Dateinamen/Thread-Name/verbleibende
CLI-Hinweistexte in `backup.html`/`settings.html`; `app/setup/paths.py`s
`resolve_data_dir()`-Datenmigration per 11 dedizierten Tests inkl. echter
SQLite-Fixture abgedeckt). Siehe `.agentic/DECISIONS.md` (Eintrag
"Supersedes...") fuer den vollen Evidenz-Kontext. Verlauf der Baseline:
1463 (31.08.) →
1472 → 1484 → 1485 → 1486 → 1487 → 1491 → 1493 (01.09.) → 1520 (12.09.,
natives Fenster-Chrome ersetzt eigene Titelleiste - 9 `_NativeApi`-Tests
entfernt, 2 DWM-Tests ergänzt) → 1522 (12.09., Local-AI-Heartbeat, 2 neue
Tests) → 1526 (12.09., FastEmbed-Lazy-Load-Fix, 4 neue Tests in
`tests/test_search_service.py`) → 1528 (12.09., First-Run-Fix: 1
fehlerhaft-kodierter Test korrigiert, 3 neue Tests in
`tests/test_run_entrypoint.py`) → 1531 (12.09., Start.vbs-Fix: 3 neue
Tests in `tests/test_setup_wizard.py`, 1 korrigiert in
`tests/test_start_vbs.py`) → 1538 (12.09., vollstaendiger
KanzleiAI→Lexono-Rename: `tests/test_setup_paths.py` komplett neu
geschrieben, 11 Tests fuer `resolve_data_dir()`-Migration statt vorher
weniger; diverse Tests in `test_auth_web.py`,
`test_rate_limiting_and_session_revocation.py`, `test_end_to_end.py`,
`test_start_vbs.py`, `test_documents_ocr.py`,
`test_local_ai_ollama_installer.py` an neue Namen angepasst, kein
Netto-Testverlust).

## Wie ausführen

Volle Suite (aus aktiviertem `.venv`):

```
pytest
```

Bei Verdacht auf Testisolationsprobleme (siehe Vorfall unten) IMMER die
volle Suite laufen lassen, nicht nur einzelne/geänderte Testdateien –
Einzeldatei-Läufe können ordnungsabhängige Fehler verdecken.

## Bekannter Vorfall: Testisolation über `app.state`

`app.state` gehört zum einzigen prozessweiten `app`-Singleton
(`from app.main import app`). Tests, die den echten FastAPI-Lifespan via
`with TestClient(app) as ...:` durchlaufen lassen (z. B.
`tests/test_main_local_ai_startup_check.py`), setzen `app.state.local_ai_status`
dauerhaft für den restlichen Testprozess. Tests, die einen bestimmten
Ausgangszustand dieses Attributs erwarten, MÜSSEN ihn selbst
speichern/löschen/wiederherstellen (siehe
`tests/test_web_chat.py::test_chat_page_shows_local_ai_checking_state_without_lifespan`
als Referenzmuster).

## Nach Änderungen an dieser Datei

Diese Datei nach jedem vollständigen Testlauf mit neuem Ist-Stand
aktualisieren (Agent I – QA/Test Engineer ist dafür zuständig), nicht nur
bei Verschlechterungen.
