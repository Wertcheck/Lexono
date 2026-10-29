"""Vollstaendiges Backup vor dem Update (nur LESEND auf dem Produktivsystem). Kopiert alle persistenten Orte, sichert die
Datenbank zusaetzlich konsistent ueber die SQLite-Backup-API (Quelle nur lesend geoeffnet) und prueft danach JEDE Datei
per SHA-256 sowie Integritaet/Zeilenzahlen der Datenbank. Gibt keine Inhalte aus.
Usage: prod_backup.py <ziel_verzeichnis> <bericht_json>"""
import hashlib, json, os, shutil, sqlite3, sys, time
from pathlib import Path

DEST, REPORT = Path(sys.argv[1]), Path(sys.argv[2])
assert not DEST.exists(), "Ziel existiert bereits"
SOURCES = {
    "ProgramData_Lexono": Path(r"C:\ProgramData\Lexono"),
    "ProgramData_KanzleiAI": Path(r"C:\ProgramData\KanzleiAI"),
    "LocalAppData_Lexono_App": Path(os.environ["LOCALAPPDATA"]) / "Lexono",
}
DB = Path(r"C:\ProgramData\Lexono\data\kanzlei_ai.db")


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def db_summary(path: Path) -> dict:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' order by name")]
    counts = {t: c.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}
    digest = hashlib.sha256()
    for t in tables:
        for row in c.execute(f'select * from "{t}" order by rowid'):
            digest.update(repr(row).encode())
    out = {"revision": c.execute("select version_num from alembic_version").fetchone()[0],
           "integrity_check": c.execute("pragma integrity_check").fetchone()[0], "tabellen": len(tables),
           "zeilen_gesamt": sum(counts.values()), "zeilen": counts, "inhalt_sha256": digest.hexdigest()}
    c.close()
    return out


R = {"zeit": time.strftime("%Y-%m-%d %H:%M:%S"), "ziel": str(DEST), "quellen": {}, "db": {}}
DEST.mkdir(parents=True)
for name, src in SOURCES.items():
    if not src.exists():
        R["quellen"][name] = {"pfad": str(src), "vorhanden": False}
        continue
    files = [p for p in src.rglob("*") if p.is_file()]
    src_hash = {str(p.relative_to(src)): sha(p) for p in files}
    shutil.copytree(src, DEST / name, copy_function=shutil.copy2)
    dst_hash = {str(p.relative_to(DEST / name)): sha(p) for p in (DEST / name).rglob("*") if p.is_file()}
    R["quellen"][name] = {"pfad": str(src), "vorhanden": True, "dateien": len(src_hash), "bytes": sum(p.stat().st_size for p in files),
                          "kopiert": len(dst_hash), "alle_hashes_gleich": src_hash == dst_hash,
                          "abweichend": sorted(k for k in src_hash if src_hash.get(k) != dst_hash.get(k))[:20]}
# konsistente DB-Sicherung ueber die Backup-API
api_copy = DEST / "kanzlei_ai.sqlite-backup-api.db"
src_c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
dst_c = sqlite3.connect(api_copy)
src_c.backup(dst_c)
dst_c.close(); src_c.close()
R["db"]["quelle"] = db_summary(DB)
R["db"]["rohkopie"] = db_summary(DEST / "ProgramData_Lexono" / "data" / "kanzlei_ai.db")
R["db"]["backup_api"] = db_summary(api_copy)
R["db"]["identisch"] = (R["db"]["quelle"]["inhalt_sha256"] == R["db"]["rohkopie"]["inhalt_sha256"] == R["db"]["backup_api"]["inhalt_sha256"])
R["vollstaendig"] = all(q.get("alle_hashes_gleich", True) for q in R["quellen"].values()) and R["db"]["identisch"] \
    and R["db"]["quelle"]["integrity_check"] == "ok"
REPORT.write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
(DEST / "backup_bericht.json").write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: R[k] for k in ("ziel", "vollstaendig")} | {"quellen": {k: {x: v.get(x) for x in ("vorhanden", "dateien", "bytes", "alle_hashes_gleich")} for k, v in R["quellen"].items()},
                 "db": {k: (v if not isinstance(v, dict) else {x: v[x] for x in ("revision", "integrity_check", "tabellen", "zeilen_gesamt")}) for k, v in R["db"].items()}}, ensure_ascii=False, indent=1))
