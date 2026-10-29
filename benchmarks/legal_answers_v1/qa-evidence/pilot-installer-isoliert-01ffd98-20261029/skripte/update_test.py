"""Isolierter Update-Test: Vorversion (Dateistand der installierten 7ce54d7, kopiert nach iso/app) legt im isolierten
Datenverzeichnis iso/data Daten an; danach installiert der QA-Identitaets-Installer (Paket 3d3e611) ueber iso/app;
geprueft werden Datenerhalt, Konfiguration, Migrationsordner, Profil und Start. Nur synthetische Daten, toter
Cloud-Endpunkt, keine Registry/Verknuepfungen. Usage: update_test.py <out_json>"""
import hashlib, http.cookiejar, json, os, re, sqlite3, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

T = Path(r"C:\Users\Bonit\.claude\jobs\02f3f8ce\tmp")
ISO = T / "iso"
APP, DATA = ISO / "app", ISO / "data"
SETUP = ISO / "setup_neu" / "Lexono_QA_Isolated_Setup.exe"
OUT = Path(sys.argv[1])
PORT = 8083
BASE = f"http://127.0.0.1:{PORT}"
R = {"vorher": {}, "nachher": {}, "pruefungen": {}}
assert "jobs" in str(ISO)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest().upper()


def env_for():
    env = dict(os.environ, LEXONO_DATA_DIR=str(DATA), HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9",
               NO_PROXY="127.0.0.1,localhost", ADMIN_EMAIL="qa-update@lexono.test", ADMIN_INITIAL_PASSWORD="QA-update-0000")
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "DATABASE_URL", "LEXONO_BUILD_PROFILE"):
        env.pop(k, None)
    return env


def db_state():
    c = sqlite3.connect(f"file:{DATA / 'data' / 'kanzlei_ai.db'}?mode=ro", uri=True)
    tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' order by name")]
    counts = {t: c.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}
    digest = {}
    for t in ("users", "clients", "matters", "documents", "chat_conversations", "chat_messages", "privacy_decisions"):
        if t in tables:
            rows = c.execute(f'select * from "{t}" order by 1').fetchall()
            digest[t] = hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest()[:16]
    rev = c.execute("select version_num from alembic_version").fetchone()[0]
    qc = c.execute("pragma quick_check").fetchone()[0]
    c.close()
    return {"revision": rev, "quick_check": qc, "zeilen": {k: v for k, v in counts.items() if v}, "inhalt_hash": digest}


def files_state():
    return {str(p.relative_to(DATA)): sha(p) for p in DATA.rglob("*") if p.is_file() and p.suffix not in (".log", ".db-wal", ".db-shm")
            and "logs" not in p.parts and p.name != "kanzlei_ai.db"}


def serve():
    proc = subprocess.Popen([str(APP / "Lexono.exe"), "serve", "--no-window"], cwd=str(DATA), env=env_for(),
                            stdout=open(ISO / f"serve_{int(time.time())}.out", "w"), stderr=subprocess.STDOUT)
    for _ in range(240):
        try:
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(BASE + "/dashboard/login", timeout=3); return proc
        except Exception:
            time.sleep(1)
    raise RuntimeError("Server startet nicht")


def stop(proc):
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True); time.sleep(3)


def session():
    cj = http.cookiejar.CookieJar()
    o = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), urllib.request.ProxyHandler({}))
    o.open(BASE + "/dashboard/login", timeout=30)
    r = o.open(urllib.request.Request(BASE + "/dashboard/login", data=urllib.parse.urlencode(
        {"email": "qa-update@lexono.test", "password": "QA-update-0000", "next": "/dashboard"}).encode()), timeout=30)
    # Produktion: Session-Cookie ist "Secure"; Chromium/WebView2 sendet es an 127.0.0.1 auch ueber http, urllib nicht -
    # nur fuer diesen Testclient wird das Flag lokal ignoriert.
    for ck in cj:
        ck.secure = False
    r = o.open(BASE + "/dashboard", timeout=60)
    return o, r.geturl()


