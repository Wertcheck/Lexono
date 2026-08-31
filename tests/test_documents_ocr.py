"""Tests fuer app/documents/ocr.py (Prompt 06).

Nutzt echte Tesseract-Ausfuehrung gegen synthetische Testbilder/-PDFs -
keine Mocks, da genau das Verhalten zaehlt, das im produktiven Einsatz
zum Tragen kommt. Toleriert kleine OCR-Ungenauigkeiten (z. B. bei eng
gesetzter Schrift), prueft aber auf klar erkennbare Kernbestandteile.
"""

import sys
from pathlib import Path

import pytest

from app.documents.ocr import (
    OcrError,
    _bundled_tesseract_paths,
    configure_tesseract,
    run_ocr,
    tesseract_health_check,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_ocr_recognizes_text_in_scanned_pdf() -> None:
    text = run_ocr(FIXTURES / "scanned_document.pdf")
    normalized = text.upper()

    assert "TESTTEXT" in normalized


def test_ocr_recognizes_text_in_image_file() -> None:
    text = run_ocr(FIXTURES / "scanned_image.png")
    normalized = text.upper()

    assert "TESTTEXT" in normalized


def test_ocr_raises_for_unsupported_format() -> None:
    with pytest.raises(OcrError):
        run_ocr(FIXTURES / "unbekannt.xyz")


def test_ocr_raises_for_missing_file(tmp_path: Path) -> None:
    with pytest.raises(OcrError):
        run_ocr(tmp_path / "existiert_nicht.pdf")


# --- Gebuendeltes Tesseract (Windows-Installer, siehe windows/fetch_tesseract.ps1
# + windows/kanzlei_ai.spec + Moduldocstring in app/documents/ocr.py) ---


def test_bundled_tesseract_paths_none_when_not_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Im Dev-Betrieb (kein PyInstaller-Bundle, `sys.frozen` nicht gesetzt)
    gibt es keinen automatisch aufzuloesenden Bundle-Pfad."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert _bundled_tesseract_paths() is None


def test_bundled_tesseract_paths_found_when_frozen_and_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Simuliert die onedir-Bundle-Struktur (kanzlei_ai.exe neben
    tesseract/bin/tesseract.exe + tesseract/tessdata/) - reine Pfadlogik,
    ohne die Datei tatsaechlich auszufuehren."""
    bin_dir = tmp_path / "tesseract" / "bin"
    tessdata_dir = tmp_path / "tesseract" / "tessdata"
    bin_dir.mkdir(parents=True)
    tessdata_dir.mkdir(parents=True)
    (bin_dir / "tesseract.exe").write_bytes(b"dummy")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "kanzlei_ai.exe"), raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    result = _bundled_tesseract_paths()

    assert result == (bin_dir / "tesseract.exe", tessdata_dir)


def test_bundled_tesseract_paths_none_when_frozen_but_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ein aelterer, vor diesem Fix erzeugter Build ohne Tesseract-Ordner
    darf nicht faelschlich einen nicht existierenden Pfad zurueckgeben."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "kanzlei_ai.exe"), raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    assert _bundled_tesseract_paths() is None


def test_configure_tesseract_explicit_override_wins_over_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ein manuell gesetztes TESSERACT_CMD muss immer Vorrang vor der
    automatischen Bundle-Erkennung haben (z. B. abweichende, bewusst vom
    Admin gewaehlte System-Installation)."""
    import pytesseract

    bin_dir = tmp_path / "tesseract" / "bin"
    bin_dir.mkdir(parents=True)
    (tmp_path / "tesseract" / "tessdata").mkdir()
    (bin_dir / "tesseract.exe").write_bytes(b"dummy")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "kanzlei_ai.exe"), raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    configure_tesseract("C:/custom/tesseract.exe")

    assert pytesseract.pytesseract.tesseract_cmd == "C:/custom/tesseract.exe"


def test_configure_tesseract_none_leaves_default_when_not_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ohne Bundle (Dev-Betrieb) und ohne expliziten Override bleibt
    `pytesseract`s eingebauter PATH-Suchmechanismus unveraendert aktiv."""
    import pytesseract

    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")

    configure_tesseract(None)

    assert pytesseract.pytesseract.tesseract_cmd == "tesseract"


def test_tesseract_health_check_reports_unavailable_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deterministisches, verstaendliches Ergebnis statt einer unbehandelten
    Exception, wenn Tesseract nicht aufrufbar ist - Grundlage der
    Systemstatus-Anzeige (app/web/monitoring_router.py)."""
    import pytesseract

    tesseract_health_check.cache_clear()

    def _raise(*args, **kwargs):
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(pytesseract, "get_tesseract_version", _raise)

    available, message = tesseract_health_check()

    assert available is False
    assert "nicht gefunden" in message
    tesseract_health_check.cache_clear()
