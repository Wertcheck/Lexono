"""QA Modularisierung (Quellstand, isolierte Instanz per Migration, nur synthetische Daten). Headless Edge, DPR 1.
Phase A ohne Lizenz (Basispaket), Phase B nach Import einer QA-Lizenz (Wegwerfschluessel), Phase C nach Deaktivierung.
Usage: modules_shots.py <out_dir>"""
import asyncio, atexit, base64, http.cookiejar, json, os, re, subprocess, sys, time, urllib.error, urllib.parse, urllib.request, uuid
from datetime import date, timedelta
from pathlib import Path

import websockets

T = Path(__file__).resolve().parent
REPO = Path(r"<REPOSITORY>")
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
INST, PORT, CDP = T / "modules_qa", 8082, 9369
BASE = f"http://127.0.0.1:{PORT}"
R = {"aufnahmen": {}, "funktion": {}}

subprocess.run([PY, str(T / "pseudo_seed.py"), str(INST), str(PORT)], check=True, capture_output=True)
DB = INST / "data" / "kanzlei_ai.db"
sys.path.insert(0, str(REPO))
os.environ["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth.security import hash_password  # noqa: E402
from app.models import Client, Role, User  # noqa: E402
from app.modules.licensing import key_id_for  # noqa: E402
from scripts.lexono_license_tool import build_license  # noqa: E402

with Session(create_engine(f"sqlite:///{DB.as_posix()}")) as db:
    staff = db.query(Role).filter(Role.name.ilike("mitarbeiter")).one()
    db.add(User(email="qa-mitarbeiter@lexono.test", display_name="QA Mitarbeiter", role_id=staff.id,
                password_hash=hash_password("QA-pseudo-0000"), must_change_password=False))
    c = Client(name="QA Muster GmbH", client_type="Unternehmen", city="Musterstadt")
    db.add(c); db.commit(); CLIENT = c.id

key = Ed25519PrivateKey.generate()
raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
KID, PUB = key_id_for(raw), base64.b64encode(raw).decode()
LICENSE = build_license(key, licensee="QA Kanzlei (synthetisch)", modules=["finanzen", "steuern"],
                        valid_from=date.today() - timedelta(days=1), valid_until=date.today() + timedelta(days=365))
FORGED = json.loads(LICENSE); FORGED["key_id"] = "0000000000000000"

env = dict(os.environ, LEXONO_DATA_DIR=str(INST), PYTHONDONTWRITEBYTECODE="1",
           HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9", NO_PROXY="127.0.0.1,localhost")
for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "DATABASE_URL"):
    env.pop(k, None)
server = subprocess.Popen([PY, str(T / "qa_serve_with_key.py"), KID, PUB, str(PORT)], cwd=str(INST), env=env,
                          stdout=open(INST / "serve.out", "w"), stderr=subprocess.STDOUT)
atexit.register(lambda: subprocess.run(["taskkill", "/F", "/T", "/PID", str(server.pid)], capture_output=True))
for _ in range(120):
    try:
        urllib.request.urlopen(BASE + "/dashboard/login", timeout=2); break
    except Exception:
        time.sleep(1)


def session(email):
    cj = http.cookiejar.CookieJar()
    o = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), urllib.request.ProxyHandler({}))
    o.open(BASE + "/dashboard/login", timeout=30)
    o.open(urllib.request.Request(BASE + "/dashboard/login", data=urllib.parse.urlencode(
        {"email": email, "password": "QA-pseudo-0000", "next": "/dashboard"}).encode()), timeout=30)
    return o, cj


op, cj = session("qa-pseudo@lexono.test")
staff_op, staff_cj = session("qa-mitarbeiter@lexono.test")


def status_get(path, o=None):
    try:
        r = (o or op).open(BASE + path, timeout=60); return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def token(o=None):
    return re.search(r'name="csrf_token" value="([^"]+)"', status_get("/dashboard/chat?new=1", o)[1]).group(1)


