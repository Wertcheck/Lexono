# Testisolation test_s80 – Ursache und Behandlung (26.10.2026)

**Befund:** `tests/test_s80_benchmark_driver.py::test_privacy_finding_in_payload_aborts_before_sending` scheiterte am
26.10. einmal in der Datenschutz-Gesamtsuite mit Status `gestoppt_isolation_nicht_bestaetigt` statt
`gestoppt_abgebrochen_vor_versand`. Einzeln bestand er, mit und ohne die damalige Änderung.

## Reproduktion

| Lauf | Ergebnis |
|---|---|
| Ursprünglicher Gesamtlauf (17:1x Uhr, parallel wurde `run.py` bearbeitet) | 1 fehlgeschlagen (s80), 2794 bestanden |
| Einzeltest mit Änderung / ohne Änderung (sauberer Worktree `e767be3`) | bestanden / bestanden |
| `test_s80` + `test_s81` zusammen | 86/86 bestanden |
| Gesamtsuite erneut, ohne parallele Bearbeitung (mit `-x -l`) | **3281 bestanden**, 0 fehlgeschlagen – s80 bestanden |
| Gesamtsuite mit Diagnose-Plugin (protokolliert Stop-Gründe) | s80 bestanden; kein Isolationsstopp für s80 |
| **Deterministisch:** Einzeltest, ohne Bearbeitung | bestanden („Datenschutzbefund im Payload … nicht gesendet“) |
| **Deterministisch:** Einzeltest, eine neue `.py`-Datei im Repo zwischen Erzeugung und Prüfung der Test-Bestätigung | **fehlgeschlagen** mit genau `gestoppt_isolation_nicht_bestaetigt`, Grund „Pruefwerkzeug seit der Bestaetigung geaendert (Commit oder Dateien)“ |

## Ursache

**Kein** gemeinsam genutzter Testzustand und **keine** Reihenfolgeabhängigkeit.

Das Isolations-Gate bindet die Bestätigung an den Fingerabdruck **aller** Python-Dateien des Repositorys
(`isolation_gate.pruefwerkzeug_stand`, §98). Bei der Prüfung berechnet es ihn neu; ändert sich dazwischen Code, verweigert
es fail-closed. Während des ursprünglichen Laufs wurde `run.py` bearbeitet (Fensterkorrektur), zeitlich passend zur
Position von `test_s80` in der Suite. Das Gate hat korrekt reagiert.

## Behandlung (ohne Abschwächung)

- **Test unverändert streng:** gleiche Assertions. Neu ist nur, dass bei einem Fehlschlag die Gründe des Gates ausgegeben
  werden (vorher war nur der Status sichtbar).
- **`tests/repo_change_guard.py` + `tests/conftest.py`:** Fingerabdruck (Größe, Änderungszeit) aller `.py`-Dateien unter
  `app/`, `scripts/`, `tests/`, `migrations/`, `windows/` und `run.py` zu Beginn und am Ende jedes pytest-Laufs. Bei
  Änderung erscheint in der Zusammenfassung ein roter Abschnitt „Repository-Code waehrend des Testlaufs geaendert“ mit den
  Dateien und dem Hinweis, dass Gate-Ergebnisse dieses Laufs nicht aussagekräftig sind.
  - Nachgewiesen mit der simulierten Bearbeitung: Abschnitt und Dateiname erscheinen.
  - Tests: `tests/test_repo_change_guard.py`.
- **Arbeitsweise:** Während laufender Testsuiten keine Repository-Dateien bearbeiten. Der abschließende Gesamtlauf wurde so
  ausgeführt.
- **Abschließender Lauf** der Datenschutz-Gesamtsuite (110 Testdateien, inklusive der neuen Tests), ohne parallele
  Bearbeitung: **3305 bestanden, 3 übersprungen, 7 xfailed, 0 fehlgeschlagen** (`privacy_suite_final.txt`). Kein
  Wächter-Hinweis.
- Einzeltest und betroffene Gruppe: `test_s80` + `test_repo_change_guard` 33/33 bestanden; `test_s80` + `test_s81` 86/86.
- Belege der Reproduktion: `repro_ohne_aenderung.jsonl`, `repro_mit_gleichzeitiger_aenderung.jsonl`. Die verwendeten
  Diagnose-Plugins lagen nur im Job-Ordner und sind nicht eingecheckt.
- **Bewusst nicht gemacht:** Den Code-Fingerabdruck im Test-Gate einzufrieren hätte den Test robuster gemacht, aber die
  Prüfung „Code seit Bestätigung geändert“ in allen Gate-Tests ausgehebelt – eine Abschwächung.
