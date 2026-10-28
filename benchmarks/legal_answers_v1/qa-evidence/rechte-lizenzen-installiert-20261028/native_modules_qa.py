"""Native Pruefung der INSTALLIERTEN Lexono.exe (WebView2) fuer Module/Lizenz/Rechte (28.10.2026).

- Isolierte Testablage (LEXONO_DATA_DIR im Job-Ordner, eigene SQLite-DB, Port 8067), nur synthetische Konten/Mandanten.
- Die installierte EXE enthaelt KEINEN vertrauenswuerdigen Lizenzschluessel (Auslieferungszustand) - geprueft wird
  daher der gesperrte Zustand: Navigation ohne Finanzbuchhaltung, Hinweisseite, Lizenz-Tab mit Hinweis, Ablehnung
  einer mit fremdem Schluessel signierten Lizenz, Sperre auch fuer Mitarbeiter und fuer POST/Export.
- Freigeschaltete Ablaeufe (FiBu, Mitarbeiterrechte, Vier-Augen) sind am Quellstand geprueft (QA-Schluessel nur dort).
Usage: native_modules_qa.py <scratch_dir> <out_dir>   (Env QA_FORCE_DPR1=1 fuer 1920er-Breite)"""
import asyncio, base64, ctypes, ctypes.wintypes as wt, hashlib, http.cookiejar, json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.error, urllib.parse, urllib.request, uuid
from datetime import date, timedelta
from pathlib import Path

S, OUT = Path(sys.argv[1]), Path(sys.argv[2])
assert "jobs" in str(S)
OUT.mkdir(parents=True, exist_ok=True)
T = Path(__file__).resolve().parent
REPO = Path(r"<REPOSITORY>")
EXE = Path(os.environ["LOCALAPPDATA"]) / "Lexono" / "Lexono.exe"
PORT, CDP_PORT = 8067, 9335
BASE = f"http://127.0.0.1:{PORT}"
DB = S / "data" / "kanzlei_ai.db"
R = {"exe": "%LOCALAPPDATA%\\Lexono\\Lexono.exe", "exe_sha256": hashlib.sha256(EXE.read_bytes()).hexdigest().upper(),
     "aufnahmen": {}, "funktion": {}}
user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

if S.exists():
    shutil.rmtree(S)
(S / "data").mkdir(parents=True)
(S / ".env").write_text("\n".join([
    f"DATABASE_URL=sqlite:///{DB.as_posix()}", "APP_ENV=development", "LOCAL_AI_ENABLED=false", "HOST=127.0.0.1",
    f"PORT={PORT}", "MAIL_AUTO_SYNC_ENABLED=false", "WEB_SEARCH_ENABLED=false", "LAW_UPDATE_CHECK_ENABLED=false",
    "LEXONO_GATEWAY_URL=http://127.0.0.1:9", "SESSION_SECRET_KEY=qa-native-wegwerf-" + os.urandom(16).hex(), ""]), encoding="utf-8")
(S / ".setup_complete").write_text("1", encoding="utf-8")
BASE_ENV = dict(os.environ, LEXONO_DATA_DIR=str(S), PYTHONDONTWRITEBYTECODE="1", HTTPS_PROXY="http://127.0.0.1:9",
                HTTP_PROXY="http://127.0.0.1:9", NO_PROXY="127.0.0.1,localhost")