def post(path, fields, files=None, o=None):
    boundary = "----QA" + uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for k, (name, data) in (files or {}).items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{name}\"\r\n"
                 f"Content-Type: text/plain\r\n\r\n").encode() + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(BASE + path, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        r = (o or op).open(req, timeout=60); return r.status, r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, ""


f = R["funktion"]
nav = lambda html: 'href="/dashboard/fibu"' in html
f["A_chat_nav_ohne_fibu"] = not nav(status_get("/dashboard/chat")[1])
f["A_fibu_admin_http"] = status_get(f"/dashboard/fibu?client={CLIENT}")[0]
f["A_fibu_mitarbeiter_http"] = status_get(f"/dashboard/fibu?client={CLIENT}", staff_op)[0]
f["A_fibu_post_http"] = post("/dashboard/fibu/accounts/seed", {"csrf_token": token(), "client_id": CLIENT})[0]
f["A_basis_seiten_http"] = {p: status_get(p)[0] for p in ("/dashboard/chat", "/dashboard/clients", "/dashboard/matters",
                                                           "/dashboard/tasks", "/dashboard/inbox", "/dashboard/knowledge")}
code, url = post("/dashboard/settings/license/import", {"csrf_token": token()},
                 {"license_file": ("gefaelscht.lexono-license", json.dumps(FORGED).encode())})
f["A_gefaelschte_lizenz"] = urllib.parse.unquote(url.split("error=", 1)[-1].split("&", 1)[0]) if "error=" in url else url
f["A_mitarbeiter_lizenzimport_http"] = post("/dashboard/settings/license/import", {"csrf_token": token(staff_op)},
                                             {"license_file": ("k.lexono-license", LICENSE.encode())}, o=staff_op)[0]


STATE = r"""(() => ({fenster: [innerWidth, innerHeight], querscroll: document.documentElement.scrollWidth > innerWidth,
 nav: [...document.querySelectorAll('.sidebar__group-label')].map(e => e.textContent.trim()),
 titel: (document.querySelector('h1') || {}).textContent, js_fehler: window.__errs || []}))()"""


async def main():
    edge = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={CDP}", f"--user-data-dir={T / 'edge_profile_modules'}",
                             "--no-first-run", "--disable-extensions", "about:blank"])
    try:
        targets = []
        for _ in range(60):
            try:
                targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP}/json", timeout=2).read())
                if any(t["type"] == "page" for t in targets):
                    break
            except Exception:
                time.sleep(1)
        page = next(t for t in targets if t["type"] == "page")
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=300_000_000) as ws:
            n = 0

            async def cdp(method, **params):
                nonlocal n
                n += 1
                await ws.send(json.dumps({"id": n, "method": method, "params": params}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == n:
                        return msg.get("result", {})

            async def js(expr):
                return (await cdp("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)).get("result", {}).get("value")

            async def go(path):
                await cdp("Page.navigate", url=BASE + path); await asyncio.sleep(1.8)

            async def shot(name, scroll_to=None):
                if scroll_to:
                    await js(f"(() => {{ const e = document.querySelector({json.dumps(scroll_to)}); if (e) e.scrollIntoView({{block: 'start'}}); }})()")
                    await asyncio.sleep(0.5)
                data = base64.b64decode((await cdp("Page.captureScreenshot", format="png"))["data"])
                (OUT / f"{name}.png").write_bytes(data)
                R["aufnahmen"][name] = await js(STATE)
                print(name, flush=True)

            async def cookies(jar):
                await cdp("Network.clearBrowserCookies")
                for c in jar:
                    await cdp("Network.setCookie", name=c.name, value=c.value, domain="127.0.0.1", path="/")

            async def size(w, h):
                await cdp("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)

            await cdp("Page.enable"); await cdp("Network.enable"); await cdp("Runtime.enable")
            await cdp("Page.addScriptToEvaluateOnNewDocument", source="window.__errs = []; addEventListener('error', e => window.__errs.push(String(e.message)));")
            await cookies(cj)
            sizes = ((1920, 1080), (1280, 720), (960, 600))
            # Phase A: Basispaket, keine Lizenz
            for w, h in sizes:
                await size(w, h); p = f"{w}x{h}"
                await go("/dashboard/chat?new=1"); await shot(f"{p}-A1-basispaket-navigation-ohne-finanzen")
                await go(f"/dashboard/fibu?client={CLIENT}"); await shot(f"{p}-A2-finanzen-gesperrt-admin")
                await go("/dashboard/settings?tab=lizenz"); await shot(f"{p}-A3-lizenz-tab-ohne-lizenz", "#module-licenses")
            # Lizenz importieren (echtes Formular, Datei-Upload per HTTP mit der Browser-Sitzung)
            code, url = post("/dashboard/settings/license/import", {"csrf_token": token()},
                             {"license_file": ("qa-kanzlei.lexono-license", LICENSE.encode())})
            R["funktion"]["B_import"] = urllib.parse.unquote(url.split("success=", 1)[-1].split("&", 1)[0]) if "success=" in url else url
            for w, h in sizes:
                await size(w, h); p = f"{w}x{h}"
                await go("/dashboard/settings?tab=lizenz"); await shot(f"{p}-B1-lizenz-tab-freigeschaltet", "#module-licenses")
                await go("/dashboard/chat?new=1"); await shot(f"{p}-B2-navigation-mit-finanzbuchhaltung")
                await go(f"/dashboard/fibu?client={CLIENT}"); await shot(f"{p}-B3-finanzbuchhaltung-nutzbar")
            await size(1280, 720)
            await cookies(staff_cj)
            await go(f"/dashboard/fibu?client={CLIENT}&tab=buchen"); await shot("1280x720-B4-mitarbeiter-finanzen-nur-lesen")
            await cookies(cj)
            R["funktion"]["B_fibu_admin_http"] = status_get(f"/dashboard/fibu?client={CLIENT}")[0]
            R["funktion"]["B_seed_konten_http"] = post("/dashboard/fibu/accounts/seed", {"csrf_token": token(), "client_id": CLIENT})[0]
            # Phase C: Lizenz deaktivieren -> wieder gesperrt, Daten erhalten
            R["funktion"]["C_deaktivieren"] = post("/dashboard/settings/license/remove", {"csrf_token": token()})[0]
            await go("/dashboard/chat?new=1"); await shot("1280x720-C1-nach-deaktivierung-navigation-ohne-finanzen")
            await cookies(staff_cj)
            await go(f"/dashboard/fibu?client={CLIENT}"); await shot("1280x720-C2-mitarbeiter-finanzen-gesperrt")
    finally:
        edge.terminate()


asyncio.run(main())
f["C_fibu_admin_http"] = status_get(f"/dashboard/fibu?client={CLIENT}")[0]
import sqlite3  # noqa: E402
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
f["C_fibu_konten_erhalten"] = con.execute("select count(*) from fibu_accounts").fetchone()[0]
f["audit_lizenz"] = [r[0] for r in con.execute("select event_type from audit_events where entity_type='ModuleLicense' order by created_at")]
f["lizenzzeilen"] = con.execute("select count(*), sum(is_active) from module_licenses").fetchone()
con.close()
(OUT / "messwerte.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
print(json.dumps(R["funktion"], ensure_ascii=False, indent=1, default=str))
