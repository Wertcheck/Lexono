"""Reproduktion/Nachweis des Ablaufs "Dokument hochladen + 'Erstelle einen Schriftsatz' senden" in der echten UI
(Quellstand, isolierte Instanz, synthetisches PDF, headless Edge per CDP, ECHTE Mausklicks per Input.dispatchMouseEvent).
Schreibmodell: lokaler, verzoegerter Fake (qa_serve_fake_writer.py) - kein Netz, keine echten Daten.
Usage: pseudo_flow_repro.py <out_dir> <phase> [delay_s] [mode ok|error]"""
import asyncio, base64, http.cookiejar, json, os, sqlite3, subprocess, sys, time, urllib.parse, urllib.request
from pathlib import Path

import websockets

T = Path(r"C:\Users\Bonit\.claude\jobs\02f3f8ce\tmp")
REPO = Path(r"C:\Users\Bonit\Lexono")
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
PHASE = sys.argv[2]
DELAY = sys.argv[3] if len(sys.argv) > 3 else "20"
MODE = sys.argv[4] if len(sys.argv) > 4 else "ok"
INST, PORT, CDP = T / "flow_qa", 8076, 9363
BASE = f"http://127.0.0.1:{PORT}"
FAKE_LOG = INST / "fake_writer.log"
R = {"phase": PHASE, "schritte": []}

subprocess.run([PY, str(T / "pseudo_seed.py"), str(INST), str(PORT)], check=True, capture_output=True)
import pymupdf  # noqa: E402

pdf = pymupdf.open(); page = pdf.new_page()
for i, line in enumerate(["Einspruch gegen den Bescheid vom 02.02.2024 (synthetisches Musterdokument)",
                          "Mandant: Max Bauer, Lindenweg 4, 50667 Koeln",
                          "Wir bestaetigen den Eingang des Schreibens von Richter vom 16.03.2024.",
                          "Wir bestaetigen den Eingang des Schreibens von Kaufmann vom 18.03.2024."]):
    page.insert_text((50, 90 + 20 * i), line, fontsize=11)
DOC = INST / "einspruch_synthetisch.pdf"; pdf.save(str(DOC)); pdf.close()

env = dict(os.environ, LEXONO_DATA_DIR=str(INST), PYTHONDONTWRITEBYTECODE="1", QA_FAKE_DELAY=DELAY, QA_FAKE_MODE=MODE,
           QA_FAKE_LOG=str(FAKE_LOG), HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9",
           NO_PROXY="127.0.0.1,localhost")
for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "DATABASE_URL"):
    env.pop(k, None)
