"""Unit-Tests für app/web/download_staging.py (Pilot-Readiness-Härtung:
temporäre Export-/Backup-Archive mit unpseudonymisierten Mandanteninhalten
dürfen nicht dauerhaft in %TEMP% liegen bleiben)."""

from __future__ import annotations

import os
import time

from app.web.download_staging import cleanup_stale_files, delete_after_send


def test_cleanup_stale_files_removes_old_files(tmp_path) -> None:
    old_file = tmp_path / "alt.zip"
    old_file.write_bytes(b"alt")
    old_time = time.time() - 1000
    os.utime(old_file, (old_time, old_time))

    cleanup_stale_files(tmp_path, max_age_seconds=900)

    assert not old_file.exists()


def test_cleanup_stale_files_keeps_recent_files(tmp_path) -> None:
    recent_file = tmp_path / "neu.zip"
    recent_file.write_bytes(b"neu")

    cleanup_stale_files(tmp_path, max_age_seconds=900)

    assert recent_file.exists()


def test_cleanup_stale_files_on_missing_directory_does_not_raise(tmp_path) -> None:
    missing_dir = tmp_path / "existiert_nicht"

    cleanup_stale_files(missing_dir)  # darf nicht werfen


def test_delete_after_send_removes_file_when_run() -> None:
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as handle:
        handle.write(b"inhalt")
        path = Path(handle.name)

    assert path.exists()
    task = delete_after_send(path)
    import asyncio

    asyncio.run(task())

    assert not path.exists()


def test_delete_after_send_missing_file_does_not_raise() -> None:
    from pathlib import Path

    task = delete_after_send(Path("does-not-exist-at-all.zip"))
    import asyncio

    asyncio.run(task())  # darf nicht werfen, auch wenn Datei schon weg ist
