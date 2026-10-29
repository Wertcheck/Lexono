"""Chat-Startseite: echte Screenshots der laufenden App (Quellstand, run.py serve --no-window), isolierte Instanz mit
ausschliesslich synthetischen Akten (QA-SYNTH), toter Cloud-Endpunkt, headless Edge per CDP, DPR 1, Bildlaufleisten
sichtbar. Hell/dunkel bei 1920x1080, 1280x720, 960x600. Usage: chatstart_shots.py <out_dir> <phase>"""
import asyncio, base64, http.cookiejar, json, os, subprocess, sys, time, urllib.parse, urllib.request
from pathlib import Path

import websockets

T = Path(__file__).resolve().parent
REPO = Path(os.environ.get("QA_REPO", r"<REPOSITORY>"))
PY = r"<REPOSITORY>\.venv\Scripts\python.exe"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
PHASE = sys.argv[2]
INST, PORT, CDP = T / "chatstart_qa", 8078, 9365
BASE = f"http://127.0.0.1:{PORT}"
R = {"phase": PHASE, "aufnahmen": {}}

subprocess.run([PY, str(T / "pseudo_seed.py"), str(INST), str(PORT)], check=True, capture_output=True)
# synthetische Akten fuer "Akte oeffnen" (nur Testablage)
seed = f"""
import os, sys
sys.path.insert(0, r"{REPO}")
os.environ["DATABASE_URL"] = r"sqlite:///{(INST / 'data' / 'kanzlei_ai.db').as_posix()}"
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.models import Client, Matter
e = create_engine(os.environ["DATABASE_URL"])
with Session(e) as db:
    for i, (c, t, ref) in enumerate([("QA-SYNTH Muster GmbH", "Einspruch USt 2024", "QA-2026/001"),
                                     ("QA-SYNTH Erika Beispiel", "Kuendigungsschutz", "QA-2026/002"),
                                     ("QA-SYNTH Nordbau AG", "Werkvertrag Maengel", "QA-2026/003")]):
        cl = Client(name=c); db.add(cl); db.flush()
        db.add(Matter(client_id=cl.id, title=t, reference_number=ref))
    db.commit()
print("ok")
"""
subprocess.run([PY, "-c", seed], check=True, capture_output=True)
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
 const box = e => { if (!e) return null; const r = e.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; };
 const vis = e => e && e.getBoundingClientRect().width > 0 && getComputedStyle(e).visibility !== 'hidden';
 const cards = [...document.querySelectorAll('.chat-empty-state__actions > .chat-quick-action, .chat-empty-state__actions > * > .chat-quick-action')].filter(vis);
 const composer = document.querySelector('.chat-composer');
 const cb = composer ? composer.getBoundingClientRect() : null;
 const overlap = cards.some(c => { const r = c.getBoundingClientRect(); return cb && r.bottom > cb.top + 1 && r.top < cb.bottom; });
 const inView = e => { if (!e) return false; const r = e.getBoundingClientRect(); return r.top >= -0.5 && r.bottom <= innerHeight + 0.5 && r.left >= -0.5 && r.right <= innerWidth + 0.5; };
 return {fenster: [innerWidth, innerHeight], querscroll: document.documentElement.scrollWidth > innerWidth || (document.querySelector('.main') || document.body).scrollWidth > (document.querySelector('.main') || document.body).clientWidth + 1,
  begruessung: (document.getElementById('chat-greeting') || {}).textContent, unterzeile: (document.querySelector('.chat-empty-state__hint') || {}).textContent,
  karten: cards.map(c => ({titel: (c.querySelector('.chat-quick-action__title') || {}).textContent, box: box(c), tag: c.tagName, bg: getComputedStyle(c).backgroundColor})),
  verwalten: (() => { const v = document.querySelector('.chat-empty-state__edit-prompts'); return v ? {text: v.textContent.trim(), box: box(v), href: v.getAttribute('href')} : null; })(),
  eingabe: box(composer), eingabe_sichtbar: inView(composer), karten_ueberlappen_eingabe: overlap,
  platzhalter: (document.querySelector('textarea[name="content"]') || {}).placeholder,
  kopf_icons: [...document.querySelectorAll('.topbar-global button, .app-header button, header .icon-btn, .header-actions > *')].length,
  eckbutton_ueberlappt_senden: (() => { const a = document.querySelector('.chat-privacy-corner .chat-composer__privacy-btn'), b = document.getElementById('chat-send-btn'); if (!a || !b) return null; const r = a.getBoundingClientRect(), s = b.getBoundingClientRect(); return r.left < s.right && r.right > s.left && r.top < s.bottom && r.bottom > s.top; })(),
  drei_punkte: document.querySelectorAll('.chat-empty-state [aria-label*="Optionen"], .chat-empty-state .kebab').length,
  js_fehler: window.__errs || []}; })()"""


async def main():
    cookies = [{"name": c.name, "value": c.value, "domain": "127.0.0.1", "path": "/"} for c in cj]
    edge = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={CDP}",
                             f"--user-data-dir={T / ('edge_profile_chatstart_' + PHASE)}", "--no-first-run",
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
                if "exceptionDetails" in res:
                    return {"js_fehler": json.dumps(res["exceptionDetails"])[:300]}
                return res.get("result", {}).get("value")

            async def shot(name):
                await asyncio.sleep(0.8)
                data = base64.b64decode((await cdp("Page.captureScreenshot", format="png"))["data"])
                (OUT / f"{name}.png").write_bytes(data)
                R["aufnahmen"][name] = await js(STATE)
                print(name, flush=True)

            await cdp("Page.enable"); await cdp("Network.enable"); await cdp("Runtime.enable")
            await cdp("Page.addScriptToEvaluateOnNewDocument", source="window.__errs = []; addEventListener('error', e => window.__errs.push(String(e.message))); Date.prototype.getHours = function () { return 20; };")
            for c in cookies:
                await cdp("Network.setCookie", **c)
            await cdp("Page.navigate", url=BASE + "/dashboard"); await asyncio.sleep(2)
            for dark in (False, True):
                tn = "dunkel" if dark else "hell"
                await js(f"localStorage.setItem('lexono_theme', '{'dark' if dark else 'light'}')")
                for w, h in ((1920, 1080), (1280, 720), (960, 600)):
                    p = f"{w}x{h}-{tn}"
                    await cdp("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
                    await cdp("Page.navigate", url=BASE + "/dashboard/chat?new=1"); await asyncio.sleep(2.5)
                    await shot(f"{p}-01-startseite")
                    if w == 1280 or (w == 1920 and not dark):
                        # Hover auf Karte 2, Tastaturfokus auf Karte 3
                        pos = await js("(() => { const c = document.querySelectorAll('.chat-empty-state__actions .chat-quick-action')[1]; const r = c.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()")
                        await cdp("Input.dispatchMouseEvent", type="mouseMoved", x=pos[0], y=pos[1])
                        await js("document.querySelectorAll('.chat-empty-state__actions .chat-quick-action')[2].focus({focusVisible: true})")
                        await shot(f"{p}-02-hover-und-fokus")
                        await cdp("Input.dispatchMouseEvent", type="mouseMoved", x=2, y=2)
                        # Karte "Akte oeffnen" bzw. vierte Karte anklicken (vorher: Link zur Prompt-Verwaltung -> nicht klicken)
                        fourth = await js("(() => { const c = document.querySelectorAll('.chat-empty-state__actions .chat-quick-action')[3]; return c ? [c.tagName, c.getAttribute('href'), c.textContent.trim().slice(0, 40)] : null; })()")
                        R.setdefault("vierte_karte", {})[p] = fourth
                        if fourth and fourth[0] == "BUTTON":
                            await js("document.querySelectorAll('.chat-empty-state__actions .chat-quick-action')[3].click()")
                            await shot(f"{p}-03-akte-oeffnen-auswahl")
                            await js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
                        # erste Karte: Prompt wird ins Eingabefeld uebernommen
                        await js("document.querySelectorAll('.chat-empty-state__actions .chat-quick-action')[1].click()")
                        await asyncio.sleep(0.6)
                        R.setdefault("prefill", {})[p] = await js("document.querySelector('textarea[name=\"content\"]').value")
                        await shot(f"{p}-04-prompt-uebernommen")
            await js("localStorage.setItem('lexono_theme', 'light')")
    finally:
        edge.terminate()
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(server.pid)], capture_output=True)


asyncio.run(main())
(OUT / f"messwerte_{PHASE}.json").write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
print("fertig", len(R["aufnahmen"]))
