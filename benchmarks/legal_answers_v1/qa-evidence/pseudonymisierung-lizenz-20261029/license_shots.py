"""QA Lizenz-Tab (Quellstand, isolierte Instanz, synthetisch, headless Edge DPR 1): mehrere Fenstergroessen, hell und
dunkel, Pilotprofil. Misst Querscroll, abgeschnittene Inhalte und Kartenbreiten. Usage: license_shots.py <out_dir> <profil>"""
import asyncio, base64, http.cookiejar, json, os, subprocess, sys, time, urllib.parse, urllib.request
from pathlib import Path

import websockets

T = Path(r"C:\Users\Bonit\.claude\jobs\02f3f8ce\tmp")
REPO = Path(r"C:\Users\Bonit\Lexono")
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
PROFILE = sys.argv[2]
INST, PORT, CDP = T / f"license_qa_{PROFILE}", 8078, 9365
BASE = f"http://127.0.0.1:{PORT}"
subprocess.run([PY, str(T / "pseudo_seed.py"), str(INST), str(PORT)], check=True, capture_output=True)
env = dict(os.environ, LEXONO_DATA_DIR=str(INST), PYTHONDONTWRITEBYTECODE="1", LEXONO_BUILD_PROFILE=PROFILE,
           HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9", NO_PROXY="127.0.0.1,localhost")
for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "DATABASE_URL"):
    env.pop(k, None)
server = subprocess.Popen([PY, str(REPO / "run.py"), "serve", "--no-window"], cwd=str(INST), env=env,
                          stdout=open(INST / "serve.out", "w"), stderr=subprocess.STDOUT)
for _ in range(120):
    try:
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(BASE + "/dashboard/login", timeout=2); break
    except Exception:
        time.sleep(1)
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), urllib.request.ProxyHandler({}))
op.open(BASE + "/dashboard/login", timeout=30)
op.open(urllib.request.Request(BASE + "/dashboard/login", data=urllib.parse.urlencode(
    {"email": "qa-pseudo@lexono.test", "password": "QA-pseudo-0000", "next": "/dashboard"}).encode()), timeout=30)
R = {}
STATE = r"""(() => { const panel = document.querySelector('[data-tabpanel="lizenz"]');
 const cards = [...document.querySelectorAll('.license-product')];
 const clipped = [...panel.querySelectorAll('*')].filter(e => { const s = getComputedStyle(e); return (s.overflow === 'hidden' || s.textOverflow === 'ellipsis') && e.scrollWidth > e.clientWidth + 1; }).map(e => e.className);
 const tableWrap = document.querySelector('.license-compare__scroll');
 return {fenster: [innerWidth, innerHeight], aktiver_tab: (document.querySelector('.settings-tab.is-active, .settings-tab[aria-selected="true"]') || {}).textContent,
   seite_querscroll: document.documentElement.scrollWidth > innerWidth,
   tabelle_querscroll: tableWrap ? tableWrap.scrollWidth > tableWrap.clientWidth + 1 : null,
   karten: cards.map(c => { const r = c.getBoundingClientRect(); return [c.dataset.product, Math.round(r.width), Math.round(r.left)]; }),
   abgeschnitten: clipped, titel: [...document.querySelectorAll('.license-product__name')].map(n => n.innerText.replace(/\n/g, ' | ')),
   js_fehler: window.__errs || []}; })()"""


async def main():
    edge = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={CDP}", f"--user-data-dir={T / 'edge_license'}",
                             "--no-first-run", "--disable-extensions", "--no-proxy-server", "about:blank"])
    try:
        for _ in range(60):
            try:
                targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP}/json", timeout=2).read())
                if any(t["type"] == "page" for t in targets): break
            except Exception:
                time.sleep(1)
        page = next(t for t in targets if t["type"] == "page")
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=300_000_000) as ws:
            n = 0

            async def cdp(method, **params):
                nonlocal n
                n += 1; my = n
                await ws.send(json.dumps({"id": my, "method": method, "params": params}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == my:
                        return msg.get("result", {})

            async def js(expr):
                return (await cdp("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)).get("result", {}).get("value")

            await cdp("Page.enable"); await cdp("Runtime.enable")
            await cdp("Page.addScriptToEvaluateOnNewDocument", source="window.__errs = []; addEventListener('error', e => window.__errs.push(String(e.message)));")
            for c in cj:
                await cdp("Network.setCookie", name=c.name, value=c.value, domain="127.0.0.1", path="/")
            for theme in ("hell", "dunkel"):
                await cdp("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark" if theme == "dunkel" else "light"}])
                for w, h in ((1920, 1080), (1280, 720), (960, 600)):
                    if theme == "dunkel" and w != 1280: continue
                    await cdp("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
                    await cdp("Page.navigate", url=BASE + "/dashboard/settings?tab=lizenz"); await asyncio.sleep(2)
                    if theme == "dunkel":
                        await js("document.documentElement.setAttribute('data-theme','dark')"); await asyncio.sleep(0.4)
                    name = f"{PROFILE}-{w}x{h}-{theme}"
                    (OUT / f"{name}-0-kopf-tabs.png").write_bytes(base64.b64decode((await cdp("Page.captureScreenshot", format="png"))["data"]))
                    await js("(() => { const e = document.querySelector('.license-products-section'); if (e) e.scrollIntoView({block: 'start'}); })()")
                    await asyncio.sleep(0.4)
                    (OUT / f"{name}-1-produkte.png").write_bytes(base64.b64decode((await cdp("Page.captureScreenshot", format="png"))["data"]))
                    await js("document.querySelector('.license-compare').scrollIntoView({block: 'start'})"); await asyncio.sleep(0.4)
                    (OUT / f"{name}-2-vergleich.png").write_bytes(base64.b64decode((await cdp("Page.captureScreenshot", format="png"))["data"]))
                    R[name] = await js(STATE)
                    print(name, json.dumps(R[name], ensure_ascii=False), flush=True)
    finally:
        edge.terminate()
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(server.pid)], capture_output=True)


asyncio.run(main())
(OUT / f"{PROFILE}-messwerte.json").write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
