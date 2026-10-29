"""Bundle-Pruefung eines PyInstaller-onedir-Builds gegen den Quellstand. Usage: bundle_check.py <dist_Lexono_dir> <out_json>"""
import json, sys
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

D, OUT = Path(sys.argv[1]), Path(sys.argv[2])
REPO = Path(r"C:\Users\Bonit\Lexono")
internal = D / "_internal"
car = CArchiveReader(str(D / "Lexono.exe"))
pyz_name = next(n for n in car.toc if n.endswith(".pyz") or n == "PYZ.pyz" or "PYZ" in n)
pyz_path = internal / "base_library.zip"  # Platzhalter; PYZ liegt im CArchive
import tempfile, os
tmp = Path(tempfile.mkdtemp())
data = car.extract(pyz_name)
(tmp / "PYZ.pyz").write_bytes(data if isinstance(data, bytes) else data[1])
pyz = ZlibArchiveReader(str(tmp / "PYZ.pyz"))
mods = set(pyz.toc.keys())
app_mods = sorted(m for m in mods if m == "app" or m.startswith("app."))
src = set()
for p in (REPO / "app").rglob("*.py"):
    rel = p.relative_to(REPO).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    src.add(".".join(parts))
expected_missing = {"app.fibu", "app.fibu.service", "app.web.fibu_router"}
R = {
    "profil_datei": (internal / "lexono_build_profile.txt").read_text(encoding="utf-8").strip() if (internal / "lexono_build_profile.txt").exists() else None,
    "app_module_im_bundle": len(app_mods),
    "finance_laufzeit_im_bundle": sorted(m for m in mods if m in expected_missing),
    "finance_templates": [str(p.relative_to(internal)) for p in internal.rglob("fibu*") if p.is_file()],
    "migrationen_040_042": sorted(p.name for p in (internal / "migrations" / "versions").glob("schritt3_04[0-2]_*.py")),
    "migrationen_gesamt": len(list((internal / "migrations" / "versions").glob("*.py"))),
    "quelle_ohne_bundle": sorted(m for m in src - set(app_mods) if not m.startswith("app.fibu") and m != "app.web.fibu_router"),
    "bundle_ohne_quelle": sorted(set(app_mods) - src),
    "lizenz_und_profil_code": {m: (m in mods) for m in ("app.modules.licensing", "app.modules.profile", "app.modules.plans",
                                                          "app.models.fibu", "app.web.chat_router", "app.web.settings_router")},
    "settings_template_mit_lizenzseite": "Lexono Produkte" in (internal / "app" / "web" / "templates" / "settings.html").read_text(encoding="utf-8"),
    "center_js_mit_fix": "readUnsentOrder" in (internal / "app" / "web" / "static" / "js" / "pseudonymization_center.js").read_text(encoding="utf-8"),
    "chat_template_mit_ladezustand": "window.LexonoChatProgress" in (internal / "app" / "web" / "templates" / "chat.html").read_text(encoding="utf-8"),
}
OUT.write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(R, ensure_ascii=False, indent=1))
