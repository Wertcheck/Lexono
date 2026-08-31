"""OCR-Verarbeitung (Tesseract).

Wird nur aufgerufen, wenn `settings.ocr_enabled=True` ist (Entscheidung
liegt beim aufrufenden Service, nicht hier). Für PDFs werden die Seiten
gerastert (über PyMuPDF, kein zusätzliches externes Tool wie Poppler
nötig) und einzeln per Tesseract erkannt; für Bilddateien direkt.

WICHTIG (Pilot-Finding, siehe FUTURE_ROADMAP.md/RELEASE_NOTES.md "Tesseract
als Abhängigkeit"): Tesseract ist keine Python-Bibliothek, sondern ein
externes Programm - ohne separate Installation auf dem Zielsystem schlägt
OCR bislang mit `TesseractNotFoundError` fehl, auch wenn `OCR_ENABLED=true`
gesetzt ist. Ab jetzt bündelt der Windows-Installer ein eigenständiges
Tesseract (siehe windows/vendor_tesseract.ps1, windows/kanzlei_ai.spec) -
`configure_tesseract()` löst dessen Pfad automatisch auf, wenn keine
explizite `TESSERACT_CMD`-Überschreibung gesetzt ist. Im Dev-Betrieb (kein
PyInstaller-Bundle) bleibt weiterhin eine lokal installierte Tesseract-
Instanz (PATH oder `TESSERACT_CMD`) nötig.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

import pymupdf
import pytesseract
from PIL import Image

from app.documents.extraction import IMAGE_EXTENSIONS


class OcrError(Exception):
    """Fehler während der OCR-Verarbeitung (z. B. Tesseract nicht
    gefunden/nicht ausführbar). Wird bewusst als eigene Exception-Klasse
    geführt, damit der aufrufende Service den Dokumentstatus korrekt auf
    "failed" statt "done" setzen kann."""


def _bundle_base_dir() -> Path:
    """Wie `run.py::_bundle_base_dir` (bewusst unabhängig re-implementiert,
    um diesem Modul keine Abhängigkeit auf `run.py` aufzuerlegen) - im
    Dev-Betrieb das Repository-Root, im gebündelten Produkt das von
    PyInstaller bereitgestellte Verzeichnis neben `kanzlei_ai.exe`
    (onedir-Build, siehe windows/kanzlei_ai.spec: KEIN `sys._MEIPASS`-
    Extraktionsverzeichnis, die Bundle-Dateien liegen direkt neben der
    .exe)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent.parent


def _bundled_tesseract_paths() -> tuple[Path, Path] | None:
    """Liefert `(tesseract.exe, tessdata-Verzeichnis)` des mit dem Windows-
    Installer gebündelten Tesseract, falls vorhanden - `None`, wenn diese
    Anwendung nicht als PyInstaller-Bundle läuft oder die Dateien fehlen
    (z. B. ein älterer, vor diesem Fix erzeugter Build). Reine Pfadprüfung,
    kein Ausführen/Netzwerkzugriff."""
    base = _bundle_base_dir()
    exe = base / "tesseract" / "bin" / "tesseract.exe"
    tessdata = base / "tesseract" / "tessdata"
    if exe.is_file() and tessdata.is_dir():
        return exe, tessdata
    return None


def configure_tesseract(tesseract_cmd: str | None) -> None:
    """Setzt den zu verwendenden Tesseract-Pfad.

    Reihenfolge (erste zutreffende gewinnt):
    1. `tesseract_cmd` (explizite `TESSERACT_CMD`-Konfiguration) - manueller
       Override hat immer Vorrang, z. B. für eine bereits vorhandene
       System-Installation oder eine abweichende Version.
    2. Mit dem Installer gebündeltes Tesseract (siehe
       `_bundled_tesseract_paths`) - läuft ohne jede weitere manuelle
       Installation auf dem Zielsystem.
    3. Unverändertes `pytesseract`-Standardverhalten (Suche nach
       "tesseract" im PATH) - insbesondere der Dev-Betrieb ohne Bundle.
    """
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        return

    bundled = _bundled_tesseract_paths()
    if bundled is not None:
        exe, tessdata = bundled
        pytesseract.pytesseract.tesseract_cmd = str(exe)
        # Tesseract sucht Sprachdaten sonst relativ zur .exe an einem fest
        # einprogrammierten Pfad, der von unserer Bundle-Struktur abweichen
        # kann - TESSDATA_PREFIX macht den Ort explizit statt sich auf eine
        # zufällig passende Heuristik zu verlassen.
        os.environ["TESSDATA_PREFIX"] = str(tessdata)