def multipart(o, path, fields, files=None):
    import uuid
    b = "----QA" + uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for k, (name, data, ct) in (files or {}).items():
        body += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{name}\"\r\nContent-Type: {ct}\r\n\r\n".encode() + data + b"\r\n"
    body += f"--{b}--\r\n".encode()
    return o.open(urllib.request.Request(BASE + path, data=body, headers={"Content-Type": f"multipart/form-data; boundary={b}"}), timeout=300).read().decode("utf-8", "replace")


# --- 1) Vorversion: Datenverzeichnis anlegen, migrieren, Admin, Daten -------------------------------------------------
import shutil
if DATA.exists():
    shutil.rmtree(DATA)
(DATA / "data").mkdir(parents=True)
(DATA / ".env").write_text("\n".join([
    f"DATABASE_URL=sqlite:///{(DATA / 'data' / 'kanzlei_ai.db').as_posix()}", "APP_ENV=production", "LOCAL_AI_ENABLED=false",
    "HOST=127.0.0.1", f"PORT={PORT}", "MAIL_AUTO_SYNC_ENABLED=false",
    "WEB_SEARCH_ENABLED=false", "LAW_UPDATE_CHECK_ENABLED=false", "# QA-Konfigurationsmarker update-test-29102026 (muss erhalten bleiben)",
    "SESSION_SECRET_KEY=qa-update-" + os.urandom(16).hex(), ""]), encoding="utf-8")
(DATA / ".setup_complete").write_text("1", encoding="utf-8")
R["vorher"]["exe_sha256"] = sha(APP / "Lexono.exe")
R["vorher"]["profil_datei"] = (APP / "_internal" / "lexono_build_profile.txt").exists()
R["vorher"]["migrationsdateien"] = len(list((APP / "_internal" / "migrations" / "versions").glob("*.py")))
r = subprocess.run([str(APP / "Lexono.exe"), "migrate"], cwd=str(DATA), env=env_for(), capture_output=True, text=True, timeout=600)
R["vorher"]["migrate_exit"] = r.returncode
r = subprocess.run([str(APP / "Lexono.exe"), "create-admin"], cwd=str(DATA), env=env_for(), capture_output=True, text=True, timeout=600)
R["vorher"]["create_admin_exit"] = r.returncode
c = sqlite3.connect(DATA / "data" / "kanzlei_ai.db"); c.execute("update users set must_change_password=0"); c.commit(); c.close()
proc = serve()
try:
    o, landed = session()
    page = o.open(BASE + "/dashboard/chat", timeout=60).read().decode("utf-8", "replace")
    tok = lambda name, pg: (re.search(rf'name="{name}" value="([^"]+)"', pg) or [None, ""])[1]
    import pymupdf
    pdf = pymupdf.open(); pg = pdf.new_page()
    pg.insert_text((50, 90), "Sachstand (synthetisch): Wir bestaetigen den Eingang des Schreibens von Richter vom 16.03.2024.", fontsize=10)
    data = pdf.tobytes(); pdf.close()
    ev = multipart(o, "/dashboard/chat/send-stream", {"upload_token": tok("upload_token", page), "csrf_token": tok("csrf_token", page),
                                                        "conversation_id": "", "content": ""}, {"documents": ("altdaten_synthetisch.pdf", data, "application/pdf")})
    conv = re.search(r'"conversation_id": "([^"]+)"', ev).group(1)
    page2 = o.open(BASE + f"/dashboard/chat/{conv}", timeout=60).read().decode("utf-8", "replace")
    multipart(o, "/dashboard/chat/send-stream", {"csrf_token": tok("csrf_token", page2), "conversation_id": conv,
                                                  "content": "Erstelle einen Schriftsatz an das Finanzamt."})
    R["vorher"]["unterhaltung"] = conv
finally:
    stop(proc)
R["vorher"]["db"] = db_state()
R["vorher"]["dateien"] = files_state()

