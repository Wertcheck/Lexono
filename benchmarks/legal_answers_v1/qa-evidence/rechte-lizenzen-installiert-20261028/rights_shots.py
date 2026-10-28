"""QA Mitarbeiterrechte (Quellstand, isolierte Instanz, nur synthetische Daten, QA-Wegwerfschluessel wie
modules_shots.py). Headless Edge, DPR 1. Usage: rights_shots.py <out_dir>"""
import asyncio, atexit, base64, http.cookiejar, json, os, re, subprocess, sys, time, urllib.error, urllib.parse, urllib.request, uuid
from datetime import date, timedelta
from pathlib import Path

import websockets

T = Path(__file__).resolve().parent
REPO = Path(r"<REPOSITORY>")
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
INST, PORT, CDP = T / "rights_qa", 8083, 9370
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
    roles = {r.name.lower(): r for r in db.query(Role).all()}
    for email, role in (("qa-anwalt@lexono.test", "anwalt"), ("qa-mitarbeiter@lexono.test", "mitarbeiter"),
                        ("qa-mitarbeiter2@lexono.test", "mitarbeiter")):
        db.add(User(email=email, display_name=email.split("@")[0], role_id=roles[role].id,
                    password_hash=hash_password("QA-pseudo-0000"), must_change_password=False))
    a = Client(name="QA Muster GmbH", client_type="Unternehmen"); b = Client(name="QA Beispiel Handwerk KG", client_type="Unternehmen")
    db.add_all([a, b]); db.commit(); CA, CB = a.id, b.id

key = Ed25519PrivateKey.generate()
raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
LICENSE = build_license(key, licensee="QA Kanzlei (synthetisch)", modules=["finanzen", "steuern"],
                        valid_from=date.today() - timedelta(days=1), valid_until=date.today() + timedelta(days=30), term="monthly")
env = dict(os.environ, LEXONO_DATA_DIR=str(INST), PYTHONDONTWRITEBYTECODE="1",
           HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9", NO_PROXY="127.0.0.1,localhost")
for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "DATABASE_URL"):
    env.pop(k, None)
server = subprocess.Popen([PY, str(T / "qa_serve_with_key.py"), key_id_for(raw), base64.b64encode(raw).decode(), str(PORT)],
                          cwd=str(INST), env=env, stdout=open(INST / "serve.out", "w"), stderr=subprocess.STDOUT)
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


admin, admin_cj = session("qa-pseudo@lexono.test")
anwalt, anwalt_cj = session("qa-anwalt@lexono.test")
ma, ma_cj = session("qa-mitarbeiter@lexono.test")
ma2, ma2_cj = session("qa-mitarbeiter2@lexono.test")


def get(path, o):
    try:
        r = o.open(BASE + path, timeout=60); return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def token(o):
    return re.search(r'name="csrf_token" value="([^"]+)"', get("/dashboard/chat?new=1", o)[1]).group(1)


