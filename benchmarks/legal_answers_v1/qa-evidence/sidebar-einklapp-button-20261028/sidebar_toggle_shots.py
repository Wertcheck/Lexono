"""Position des Sidebar-Einklapp-Buttons (Quellstand, isolierte Instanz, headless Edge, DPR 1): 1920x1080, 1280x720,
960x600, ein- und ausgeklappt. Misst Abstand zur Kopfzeile und Ueberlappung mit "Neuen Chat starten"/Navigation.
Usage: sidebar_toggle_shots.py <out_dir> <phase>"""
import asyncio, base64, http.cookiejar, json, os, subprocess, sys, time, urllib.parse, urllib.request
from pathlib import Path

import websockets

T = Path(__file__).resolve().parent
REPO = Path(os.environ.get("QA_REPO", r"<REPOSITORY>"))
PY = r"<REPOSITORY>\.venv\Scripts\python.exe"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
PHASE = sys.argv[2]
INST, PORT, CDP = T / "sidebar_qa", 8079, 9366
BASE = f"http://127.0.0.1:{PORT}"
R = {"phase": PHASE, "aufnahmen": {}}

subprocess.run([PY, str(T / "pseudo_seed.py"), str(INST), str(PORT)], check=True, capture_output=True)
env = dict(os.environ, LEXONO_DATA_DIR=str(INST), PYTHONDONTWRITEBYTECODE="1",
           HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9", NO_PROXY="127.0.0.1,localhost")
for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "DATABASE_URL"):
    env.pop(k, None)
server = subprocess.Popen([PY, str(REPO / "run.py"), "serve", "--no-window"], cwd=str(INST), env=env,
                          stdout=open(INST / f"serve_{PHASE}.out", "w"), stderr=subprocess.STDOUT)
for _ in range(120):
    try:
        urllib.request.urlopen(BASE + "/dashboard/login", timeout=2); break
    except Exception:
        time.sleep(1)
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), urllib.request.ProxyHandler({}))
op.open(BASE + "/dashboard/login", timeout=30)
op.open(urllib.request.Request(BASE + "/dashboard/login", data=urllib.parse.urlencode(
    {"email": "qa-pseudo@lexono.test", "password": "QA-pseudo-0000", "next": "/dashboard"}).encode()), timeout=30)

STATE = r"""(() => {
 const box = e => { if (!e) return null; const r = e.getBoundingClientRect(); return {l: Math.round(r.left), t: Math.round(r.top), r: Math.round(r.right), b: Math.round(r.bottom)}; };
 const hit = (a, b) => a && b && a.l < b.r && a.r > b.l && a.t < b.b && a.b > b.t;
 const tog = box(document.getElementById('sidebar-collapse-toggle'));
 const header = box(document.querySelector('.global-header'));
 const sidebar = box(document.querySelector('.sidebar'));
 const newChat = box(document.querySelector('.sidebar .sidebar__new-chat, .sidebar a[href*="new=1"], .sidebar .btn--primary'));
 const nav = [...document.querySelectorAll('.sidebar a, .sidebar button')].map(box).filter(Boolean);
 const s = getComputedStyle(document.getElementById('sidebar-collapse-toggle'));
 return {eingeklappt: document.documentElement.classList.contains('sidebar-collapsed'), toggle: tog, kopfzeile_unten: header && header.b,
   sidebar: sidebar, neuer_chat: newChat, abstand_zur_kopfzeile: tog && header ? tog.t - header.b : null,
   mitte_auf_sidebarkante: tog && sidebar ? Math.abs((tog.l + tog.r) / 2 - sidebar.r) <= 1 : null,
   ueberdeckt_neuer_chat: hit(tog, newChat), ueberdeckt_navigation: nav.some(n => hit(tog, n)),
   unter_kopfzeile: tog && header ? tog.t >= header.b : null,
   stil: {w: s.width, h: s.height, bg: s.backgroundColor, shadow: s.boxShadow.slice(0, 60), radius: s.borderTopLeftRadius}}; })()"""


async def main():
    cookies = [{"name": c.name, "value": c.value, "domain": "127.0.0.1", "path": "/"} for c in cj]
    edge = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={CDP}",
                             f"--user-data-dir={T / ('edge_profile_sidebar_' + PHASE)}", "--no-first-run",
                             "--disable-extensions", "about:blank"])
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

            async def js(expr):
                res = await cdp("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
                return res.get("result", {}).get("value")

            await cdp("Page.enable"); await cdp("Network.enable"); await cdp("Runtime.enable")
            for c in cookies:
                await cdp("Network.setCookie", **c)
            await cdp("Page.navigate", url=BASE + "/dashboard/chat?new=1"); await asyncio.sleep(2)
            for collapsed in (False, True):
                zs = "eingeklappt" if collapsed else "ausgeklappt"
                for w, h in ((1920, 1080), (1280, 720), (960, 600)):
                    await cdp("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
                    await cdp("Page.navigate", url=BASE + "/dashboard/chat?new=1"); await asyncio.sleep(2)
                    is_c = await js("document.documentElement.classList.contains('sidebar-collapsed')")
                    if is_c != collapsed:
                        await js("document.getElementById('sidebar-collapse-toggle').click()")  # bestehende Klickfunktion
                        await asyncio.sleep(0.8)
                    name = f"{w}x{h}-{zs}"
                    clip = {"x": 0, "y": 0, "width": min(w, 420), "height": 220, "scale": 2}
                    data = base64.b64decode((await cdp("Page.captureScreenshot", format="png", clip=clip))["data"])
                    (OUT / f"{name}-ausschnitt.png").write_bytes(data)
                    data = base64.b64decode((await cdp("Page.captureScreenshot", format="png"))["data"])
                    (OUT / f"{name}-fenster.png").write_bytes(data)
                    R["aufnahmen"][name] = await js(STATE)
                    print(name, flush=True)
            # Ausgangszustand wiederherstellen (ausgeklappt)
            if await js("document.documentElement.classList.contains('sidebar-collapsed')"):
                await js("document.getElementById('sidebar-collapse-toggle').click()")
    finally:
        edge.terminate()
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(server.pid)], capture_output=True)


asyncio.run(main())
(OUT / f"messwerte_{PHASE}.json").write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
print("fertig", len(R["aufnahmen"]))
