"""Browser-QA "Dokument hochladen + 'Erstelle einen Schriftsatz'" (Quellstand, je Szenario frische isolierte Instanz,
synthetisches PDF, headless Edge per CDP, ECHTE Mausklicks per Input.dispatchMouseEvent). Schreibmodell: lokaler,
verzoegerter Fake (qa_serve_fake_writer.py) - kein Netz, keine echten Daten; jeder Aufruf wird gezaehlt.
Usage: pseudo_flow_qa.py <out_dir> <szenario> [delay_s]
Szenarien: erstversuch | abbrechen | doppelklick | optionen | fehler | timeout"""
import asyncio, base64, http.cookiejar, json, os, sqlite3, subprocess, sys, time, urllib.parse, urllib.request
from pathlib import Path

import websockets

T = Path(r"C:\Users\Bonit\.claude\jobs\02f3f8ce\tmp")
REPO = Path(r"C:\Users\Bonit\Lexono")
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
SC = sys.argv[2]
DELAY = sys.argv[3] if len(sys.argv) > 3 else "20"
MODE = "error" if SC == "fehler" else "ok"
INST, PORT, CDP = T / f"flowqa_{SC}", 8077, (9374 if os.environ.get("QA_WEBVIEW") == "1" else 9364)
BASE = f"http://127.0.0.1:{PORT}"
FAKE_LOG = INST / "fake_writer.log"
R = {"szenario": SC, "fake_delay_s": DELAY, "schritte": [], "pruefungen": {}}

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

UI = r"""(() => { const d = document.getElementById('pseudonymization-center');
 const opts = d ? [...d.querySelectorAll('.pseudo-question__option')] : [];
 const conf = d && d.querySelector('[data-pseudo-center-confirm]'); const apply = d && d.querySelector('[data-pseudo-q-apply]');
 const hit = opts.map(b => { const r = b.getBoundingClientRect(); const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return !!(e && b.contains(e)); });
 const ta = document.getElementById('chat-input'); const prog = document.querySelector('[data-chat-progress]');
 const err = document.querySelector('[data-chat-progress-error]') || [...document.querySelectorAll('.chat-message--assistant')].find(m => /technischen Gr|nicht erzeugt|Fehler/.test(m.textContent));
 return {dialog_offen: !!(d && d.open), optionen: opts.length, optionen_gesperrt: opts.map(b => b.disabled), klickbar_elementFromPoint: hit,
   gewaehlt: opts.filter(b => b.classList.contains('is-selected')).map(b => b.dataset.decision),
   ansicht_hinweis: !!(d && d.querySelector('.pseudo-question__view-hint')),
   bestaetigen: conf ? {text: conf.textContent.trim(), gesperrt: conf.disabled, sichtbar: !conf.hidden} : null,
   uebernehmen: apply ? {sichtbar: !apply.hidden, gesperrt: apply.disabled} : null,
   fusszeile: d ? (d.querySelector('[data-pseudo-open-count]') || {}).textContent : null,
   composer_text: ta ? ta.value : null, composer_gesperrt: ta ? ta.readOnly : null,
   ladeanzeige: prog ? prog.textContent.replace(/\s+/g, ' ').trim() : null, fehleranzeige: err ? err.textContent.replace(/\s+/g, ' ').trim().slice(0, 160) : null,
   ergebnisblock: !!document.querySelector('.chat-message--assistant .chat-draft, .chat-message--assistant [class*=draft]'),
   offene_fragen_im_ergebnis: [...document.querySelectorAll('.chat-message--assistant')].some(m => /Offene Fragen|Offene Pr/.test(m.textContent)),
   hinweis_unbeantwortet: !!document.querySelector('[data-chat-unanswered]'),
   url: location.pathname + location.search, js_fehler: window.__errs || []}; })()"""


def db():
    c = sqlite3.connect(f"file:{INST / 'data' / 'kanzlei_ai.db'}?mode=ro", uri=True)
    out = {"nachrichten": [r[0] + (":blocked" if r[1] else "") for r in c.execute(
               "select role, blocked from chat_messages order by created_at, id")],
           "entwuerfe": c.execute("select count(*) from drafts").fetchone()[0],
           "entscheidungen": sorted(c.execute("select term, decision from privacy_decisions").fetchall())}
    c.close()
    out["fake_aufrufe"] = len(FAKE_LOG.read_text().splitlines()) if FAKE_LOG.exists() else 0
    return out


