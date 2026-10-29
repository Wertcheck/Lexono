"""Nachpruefung mit den ECHTEN Daten (nur lesend, ohne Anmeldung, keine Inhalte ausgeben): startet die installierte
Lexono.exe ohne Fenster mit dem Standard-Datenverzeichnis, prueft Start/Migration, unauthentifizierte Endpunkte,
danach Revision/Integritaet/Zeilenzahlen gegen das Backup. Usage: prod_postcheck.py <out_json>"""
import hashlib, json, os, sqlite3, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

OUT = Path(sys.argv[1])
APP = Path(os.environ["LOCALAPPDATA"]) / "Lexono" / "Lexono.exe"
PD = Path(r"C:\ProgramData\Lexono")
DB = PD / "data" / "kanzlei_ai.db"
BACKUP_REPORT = json.loads(Path(r"C:\Users\Bonit\LexonoBackups\vor-update-01ffd98-20261029-1726\backup_bericht.json").read_text(encoding="utf-8"))
env_vals = {}
for line in (PD / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1); env_vals[k.strip()] = v.strip()
port = int(env_vals.get("PORT", "8000")); host = "127.0.0.1"
BASE = f"http://{host}:{port}"


def db_summary():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' order by name")]
    counts = {t: c.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}
    out = {"revision": c.execute("select version_num from alembic_version").fetchone()[0],
           "integrity_check": c.execute("pragma integrity_check").fetchone()[0], "zeilen": counts}
    c.close()
    return out


def get(path):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPRedirectHandler())
    try:
        r = op.open(BASE + path, timeout=30)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


R = {"port_aus_konfiguration": port}
env = {k: v for k, v in os.environ.items() if k not in ("LEXONO_DATA_DIR", "DATABASE_URL", "LEXONO_BUILD_PROFILE",
                                                         "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL")}
t0 = time.time()
proc = subprocess.Popen([str(APP), "serve", "--no-window"], cwd=str(APP.parent), env=env,
                        stdout=open(Path(os.environ["TEMP"]) / "lexono_postcheck.out", "w"), stderr=subprocess.STDOUT)
try:
    up = False
    for _ in range(240):
        try:
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(BASE + "/dashboard/login", timeout=3); up = True; break
        except Exception:
            time.sleep(1)
    R["start_ok"] = up; R["start_sekunden"] = round(time.time() - t0, 1)
    R["login_seite"] = get("/dashboard/login")[0]
    R["fibu_ohne_route"] = get("/dashboard/fibu")[0]
    R["fibu_css"] = get("/dashboard/static/css/fibu.css")[0]
    s, body = get("/dashboard/static/js/pseudonymization_center.js")
    R["pseudo_js_mit_fix"] = (s, b"readUnsentOrder" in body)
    s, body = get("/dashboard/static/css/app.css")
    R["app_css_lizenzseite"] = (s, b"license-product__state" in body)
finally:
    subprocess.run(["taskkill", "/T", "/PID", str(proc.pid)], capture_output=True); time.sleep(4)
    if proc.poll() is None:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True); time.sleep(2)
log = (Path(os.environ["TEMP"]) / "lexono_postcheck.out").read_text(encoding="utf-8", errors="replace")
R["log_tracebacks"] = log.count("Traceback")
R["log_migration_hinweise"] = [l.strip()[:160] for l in log.splitlines() if "alembic" in l.lower() or "migrat" in l.lower()][:6]
after = db_summary()
before = BACKUP_REPORT["db"]["quelle"]
R["revision_vorher_nachher"] = (before["revision"], after["revision"])
R["integrity_nachher"] = after["integrity_check"]
diff = {t: (before["zeilen"].get(t, 0), after["zeilen"].get(t, 0)) for t in set(before["zeilen"]) | set(after["zeilen"])
        if before["zeilen"].get(t, 0) != after["zeilen"].get(t, 0)}
R["zeilen_aenderungen_durch_start"] = diff
R["zeilen_gesamt_vorher_nachher"] = (sum(before["zeilen"].values()), sum(after["zeilen"].values()))
R["kerntabellen_unveraendert"] = all(before["zeilen"].get(t) == after["zeilen"].get(t) for t in
                                     ("clients", "matters", "documents", "chat_conversations", "chat_messages", "drafts",
                                      "users", "privacy_decisions", "module_licenses", "deadlines"))
OUT.write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(R, ensure_ascii=False, indent=1))