# --- 2) Update mit dem QA-Identitaets-Installer -----------------------------------------------------------------------
r = subprocess.run([str(SETUP), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/DIR={APP}", f"/LOG={ISO / 'update_install.log'}"],
                   capture_output=True, timeout=1200)
R["installer_exit"] = r.returncode

# --- 3) nachher -------------------------------------------------------------------------------------------------------
R["nachher"]["exe_sha256"] = sha(APP / "Lexono.exe")
R["nachher"]["profil_datei"] = (APP / "_internal" / "lexono_build_profile.txt").read_text(encoding="utf-8").strip()
neu = sorted(p.name for p in (APP / "_internal" / "migrations" / "versions").glob("*.py"))
dist = sorted(p.name for p in (Path(r"C:\Users\Bonit\Lexono\dist\Lexono\_internal\migrations\versions")).glob("*.py"))
R["nachher"]["migrationsordner_gleich_paket"] = neu == dist
R["nachher"]["fibu_router_template_im_programmordner"] = [str(p.relative_to(APP)) for p in (APP / "_internal").rglob("fibu*") if p.is_file()]
R["nachher"]["db_vor_start"] = db_state()
proc = serve()
try:
    o, landed = session()
    R["nachher"]["login_ziel"] = landed.replace(BASE, "")
    chat = o.open(BASE + f"/dashboard/chat/{R['vorher']['unterhaltung']}", timeout=60)
    html = chat.read().decode("utf-8", "replace")
    R["nachher"]["alte_unterhaltung_http"] = chat.status
    R["nachher"]["alte_unterhaltung_inhalt"] = ("altdaten_synthetisch.pdf" in html, "Erstelle einen Schriftsatz an das Finanzamt." in html)
    try:
        R["nachher"]["fibu_http"] = o.open(BASE + "/dashboard/fibu", timeout=30).status
    except urllib.error.HTTPError as e:
        R["nachher"]["fibu_http"] = e.code
    lic = o.open(BASE + "/dashboard/settings?tab=lizenz", timeout=60).read().decode("utf-8", "replace")
    R["nachher"]["lizenzseite"] = ("Lexono Produkte" in lic, 'data-module="finanzen"' in lic)
finally:
    stop(proc)
R["nachher"]["db_nach_start"] = db_state()
R["nachher"]["dateien"] = files_state()

P = R["pruefungen"]
P["installer_ok"] = R["installer_exit"] == 0
P["exe_ersetzt"] = R["vorher"]["exe_sha256"] != R["nachher"]["exe_sha256"] and R["nachher"]["exe_sha256"] == sha(Path(r"C:\Users\Bonit\Lexono\dist\Lexono\Lexono.exe"))
P["profil_pilot"] = R["nachher"]["profil_datei"] == "kanzlei-pilot"
P["migrationsordner_ohne_altlasten"] = R["nachher"]["migrationsordner_gleich_paket"]
P["keine_finance_dateien"] = R["nachher"]["fibu_router_template_im_programmordner"] == []
P["revision_unveraendert_042"] = R["vorher"]["db"]["revision"] == R["nachher"]["db_nach_start"]["revision"] == "schritt3_042"
P["daten_zeilen_erhalten"] = all(R["nachher"]["db_nach_start"]["zeilen"].get(k, 0) >= v for k, v in R["vorher"]["db"]["zeilen"].items())
P["kerninhalte_identisch"] = all(R["nachher"]["db_nach_start"]["inhalt_hash"].get(k) == v for k, v in R["vorher"]["db"]["inhalt_hash"].items()
                                 if k not in ("users",))  # users: last_login aendert sich beim Anmelden
P["konfiguration_und_dateien_erhalten"] = all(R["nachher"]["dateien"].get(k) == v for k, v in R["vorher"]["dateien"].items())
P["quick_check"] = R["nachher"]["db_nach_start"]["quick_check"] == "ok"
P["login_und_alte_unterhaltung"] = R["nachher"]["alte_unterhaltung_http"] == 200 and all(R["nachher"]["alte_unterhaltung_inhalt"])
P["finance_nicht_erreichbar"] = R["nachher"]["fibu_http"] == 404
P["lizenzseite_neu_ohne_finance_modul"] = R["nachher"]["lizenzseite"] == (True, False)
OUT.write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(R["pruefungen"], ensure_ascii=False, indent=1))