async def main():
    if os.environ.get("QA_WEBVIEW") == "1":  # echtes WebView2-Fenster statt headless Edge
        wv_env = dict(os.environ, WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f"--remote-debugging-port={CDP} --no-proxy-server",
                      WEBVIEW2_USER_DATA_FOLDER=str(T / ("webview2_flowqa_" + SC)))
        edge = subprocess.Popen([PY, str(T / "webview_launcher.py"), BASE + "/dashboard/login"], env=wv_env)
    else:
        edge = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={CDP}", f"--user-data-dir={T / ('edge_flowqa_' + SC)}",
                                 "--no-first-run", "--disable-extensions", "--no-proxy-server", "about:blank"])
    try:
        for _ in range(60):
            try:
                targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP}/json", timeout=2).read())
                if any(t["type"] == "page" for t in targets): break
            except Exception:
                time.sleep(1)
        page = next(t for t in targets if t["type"] == "page")
        R["engine_ua"] = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP}/json/version", timeout=5).read()).get("User-Agent", "")
        if os.environ.get("QA_WEBVIEW") == "1":
            assert "Headless" not in R["engine_ua"] and "Edg/" in R["engine_ua"], "nicht am WebView2-Fenster: " + R["engine_ua"]
            R["webview2_fenster"] = True
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

            async def click(selector, nth=0, count=1):
                box = await js(f"(() => {{ const e = document.querySelectorAll({json.dumps(selector)})[{nth}]; if (!e) return null; e.scrollIntoView({{block: 'center'}}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()")
                if not box: return False
                for i in range(count):
                    for t in ("mousePressed", "mouseReleased"):
                        await cdp("Input.dispatchMouseEvent", type=t, x=box[0], y=box[1], button="left", clickCount=i + 1)
                return True

            async def step(name, shot=True):
                await asyncio.sleep(0.4)
                st = {"schritt": name, "t": round(time.time() - T0, 1), "ui": await js(UI), "db": db()}
                R["schritte"].append(st)
                if shot:
                    (OUT / f"{SC}-{name}.png").write_bytes(base64.b64decode((await cdp("Page.captureScreenshot", format="png"))["data"]))
                print(SC, name, json.dumps({k: st["ui"].get(k) for k in ("dialog_offen", "optionen_gesperrt", "ladeanzeige", "fehleranzeige", "composer_text")}, ensure_ascii=False), st["db"]["nachrichten"], "fake", st["db"]["fake_aufrufe"], flush=True)
                return st

            async def wait_for(expr, timeout=120):
                end = time.time() + timeout
                while time.time() < end:
                    try:
                        if await js(expr): return True
                    except Exception:
                        pass
                    await asyncio.sleep(0.4)
                return False

            DIALOG_READY = "(() => { const d = document.getElementById('pseudonymization-center'); return d && d.open && d.querySelectorAll('.pseudo-question__option').length > 0 && !document.querySelector('.pseudo-center__loading'); })()"

            async def upload_and_send():
                await cdp("Page.navigate", url=BASE + "/dashboard/chat?new=1"); await asyncio.sleep(2.5)
                doc = await cdp("DOM.getDocument", depth=-1)
                node = await cdp("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="#chat-file-input")
                await cdp("DOM.setFileInputFiles", nodeId=node["nodeId"], files=[str(DOC)])
                await js("document.getElementById('chat-input').focus()")
                await cdp("Input.insertText", text="Erstelle einen Schriftsatz")
                await asyncio.sleep(0.4)
                await click("#chat-send-btn")
                return await wait_for(DIALOG_READY, 120)

            async def answer_all(decision="personal"):
                for _ in range(12):
                    if not await js("!!document.querySelector('.pseudo-question__option:not([disabled])')"): break
                    await click(f".pseudo-question__option[data-decision='{decision}']")
                    await asyncio.sleep(0.3)
                    if await js("(() => { const c = document.querySelector('[data-pseudo-center-confirm]'); return c && !c.hidden && !c.disabled; })()"):
                        return True
                    if await js("(() => { const a = document.querySelector('[data-pseudo-q-apply]'); return a && !a.hidden && !a.disabled; })()"):
                        await click("[data-pseudo-q-apply]"); await asyncio.sleep(0.3)
                return await js("(() => { const c = document.querySelector('[data-pseudo-center-confirm]'); return c && !c.hidden && !c.disabled; })()")

            async def follow_progress(limit_s):
                seen = []
                end = time.time() + limit_s
                k = 0
                while time.time() < end:
                    await asyncio.sleep(2)
                    ui = await js(UI)
                    if ui and ui.get("ladeanzeige") and ui["ladeanzeige"] not in seen:
                        seen.append(ui["ladeanzeige"])
                    if k in (0, 4):
                        await step(f"10-ladezustand-{k:02d}")
                    k += 1
                    if (ui and ui.get("fehleranzeige")) or db()["entwuerfe"] or (ui and not ui.get("ladeanzeige") and k > 2 and not ui.get("dialog_offen")):
                        break
                R["pruefungen"]["gesehene_ladetexte"] = seen
                await asyncio.sleep(3)

            await cdp("Page.enable"); await cdp("Runtime.enable"); await cdp("DOM.enable")
            await cdp("Page.addScriptToEvaluateOnNewDocument", source="window.__errs = []; addEventListener('error', e => window.__errs.push(String(e.message)));" + ("window.LEXONO_STREAM_IDLE_MS = 6000;" if SC == "timeout" else ""))
            await cdp("Emulation.setDeviceMetricsOverride", width=1600, height=900, deviceScaleFactor=1, mobile=False)
            for c in cj:
                await cdp("Network.setCookie", name=c.name, value=c.value, domain="127.0.0.1", path="/")

            P = R["pruefungen"]
            P["dialog_beim_ersten_versuch_bereit"] = await upload_and_send()
            s1 = await step("01-erster-versuch-dialog")
            P["erster_versuch_optionen_bedienbar"] = s1["ui"]["optionen"] > 0 and not any(s1["ui"]["optionen_gesperrt"]) and all(s1["ui"]["klickbar_elementFromPoint"])
            P["vor_bestaetigen_kein_auftrag_kein_ki_aufruf"] = s1["db"]["nachrichten"] == ["upload"] and s1["db"]["fake_aufrufe"] == 0

            if SC == "optionen":
                for dec in ("personal", "not_personal", "unsure"):
                    await click(f".pseudo-question__option[data-decision='{dec}']")
                    st = await step(f"02-option-{dec}")
                    P[f"option_{dec}_gewaehlt"] = st["ui"]["gewaehlt"] == [dec]
                # erste Rueckfrage "Nicht pseudonymisieren", alle weiteren "Unsicher" -> Server muss genau das speichern
                await click(".pseudo-question__option[data-decision='not_personal']")
                if await js("(() => { const a = document.querySelector('[data-pseudo-q-apply]'); return a && !a.hidden && !a.disabled; })()"):
                    await click("[data-pseudo-q-apply]"); await asyncio.sleep(0.3)
                P["alle_beantwortet"] = await answer_all("unsure")
                await step("03-alle-beantwortet")
                await click("[data-pseudo-center-confirm]")
                await follow_progress(float(DELAY) + 60)
                fin = await step("20-ergebnis")
                P["gespeicherte_entscheidungen"] = fin["db"]["entscheidungen"]

            elif SC == "abbrechen":
                await click(".pseudo-question__option[data-decision='personal']")
                await step("02-auswahl-vor-abbruch")
                await click(".pseudo-center__footer [data-pseudo-center-cancel]")
                st = await step("03-nach-abbrechen")
                P["abbruch_dialog_zu"] = not st["ui"]["dialog_offen"]
                P["abbruch_kein_auftrag_kein_ki_aufruf"] = st["db"]["nachrichten"] == ["upload"] and st["db"]["fake_aufrufe"] == 0 and not st["db"]["entscheidungen"]
                P["abbruch_eingabe_erhalten"] = st["ui"]["composer_text"] == "Erstelle einen Schriftsatz"
                await asyncio.sleep(3)
                P["abbruch_nach_3s_weiterhin_kein_ki_aufruf"] = db()["fake_aufrufe"] == 0
                # bewusst erneut senden (Text steht im Eingabefeld)
                await click("#chat-send-btn")
                P["erneut_dialog_bereit"] = await wait_for(DIALOG_READY + " && !document.querySelector('.pseudo-question__option[disabled]')", 120)
                st = await step("04-erneut-gesendet-dialog")
                P["erneut_genau_ein_auftrag"] = st["db"]["nachrichten"].count("user") == 1
                P["alle_beantwortet"] = await answer_all()
                await click("[data-pseudo-center-confirm]")
                await follow_progress(float(DELAY) + 60)
                fin = await step("20-ergebnis")
                P["genau_ein_ki_aufruf"] = fin["db"]["fake_aufrufe"] == 1
                P["genau_ein_entwurf"] = fin["db"]["entwuerfe"] == 1

            else:
                P["alle_beantwortet"] = await answer_all()
                await step("03-alle-beantwortet")
                await click("[data-pseudo-center-confirm]", count=(3 if SC == "doppelklick" else 1))
                t_confirm = time.time()
                await asyncio.sleep(0.6)
                st = await step("04-direkt-nach-bestaetigen")
                P["sofort_ladeanzeige"] = bool(st["ui"]["ladeanzeige"]) and not st["ui"]["dialog_offen"]
                P["eingabe_waehrend_verarbeitung_gesperrt"] = st["ui"]["composer_gesperrt"] is True
                if SC == "doppelklick":
                    # zusaetzlich: Senden-Button waehrend der Verarbeitung klicken -> darf nichts ausloesen
                    await click("#chat-send-btn", count=2)
                await follow_progress(float(DELAY) + 60)
                fin = await step("20-ergebnis")
                P["sekunden_bis_ende"] = round(time.time() - t_confirm, 1)
                P["ki_aufrufe"] = fin["db"]["fake_aufrufe"]
                P["nutzernachrichten"] = fin["db"]["nachrichten"].count("user")
                P["entwuerfe"] = fin["db"]["entwuerfe"]
                if SC in ("erstversuch", "doppelklick"):
                    P["ergebnis_mit_offenen_fragen"] = fin["ui"]["offene_fragen_im_ergebnis"]
                    P["ladeanzeige_entfernt"] = not fin["ui"]["ladeanzeige"]
                if SC in ("fehler", "timeout"):
                    P["fehler_sichtbar"] = bool(fin["ui"]["fehleranzeige"])
                    P["keine_endlose_ladeanzeige"] = not fin["ui"]["ladeanzeige"]
                    P["eingabe_wieder_frei"] = fin["ui"]["composer_gesperrt"] is False
                    if SC == "timeout":
                        await asyncio.sleep(float(DELAY) + 5)
                        P["timeout_kein_zweiter_ki_aufruf"] = db()["fake_aufrufe"] <= 1
                        await cdp("Page.reload"); await asyncio.sleep(6)
                        st = await step("30-nach-neuladen")
                        P["neuladen_zeigt_unterhaltung_nicht_pruefansicht"] = ("uploaded=1" not in st["ui"]["url"]) and not st["ui"]["dialog_offen"]
                        P["neuladen_ergebnis_oder_status"] = st["db"]["nachrichten"]
                        P["neuladen_hinweis_unbeantwortet"] = st["ui"]["hinweis_unbeantwortet"]
                        P["neuladen_auftrag_im_eingabefeld"] = st["ui"]["composer_text"]
                        P["neuladen_kein_weiterer_ki_aufruf"] = st["db"]["fake_aufrufe"] <= 1
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(edge.pid)], capture_output=True)
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(server.pid)], capture_output=True)


T0 = time.time()
asyncio.run(main())
(OUT / f"{SC}-messwerte.json").write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
print("ENGINE", R.get("engine_ua", "")[-60:])
print("PRUEFUNGEN", json.dumps(R["pruefungen"], ensure_ascii=False))
