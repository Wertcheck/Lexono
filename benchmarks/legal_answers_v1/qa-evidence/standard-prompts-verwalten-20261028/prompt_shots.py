"""QA "Standard-Prompts verwalten" (Quellstand, run.py serve --no-window, isolierte Instanz per Migration -> die vier
bisherigen Startkarten werden uebernommen; drei weitere SYNTHETISCHE Prompts, inaktiv). Headless Edge per CDP, DPR 1.
Usage: prompt_shots.py <out_dir> <phase>"""
import asyncio, base64, http.cookiejar, json, os, re, subprocess, sys, time, urllib.parse, urllib.request
from pathlib import Path

import websockets

T = Path(__file__).resolve().parent
REPO = Path(os.environ.get("QA_REPO", r"<REPOSITORY>"))
PY = r"<REPOSITORY>\.venv\Scripts\python.exe"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
PHASE = sys.argv[2]
INST, PORT, CDP = T / "prompt_qa", 8080, 9367
BASE = f"http://127.0.0.1:{PORT}"
R = {"phase": PHASE, "aufnahmen": {}, "funktion": {}}

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


def get(path):
    return op.open(BASE + path, timeout=60).read().decode("utf-8")


R["funktion"]["nach_migration"] = [(i["name"], i["show_on_start"]) for i in json.loads(get("/dashboard/library/prompts/manage.json"))["items"]]
# drei weitere synthetische Prompts (inaktiv) ueber die echte Speicher-API
items = json.loads(get("/dashboard/library/prompts/manage.json"))["items"]
extra = [("Rechtliche Bewertung", "Juristische Einschätzung und Argumente", "scale",
          "Bitte bewerte den folgenden Sachverhalt rechtlich und nenne die wichtigsten Argumente: "),
         ("Recherche", "Gesetze, Urteile und Literatur finden", "bulb",
          "Bitte recherchiere die einschlägigen Normen und Entscheidungen zu folgender Frage: "),
         ("Fristenkontrolle", "Fristen im Dokument erkennen und prüfen", "calendar",
          "Bitte prüfe das angehängte Dokument auf Fristen und Termine.")]
payload = {"items": items + [{"id": None, "key": "k" + str(n), "name": a, "description": b, "icon": c, "content": d,
                              "show_on_start": False} for n, (a, b, c, d) in enumerate(extra)], "deleted": []}
csrf = re.search(r'name="csrf_token" value="([^"]+)"', get("/dashboard/chat?new=1")).group(1)
r = op.open(urllib.request.Request(BASE + "/dashboard/library/prompts/manage", data=urllib.parse.urlencode(
    {"csrf_token": csrf, "payload": json.dumps(payload)}).encode()), timeout=30)
R["funktion"]["synthetische_prompts_angelegt"] = json.loads(r.read())["ok"]

STATE = r"""(() => { const d = document.getElementById('prompt-manager');
 const box = e => { if (!e) return null; const r = e.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; };
 const inView = e => { if (!e || e.closest('[hidden]')) return false; const r = e.getBoundingClientRect(); return r.width > 0 && r.top >= -0.5 && r.bottom <= innerHeight + 0.5 && r.left >= -0.5 && r.right <= innerWidth + 0.5; };
 const rows = [...d.querySelectorAll('.pm__row-item')];
 return {offen: d.open, modal: box(d), zeilen: rows.map(r => [r.querySelector('.pm__row-title').textContent, r.classList.contains('is-on'), r.classList.contains('is-selected')]),
  zaehler: d.querySelector('[data-pm-count]').textContent, limit_hinweis: d.querySelector('[data-pm-limit-msg]').hidden ? null : d.querySelector('[data-pm-limit-msg]').textContent,
  leer_sichtbar: !d.querySelector('[data-pm-empty]').hidden, formular_sichtbar: !d.querySelector('[data-pm-form]').hidden,
  formular_titel: d.querySelector('[data-pm-form-title]').textContent, schalter_hinweis: d.querySelector('[data-pm-switch-note]').hidden ? null : d.querySelector('[data-pm-switch-note]').textContent,
  status: d.querySelector('[data-pm-status]').textContent, speichern_aktiv: !d.querySelector('[data-pm-save]').disabled,
  speichern_sichtbar: inView(d.querySelector('[data-pm-save]')), abbrechen_sichtbar: inView(d.querySelector('[data-pm-cancel]')),
  rueckfrage: d.querySelector('[data-pm-confirm]').hidden ? null : d.querySelector('[data-pm-confirm-text]').textContent,
  fehler: [...d.querySelectorAll('[data-pm-error]')].map(e => e.textContent).filter(Boolean),
  querscroll: document.documentElement.scrollWidth > innerWidth, js_fehler: window.__errs || []}; })()"""

