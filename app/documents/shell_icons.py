"""Native Windows-Shell-Dateisymbole fuer Dokumentlisten (03.10., Owner-
Direktive "INDIVIDUELLE MANDANTENDETAILSEITE", Referenzabgleich
`30_mandant_detail.png` §5.8).

ECHTE Windows-Shell-Integration ueber `shell32.SHGetFileInfoW` mit
`SHGFI_USEFILEATTRIBUTES` - genau die vom Auftrag selbst genannte API.
`SHGFI_USEFILEATTRIBUTES` ist hier bewusst gewaehlt (nicht ein Aufruf mit
dem echten Dateipfad): liefert das von Windows fuer eine Dateiendung
REGISTRIERTE Symbol rein aus der Endung, OHNE die echte Datei zu oeffnen,
ihren Inhalt zu lesen oder ihre Existenz vorauszusetzen - erfuellt damit
ausdruecklich "darf keine Dokumente oeffnen... sensible Inhalte auslesen"
UND "Leite den Typ nicht aus dem Dokumentinhalt ab, verwende die
tatsaechliche Dateiendung". Nebeneffekt: das Ergebnis haengt NUR von der
Endung ab, nicht vom einzelnen Dokument - der Cache (`_icon_cache`) ist
deshalb korrekt pro Endung (nicht pro Datei) gehalten, siehe Auftrag §9
("Caching... um unnoetige wiederholte Shell-Aufrufe zu verhindern").

Die Umwandlung des rohen `HICON`-Handles in ein PNG (fuer die Einbettung
als `data:`-URI in server-gerendertem HTML) nutzt `System.Drawing` ueber
pythonnet (`clr`) - KEINE neue Abhaengigkeit: pywebviews WinForms-Backend
(siehe `.venv/Lib/site-packages/webview/platforms/winforms.py`) baut
bereits vollstaendig darauf auf, dieselbe Technik, die `run.py` zuvor
schon fuer Fenster-Icon-Handling genutzt hat.

Server-seitig statt per JS-Bridge bewusst gewaehlt: Lexono laeuft als
Server-Prozess sowohl im nativen WebView2-Fenster als auch im reinen
Browser-/Dev-Modus (`--no-window`) - eine serverseitige Umsetzung
funktioniert in BEIDEN Faellen identisch (der Windows-Prozess, der die
Icons extrahiert, ist in beiden Faellen derselbe), waehrend eine JS-
Bridge-Loesung (`window.pywebview.api...`) im reinen Browser-Modus gar
nicht existieren wuerde. Kein neuer Hintergrundprozess, keine
Architekturaenderung - nur ein zusaetzlicher, synchroner Python-Aufruf
waehrend des bereits bestehenden Template-Renderings.

Nur unter Windows verfuegbar - auf anderen Plattformen (z. B. eine
Linux-CI, die diese Tests ausfuehrt) liefert `get_shell_icon_data_uri`
zuverlaessig `None`, die Template-Seite faellt dann auf den bereits
bestehenden CSS-/SVG-Fallback zurueck (`_icons.html::file_type_badge`) -
siehe Auftrag §6 ("sichtbarer, konsistenter Lexono-Fallback")."""

from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes

_SHGFI_ICON = 0x000000100
_SHGFI_SMALLICON = 0x000000001
_SHGFI_USEFILEATTRIBUTES = 0x000000010
_FILE_ATTRIBUTE_NORMAL = 0x00000080


class _SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


#: Pro Dateiendung (kleingeschrieben, mit fuehrendem Punkt, z. B. ".pdf")
#: gehaltener Cache - siehe Moduldocstring zur Begruendung, warum ein
#: Cache nach Endung (nicht nach Datei) hier korrekt ist. `None` als Wert
#: bedeutet "fuer diese Endung real versucht, aber kein Icon verfuegbar"
#: (unterscheidet sich von "noch nie versucht" = Schluessel fehlt) - ein
#: einmaliger Fehlschlag loest also nicht bei jeder weiteren Zeile erneut
#: einen teuren Shell-Aufruf aus.
_icon_cache: dict[str, str | None] = {}


def get_shell_icon_data_uri(filename_or_extension: str | None) -> str | None:
    """Liefert das native Windows-Shell-Symbol fuer die Dateiendung von
    `filename_or_extension` als `data:image/png;base64,...`-URI, oder
    `None`, wenn nicht unter Windows, ohne Endung, oder bei jedem
    Fehlschlag der Shell-/GDI-Aufrufe (rein kosmetisch, darf eine
    Dokumentliste nie zum Absturz bringen - siehe breites `except
    Exception` unten)."""
    if not filename_or_extension:
        return None
    ext = _normalize_extension(filename_or_extension)
    if not ext:
        return None
    if ext in _icon_cache:
        return _icon_cache[ext]

    result = _extract_icon_for_extension(ext) if sys.platform == "win32" else None
    _icon_cache[ext] = result
    return result


def _normalize_extension(filename_or_extension: str) -> str:
    value = filename_or_extension.strip().lower()
    if "." in value:
        value = "." + value.rsplit(".", 1)[-1]
    elif value and not value.startswith("."):
        value = "." + value
    return value if value != "." else ""


def _extract_icon_for_extension(ext: str) -> str | None:
    try:
        info = _SHFILEINFOW()
        # Nur die Endung zaehlt (SHGFI_USEFILEATTRIBUTES) - der restliche
        # "Dateiname" ist beliebig/nicht real vorhanden, siehe Moduldocstring.
        fake_name = "lexono_dummy" + ext
        flags = _SHGFI_ICON | _SHGFI_SMALLICON | _SHGFI_USEFILEATTRIBUTES
        result = ctypes.windll.shell32.SHGetFileInfoW(
            fake_name,
            _FILE_ATTRIBUTE_NORMAL,
            ctypes.byref(info),
            ctypes.sizeof(info),
            flags,
        )
        if not result or not info.hIcon:
            return None

        try:
            import clr  # type: ignore[import-not-found]

            clr.AddReference("System.Drawing")
            from System import IntPtr  # type: ignore[import-not-found]
            from System.Drawing import Icon  # type: ignore[import-not-found]
            from System.Drawing.Imaging import ImageFormat  # type: ignore[import-not-found]
            from System.IO import MemoryStream  # type: ignore[import-not-found]

            net_icon = Icon.FromHandle(IntPtr(info.hIcon))
            bitmap = net_icon.ToBitmap()
            stream = MemoryStream()
            bitmap.Save(stream, ImageFormat.Png)
            png_bytes = bytes(stream.ToArray())
        finally:
            ctypes.windll.user32.DestroyIcon(info.hIcon)

        if not png_bytes:
            return None
        return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
    except Exception:  # noqa: BLE001 - rein kosmetisch, Fallback uebernimmt
        return None
