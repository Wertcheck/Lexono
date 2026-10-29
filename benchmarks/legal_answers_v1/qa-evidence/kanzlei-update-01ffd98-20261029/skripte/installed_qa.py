"""Native QA der ISOLIERT installierten Pilot-EXE (iso/app, nach dem Update-Test) im eigenen WebView2-Fenster.
Datenverzeichnis iso/data (synthetisch, aus dem Update-Test), Cloud = lokaler Loopback-Recorder (offizieller
Pruefendpunkt: ANTHROPIC_BASE_URL=127.0.0.1 + LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT=1, ungueltiger Platzhalterschluessel,
kein Netz). Szenarien: Lizenzseite, Erstversuch, alle Optionen, Abbruch, erneutes Senden, Dreifachklick, Ladezustand,
Erfolgsweg mit offenen Fragen, Fehlerweg (Recorder gestoppt). Usage: installed_qa.py <out_dir>"""
import asyncio, base64, json, os, sqlite3, subprocess, sys, time, urllib.request
from pathlib import Path

import websockets

T = Path(r"C:\Users\Bonit\.claude\jobs\02f3f8ce\tmp")
REPO = Path(r"C:\Users\Bonit\Lexono")
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
ISO = T / "iso"
APP = Path(os.environ["QA_APP_DIR"]) if os.environ.get("QA_APP_DIR") else ISO / "app"
DATA = Path(os.environ["QA_DATA_DIR"]) if os.environ.get("QA_DATA_DIR") else ISO / "data"
assert "jobs" in str(DATA), "Datenverzeichnis muss isoliert im Job-Ordner liegen"
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
PORT, CDP, REC = 8083, 9377, 8097
BASE = f"http://127.0.0.1:{PORT}"
RECDIR = ISO / "recorder"
R = {"schritte": {}, "pruefungen": {}}
import pymupdf  # noqa: E402

DOC = ISO / "qa_bescheid_synthetisch.pdf"
pdf = pymupdf.open(); pg = pdf.new_page()
for i, line in enumerate(["Einspruch gegen den Bescheid vom 02.02.2024 (synthetisches Musterdokument)",
                          "Wir bestaetigen den Eingang des Schreibens von Richter vom 16.03.2024.",
                          "Wir bestaetigen den Eingang des Schreibens von Kaufmann vom 18.03.2024."]):
    pg.insert_text((50, 90 + 20 * i), line, fontsize=11)
pdf.save(str(DOC)); pdf.close()
if RECDIR.exists():
    import shutil; shutil.rmtree(RECDIR)
rec = subprocess.Popen([PY, str(T / "qa_recorder_delay.py"), str(REC), str(RECDIR)], env=dict(os.environ, QA_REC_DELAY="12"))
env = dict(os.environ, LEXONO_DATA_DIR=str(DATA), HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9",
           NO_PROXY="127.0.0.1,localhost", WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f"--remote-debugging-port={CDP}",
           WEBVIEW2_USER_DATA_FOLDER=str(T / "webview2_installed_qa"), ANTHROPIC_BASE_URL=f"http://127.0.0.1:{REC}",
           LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT="1", ANTHROPIC_API_KEY="sk-ant-qa-invalid-test-key-0000",
           LEXONO_BUILD_PROFILE="full")  # darf die Pilot-EXE nicht erweitern
for k in ("DATABASE_URL",):
    env.pop(k, None)
app = subprocess.Popen([str(APP / "Lexono.exe")], cwd=str(DATA), env=env, stdout=open(ISO / "installed_qa_app.out", "w"),
                       stderr=subprocess.STDOUT)


def rec_calls():
    f = RECDIR / "requests.jsonl"
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


def msgs(conv=None):
    c = sqlite3.connect(f"file:{DATA / 'data' / 'kanzlei_ai.db'}?mode=ro", uri=True)
    q = "select role, blocked from chat_messages" + (" where conversation_id=?" if conv else "") + " order by created_at, id"
    out = [r[0] + (":blocked" if r[1] else "") for r in c.execute(q, (conv,) if conv else ())]
    c.close()
    return out


