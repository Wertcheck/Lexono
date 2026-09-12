"""Erzeugt `windows/app_icon.ico` aus dem echten Lexono-Markenzeichen
(Prompt 47; Markenumbenennung "Kanzlei-AI" -> "Lexono"; offizielles
Dokument+Schild+Kette-Logo, 20.08.).

Historisch hiess dieses Skript "Platzhalter", weil zunaechst kein echtes
Kanzlei-/Produktlogo vorlag (Kreissiegel-Motiv mit Initialen "KA") - der
Dateiname wurde bewusst NICHT geaendert (siehe Referenzen in
windows/installer.iss, windows/lexono.spec, README.md, ARCHITECTURE.md),
nur der Inhalt: das Icon rastert dieselbe Pfadgeometrie wie
app/web/static/img/logo.svg (das offizielle Lexono-Icon: Dokument mit
umgeknickter Ecke, zwei Textzeilen, darunter ein Schild mit Kettenglied)
auf einem abgerundeten Quadrat im neuen CI-Farbcode `#101828`.

Die Icon-Geometrie besteht aus mehreren, teils ineinander verschachtelten
Konturen (z. B. die weissen Zwischenraeume im Kettenglied, das hohle
Schild-Innere) - `fill-rule="evenodd"` im SVG loest das elegant, PIL kennt
kein evenodd direkt, daher hier per XOR mehrerer Einzelmasken nachgebildet
(_render_mark_mask) - exakt dieselbe Technik, mit der die Pfadgeometrie
waehrend der Session gegen die Vorlage verifiziert wurde.

Erneutes Ausführen (aus dem Projekt-Root, aktivierte venv, "pillow" ist
bereits Projektabhängigkeit):

    python windows/generate_placeholder_icon.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

#: Neuer Corporate-Identity-Farbcode (20.08.) - ersetzt fuer Icon/
#: Desktop-Verknuepfung/Installer-Elemente das vorherige --ink-900
#: (#0F172A); die allgemeinen UI-Text-/Akzentfarben (app.css) sind davon
#: NICHT betroffen, das war nicht Teil dieser Anfrage.
_CI_INK = (16, 24, 40)  # #101828
_PAPER = (255, 255, 255)
_SIZES = (16, 24, 32, 48, 64, 128, 256)
_OUTPUT_PATH = Path(__file__).resolve().parent / "app_icon.ico"

#: Identische Pfadgeometrie wie app/web/static/img/logo.svg (viewBox
#: "0 0 180 320") - aus der vom Anwalt bereitgestellten offiziellen
#: Logo-Datei nachgezeichnet (Konturerkennung + Polygon-Vereinfachung,
#: siehe Session). Reihenfolge/Anzahl der Konturen ist fuer die
#: XOR-Loecher-Rekonstruktion irrelevant, nur die einzelnen Polygone
#: muessen vollstaendig sein.
_MARK_VIEWBOX = (180, 320)
_MARK_POLYGONS = [
    [(89, 136), (18, 163), (18, 222), (28, 253), (49, 276), (88, 301), (114, 287),
     (139, 267), (151, 252), (160, 228), (160, 163)],
    [(126, 169), (139, 188), (134, 209), (114, 227), (99, 228), (91, 223), (82, 232),
     (75, 229), (74, 223), (83, 213), (73, 212), (55, 233), (62, 247), (76, 248),
     (93, 232), (99, 232), (102, 240), (77, 262), (66, 263), (51, 256), (42, 241),
     (45, 221), (63, 203), (73, 198), (91, 201), (103, 198), (106, 207), (101, 214),
     (111, 213), (125, 198), (124, 186), (118, 180), (106, 179), (88, 195), (80, 194),
     (78, 187), (102, 166), (116, 165)],
    [(132, 24), (132, 43), (151, 43), (151, 42), (145, 36), (144, 36)],
    [(86, 121), (164, 148), (174, 155), (174, 228), (164, 258), (139, 287), (92, 316),
     (85, 316), (57, 300), (25, 273), (10, 250), (4, 227), (4, 155), (9, 150)],
    [(37, 95), (42, 89), (132, 89), (136, 93), (137, 98), (131, 104), (43, 104), (38, 100)],
    [(37, 61), (38, 60), (38, 58), (42, 54), (94, 54), (98, 59), (98, 63), (97, 65),
     (92, 69), (43, 69), (38, 65)],
    [(7, 9), (15, 3), (130, 3), (172, 45), (171, 129), (162, 132), (158, 128), (158, 59),
     (128, 58), (119, 51), (117, 17), (21, 17), (19, 126), (9, 132), (4, 126)],
]


def _render_mark_mask(size_x: int, size_y: int, scale: float, offset_x: float, offset_y: float) -> Image.Image:
    """Rendert die Markengeometrie als "L"-Graustufenmaske (255 = sichtbar)
    mit korrekter Loch-Behandlung - per XOR mehrerer Einzelpolygon-Masken
    nachgebildetes `fill-rule="evenodd"` (siehe Moduldocstring)."""
    accum = np.zeros((size_y, size_x), dtype=bool)
    for polygon in _MARK_POLYGONS:
        scaled = [(offset_x + x * scale, offset_y + y * scale) for x, y in polygon]
        layer = Image.new("1", (size_x, size_y), 0)
        ImageDraw.Draw(layer).polygon(scaled, fill=1)
        accum ^= np.array(layer, dtype=bool)
    mask = Image.fromarray((accum * 255).astype(np.uint8), mode="L")
    return mask


def _render_at(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = max(1, size // 16)
    radius = max(2, size // 6)
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad], radius=radius, fill=_CI_INK + (255,)
    )

    # Markenzeichen mittig einpassen, mit Rand innerhalb des abgerundeten
    # Quadrats - Seitenverhaeltnis (schmaler/hoeher als das vorherige
    # Schild-Icon) bleibt erhalten, kein Verzerren auf ein Quadrat.
    inner = size - 2 * pad
    mark_margin = inner * 0.14
    mark_box = inner - 2 * mark_margin
    scale = mark_box / max(_MARK_VIEWBOX)
    mark_w = _MARK_VIEWBOX[0] * scale
    mark_h = _MARK_VIEWBOX[1] * scale
    offset_x = pad + mark_margin + (mark_box - mark_w) / 2
    offset_y = pad + mark_margin + (mark_box - mark_h) / 2

    mark_mask = _render_mark_mask(size, size, scale, offset_x, offset_y)
    paper_layer = Image.new("RGBA", (size, size), _PAPER + (255,))
    img.paste(paper_layer, (0, 0), mark_mask)

    return img


def main() -> None:
    largest = _render_at(max(_SIZES))
    largest.save(str(_OUTPUT_PATH), format="ICO", sizes=[(s, s) for s in _SIZES])
    print(f"Icon geschrieben: {_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