for k in ("DATABASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
    BASE_ENV.pop(k, None)
subprocess.run([str(EXE), "migrate"], cwd=str(S), env=BASE_ENV, check=True, timeout=600,
               stdout=open(S / "migrate.out", "w"), stderr=subprocess.STDOUT)
sys.path.insert(0, str(REPO))
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth.security import hash_password  # noqa: E402
from app.models import Client, Role, User  # noqa: E402
from scripts.lexono_license_tool import build_license  # noqa: E402

with Session(create_engine(f"sqlite:///{DB.as_posix()}")) as db:
    roles = {r.name.lower(): r for r in db.query(Role).all()}
    for email, role in (("qa-native@lexono.test", "admin"), ("qa-mitarbeiter@lexono.test", "mitarbeiter")):
        db.add(User(email=email, display_name=email.split("@")[0], role_id=roles[role].id, is_active=True,
                    password_hash=hash_password("QA-native-0000"), must_change_password=False))
    c = Client(name="QA Muster GmbH"); db.add(c); db.commit(); CLIENT = c.id
R["funktion"]["alembic_nach_migrate"] = sqlite3.connect(DB).execute("select version_num from alembic_version").fetchone()[0]
# Lizenz mit fremdem (nicht vertrauenswuerdigem) Wegwerfschluessel - muss abgelehnt werden
FOREIGN = build_license(Ed25519PrivateKey.generate(), licensee="QA fremd", modules=["finanzen", "steuern"],
                        valid_from=date.today() - timedelta(days=1), valid_until=date.today() + timedelta(days=30))


def session(email):
    cj = http.cookiejar.CookieJar()
    o = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), urllib.request.ProxyHandler({}))
    o.open(BASE + "/dashboard/login", timeout=30)
    o.open(urllib.request.Request(BASE + "/dashboard/login", data=urllib.parse.urlencode(
        {"email": email, "password": "QA-native-0000", "next": "/dashboard"}).encode()), timeout=30)
    return o


def get(path, o):
    try:
        r = o.open(BASE + path, timeout=60); return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def post(path, fields, o, files=None):
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
        r = o.open(req, timeout=60); return r.status, r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, ""


def token(o):
    return re.search(rb'name="csrf_token" value="([^"]+)"', get("/dashboard/chat?new=1", o)[1]).group(1).decode()


def find_window(pid):
    found = []
    PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        p = wt.DWORD(); user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):
            r = wt.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(r))
            if r.right - r.left > 400:
                found.append(hwnd)
        return True
    user32.EnumWindows(PROC(cb), 0)
    return found[0] if found else None


def capture(hwnd, path):
    from PIL import Image, ImageGrab, ImageStat
    user32.SetForegroundWindow(hwnd); time.sleep(0.3)
    r = wt.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(r)); w, h = r.right - r.left, r.bottom - r.top
    hdc_win = user32.GetWindowDC(hwnd); hdc = gdi32.CreateCompatibleDC(hdc_win)
    bmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h); gdi32.SelectObject(hdc, bmp)
    ok = user32.PrintWindow(hwnd, hdc, 2)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]
    bi = BIH(); bi.biSize = ctypes.sizeof(bi); bi.biWidth = w; bi.biHeight = -h; bi.biPlanes = 1; bi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4); gdi32.GetDIBits(hdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp); gdi32.DeleteDC(hdc); user32.ReleaseDC(hwnd, hdc_win)
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    if not ok or max(ImageStat.Stat(img).stddev) < 3:
        img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)
    img.save(path)


def set_client_css(hwnd, ev_sync, tw, th):
    for _ in range(8):
        if user32.IsZoomed(hwnd):
            user32.ShowWindow(hwnd, 9); time.sleep(1.0)
        iw, ih, dpr = json.loads(ev_sync("JSON.stringify([innerWidth, innerHeight, devicePixelRatio])"))
        if abs(iw - tw) <= 1 and abs(ih - th) <= 1:
            break
        r = wt.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(r))
        user32.SetWindowPos(hwnd, 0, 0, 0, (r.right - r.left) + round((tw - iw) * dpr), (r.bottom - r.top) + round((th - ih) * dpr), 0x0004)
        time.sleep(1.5)
    return json.loads(ev_sync("JSON.stringify([innerWidth, innerHeight, devicePixelRatio])"))


