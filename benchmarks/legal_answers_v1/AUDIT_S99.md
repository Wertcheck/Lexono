# §99 Ausstehende Tests ressourcenschonend abschließen

Stand 20.10.2026. Branch `feature/feedback-diagnose`, HEAD `b7e2daa3b1c0a67dcfd55f2852e781b033817fd5` (vor Beginn und
nach Abschluss geprüft). §90–§98 sind weiterhin **uncommittet**. `git status` zeigt vor und nach den Läufen dieselben 33
Einträge; die Tests haben im Repository nichts verändert.

**Umfang:** nur die zwei in AUDIT_S98 §4 als NICHT DURCHGEFÜHRT dokumentierten Läufe:
- die Benchmark-Tests §77–§85,
- die Gesamtsuite in sechs Teilen.

Kein Code geändert. Keine System-, Netz- oder Kontenänderung. Keine externe Verbindung. Kein Zugriff auf die
Produktivdatenbank. F1, D1 und C-14 bleiben gesperrt.

## 1 Startbedingungen und Speicher

- **Ollama-Vorwärmung:** beendet. Es lief kein `llama-server`; nur der Ollama-Dienst und die App mit je ≤ 46 MB.
- **RAM:** 15,7 GB gesamt, vor Beginn 6,9 GB frei. Es liefen keine weiteren speicherintensiven Prozesse; der größte
  Einzelprozess war etwa 0,9 GB groß. Kein Prozess wurde beendet.
- **Vorgehen:**
  - Jeder Teil lief einzeln im Vordergrund, über einen Speicherwächter (Job-Ordner, `run99.py`, außerhalb des
    Repositorys).
  - Start nur bei mindestens 4 GB freiem RAM.
  - Kontrollierter Stopp, falls der freie RAM unter 1,5 GB fällt oder das Zeitlimit überschritten wird.
  - Messung jede Sekunde.
  - Kein paralleler Testprozess; der nächste Teil startete erst nach dem vollständigen Ende des vorherigen.
- **Befehl je Teil:** `python -m pytest <Dateien> -q -p no:cacheprovider -rfE` (mit `.venv\Scripts\python.exe`).
- **Ergebnis:** In keinem Teil wurde der Stopp ausgelöst. Der niedrigste gemessene freie RAM lag bei 4,27 GB (Teil G2).

## 2 Benchmark-Tests §77–§85

| Teil | Dateien | Bestanden | Fehlgeschl. | Übersprungen | Laufzeit | Min. frei | Status |
|---|---|---|---|---|---|---|---|
| B1 | `test_s77_answer_benchmark_audit.py`, `test_s78_benchmark_gate.py`, `test_s78_wire_privacy.py` | 125 | 0 | 0 | 60 s | 5,99 GB | **PASS** |
| B2 | `test_s79_benchmark_exec_guard.py`, `test_s79_privacy_check_failure.py`, `test_s80_benchmark_driver.py` | 55 | 0 | 0 | 323 s | 5,68 GB | **PASS** |
| B3 | `test_s81_benchmark_hardening.py`, `test_s82_decision_matrix.py`, `test_s83_pilot_readiness.py` | 85 | 0 | 0 | 255 s | 5,64 GB | **PASS** |
| B4 | `test_s84_network_process_fence.py`, `test_s85_isolation_boundaries.py` | 50 | 0 | 0 | 52 s | 6,02 GB | **PASS** |
| **Summe** | 11 Dateien | **315** | **0** | **0** | 11:30 min | | **PASS** |

Kein Teil wurde abgebrochen.

## 3 Gesamtsuite (sechs Teile)

Die Teile `suite_part_00` bis `suite_part_05` enthalten je 46, zusammen 276 Testdateien. Das sind alle `tests/test_*.py`
außer den 19 Benchmark- und Isolationsdateien §77–§98: §77–§85 laufen in §2 dieses Berichts, §86–§98 liefen in §98 (386
bestanden, 8 übersprungen).

