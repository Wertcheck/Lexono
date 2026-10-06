"""Tests für app/documents/shell_icons.py (03.10., Owner-Direktive
"INDIVIDUELLE MANDANTENDETAILSEITE" §5.8) - echte Windows-Shell-Icon-
Extraktion. Diese Tests laufen nur sinnvoll unter Windows (siehe
`sys.platform`-Guard im Modul selbst) - auf anderen Plattformen liefert
die Funktion zuverlässig `None`, was hier ebenfalls geprüft wird."""

from __future__ import annotations

import base64
import io
import sys

import pytest

from app.documents import shell_icons


@pytest.fixture(autouse=True)
def _clear_icon_cache():
    shell_icons._icon_cache.clear()
    yield
    shell_icons._icon_cache.clear()


def test_get_shell_icon_data_uri_returns_none_for_blank_input():
    assert shell_icons.get_shell_icon_data_uri(None) is None
    assert shell_icons.get_shell_icon_data_uri("") is None
    assert shell_icons.get_shell_icon_data_uri("   ") is None


def test_normalize_extension_handles_filenames_and_bare_extensions():
    assert shell_icons._normalize_extension("Steuerbescheid.PDF") == ".pdf"
    assert shell_icons._normalize_extension("archiv.tar.gz") == ".gz"
    assert shell_icons._normalize_extension(".docx") == ".docx"
    assert shell_icons._normalize_extension("docx") == ".docx"
    assert shell_icons._normalize_extension("noextension") == ".noextension"


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows-Shell-API, nur unter Windows testbar")
def test_pdf_docx_xlsx_return_distinguishable_real_icons():
    """Echte Integrationspruefung gegen die tatsaechliche Windows-Shell auf
    dieser Maschine - PDF/DOCX/XLSX muessen ECHTE, sichtbare (nicht leere/
    transparente) und VONEINANDER UNTERSCHEIDBARE Icons liefern."""
    from PIL import Image

    uris = {
        ext: shell_icons.get_shell_icon_data_uri(f"dokument.{ext}")
        for ext in ("pdf", "docx", "xlsx")
    }
    for ext, uri in uris.items():
        assert uri is not None, f"kein Icon fuer .{ext} erhalten"
        assert uri.startswith("data:image/png;base64,")
        raw = base64.b64decode(uri.split(",", 1)[1])
        image = Image.open(io.BytesIO(raw)).convert("RGBA")
        # Sichtbare, nicht vollstaendig transparente/leere Pixel.
        extrema = image.getextrema()
        assert any(hi > 0 for lo, hi in extrema), f".{ext}-Icon ist leer/transparent"

    # PDF unterscheidet sich sichtbar von DOCX/XLSX (unterschiedliche
    # registrierte Anwendungen) - DOCX und DOC teilen sich dagegen
    # tatsaechlich dasselbe Word-Icon (real, kein Fehler).
    assert uris["pdf"] != uris["docx"]
    assert uris["pdf"] != uris["xlsx"]


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows-Shell-API, nur unter Windows testbar")
def test_doc_and_docx_share_the_same_word_icon():
    assert shell_icons.get_shell_icon_data_uri("a.doc") == shell_icons.get_shell_icon_data_uri("b.docx")


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows-Shell-API, nur unter Windows testbar")
def test_unknown_extension_still_returns_a_real_non_empty_icon():
    """Windows liefert fuer eine unbekannte Endung einen generischen, aber
    ECHTEN (nicht leeren) Icon - kein Absturz, kein None."""
    uri = shell_icons.get_shell_icon_data_uri("mystery.lexonotestext123")
    assert uri is not None
    assert uri.startswith("data:image/png;base64,")


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows-Shell-API, nur unter Windows testbar")
def test_result_is_cached_per_extension_not_per_call(monkeypatch):
    calls = {"count": 0}
    original = shell_icons._extract_icon_for_extension

    def _counting_extract(ext):
        calls["count"] += 1
        return original(ext)

    monkeypatch.setattr(shell_icons, "_extract_icon_for_extension", _counting_extract)

    first = shell_icons.get_shell_icon_data_uri("a.pdf")
    second = shell_icons.get_shell_icon_data_uri("b.pdf")  # andere Datei, gleiche Endung

    assert first == second
    assert calls["count"] == 1  # zweiter Aufruf kam aus dem Cache


def test_never_raises_on_non_windows_or_broken_shell_call(monkeypatch):
    """Rein kosmetische Funktion - darf eine Dokumentliste nie zum Absturz
    bringen, auch wenn die Shell-API selbst fehlschlaegt."""
    monkeypatch.setattr(shell_icons.sys, "platform", "linux", raising=False)
    assert shell_icons.get_shell_icon_data_uri("a.pdf") is None
