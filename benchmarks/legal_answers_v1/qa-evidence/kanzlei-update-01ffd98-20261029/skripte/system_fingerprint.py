"""Nur lesender Fingerprint des gemeinsam genutzten Kanzlei-Systems (installierte App, produktive Daten, Registrierung,
Verknuepfungen). Liest Dateien nur; oeffnet die Datenbank NICHT mit SQLite (nur Groesse/Zeit/Hash der Datei).
Usage: system_fingerprint.py <out_json>"""
import hashlib, json, os, sys, winreg
from pathlib import Path

OUT = Path(sys.argv[1])
LA = Path(os.environ["LOCALAPPDATA"]) / "Lexono"
PD = Path(r"C:\ProgramData\Lexono")


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def meta(p: Path) -> dict:
    if not p.exists():
        return {"vorhanden": False}
    st = p.stat()
    return {"vorhanden": True, "bytes": st.st_size, "mtime": st.st_mtime, "sha256": sha(p) if p.is_file() else None}


R = {"installierte_app": {"exe": meta(LA / "Lexono.exe"),
                          "dateien": sum(1 for _ in LA.rglob("*") if _.is_file()) if LA.exists() else 0,
                          "migrationen": sorted(x.name for x in (LA / "_internal" / "migrations" / "versions").glob("*.py"))[-4:] if LA.exists() else []},
     "produktiv_daten": {"db": meta(PD / "data" / "kanzlei_ai.db"), "env": meta(PD / ".env"),
                         "setup_complete": meta(PD / ".setup_complete")},
     "uninstall_eintraege": [], "verknuepfungen": {}}
for hive, name in ((winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
    try:
        key = winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")
    except OSError:
        continue
    i = 0
    while True:
        try:
            sub = winreg.EnumKey(key, i)
        except OSError:
            break
        i += 1
        try:
            with winreg.OpenKey(key, sub) as sk:
                dn = winreg.QueryValueEx(sk, "DisplayName")[0]
        except OSError:
            continue
        if "lexono" in str(dn).lower():
            R["uninstall_eintraege"].append(f"{name}\\{sub} = {dn}")
for label, base in (("desktop", Path(os.environ["USERPROFILE"]) / "Desktop"),
                    ("desktop_onedrive", Path(os.environ["USERPROFILE"]) / "OneDrive" / "Desktop"),
                    ("startmenue", Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs")):
    if base.exists():
        R["verknuepfungen"][label] = {str(p.relative_to(base)): meta(p)["sha256"] for p in base.rglob("*Lexono*.lnk")}
OUT.write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(R, ensure_ascii=False, indent=1)[:2500])