def post(path, fields, o, files=None):
    boundary = "----QA" + uuid.uuid4().hex
    body = b""
    for k, vs in fields.items():
        for v in (vs if isinstance(vs, list) else [vs]):
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for k, (name, data, ctype) in (files or {}).items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{name}\"\r\n"
                 f"Content-Type: {ctype}\r\n\r\n").encode() + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(BASE + path, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        r = o.open(req, timeout=60); return r.status
    except urllib.error.HTTPError as e:
        return e.code


f = R["funktion"]
f["lizenzimport"] = post("/dashboard/settings/license/import", {"csrf_token": token(admin)}, admin,
                         {"license_file": ("qa.lexono-license", LICENSE.encode(), "text/plain")})
for cid in (CA, CB):
    post("/dashboard/fibu/accounts/seed", {"csrf_token": token(admin), "client_id": cid}, admin)


def accounts(cid, o):
    page = get(f"/dashboard/fibu?client={cid}&tab=buchen", o)[1]
    return {m.group(2): m.group(1) for m in re.finditer(r'<option value="([0-9a-f-]{36})"[^>]*>(\d+) ', page)}


acc = accounts(CA, admin)
post("/dashboard/fibu/bookings", {"csrf_token": token(admin), "client_id": CA, "year": "2026", "action": "save_post",
                                  "booking_date": "2026-10-27", "receipt_number": "RE-2026-105", "description": "Beratung Projekt XY",
                                  "direction": "income", "amount": "1.250,00", "tax_rate": "19", "account_id": acc["8400"],
                                  "contra_account_id": acc["1000"]}, admin)
f["ma_ohne_zuweisung_mandant_a"] = get(f"/dashboard/fibu?client={CA}", ma)[0]
f["ma_ohne_zuweisung_liste_leer"] = "Noch kein aktiver Mandant" in get("/dashboard/fibu", ma)[1]
staff_ids = re.findall(r'name="user_ids" value="([0-9a-f-]{36})"', get(f"/dashboard/fibu?client={CA}&tab=einstellungen", anwalt)[1])
ma_id = re.search(r'name="user_ids" value="([0-9a-f-]{36})"[^>]*/> qa-mitarbeiter <', get(f"/dashboard/fibu?client={CA}&tab=einstellungen", anwalt)[1])
f["zuweisung_durch_anwalt"] = post("/dashboard/fibu/access", {"csrf_token": token(anwalt), "client_id": CA, "year": "2026",
                                                               "user_ids": [ma_id.group(1)] if ma_id else []}, anwalt)
f["ma_mit_zuweisung_mandant_a"] = get(f"/dashboard/fibu?client={CA}", ma)[0]
f["ma_mandant_b"] = get(f"/dashboard/fibu?client={CB}", ma)[0]
t = token(ma)
f["ma_buchung_erfassen_offen"] = post("/dashboard/fibu/bookings", {"csrf_token": t, "client_id": CA, "year": "2026", "action": "save",
                                                                   "booking_date": "2026-10-28", "receipt_number": "BE-MA-001", "description": "Büromaterial (Mitarbeiter)",
                                                                   "direction": "expense", "amount": "45,90", "tax_rate": "19",
                                                                   "account_id": acc["4930"], "contra_account_id": acc["1000"]}, ma)
f["ma_speichern_und_buchen"] = post("/dashboard/fibu/bookings", {"csrf_token": t, "client_id": CA, "year": "2026", "action": "save_post",
                                                                 "booking_date": "2026-10-28", "receipt_number": "BE-MA-002", "description": "x",
                                                                 "direction": "expense", "amount": "1,00", "tax_rate": "19",
                                                                 "account_id": acc["4930"], "contra_account_id": acc["1000"]}, ma)
f["ma_beleg_hochladen"] = post("/dashboard/fibu/receipts", {"csrf_token": t, "client_id": CA, "year": "2026"}, ma,
                               {"files": ("QA-SYNTH-Quittung.pdf", b"%PDF-1.4 QA-SYNTH", "application/pdf")})
booking_id = re.search(r'edit=([0-9a-f-]{36})', get(f"/dashboard/fibu?client={CA}&tab=buchen&q=BE-MA-001", ma)[1]).group(1)
f["ma_buchen"] = post(f"/dashboard/fibu/bookings/{booking_id}/post", {"csrf_token": t, "client_id": CA, "year": "2026"}, ma)
f["ma_stornieren"] = post(f"/dashboard/fibu/bookings/{booking_id}/cancel", {"csrf_token": t, "client_id": CA, "year": "2026", "reason": "x"}, ma)
f["ma_konto_anlegen"] = post("/dashboard/fibu/accounts", {"csrf_token": t, "client_id": CA, "number": "4999", "name": "x", "kind": "expense"}, ma)
f["ma_zugriff_verwalten"] = post("/dashboard/fibu/access", {"csrf_token": t, "client_id": CA, "user_ids": ma_id.group(1) if ma_id else ""}, ma)
f["ma_datev"] = get(f"/dashboard/fibu/export/datev-entwurf.csv?client={CA}", ma)[0]
f["ma_buchung_fremder_mandant"] = post("/dashboard/fibu/bookings", {"csrf_token": t, "client_id": CB, "year": "2026", "action": "save",
                                                                    "booking_date": "2026-10-28", "receipt_number": "X", "description": "x",
                                                                    "direction": "expense", "amount": "1,00", "tax_rate": "19",
                                                                    "account_id": acc["4930"], "contra_account_id": acc["1000"]}, ma)
f["anwalt_bucht_ma_buchung"] = post(f"/dashboard/fibu/bookings/{booking_id}/post", {"csrf_token": token(anwalt), "client_id": CA, "year": "2026"}, anwalt)

STATE = r"""(() => ({fenster: [innerWidth, innerHeight], querscroll: document.documentElement.scrollWidth > innerWidth,
 mandanten: [...document.querySelectorAll('#fibu-client option')].map(o => o.textContent.trim()),
 buchen_knopf: !!document.querySelector('button[value="save_post"]'), offen_knopf: !!document.querySelector('button[value="save"]'),
 hochladen: !!document.querySelector('.fibu-split__main'), titel: (document.querySelector('h1, h2') || {}).textContent, js_fehler: window.__errs || []}))()"""


async def main():
    edge = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={CDP}", f"--user-data-dir={T / 'edge_profile_rights'}",
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

            async def shot(name, scroll_to=None, click=None):
                if scroll_to:
                    await js(f"(() => {{ const e = document.querySelector({json.dumps(scroll_to)}); if (e) e.scrollIntoView({{block: 'start'}}); }})()")
                if click:
                    await js(f"(() => {{ const e = document.querySelector({json.dumps(click)}); if (e) e.click(); }})()")
                await asyncio.sleep(0.6)
                data = base64.b64decode((await cdp("Page.captureScreenshot", format="png"))["data"])
                (OUT / f"{name}.png").write_bytes(data)
                R["aufnahmen"][name] = await js(STATE)
                print(name, flush=True)

            async def as_user(jar):
                await cdp("Network.clearBrowserCookies")
                for c in jar:
                    await cdp("Network.setCookie", name=c.name, value=c.value, domain="127.0.0.1", path="/")

            async def size(w, h):
                await cdp("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)

            await cdp("Page.enable"); await cdp("Network.enable"); await cdp("Runtime.enable")
            await cdp("Page.addScriptToEvaluateOnNewDocument", source="window.__errs = []; addEventListener('error', e => window.__errs.push(String(e.message)));")
            for w, h in ((1920, 1080), (1280, 720), (960, 600)):
                await size(w, h); p = f"{w}x{h}"
                await as_user(ma_cj)
                await go(f"/dashboard/fibu?client={CA}&year=2026"); await shot(f"{p}-01-mitarbeiter-uebersicht-nur-zugewiesener-mandant")
                await go(f"/dashboard/fibu?client={CA}&year=2026&tab=buchen"); await shot(f"{p}-02-mitarbeiter-erfassen-ohne-buchen", "#fibu-booking-form")
                await as_user(anwalt_cj)
                await go(f"/dashboard/fibu?client={CA}&year=2026&tab=einstellungen"); await shot(f"{p}-03-anwalt-mitarbeiterzugriff", "#fibu-access")
            await size(1280, 720)
            await as_user(ma_cj)
            await go(f"/dashboard/fibu?client={CA}&year=2026&tab=buchen&q=BE-MA"); await shot("1280x720-04-mitarbeiter-zeilenmenue-ohne-buchen-storno", click=".fibu-menu summary")
            await as_user(ma2_cj)
            await go("/dashboard/fibu"); await shot("1280x720-05-mitarbeiter-ohne-zuweisung-kein-mandant")
            await as_user(admin_cj)
            await go("/dashboard/settings?tab=lizenz"); await shot("1280x720-06-lizenz-mit-plan", "#module-licenses")
    finally:
        edge.terminate()


asyncio.run(main())
import sqlite3  # noqa: E402
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
f["buchungen"] = con.execute("select receipt_number, status, created_by, updated_by from fibu_bookings order by receipt_number").fetchall()
f["audit_zugriff"] = [r[0] for r in con.execute("select details from audit_events where event_type='client_access_changed'")]
f["audit_buchung_ma"] = con.execute("select event_type, actor from audit_events where entity_type='FibuBooking' order by created_at").fetchall()
con.close()
(OUT / "messwerte.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
print(json.dumps(R["funktion"], ensure_ascii=False, indent=1, default=str))
