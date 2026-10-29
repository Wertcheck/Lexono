"""Native Pruefung der GEBAUTEN Pilot-EXE (nicht installiert) im eigenen WebView2-Fenster: isoliertes Datenverzeichnis,
synthetische Daten, toter Cloud-Endpunkt (kein Schreibmodell -> nach dem Bestaetigen muss eine verstaendliche
Fehlermeldung statt Haengen erscheinen). Steuerung per CDP ueber WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS.
Usage: native_exe_qa.py <out_dir>"""
import asyncio, base64, json, os, sqlite3, subprocess, sys, time, urllib.request
from pathlib import Path

import websockets

T = Path(r"C:\Users\Bonit\.claude\jobs\02f3f8ce\tmp")
REPO = Path(r"C:\Users\Bonit\Lexono")
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
EXE = T / "exe_dist" / "Lexono" / "Lexono.exe"
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
INST, PORT, CDP = T / "native_exe_qa", 8081, 9376
R = {"exe": str(EXE), "schritte": {}, "pruefungen": {}}
subprocess.run([PY, str(T / "pseudo_seed.py"), str(INST), str(PORT)], check=True, capture_output=True)
import pymupdf  # noqa: E402

pdf = pymupdf.open(); page = pdf.new_page()
for i, line in enumerate(["Einspruch gegen den Bescheid vom 02.02.2024 (synthetisches Musterdokument)",
                          "Wir bestaetigen den Eingang des Schreibens von Richter vom 16.03.2024."]):
    page.insert_text((50, 90 + 20 * i), line, fontsize=11)
DOC = INST / "einspruch_synthetisch.pdf"; pdf.save(str(DOC)); pdf.close()
env = dict(os.environ, LEXONO_DATA_DIR=str(INST), HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9",
           NO_PROXY="127.0.0.1,localhost", WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f"--remote-debugging-port={CDP}",
           WEBVIEW2_USER_DATA_FOLDER=str(T / "webview2_native_exe"))
for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "DATABASE_URL", "LEXONO_BUILD_PROFILE"):
    env.pop(k, None)
env["LEXONO_BUILD_PROFILE"] = "full"  # darf die Pilot-EXE NICHT erweitern
proc = subprocess.Popen([str(EXE)], cwd=str(INST), env=env, stdout=open(INST / "exe.out", "w"), stderr=subprocess.STDOUT)


def db():
    c = sqlite3.connect(f"file:{INST / 'data' / 'kanzlei_ai.db'}?mode=ro", uri=True)
    out = [r[0] + (":blocked" if r[1] else "") for r in c.execute("select role, blocked from chat_messages order by created_at, id")]
    c.close()
    return out


