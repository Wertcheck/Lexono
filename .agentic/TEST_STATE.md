# TEST_STATE – Regressions-Baseline

## Aktuelle Baseline (verbindlich, darf sich nicht verschlechtern)

```
1493 passed, 1 skipped, 0 failed  (249.25s, voller Lauf via pytest)
```

Letzter voller, bestätigter Lauf: 01.09. (nach dem Minimieren-Icon-Fix,
Reliability & Deployment Hardening Cycle - Testzahl unverändert
gegenüber dem vorherigen Lauf, da `installer.iss`/Icon-SVG nicht von
pytest abgedeckt werden). Verlauf der Baseline: 1463 (31.08.) → 1472 →
1484 → 1485 → 1486 → 1487 → 1491 → 1493 (01.09., diverse Fixes + neue
Tests, siehe `SESSION_LOG.md` für Details).

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