async def main():
    target = None
    for _ in range(240):
        try:
            targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP}/json", timeout=2).read())
            target = next((t for t in targets if t["type"] == "page" and "127.0.0.1" in t.get("url", "")), None)
            if target: break
        except Exception:
            pass
        time.sleep(1)
    R["engine_ua"] = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP}/json/version", timeout=5).read()).get("User-Agent", "")
    R["pruefungen"]["natives_webview2_fenster_der_installierten_exe"] = "Headless" not in R["engine_ua"] and "Edg/" in R["engine_ua"]
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
            (OUT / f"installiert-{name}.png").write_bytes(base64.b64decode((await cdp("Page.captureScreenshot", format="png"))["data"]))

        async def click(sel, count=1):
            box = await js(f"(() => {{ const e = document.querySelector({json.dumps(sel)}); if (!e) return null; e.scrollIntoView({{block: 'center'}}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()")
            if not box: return False
            for i in range(count):
                for t in ("mousePressed", "mouseReleased"):
                    await cdp("Input.dispatchMouseEvent", type=t, x=box[0], y=box[1], button="left", clickCount=i + 1)
            return True

        async def wait_for(expr, timeout=180):
            end = time.time() + timeout
            while time.time() < end:
                try:
                    if await js(expr): return True
                except Exception:
                    pass
                await asyncio.sleep(0.5)
            return False

        READY = "(() => { const d = document.getElementById('pseudonymization-center'); return d && d.open && d.querySelectorAll('.pseudo-question__option').length > 0 && !document.querySelector('.pseudo-center__loading'); })()"
        OPTS = "(() => { const o = [...document.querySelectorAll('.pseudo-question__option')]; return {gesperrt: o.map(b => b.disabled), treffer: o.map(b => { b.scrollIntoView({block: 'center'}); const r = b.getBoundingClientRect(); const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return !!(e && b.contains(e)); })}; })()"
        PROGRESS_LOG = ("window.__p = JSON.parse(sessionStorage.getItem('qa-progress') || '[]'); new MutationObserver(() => { const p = document.querySelector('[data-chat-progress]'); "
                        "if (p) { const t = p.textContent.replace(/\\s+/g, ' ').trim().replace(/ \\(\\d+ s\\)$/, ''); if (window.__p.indexOf(t) < 0) { window.__p.push(t); sessionStorage.setItem('qa-progress', JSON.stringify(window.__p)); } } })"
                        ".observe(document.body, {subtree: true, childList: true, characterData: true}); true")

        async def upload_and_send(text="Erstelle einen Schriftsatz"):
            await cdp("Page.navigate", url=BASE + "/dashboard/chat?new=1"); await asyncio.sleep(2.5)
            d = await cdp("DOM.getDocument", depth=-1)
            node = await cdp("DOM.querySelector", nodeId=d["root"]["nodeId"], selector="#chat-file-input")
            await cdp("DOM.setFileInputFiles", nodeId=node["nodeId"], files=[str(DOC)])
            await js("document.getElementById('chat-input').focus()")
            await cdp("Input.insertText", text=text)
            await click("#chat-send-btn")
            return await wait_for(READY)

        async def answer_all(decision="personal"):
            for _ in range(10):
                await click(f".pseudo-question__option[data-decision='{decision}']"); await asyncio.sleep(0.3)
                if await js("(() => { const c = document.querySelector('[data-pseudo-center-confirm]'); return c && !c.hidden && !c.disabled; })()"): return True
                if await js("(() => { const a = document.querySelector('[data-pseudo-q-apply]'); return a && !a.hidden && !a.disabled; })()"):
                    await click("[data-pseudo-q-apply]"); await asyncio.sleep(0.3)
            return False

        async def conv_id():
            return await js("(() => { const f = document.querySelector('input[name=conversation_id]'); return f ? f.value : (location.pathname.split('/')[3] || null); })()")

        await cdp("Page.enable"); await cdp("Runtime.enable"); await cdp("DOM.enable")
        await cdp("Page.navigate", url=BASE + "/dashboard/login"); await asyncio.sleep(2)
        await js("document.querySelector('input[name=email]').value='qa-update@lexono.test'; document.querySelector('input[name=password]').value='QA-update-0000'")
        await click("button[type=submit]"); await asyncio.sleep(3)
        P = R["pruefungen"]
        # A) Lizenzseite
        await cdp("Page.navigate", url=BASE + "/dashboard/settings?tab=lizenz"); await asyncio.sleep(2.5)
        R["schritte"]["lizenz"] = await js("({produkte: [...document.querySelectorAll('[data-product]')].map(e => e.dataset.product + ':' + e.dataset.productStatus), aktiv: document.querySelectorAll('[data-product-state=active]').length, bald: document.querySelectorAll('[data-product-state=soon]').length, module: [...document.querySelectorAll('tr[data-module]')].map(e => e.dataset.module), querscroll: document.querySelector('.main').scrollWidth > document.querySelector('.main').clientWidth, mwst: (document.body.innerText.match(/inkl\\. MwSt\\./g) || []).length})")
        L = R["schritte"]["lizenz"]
        P["lizenzseite"] = L["produkte"] == ["lexono:verfuegbar", "finance:in_entwicklung", "finance_tax:in_entwicklung"] and L["aktiv"] == 1 and L["bald"] == 2 and L["module"] == ["kanzlei"] and not L["querscroll"]
        await shot("01-lizenz"); await js("document.querySelector('.license-compare').scrollIntoView({block: 'start'})"); await asyncio.sleep(0.4); await shot("02-lizenz-vergleich")
        # B) Erstversuch + Optionen + Abbruch
        P["erstversuch_dialog_bereit"] = await upload_and_send()
        st = await js(OPTS); R["schritte"]["optionen_erstversuch"] = st
        P["erstversuch_optionen_bedienbar"] = bool(st["gesperrt"]) and not any(st["gesperrt"]) and all(st["treffer"])
        await shot("03-dialog-erstversuch")
        sel = {}
        for dec in ("personal", "not_personal", "unsure"):
            await click(f".pseudo-question__option[data-decision='{dec}']"); await asyncio.sleep(0.3)
            sel[dec] = await js("[...document.querySelectorAll('.pseudo-question__option.is-selected')].map(b => b.dataset.decision)")
        P["alle_optionen_waehlbar"] = all(sel[d] == [d] for d in sel)
        c1 = await conv_id()
        await click(".pseudo-center__footer [data-pseudo-center-cancel]"); await asyncio.sleep(1.5)
        R["schritte"]["nach_abbruch"] = {"nachrichten": msgs(c1), "recorder": len(rec_calls()),
                                         "eingabe": await js("document.getElementById('chat-input').value")}
        P["abbruch_sauber"] = R["schritte"]["nach_abbruch"]["nachrichten"] == ["upload"] and R["schritte"]["nach_abbruch"]["recorder"] == 0 \
            and R["schritte"]["nach_abbruch"]["eingabe"] == "Erstelle einen Schriftsatz"
        await shot("04-nach-abbruch")
        # C) bewusst erneut senden -> Dialog (offener Auftrag) -> Dreifachklick -> Ladezustand -> Erfolg
        await click("#chat-send-btn")
        P["erneut_gesendet_dialog_bereit"] = await wait_for(READY + " && !document.querySelector('.pseudo-question__option[disabled]')")
        P["erneut_genau_ein_auftrag"] = msgs(c1).count("user") == 1
        await answer_all("personal")
        await js("sessionStorage.removeItem('qa-progress')"); await js(PROGRESS_LOG)
        await click("[data-pseudo-center-confirm]", count=3)
        await asyncio.sleep(1.5); await shot("05-ladezustand")
        ok = await wait_for("(() => [...document.querySelectorAll('.chat-message--assistant')].some(m => /Offene Fragen/.test(m.textContent)) && !document.querySelector('[data-chat-progress]'))()", 120)
        await asyncio.sleep(1.5)
        R["schritte"]["erfolg_abbruchpfad"] = {"ladeanzeige": await js("JSON.parse(sessionStorage.getItem('qa-progress') || '[]')"),
                                               "nachrichten": msgs(c1), "recorder": len(rec_calls())}
        E = R["schritte"]["erfolg_abbruchpfad"]
        P["ladeanzeige_mit_echten_phasen"] = any("Schriftsatz wird erstellt" in t for t in E["ladeanzeige"])
        P["erfolg_ergebnis_mit_offenen_fragen"] = ok and E["nachrichten"].count("assistant") == 1
        P["dreifachklick_genau_ein_ki_aufruf"] = E["recorder"] == 1 and E["nachrichten"].count("user") == 1
        await shot("06-ergebnis-offene-fragen")
        calls = rec_calls()
        P["anfrage_pseudonymisiert"] = bool(calls) and "Richter" not in json.dumps(calls[-1]["body"], ensure_ascii=False)
        # D) Erstversuch direkt bestaetigen -> Erfolg
        P["erstversuch2_dialog_bereit"] = await upload_and_send()
        c2 = await conv_id()
        await answer_all("unsure")
        await js("sessionStorage.removeItem('qa-progress')"); await js(PROGRESS_LOG)
        await click("[data-pseudo-center-confirm]")
        ok2 = await wait_for("(() => [...document.querySelectorAll('.chat-message--assistant')].some(m => /Offene Fragen/.test(m.textContent)) && !document.querySelector('[data-chat-progress]'))()", 120)
        R["schritte"]["erfolg_erstversuch"] = {"nachrichten": msgs(c2), "recorder_gesamt": len(rec_calls())}
        P["erstversuch_erfolg_genau_einmal"] = ok2 and msgs(c2).count("user") == 1 and len(rec_calls()) == 2
        await shot("07-erstversuch-ergebnis")
        # E) Fehlerweg: Recorder gestoppt
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(rec.pid)], capture_output=True); time.sleep(1)
        P["fehlerweg_dialog_bereit"] = await upload_and_send()
        c3 = await conv_id()
        await answer_all("personal")
        await click("[data-pseudo-center-confirm]")
        ended = await wait_for("(() => !document.querySelector('[data-chat-progress]') && (!!document.querySelector('[data-chat-progress-error]') || [...document.querySelectorAll('.chat-message--assistant')].some(m => /nicht|Fehler|technisch/.test(m.textContent))))()", 180)
        await asyncio.sleep(1.5)
        R["schritte"]["fehlerweg"] = {"nachrichten": msgs(c3), "meldung": await js("([...document.querySelectorAll('[data-chat-progress-error], .chat-message--assistant')].map(m => m.textContent.replace(/\\s+/g, ' ').trim().slice(0, 220)).slice(-1)[0]) || null"),
                                      "eingabe_frei": await js("!document.getElementById('chat-input').readOnly")}
        P["fehlerweg_verstaendlich_ohne_haengen"] = bool(ended) and R["schritte"]["fehlerweg"]["eingabe_frei"]
        await shot("08-fehlerweg")


try:
    asyncio.run(main())
finally:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(app.pid)], capture_output=True)
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(rec.pid)], capture_output=True)
log = (ISO / "installed_qa_app.out").read_text(encoding="utf-8", errors="replace")
R["tracebacks_app_log"] = log.count("Traceback")
R["recorder_aufrufe_gesamt"] = len(rec_calls())
(OUT / "installiert-messwerte.json").write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(R["pruefungen"], ensure_ascii=False, indent=1))
print("tracebacks", R["tracebacks_app_log"], "recorder", R["recorder_aufrufe_gesamt"])
