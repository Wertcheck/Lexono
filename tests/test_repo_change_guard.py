"""Waechter gegen Code-Aenderungen waehrend eines Testlaufs (tests/repo_change_guard.py) - mit temporaerem Repo-Abbild."""

from __future__ import annotations

import os

from tests.repo_change_guard import REPO, changed_files, code_fingerprint


def test_detects_added_modified_and_removed_python_files(tmp_path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "app" / "b.py").write_text("y = 1\n", encoding="utf-8")
    (tmp_path / "run.py").write_text("print()\n", encoding="utf-8")
    before = code_fingerprint(tmp_path)
    assert set(before) == {"app/a.py", "app/b.py", "run.py"}
    (tmp_path / "app" / "a.py").write_text("x = 22\n", encoding="utf-8")
    (tmp_path / "app" / "b.py").unlink()
    (tmp_path / "app" / "c.py").write_text("z = 1\n", encoding="utf-8")
    assert changed_files(before, code_fingerprint(tmp_path)) == ["app/a.py", "app/b.py", "app/c.py"]


def test_ignores_caches_and_non_python_files(tmp_path) -> None:
    (tmp_path / "app" / "__pycache__").mkdir(parents=True)
    (tmp_path / "app" / "__pycache__" / "a.cpython-313.pyc").write_bytes(b"\0")
    (tmp_path / "app" / "notes.md").write_text("x", encoding="utf-8")
    assert code_fingerprint(tmp_path) == {}


def test_unchanged_repository_reports_nothing() -> None:
    first = code_fingerprint(REPO)
    assert first and "run.py" in first
    assert changed_files(first, code_fingerprint(REPO)) == [] or os.environ.get("LEXONO_EDITING_DURING_TESTS")