START = r"""(() => [...document.querySelectorAll('.chat-empty-state__actions .chat-quick-action__title')].map(e => e.textContent))()"""


async def main():
    cookies = [{"name": c.name, "value": c.value, "domain": "127.0.0.1", "path": "/"} for c in cj]
    edge = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={CDP}",
                             f"--user-data-dir={T / ('edge_profile_prompts_' + PHASE)}", "--no-first-run",
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
                    return {"js_fehler": json.dumps(res["exceptionDetails"])[:400]}
                return res.get("result", {}).get("value")

            async def shot(name, state=True):
                await asyncio.sleep(0.7)
                data = base64.b64decode((await cdp("Page.captureScreenshot", format="png"))["data"])
                (OUT / f"{name}.png").write_bytes(data)
                if state:
                    R["aufnahmen"][name] = await js(STATE)
                print(name, flush=True)

            async def open_start():
                await cdp("Page.navigate", url=BASE + "/dashboard/chat?new=1"); await asyncio.sleep(2.2)

            async def open_modal():
                await js("document.querySelector('[data-prompt-manager-open]').click()"); await asyncio.sleep(1.5)

            async def click(sel):
                return await js(f"(() => {{ const b = document.querySelector({json.dumps(sel)}); if (!b) return 'fehlt'; b.click(); return 'ok'; }})()")

            def row(title, inner):
                return f"[...document.querySelectorAll('#prompt-manager .pm__row-item')].find(r => r.querySelector('.pm__row-title').textContent === {json.dumps(title)}).querySelector({json.dumps(inner)})"

            async def set_field(name, value):
                await js(f"(() => {{ const f = document.querySelector('#prompt-manager [data-pm-field={name}]'); f.value = {json.dumps(value)}; f.dispatchEvent(new Event('input', {{bubbles: true}})); }})()")

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
                    await open_start()
                    await open_modal()
                    await shot(f"{p}-01-uebersicht-leerer-zustand")
                    await click("#prompt-manager [data-pm-add]"); await asyncio.sleep(0.8)
                    await shot(f"{p}-02-neuer-prompt-formular")
                    await click("#prompt-manager [data-pm-cancel]"); await asyncio.sleep(0.5)
                    if await js("!document.querySelector('#prompt-manager [data-pm-confirm]').hidden"):
                        await click("#prompt-manager [data-pm-confirm-yes]"); await asyncio.sleep(0.5)

            # ---- Funktionsablauf bei 1280x720 hell ----
            await js("localStorage.setItem('lexono_theme', 'light')")
            await cdp("Emulation.setDeviceMetricsOverride", width=1280, height=720, deviceScaleFactor=1, mobile=False)
            await open_start()
            R["funktion"]["startseite_vorher"] = await js(START)
            await shot("1280x720-hell-10-startseite-vorher", state=False)
            await open_modal()
            await js(f"{row('Schriftsatz erstellen', '.pm__row-text')}.click()"); await asyncio.sleep(0.8)
            await shot("1280x720-hell-11-prompt-bearbeiten-auswahl")
            await js(f"{row('Recherche', '.pm__switch')}.click()"); await asyncio.sleep(0.6)
            await shot("1280x720-hell-12-fuenfte-aktivierung-verhindert")
            await js(f"{row('Zusammenfassen', '.pm__switch')}.click()"); await asyncio.sleep(0.4)
            await js(f"{row('Recherche', '.pm__switch')}.click()"); await asyncio.sleep(0.4)
            # Sortieren: Tastatur am Ziehgriff (Recherche zwei Plaetze nach oben)
            for _ in range(2):
                await js(f"(() => {{ const g = {row('Recherche', '.pm__grip')}; g.focus(); g.dispatchEvent(new KeyboardEvent('keydown', {{key: 'ArrowUp', bubbles: true}})); }})()")
                await asyncio.sleep(0.3)
            # Sortieren: Drag-and-drop (Akte oeffnen vor Dokument analysieren)
            R["funktion"]["drag_and_drop"] = await js("""(() => { const rows = () => [...document.querySelectorAll('#prompt-manager .pm__row-item')];
              const find = t => rows().find(r => r.querySelector('.pm__row-title').textContent === t);
              const src = find('Akte öffnen'), dst = find('Dokument analysieren'); const dt = new DataTransfer();
              src.querySelector('.pm__grip').dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
              src.dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: dt}));
              const r = find('Dokument analysieren').getBoundingClientRect();
              find('Dokument analysieren').dispatchEvent(new DragEvent('dragover', {bubbles: true, cancelable: true, dataTransfer: dt, clientY: r.top + 2}));
              find('Akte öffnen').dispatchEvent(new DragEvent('dragend', {bubbles: true, dataTransfer: dt}));
              return rows().map(x => x.querySelector('.pm__row-title').textContent); })()""")
            await asyncio.sleep(0.6)
            await shot("1280x720-hell-13-aktiviert-und-sortiert-ungespeichert")
            await click("#prompt-manager [data-pm-cancel]"); await asyncio.sleep(0.6)
            await shot("1280x720-hell-14-rueckfrage-ungespeichert")
            await click("#prompt-manager [data-pm-confirm-no]"); await asyncio.sleep(0.4)
            await click("#prompt-manager [data-pm-save]"); await asyncio.sleep(1.5)
            await shot("1280x720-hell-15-gespeichert")
            # Loeschen mit Bestaetigung (Kebab-Menue)
            await js(f"{row('Fristenkontrolle', '.pm__dots')}.click()"); await asyncio.sleep(0.4)
            await shot("1280x720-hell-16-menue")
            await js("[...document.querySelectorAll('#prompt-manager .pm__menu:not([hidden]) .pm__menu-item')].find(b => b.textContent === 'Löschen').click()")
            await asyncio.sleep(0.5)
            await shot("1280x720-hell-17-loeschen-bestaetigen")
            await click("#prompt-manager [data-pm-confirm-yes]"); await asyncio.sleep(0.4)
            # Neuer Prompt mit Validierung
            await click("#prompt-manager [data-pm-add]"); await asyncio.sleep(0.6)
            await set_field("name", "Mandantenschreiben")
            await set_field("content", "Bitte formuliere ein verständliches Schreiben an die Mandantschaft zu: ")
            await click("#prompt-manager [data-pm-save]"); await asyncio.sleep(0.8)
            await shot("1280x720-hell-18-validierung-pflichtfeld")
            await set_field("description", "Verständliche Information an Mandanten")
            await click("#prompt-manager [data-pm-save]"); await asyncio.sleep(1.5)
            await shot("1280x720-hell-19-neu-gespeichert")
            R["funktion"]["nach_speichern"] = (await js(STATE))["zeilen"]
            await click("#prompt-manager [data-pm-close]"); await asyncio.sleep(2.5)
            R["funktion"]["startseite_nachher"] = await js(START)
            await shot("1280x720-hell-20-startseite-nachher", state=False)
            # Persistenz: neu laden
            await open_start()
            R["funktion"]["startseite_nach_neuladen"] = await js(START)
            await open_modal()
            R["funktion"]["verwaltung_nach_neuladen"] = (await js(STATE))["zeilen"]
            await shot("1280x720-hell-21-nach-neuladen")
    finally:
        edge.terminate()
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(server.pid)], capture_output=True)


asyncio.run(main())
(OUT / f"messwerte_{PHASE}.json").write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
print("fertig", len(R["aufnahmen"]))
