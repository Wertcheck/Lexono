"""Erzeugt aus windows/installer.iss einen QA-Identitaets-Installer fuer eine ISOLIERTE Testinstallation:
gleiches Paket (dist\\Lexono), gleiches [InstallDelete], aber eigene AppId/Name/Mutex, Ziel im Job-Verzeichnis,
KEIN Uninstall-Registry-Eintrag, KEINE Verknuepfungen, KEIN WebView2-Systemschritt, kein Autostart.
Usage: make_qa_iss.py <dist_lexono_dir> <out_dir> <install_dir> <out_iss>"""
import sys
from pathlib import Path

REPO = Path(r"C:\Users\Bonit\Lexono")
dist, out_dir, install_dir, out_iss = (Path(a) for a in sys.argv[1:5])
src = (REPO / "windows" / "installer.iss").read_text(encoding="utf-8")
lines, section, kept = [], None, []
for line in src.splitlines():
    s = line.strip()
    if s.startswith("[") and s.endswith("]"):
        section = s
        if section in ("[Tasks]", "[Icons]", "[Run]", "[Code]"):
            continue
    if section in ("[Tasks]", "[Icons]", "[Run]", "[Code]"):
        continue
    if section == "[Setup]":
        key = s.split("=", 1)[0].strip() if "=" in s and not s.startswith(";") else None
        repl = {
            "AppId": "AppId={{6D2C8E41-5A7B-4F0E-9B3D-QA1500000001}",
            "AppName": "AppName=Lexono QA Isoliert",
            "AppMutex": "AppMutex=Lexono_QA_Isolated_Install_Mutex",
            "DefaultDirName": f"DefaultDirName={install_dir}",
            "DefaultGroupName": "DefaultGroupName=Lexono QA Isoliert",
            "OutputDir": f"OutputDir={out_dir}",
            "OutputBaseFilename": "OutputBaseFilename=Lexono_QA_Isolated_Setup",
            "SetupIconFile": f"SetupIconFile={REPO / 'windows' / 'app_icon.ico'}",
            "UninstallDisplayName": "UninstallDisplayName=Lexono QA Isoliert",
        }
        if key in repl:
            line = repl[key]
    if section == "[Files]" and "MicrosoftEdgeWebview2Setup" in s:
        continue
    if section == "[Files]" and s.startswith("Source:"):
        line = line.replace('"..\\dist\\Lexono\\*"', f'"{dist}\\*"').replace('"..\\Start.vbs"', f'"{REPO / "Start.vbs"}"')
    lines.append(line)
text = "\n".join(lines)
text = text.replace("[Setup]\n", "[Setup]\nUninstallable=no\nCreateUninstallRegKey=no\nUsePreviousAppDir=no\nDisableDirPage=yes\nDisableReadyPage=yes\nDisableFinishedPage=yes\n", 1)
assert "AppId={{9F4B9E7A" not in text and "{autodesktop}" not in text and "{group}" not in text.split("[Setup]")[1]
out_iss.write_text(text, encoding="utf-8-sig")
print(out_iss)