server = subprocess.Popen([PY, str(T / "qa_serve_fake_writer.py"), str(PORT)], cwd=str(INST), env=env,
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

DLG = r"""(() => { const d = document.getElementById('pseudonymization-center'); if (!d) return {dialog: 'fehlt'};
 const opts = [...d.querySelectorAll('.pseudo-question__option')];
 const conf = d.querySelector('[data-pseudo-center-confirm]'); const apply = d.querySelector('[data-pseudo-q-apply]');
 const hit = opts.map(b => { const r = b.getBoundingClientRect(); const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return !!(e && b.contains(e)); });
 const ta = document.getElementById('chat-input');
 return {offen: d.open, optionen: opts.length, optionen_gesperrt: opts.map(b => b.disabled), treffer_elementFromPoint: hit,
   gewaehlt: opts.filter(b => b.classList.contains('is-selected')).map(b => b.dataset.decision),
   bestaetigen: conf ? {text: conf.textContent.trim(), gesperrt: conf.disabled, sichtbar: !conf.hidden} : null,
   uebernehmen: apply ? {sichtbar: !apply.hidden, gesperrt: apply.disabled} : null,
   offene_zaehlung: (d.querySelector('[data-pseudo-open-count]') || {}).textContent,
   hinweis: (d.querySelector('[data-pseudo-notice]') || {}).textContent,
   composer_text: ta ? ta.value : null, url: location.pathname + location.search,
   blasen: [...document.querySelectorAll('.chat-message')].slice(-3).map(m => (m.className.match(/chat-message--\w+/g) || []).join(' ') + ' | ' + m.textContent.replace(/\s+/g, ' ').trim().slice(0, 140)),
   ladeanzeige: [...document.querySelectorAll('.chat-thinking-indicator, .chat-message--thinking, [data-draft-progress]')].filter(e => e.getBoundingClientRect().height > 0).map(e => e.textContent.replace(/\s+/g, ' ').trim().slice(0, 120)),
   js_fehler: window.__errs || []}; })()"""


def db():
    c = sqlite3.connect(f"file:{INST / 'data' / 'kanzlei_ai.db'}?mode=ro", uri=True)
    out = {"nachrichten": [r[0] + (":blocked" if r[1] else "") for r in c.execute(
               "select role, blocked from chat_messages order by created_at, id")],
           "entwuerfe": c.execute("select count(*) from drafts").fetchone()[0],
           "freigaben": c.execute("select count(*) from document_releases").fetchone()[0]}
    c.close()
    out["fake_aufrufe"] = len(FAKE_LOG.read_text().splitlines()) if FAKE_LOG.exists() else 0
    return out


async def main():
    edge = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={CDP}", f"--user-data-dir={T / 'edge_flow'}",
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
                        return msg.get("result", msg.get("error", {}))

            async def js(expr):
                return (await cdp("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)).get("result", {}).get("value")

            async def shot(name):
                (OUT / f"{PHASE}-{name}.png").write_bytes(base64.b64decode((await cdp("Page.captureScreenshot", format="png"))["data"]))

            async def click(selector, nth=0):
                box = await js(f"(() => {{ const e = document.querySelectorAll({json.dumps(selector)})[{nth}]; if (!e) return null; e.scrollIntoView({{block: 'center'}}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()")
                if not box: return False
                for t in ("mousePressed", "mouseReleased"):
                    await cdp("Input.dispatchMouseEvent", type=t, x=box[0], y=box[1], button="left", clickCount=1)
                return True

            async def step(name, extra=None):
                await asyncio.sleep(0.4)
                st = {"schritt": name, "t": round(time.time() - T0, 1), "ui": await js(DLG), "db": db()}
                if extra: st.update(extra)
                R["schritte"].append(st); await shot(name)
                print(name, json.dumps(st["ui"].get("optionen_gesperrt")), st["ui"].get("bestaetigen"), st["db"], flush=True)

            async def wait_for(expr, timeout=120):
                end = time.time() + timeout
                while time.time() < end:
                    try:
                        if await js(expr): return True
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)
                return False

            await cdp("Page.enable"); await cdp("Runtime.enable"); await cdp("DOM.enable")
            await cdp("Page.addScriptToEvaluateOnNewDocument", source="window.__errs = []; addEventListener('error', e => window.__errs.push(String(e.message)));")
            await cdp("Emulation.setDeviceMetricsOverride", width=1600, height=900, deviceScaleFactor=1, mobile=False)
            for c in cj:
                await cdp("Network.setCookie", name=c.name, value=c.value, domain="127.0.0.1", path="/")
            await cdp("Page.navigate", url=BASE + "/dashboard/chat?new=1"); await asyncio.sleep(2.5)
            # 1) Dokument anhaengen + Auftrag tippen + Senden (echter Klick)
            doc = await cdp("DOM.getDocument", depth=-1)
            node = await cdp("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="#chat-file-input")
            await cdp("DOM.setFileInputFiles", nodeId=node["nodeId"], files=[str(DOC)])
            await js("(() => { const t = document.getElementById('chat-input'); t.focus(); })()")
            await cdp("Input.insertText", text="Erstelle einen Schriftsatz")
            await asyncio.sleep(0.5)
            await click("#chat-send-btn")
            ok = await wait_for("(() => { const d = document.getElementById('pseudonymization-center'); return d && d.open && d.querySelectorAll('.pseudo-question__option').length > 0; })()", 120)
            await step("01-erster-versuch-dialog", {"dialog_mit_optionen": ok})
            await click(".pseudo-question__option", 0)
            await step("02-erster-versuch-klick-pseudonymisieren")
            # 2) Abbrechen
            await click(".pseudo-center__footer [data-pseudo-center-cancel]")
            await step("03-nach-abbrechen")
            # 3) erneut senden
            await click("#chat-send-btn")
            ok = await wait_for("(() => { const d = document.getElementById('pseudonymization-center'); return d && d.open && d.querySelectorAll('.pseudo-question__option:not([disabled])').length > 0; })()", 120)
            await step("04-zweiter-versuch-dialog", {"dialog_bedienbar": ok})
            # alle Rueckfragen beantworten
            for _ in range(10):
                if not await js("!!document.querySelector('.pseudo-question__option:not([disabled])')"): break
                await click(".pseudo-question__option[data-decision='personal']")
                await asyncio.sleep(0.3)
                if await js("(() => { const a = document.querySelector('[data-pseudo-q-apply]'); return a && !a.hidden && !a.disabled; })()"):
                    await click("[data-pseudo-q-apply]"); await asyncio.sleep(0.3)
                if await js("(() => { const c = document.querySelector('[data-pseudo-center-confirm]'); return c && !c.hidden && !c.disabled; })()"):
                    break
            await step("05-entscheidungen-gewaehlt")
            await click("[data-pseudo-center-confirm]")
            t_confirm = time.time()
            for i in range(int(float(DELAY)) // 5 + 8):
                await asyncio.sleep(5)
                await step(f"06-warten-{i:02d}")
                if db()["entwuerfe"] or (await js("!!document.querySelector('.chat-message--assistant .draft-result, .chat-message__draft, [data-draft-id]')")):
                    break
            await asyncio.sleep(3)
            await step("07-ergebnis", {"sekunden_seit_bestaetigen": round(time.time() - t_confirm, 1)})
    finally:
        edge.terminate()
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(server.pid)], capture_output=True)


T0 = time.time()
asyncio.run(main())
(OUT / f"{PHASE}-messwerte.json").write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