@lru_cache(maxsize=1)
def tesseract_health_check() -> tuple[bool, str]:
    """Deterministische, seiteneffektarme Prüfung, ob Tesseract tatsächlich
    aufrufbar ist - für den System-Health-/Setup-Bereich (verständliche
    Fehlermeldung STATT eines erst dokumentweise auffallenden
    `TesseractNotFoundError` nach mehreren Retry-Versuchen). Ergebnis wird
    für die Prozesslaufzeit gecacht (Tesseract-Verfügbarkeit ändert sich
    nicht während eines laufenden Serverprozesses).

    Gibt `(verfügbar, für den Anwalt verständliche Meldung)` zurück - ruft
    absichtlich `configure_tesseract(None)` NICHT selbst auf (das bleibt
    Aufgabe des Aufrufers/`DocumentProcessingService`, damit eine explizite
    `TESSERACT_CMD`-Konfiguration konsistent berücksichtigt wird)."""
    try:
        version = pytesseract.get_tesseract_version()
    except Exception:  # noqa: BLE001 - jede Form von "nicht verfügbar" abfangen
        return False, (
            "Tesseract OCR wurde nicht gefunden. Gescannte Dokumente/Bilder "
            "können nicht per Texterkennung verarbeitet werden. Prüfen Sie "
            "die Installation (siehe README.md/ARCHITECTURE.md, Abschnitt "
            "OCR) oder deaktivieren Sie OCR_ENABLED, falls nicht benötigt."
        )
    return True, f"Tesseract OCR verfügbar (Version {version})."


def run_ocr(path: Path, *, languages: str = "deu+eng", dpi: int = 200) -> str:
    """Führt OCR auf einer Datei aus und gibt den erkannten Text zurück.

    Wirft `OcrError`, wenn die Datei weder als PDF noch als unterstütztes
    Bildformat erkannt wird, oder wenn Tesseract selbst fehlschlägt.
    """
    suffix = path.suffix.lower()

    try:
        if suffix == ".pdf":
            return _run_ocr_on_pdf(path, languages=languages, dpi=dpi)
        if suffix in IMAGE_EXTENSIONS:
            return _run_ocr_on_image(path, languages=languages)
    except Exception as exc:  # noqa: BLE001 - in OcrError kapseln
        # SICHERHEITSKRITISCH (Prompt 31, gefunden bei der Absicherung des
        # neuen Fehler-/Retry-Systems): weder der volle Dateipfad NOCH die
        # Nachricht der zugrunde liegenden Exception (`str(exc)`) dürfen
        # hier verwendet werden - Bibliotheken wie PyMuPDF/PIL betten den
        # Dateipfad standardmäßig in IHRE EIGENE Fehlermeldung ein (z. B.
        # "no such file: '.../Max_Mustermann_Steuerbescheid.pdf'"), auch
        # wenn der eigene f-String keinen Pfad mehr referenziert. Nur der
        # EXCEPTION-TYP (z. B. "FileNotFoundError") ist sicher - enthält
        # nie Datei-/Personennamen. Die vollständige Original-Exception
        # bleibt über `from exc` im Stacktrace/`__cause__` erhalten, falls
        # später tiefergehendes (lokales) Debugging nötig ist - landet
        # aber NICHT im persistierten `error_message`/Audit-Log.
        raise OcrError(f"OCR fehlgeschlagen: {type(exc).__name__}") from exc

    raise OcrError(f"OCR wird für dieses Dateiformat nicht unterstützt: {suffix}")


def _run_ocr_on_pdf(path: Path, *, languages: str, dpi: int) -> str:
    text_parts: list[str] = []
    with pymupdf.open(path) as pdf:
        for page in pdf:
            pixmap = page.get_pixmap(dpi=dpi)
            image = Image.frombytes(
                "RGB", (pixmap.width, pixmap.height), pixmap.samples
            )
            text_parts.append(pytesseract.image_to_string(image, lang=languages))
    return "\n".join(text_parts)


def _run_ocr_on_image(path: Path, *, languages: str) -> str:
    with Image.open(path) as image:
        return pytesseract.image_to_string(image, lang=languages)
