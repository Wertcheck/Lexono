"""Projektweite, gemeinsam genutzte pytest-Fixtures.

Bewusst schlank gehalten - die meisten Testdateien bauen ihre eigene
In-Memory-SQLite-`db_session`-Fixture lokal auf (siehe z. B.
tests/test_privacy_gateway.py), das bleibt unveraendert. Hier landen nur
Fixtures, die von MEHREREN Testdateien identisch gebraucht werden.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

#: Vom Windows-Installer gebuendeltes Tesseract (siehe
#: windows/fetch_tesseract.ps1 + windows/lexono.spec) - auf einer
#: Entwicklungsmaschine ohne SEPARAT installiertes System-Tesseract der
#: einzig realistische Weg, echte OCR-Tests lauffaehig zu machen, ohne eine
#: weitere manuelle Abhaengigkeit einzufuehren (Pilot Readiness Review,
#: Abschnitt 8: "Testumgebungsproblem" behoben statt nur dokumentiert).
_VENDOR_TESSERACT_EXE = (
    Path(__file__).resolve().parent.parent / "windows" / "vendor" / "tesseract" / "bin" / "tesseract.exe"
)


@pytest.fixture(scope="module")
def use_bundled_tesseract_if_no_system_install():
    """Verwendet automatisch das mit `windows/fetch_tesseract.ps1` erzeugte,
    gebuendelte Tesseract, falls kein System-Tesseract im PATH gefunden
    wird - macht OCR-Tests auf jeder Maschine lauffaehig, die den Windows-
    Installer-Build vorbereitet hat (bzw. in CI, sobald das Fetch-Skript
    vorgeschaltet ist), statt dauerhaft an einer reinen Entwicklungs-
    umgebungs-Voraussetzung zu scheitern.

    Faellt NICHT auf einen Mock zurueck - ohne echtes System- ODER
    Bundle-Tesseract werden die betroffenen Tests bewusst uebersprungen
    (klarer Skip-Grund statt eines irrefuehrenden Fehlschlags). Per
    `pytest.mark.usefixtures(...)`/direktem Fixture-Parameter in der
    jeweiligen Testdatei einzubinden (kein globales autouse - OCR-fremde
    Tests sollen diesen Check nicht bezahlen)."""
    import pytesseract

    try:
        pytesseract.get_tesseract_version()
        yield  # System-Tesseract bereits vorhanden - nichts zu tun
        return
    except Exception:  # noqa: BLE001 - jede Form von "nicht verfuegbar"
        pass

    if not _VENDOR_TESSERACT_EXE.is_file():
        pytest.skip(
            "Weder System-Tesseract im PATH noch windows/vendor/tesseract "
            "vorhanden - vor diesem Testlauf einmalig "
            "'powershell -ExecutionPolicy Bypass -File windows/fetch_tesseract.ps1' "
            "ausfuehren, oder Tesseract lokal installieren."
        )

    from app.documents.ocr import configure_tesseract

    original_cmd = pytesseract.pytesseract.tesseract_cmd
    original_tessdata_prefix = os.environ.get("TESSDATA_PREFIX")
    configure_tesseract(str(_VENDOR_TESSERACT_EXE))
    # `configure_tesseract(explicit_path)` setzt bewusst NUR `tesseract_cmd`
    # (der explizite-Override-Zweig gilt production-seitig fuer eine bereits
    # vollstaendig konfigurierte System-Installation, die TESSDATA_PREFIX
    # i. d. R. schon korrekt gesetzt hat) - hier zusaetzlich noetig, weil wir
    # explizit auf die Bundle-Struktur zeigen, nicht auf eine System-
    # Installation.
    os.environ["TESSDATA_PREFIX"] = str(_VENDOR_TESSERACT_EXE.parent.parent / "tessdata")
    yield
    pytesseract.pytesseract.tesseract_cmd = original_cmd
    if original_tessdata_prefix is None:
        os.environ.pop("TESSDATA_PREFIX", None)
    else:
        os.environ["TESSDATA_PREFIX"] = original_tessdata_prefix
