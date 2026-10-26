"""Erkennt Aenderungen am Python-Code des Repositorys WAEHREND eines Testlaufs (26.10.2026).

Hintergrund (reproduziert): Das Isolations-Gate bindet seine Bestaetigung an den Fingerabdruck aller Python-Dateien
(`isolation_gate.pruefwerkzeug_stand`, §98) und verweigert fail-closed, wenn sich Code zwischen Bestaetigung und
Pruefung aendert. Wurde waehrend eines laufenden Testlaufs eine Datei bearbeitet, scheiterte deshalb
`test_s80_benchmark_driver::test_privacy_finding_in_payload_aborts_before_sending` mit "gestoppt_isolation_nicht_
bestaetigt" - korrektes Gate-Verhalten, kein Fehler im Code und keine Reihenfolgeabhaengigkeit. Dieser Waechter macht
solche Laeufe in der pytest-Zusammenfassung sichtbar, statt den Test abzuschwaechen."""

from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_ROOTS = ("app", "scripts", "tests", "migrations", "windows")
_SKIP_DIRS = {"__pycache__", ".venv", "build", "dist", "node_modules", ".git"}


def code_fingerprint(repo: Path = REPO) -> dict[str, tuple[int, int]]:
    """{relativer Pfad: (Groesse, mtime_ns)} aller .py-Dateien unter den Code-Wurzeln plus run.py."""
    result: dict[str, tuple[int, int]] = {}
    candidates = [repo / "run.py"]
    for root in _ROOTS:
        base = repo / root
        if not base.is_dir():
            continue
        for folder, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            candidates.extend(Path(folder) / f for f in files if f.endswith(".py"))
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        result[path.relative_to(repo).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return result


def changed_files(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> list[str]:
    """Hinzugefuegte, entfernte oder geaenderte Dateien (sortiert)."""
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