async def main():
    target = None
    for _ in range(180):
        try:
            targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP}/json", timeout=2).read())
            target = next((t for t in targets if t["type"] == "page" and "127.0.0.1" in t.get("url", "")), None)
            if target: break
        except Exception:
            pass
        time.sleep(1)
    ua = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP}/json/version", timeout=5).read()).get("User-Agent", "")
    R["engine_ua"] = ua
    R["pruefungen"]["natives_webview2_fenster"] = "Headless" not in ua and "Edg/" in ua
    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=300_000_000) as ws:
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
            (OUT / f"native-exe-{name}.png").write_bytes(base64.b64decode((await cdp("Page.captureScreenshot", format="png"))["data"]))

        async def click(sel):
            box = await js(f"(() => {{ const e = document.querySelector({json.dumps(sel)}); if (!e) return null; e.scrollIntoView({{block: 'center'}}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()")
            if not box: return False
            for t in ("mousePressed", "mouseReleased"):
                await cdp("Input.dispatchMouseEvent", type=t, x=box[0], y=box[1], button="left", clickCount=1)
            return True

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
        base = f"http://127.0.0.1:{PORT}"
        await cdp("Page.navigate", url=base + "/dashboard/login"); await asyncio.sleep(2)
        await js("document.querySelector('input[name=email]').value='qa-pseudo@lexono.test'; document.querySelector('input[name=password]').value='QA-pseudo-0000'")
        await click("button[type=submit]"); await asyncio.sleep(3)
        # Lizenz-Tab
        await cdp("Page.navigate", url=base + "/dashboard/settings?tab=lizenz"); await asyncio.sleep(2.5)
        R["schritte"]["lizenz"] = await js("({produkte: [...document.querySelectorAll('[data-product]')].map(e => e.dataset.product + ':' + e.dataset.productStatus), aktiv: [...document.querySelectorAll('[data-product-state=active]')].length, module: [...document.querySelectorAll('tr[data-module]')].map(e => e.dataset.module), querscroll: document.querySelector('.main').scrollWidth > document.querySelector('.main').clientWidth})")
        await shot("01-lizenz")
        await js("document.querySelector('.license-compare').scrollIntoView({block: 'start'})"); await asyncio.sleep(0.5)
        await shot("02-lizenz-vergleich")
        # Upload + Auftrag
        await cdp("Page.navigate", url=base + "/dashboard/chat?new=1"); await asyncio.sleep(2.5)
        d = await cdp("DOM.getDocument", depth=-1)
        node = await cdp("DOM.querySelector", nodeId=d["root"]["nodeId"], selector="#chat-file-input")
        await cdp("DOM.setFileInputFiles", nodeId=node["nodeId"], files=[str(DOC)])
        await js("document.getElementById('chat-input').focus()")
        await cdp("Input.insertText", text="Erstelle einen Schriftsatz")
        await click("#chat-send-btn")
        ready = await wait_for("(() => { const d = document.getElementById('pseudonymization-center'); return d && d.open && d.querySelectorAll('.pseudo-question__option').length > 0 && !document.querySelector('.pseudo-center__loading'); })()", 180)
        st = await js("(() => { const o = [...document.querySelectorAll('.pseudo-question__option')]; return {gesperrt: o.map(b => b.disabled), treffer: o.map(b => { b.scrollIntoView({block: 'center'}); const r = b.getBoundingClientRect(); const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return !!(e && b.contains(e)); })}; })()")
        R["schritte"]["optionen"] = st
        R["pruefungen"]["erster_versuch_dialog_bedienbar"] = bool(ready and st["gesperrt"] and not any(st["gesperrt"]) and all(st["treffer"]))
        R["pruefungen"]["vor_bestaetigen_nur_upload"] = db() == ["upload"]
        await shot("03-dialog-erster-versuch")
        for _ in range(8):
            await click(".pseudo-question__option[data-decision='personal']"); await asyncio.sleep(0.3)
            if await js("(() => { const c = document.querySelector('[data-pseudo-center-confirm]'); return c && !c.hidden && !c.disabled; })()"): break
            if await js("(() => { const a = document.querySelector('[data-pseudo-q-apply]'); return a && !a.hidden && !a.disabled; })()"):
                await click("[data-pseudo-q-apply]"); await asyncio.sleep(0.3)
        await shot("04-entschieden")
        # Ladeanzeige protokollieren, auch wenn sie nur kurz sichtbar ist (ohne API-Schluessel antwortet der Server sofort)
        await js("window.__progressSeen = []; new MutationObserver(() => { const p = document.querySelector('[data-chat-progress]'); if (p) { const t = p.textContent.replace(/\s+/g, ' ').trim(); if (window.__progressSeen.indexOf(t) < 0) { window.__progressSeen.push(t); sessionStorage.setItem('qa-progress', JSON.stringify(window.__progressSeen)); } } }).observe(document.body, {subtree: true, childList: true, characterData: true, attributes: true}); true")
        await click("[data-pseudo-center-confirm]")
        await asyncio.sleep(0.3)
        await asyncio.sleep(2.5)
        R["schritte"]["ladeanzeige_protokoll"] = await js("JSON.parse(sessionStorage.getItem('qa-progress') || '[]')")
        R["schritte"]["nach_bestaetigen"] = await js("({dialog_offen: document.getElementById('pseudonymization-center').open, ladeanzeige: (document.querySelector('[data-chat-progress]') || {}).textContent || null, eingabe_gesperrt: document.getElementById('chat-input').readOnly})")
        R["pruefungen"]["ladeanzeige_sofort"] = bool(R["schritte"]["ladeanzeige_protokoll"]) and not R["schritte"]["nach_bestaetigen"]["dialog_offen"]
        await shot("05-ladezustand")
        ended = await wait_for("(() => !document.querySelector('[data-chat-progress]') && (document.querySelector('[data-chat-progress-error]') || [...document.querySelectorAll('.chat-message--assistant')].some(m => /nicht|Fehler|konfiguriert/.test(m.textContent))))()", 240)
        await asyncio.sleep(2)
        R["schritte"]["ende"] = await js("({fehler: ([...document.querySelectorAll('[data-chat-progress-error], .chat-message--assistant')].map(m => m.textContent.replace(/\\s+/g, ' ').trim().slice(0, 200)).slice(-1)[0]) || null, ladeanzeige: !!document.querySelector('[data-chat-progress]')})")
        R["pruefungen"]["ende_mit_verstaendlicher_meldung_statt_haengen"] = bool(ended) and not R["schritte"]["ende"]["ladeanzeige"]
        R["schritte"]["nachrichten"] = db()
        R["pruefungen"]["genau_ein_auftrag"] = db().count("user") == 1
        await shot("06-ende")


try:
    asyncio.run(main())
finally:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
log = (INST / "exe.out").read_text(encoding="utf-8", errors="replace") if (INST / "exe.out").exists() else ""
R["tracebacks"] = log.count("Traceback")
(OUT / "native-exe-messwerte.json").write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(R, ensure_ascii=False, indent=1))