STATE = r"""(() => ({viewport: [innerWidth, innerHeight, devicePixelRatio], pfad: location.pathname,
 nav: [...document.querySelectorAll('.sidebar__group-label')].map(e => e.textContent.trim()),
 titel: (document.querySelector('h1') || {}).textContent, querscroll: document.documentElement.scrollWidth > innerWidth,
 js_fehler: window.__errs || []}))()"""


async def main():
    dpr1 = os.environ.get("QA_FORCE_DPR1") == "1"
    wv = f"--remote-debugging-port={CDP_PORT}" + (" --force-device-scale-factor=1" if dpr1 else "")
    env = dict(BASE_ENV, WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=wv, PORT=str(PORT))
    proc = subprocess.Popen([str(EXE), "serve"], cwd=str(S), env=env, stdout=open(S / "native_serve.out", "w"),
                            stderr=open(S / "native_serve.err", "w"))
    try:
        for _ in range(180):
            try:
                urllib.request.urlopen(BASE + "/dashboard/login", timeout=3); break
            except Exception:
                time.sleep(1)
        admin, staff = session("qa-native@lexono.test"), session("qa-mitarbeiter@lexono.test")
        f = R["funktion"]
        css = get("/dashboard/static/css/app.css", admin)[1]
        f["app_css_gleich_head"] = css == subprocess.run(["git", "show", "HEAD:app/web/static/css/app.css"], cwd=str(REPO), capture_output=True).stdout
        f["chat_nav_ohne_fibu"] = b'href="/dashboard/fibu"' not in get("/dashboard/chat", admin)[1]
        f["fibu_admin"] = get(f"/dashboard/fibu?client={CLIENT}", admin)[0]
        f["fibu_mitarbeiter"] = get(f"/dashboard/fibu?client={CLIENT}", staff)[0]
        f["fibu_post_admin"] = post("/dashboard/fibu/accounts/seed", {"csrf_token": token(admin), "client_id": CLIENT}, admin)[0]
        f["fibu_export_admin"] = get(f"/dashboard/fibu/export/datev-entwurf.csv?client={CLIENT}", admin)[0]
        lic_page = get("/dashboard/settings?tab=lizenz", admin)[1].decode("utf-8")
        f["lizenz_tab_hinweis_kein_schluessel"] = "noch kein Hersteller-Prüfschlüssel hinterlegt" in lic_page
        code, url = post("/dashboard/settings/license/import", {"csrf_token": token(admin)}, admin,
                         {"license_file": ("fremd.lexono-license", FOREIGN.encode())})
        f["fremde_lizenz"] = urllib.parse.unquote(url.split("error=", 1)[1].split("&")[0]) if "error=" in url else (code, url)
        f["fibu_nach_fremder_lizenz"] = get(f"/dashboard/fibu?client={CLIENT}", admin)[0]
        f["mitarbeiter_lizenzimport"] = post("/dashboard/settings/license/import", {"csrf_token": token(staff)}, staff,
                                             {"license_file": ("fremd.lexono-license", FOREIGN.encode())})[0]
        f["basis_seiten"] = {p: get(p, admin)[0] for p in ("/dashboard/chat", "/dashboard/clients", "/dashboard/matters",
                                                           "/dashboard/tasks", "/dashboard/inbox", "/dashboard/knowledge")}

        hwnd = None
        for _ in range(90):
            hwnd = find_window(proc.pid)
            if hwnd:
                break
            time.sleep(1)
        targets = []
        for _ in range(60):
            try:
                targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=2).read())
                if any(t["type"] == "page" for t in targets):
                    break
            except Exception:
                pass
            time.sleep(1)
        page = next(t for t in targets if t["type"] == "page")
        import websockets
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=200_000_000) as ws:
            n = 0

            async def cdp(method, **params):
                nonlocal n
                n += 1
                await ws.send(json.dumps({"id": n, "method": method, "params": params}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == n:
                        return msg.get("result", {})

            async def ev(js):
                return (await cdp("Runtime.evaluate", expression=js, returnByValue=True, awaitPromise=True)).get("result", {}).get("value")

            loop = asyncio.get_running_loop()

            def ev_sync(js):
                return asyncio.run_coroutine_threadsafe(ev(js), loop).result(30)

            async def go(path, wait=2.5):
                await ev(f"location.href = {json.dumps(BASE + path)}"); await asyncio.sleep(wait)

            async def shot(name, scroll_to=None):
                if scroll_to:
                    await ev(f"(() => {{ const e = document.querySelector({json.dumps(scroll_to)}); if (e) e.scrollIntoView({{block: 'start'}}); }})()")
                await asyncio.sleep(0.8)
                await asyncio.to_thread(capture, hwnd, OUT / f"{name}.png")
                st = await ev(STATE)
                R["aufnahmen"][name] = st
                print(name, st.get("viewport") if isinstance(st, dict) else st, flush=True)

            async def login_ui(email):
                await go("/dashboard/logout") if False else None
                await cdp("Network.clearBrowserCookies")
                await go("/dashboard/login")
                await ev(f"(() => {{ const f = document.querySelector('form'); f.querySelector('input[name=email]').value = {json.dumps(email)}; f.querySelector('input[name=password]').value = 'QA-native-0000'; f.requestSubmit(); }})()")
                await asyncio.sleep(3.5)

            await cdp("Runtime.enable"); await cdp("Page.enable"); await cdp("Network.enable")
            await cdp("Page.addScriptToEvaluateOnNewDocument", source="window.__errs = []; addEventListener('error', e => window.__errs.push(String(e.message)));")
            await asyncio.sleep(2)
            await login_ui("qa-native@lexono.test")
            sizes = [(1920, 1080), (1280, 720)] if dpr1 else [(1920, 1080), (1280, 720), (960, 600)]
            for w, h in sizes:
                R["funktion"].setdefault("fenster", {})[f"{w}x{h}"] = await asyncio.to_thread(set_client_css, hwnd, ev_sync, w, h)
                p = f"{w}x{h}"
                await go("/dashboard/chat?new=1"); await shot(f"{p}-01-basispaket-ohne-finanzen")
                await go(f"/dashboard/fibu?client={CLIENT}"); await shot(f"{p}-02-finanzen-gesperrt-admin")
                await go("/dashboard/settings?tab=lizenz"); await shot(f"{p}-03-lizenz-tab-kein-pruefschluessel", "#module-licenses")
            await login_ui("qa-mitarbeiter@lexono.test")
            await go(f"/dashboard/fibu?client={CLIENT}"); await shot(f"{sizes[-1][0]}x{sizes[-1][1]}-04-mitarbeiter-finanzen-gesperrt")
            R["funktion"]["js_fehler"] = await ev("window.__errs")
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except Exception:
            proc.kill()
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)


asyncio.run(main())
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
R["funktion"]["db_quick_check"] = con.execute("pragma quick_check").fetchone()[0]
R["funktion"]["db_alembic"] = con.execute("select version_num from alembic_version").fetchone()[0]
R["funktion"]["db_neue_tabellen"] = sorted(r[0] for r in con.execute("select name from sqlite_master where type='table' and (name like 'fibu_%' or name like 'tax_%' or name in ('module_licenses','client_user_access'))"))
R["funktion"]["lizenzzeilen"] = con.execute("select count(*) from module_licenses").fetchone()[0]
con.close()
err = (S / "native_serve.err").read_text(encoding="utf-8", errors="replace") + (S / "native_serve.out").read_text(encoding="utf-8", errors="replace")
R["funktion"]["log_tracebacks"] = err.count("Traceback")
R["funktion"]["log_error_zeilen"] = [l for l in err.splitlines() if " ERROR " in l][:10]
(OUT / "native.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
print(json.dumps(R["funktion"], ensure_ascii=False, indent=1, default=str))
