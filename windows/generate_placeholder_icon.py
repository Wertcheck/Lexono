"""Erzeugt `windows/app_icon.ico` aus dem echten Lexono-Markenzeichen
(Prompt 47; Markenumbenennung "Kanzlei-AI" -> "Lexono"; offizielles
Dokument+Schild+Kette-Logo).

Historisch hiess dieses Skript "Platzhalter", weil zunaechst kein echtes
Kanzlei-/Produktlogo vorlag - der Dateiname wurde bewusst NICHT geaendert
(siehe Referenzen in windows/installer.iss, windows/lexono.spec, README.md,
ARCHITECTURE.md), nur der Inhalt.

ECHTER FUND (realer Abnahme-Test, 13.09.): die vorherige Version zeichnete
die Markengeometrie manuell aus per Hand abgetippten Polygon-Koordinaten
nach (fragil, fehleranfaellig) UND nutzte dabei die MITTLERWEILE VERALTETE
Navy-Farbe `#101828` - nicht mehr konsistent mit dem tatsaechlichen, seit
dem Referenzbild-Redesign gueltigen gruenen Schild-Logo
(`app/web/static/img/logo-mark.png`, ueberall sonst in der UI verwendet).
Das erzeugte `.ico` war zudem tatsaechlich korrupt (beim Extrahieren nur
Bildrauschen statt der Markengeometrie, real per PowerShell/System.Drawing
geprueft) - vermutlich ein Fehler in der manuellen XOR-Masken-Rekonstruktion
oder der Icon-Kodierung selbst.

Deutlich einfacher und robuster: das bereits vorhandene, bereits korrekte
Raster-Logo (`logo-mark.png`) direkt verwenden - zentriert auf einer
transparenten quadratischen Leinwand (das Schild ist hoeher als breit),
in mehreren Standardgroessen als Multi-Resolution-.ico gespeichert. Keine
Nachzeichnung, keine Farbentscheidung hier - das Icon ist danach garantiert
identisch zum ueberall sonst verwendeten Markenzeichen.

Erneutes Ausführen (aus dem Projekt-Root, aktivierte venv, "pillow" ist
bereits Projektabhängigkeit):

    python windows/generate_placeholder_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

_SOURCE_PATH = (
    Path(__file__).resolve().parent.parent / "app" / "web" / "static" / "img" / "logo-mark.png"
)
_OUTPUT_PATH = Path(__file__).resolve().parent / "app_icon.ico"
_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _render_square(mark: Image.Image, size: int) -> Image.Image:
    """Zentriert `mark` (RGBA, hoeher als breit) auf einer transparenten
    quadratischen Leinwand der Kantenlaenge `size`, mit kleinem Rand."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    margin = size * 0.06
    box = size - 2 * margin
    scale = box / max(mark.size)
    target_w = max(1, round(mark.width * scale))
    target_h = max(1, round(mark.height * scale))
    resized = mark.resize((target_w, target_h), Image.LANCZOS)
    offset_x = round((size - target_w) / 2)
    offset_y = round((size - target_h) / 2)
    canvas.paste(resized, (offset_x, offset_y), resized)
    return canvas


def main() -> None:
    mark = Image.open(_SOURCE_PATH).convert("RGBA")
    largest = _render_square(mark, max(_SIZES))
    largest.save(str(_OUTPUT_PATH), format="ICO", sizes=[(s, s) for s in _SIZES])
    print(f"Icon geschrieben: {_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
