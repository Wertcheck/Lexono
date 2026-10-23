"""§110: Regressionstests zu AUDIT_S109 A16 (Windows-ACL-Rechte WRITE_DAC/WRITE_OWNER, dist-info/entry_points).

Nur lokal: tmp-Verzeichnisse, echte WinAPI-Probe gegen read-only-Attribut, Stubs für die geschützt-Verdikte (eine real
geschützte Datei ließe sich ohne Eigentümer-/Rechteänderung nicht herstellen - ausdrücklich simuliert). Keine System-,
venv- oder Rechteänderung. Kein Nachweis realer Isolation."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.legal_bench import isolation_codepfad as cp, isolation_evidence as ev

win = pytest.mark.skipif(sys.platform != "win32", reason="Windows-ACL-Semantik")


def _has(items, text) -> bool:  # noqa: ANN001
    return any(text in i for i in items)


# === A16: WRITE_DAC/WRITE_OWNER unterscheiden Inhalts-Schreibzugriff vom Recht, den Schutz zu aendern ===================


def test_access_masks_are_distinct_rights() -> None:
    assert cp._WRITE_DAC == 0x00040000 and cp._WRITE_OWNER == 0x00080000
    assert cp._WRITE_DAC != cp._GENERIC_WRITE and cp._WRITE_OWNER != cp._GENERIC_WRITE


@win
def test_owned_readonly_file_is_writable_via_write_dac(tmp_path) -> None:
    """Das Read-only-ATTRIBUT sperrt nur den Inhalt; der Eigentuemer kann es via WRITE_DAC aufheben -> beschreibbar."""
    f = tmp_path / "r.py"
    f.write_text("x", encoding="utf-8")
    os.chmod(f, stat.S_IREAD)
    try:
        assert cp._probe_file(f) == "beschreibbar"
    finally:
        os.chmod(f, stat.S_IREAD | stat.S_IWRITE)


@win
def test_writable_and_missing_paths(tmp_path) -> None:
    w = tmp_path / "w.py"
    w.write_text("x", encoding="utf-8")
    assert cp._probe_file(w) == "beschreibbar"
    assert cp._probe_dir(tmp_path) == "beschreibbar"
    assert cp._probe_file(tmp_path / "weg.py") == "nicht_pruefbar"


def test_probe_file_protected_only_if_content_and_acl_rights_denied(monkeypatch) -> None:
    """Stub (simuliert): geschuetzt NUR, wenn Inhalt UND WRITE_DAC UND WRITE_OWNER ACCESS_DENIED ergeben."""
    monkeypatch.setattr(cp.sys, "platform", "win32")

    def all_denied(path, access, share, disp, flags):  # noqa: ANN001, ANN202
        return (-1, cp.ERROR_ACCESS_DENIED)

    monkeypatch.setattr(cp, "_win_create", all_denied)
    assert cp._probe_file(Path("x")) == "geschuetzt"

    calls = {"n": 0}

    def write_dac_open(path, access, share, disp, flags):  # noqa: ANN001, ANN202
        calls["n"] += 1
        # Inhalt verweigert, aber WRITE_DAC-Handle gelingt -> Konto kann die ACL aendern -> beschreibbar
        if access == cp._WRITE_DAC:
            return (0x123, 0)
        return (-1, cp.ERROR_ACCESS_DENIED)

    monkeypatch.setattr(cp, "_win_create", write_dac_open)
    assert cp._probe_file(Path("x")) == "beschreibbar"


def test_probe_file_unclear_when_not_access_denied(monkeypatch) -> None:
    """Ein anderer Fehler als ACCESS_DENIED (z. B. Freigabeverletzung) ist KEIN Schutznachweis -> nicht_pruefbar."""
    monkeypatch.setattr(cp.sys, "platform", "win32")
    monkeypatch.setattr(cp, "_win_create", lambda *a: (-1, cp.ERROR_SHARING_VIOLATION))
    assert cp._probe_file(Path("x")) == "nicht_pruefbar"


def test_probe_dir_protected_only_if_all_rights_denied(monkeypatch) -> None:
    import ctypes

    monkeypatch.setattr(cp.sys, "platform", "win32")
    monkeypatch.setattr(cp, "_win_create", lambda *a: (-1, cp.ERROR_ACCESS_DENIED))  # Datei-Anlage + WRITE_DAC/OWNER denied
    monkeypatch.setattr(cp, "_kernel32", lambda: SimpleNamespace(CreateDirectoryW=lambda *a: 0, RemoveDirectoryW=lambda *a: 1))
    monkeypatch.setattr(ctypes, "get_last_error", lambda: cp.ERROR_ACCESS_DENIED)  # Unterordner-Anlage denied
    assert cp._probe_dir(Path("x")) == "geschuetzt"


def test_probe_dir_writable_if_can_change_acl(monkeypatch) -> None:
    """Inhalt/Anlage verweigert, aber WRITE_OWNER-Handle gelingt -> Eigentum uebernehmbar -> beschreibbar."""
    monkeypatch.setattr(cp.sys, "platform", "win32")

    def opener(path, access, share, disp, flags):  # noqa: ANN001, ANN202
        return (0x55, 0) if access == cp._WRITE_OWNER else (-1, cp.ERROR_ACCESS_DENIED)

    monkeypatch.setattr(cp, "_win_create", opener)
    import ctypes

    monkeypatch.setattr(cp, "_kernel32", lambda: SimpleNamespace(CreateDirectoryW=lambda *a: 0, RemoveDirectoryW=lambda *a: 1))
    monkeypatch.setattr(ctypes, "get_last_error", lambda: cp.ERROR_ACCESS_DENIED)
    assert cp._probe_dir(Path("x")) == "beschreibbar"


# === A16: dist-info / entry_points / Metadaten im Schreibtest ==========================================================


def _installation(tmp_path: Path) -> Path:
    inst = tmp_path / "site-packages"
    di = inst / "pkg-1.0.dist-info"
    di.mkdir(parents=True)
    (di / "entry_points.txt").write_text("[pytest11]\np = pkg.plugin\n", encoding="utf-8")
    (di / "RECORD").write_text("pkg/__init__.py,,\n", encoding="utf-8")
    (di / "top_level.txt").write_text("pkg\n", encoding="utf-8")
    (di / "METADATA").write_text("Name: pkg\n", encoding="utf-8")
    (inst / "pkg").mkdir()
    (inst / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    return inst


def test_write_access_covers_dist_info_metadata(tmp_path) -> None:
    """§110 (A16): entry_points.txt/RECORD/top_level.txt in *.dist-info werden erfasst - sonst koennte ueber Entry
    Points/Plugin-Registrierung Code nachgeladen werden, ohne im Fingerabdruck zu stehen."""
    inst = _installation(tmp_path)
    seen = []
    result = cp.write_access(tmp_path / "repo", venv=[inst], base=[],
                             probe_dir=lambda p: "geschuetzt",
                             probe_file=lambda p: seen.append(str(p)) or "geschuetzt")
    joined = "\n".join(seen)
    for name in ("entry_points.txt", "RECORD", "top_level.txt"):
        assert name in joined, name
    assert "pkg/__init__.py".replace("/", os.sep) in joined or str(inst / "pkg" / "__init__.py") in joined


def test_write_access_flags_writable_dist_info(tmp_path) -> None:
    inst = _installation(tmp_path)
    result = cp.write_access(tmp_path / "repo", venv=[inst], base=[], probe_dir=lambda p: "geschuetzt",
                             probe_file=lambda p: "beschreibbar" if str(p).endswith("entry_points.txt") else "geschuetzt")
    assert _has(result["beschreibbar"], "entry_points.txt") and ev._write_protected(result) is False


def test_tool_fingerprint_lists_this_file() -> None:
    from scripts.legal_bench import isolation_gate

    assert "tests/test_s110_review_fixes.py" in isolation_gate.tool_files()