| Teil | Bestanden | Fehlgeschl. | Übersprungen | xfail / xpass | Laufzeit | Min. frei | Status |
|---|---|---|---|---|---|---|---|
| G0 | 963 | 0 | 0 | 2 / 0 | 202 s | 5,64 GB | **PASS** |
| G1 | 1049 | 0 | 0 | 0 / 0 | 137 s | 5,79 GB | **PASS** |
| G2 | 928 | 0 | 0 | 1 / 0 | 241 s | 4,27 GB | **PASS** |
| G3 | 761 | 4 | 0 | 2 / 1 | 141 s | 5,88 GB | **FAIL** – nur die 4 bekannten fremden Logo-Fehler (§4) |
| G4 | 1085 | 0 | 1 | 3 / 0 | 179 s | 5,38 GB | **PASS** |
| G5 | 840 | 0 | 0 | 2 / 0 | 135 s | 4,39 GB | **PASS** |
| **Summe** | **5626** | **4** | **1** | 10 / 1 | 17:15 min | | siehe §5 |

- Laufzeiten mit Start und Ende des Prozesses (Wächter); pytest selbst meldet etwas weniger.
- `xfail` sind erwartete Fehlschläge, die im Code so markiert sind.
- Der eine `xpass` in G3 ist ein als erwarteter Fehlschlag markierter Test, der bestanden hat. Er ist nicht strikt
  markiert und kein Fehler; ich habe ihn nicht weiter untersucht.

## 4 Bekannte fremde Logo-Testfehler (nicht behoben)

Teil G3, Datei `tests/test_design_refresh.py`:
1. `test_full_logo_lockup_assets_have_a_real_alpha_channel`
2. `test_dark_logo_icon_is_byte_identical_to_the_light_logo_icon`
3. `test_dark_logo_has_identical_overall_geometry_to_light_logo`
4. `test_dark_logo_wordmark_is_recolored_to_white`

**Ursache:** die fremden Änderungen an den Logo-Dateien im Arbeitsbaum. `assets/branding/lexono-logo.png` und
`Lexono-Logo Dark Mode.png` sind gelöscht, zwei neue Bilder liegen in `assets/ux-ui/`.

**Vorgabe:** Diese Dateien und die Fehler sind nicht Gegenstand dieser Arbeit und wurden nicht angefasst. Weitere Fehler
gab es in keinem Teil.

## 5 Gesamtergebnis und Einschränkungen

| Lauf | Status |
|---|---|
| Benchmark-Tests §77–§85 | **PASS** – 315 bestanden, 0 fehlgeschlagen, vollständig |
| Gesamtsuite, 6 Teile | **FAIL nur durch die 4 bekannten fremden Logo-Fehler**; sonst 5626 bestanden, 1 übersprungen, alle Teile vollständig |
| Isolationssuites §86–§98 | in §98 durchgeführt (**PASS**), hier nicht wiederholt |
| Reale Abnahme V1–V13 | **NICHT DURCHGEFÜHRT** |

**Bedeutung der Status:**
- **PASS:** vollständiger Lauf ohne Fehler.
- **FAIL:** vollständiger Lauf mit fehlgeschlagenen Tests.
- **BLOCKIERT:** kam nicht vor.
- **NICHT DURCHGEFÜHRT:** kein vollständiges Ergebnis. Betrifft hier nur die reale Abnahme.

**Einschränkungen:**
- Alle Tests sind lokal: Unit-, Integrations- und simulierte Abnahmetests. Sie belegen **keine reale Netzwerkisolation**
  und keine Sicherheitsfreigabe.
- Lint wurde nicht ausgeführt, weil ruff in der `.venv` fehlt; das war nicht Teil von §99.
- Der Stand ist nicht committet. Die Ergebnisse gelten für den Arbeitsbaum zum Zeitpunkt der Läufe.
