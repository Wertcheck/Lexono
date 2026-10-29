"""QA Finanzbuchhaltung (Quellstand, run.py serve --no-window, isolierte Instanz per Migration). Ausschliesslich
SYNTHETISCHE Mandanten/Buchungen/Belege, angelegt ueber die echten Routen der laufenden App. Headless Edge per CDP, DPR 1.
Usage: fibu_shots.py <out_dir>"""
import asyncio, base64, http.cookiejar, io, json, os, re, subprocess, sys, time, urllib.error, urllib.parse, urllib.request, uuid
from pathlib import Path

import websockets

T = Path(__file__).resolve().parent
REPO = Path(r"<REPOSITORY>")
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
INST, PORT, CDP = T / "fibu_qa", 8081, 9368
BASE = f"http://127.0.0.1:{PORT}"
R = {"aufnahmen": {}, "funktion": {}}

subprocess.run([PY, str(T / "pseudo_seed.py"), str(INST), str(PORT)], check=True, capture_output=True)
DB = INST / "data" / "kanzlei_ai.db"
sys.path.insert(0, str(REPO))
os.environ["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth.security import hash_password  # noqa: E402
from app.models import Client, Role, User  # noqa: E402

with Session(create_engine(f"sqlite:///{DB.as_posix()}")) as db:
    staff_role = db.query(Role).filter(Role.name.ilike("mitarbeiter")).one()
    db.add(User(email="qa-mitarbeiter@lexono.test", display_name="QA Mitarbeiter", role_id=staff_role.id,
                password_hash=hash_password("QA-pseudo-0000"), must_change_password=False))
    a = Client(name="QA Muster GmbH", client_type="Unternehmen", city="Musterstadt")
    b = Client(name="QA Beispiel Handwerk KG", client_type="Unternehmen", city="Beispielort")
    db.add_all([a, b]); db.commit()
    CLIENT_A, CLIENT_B = a.id, b.id

env = dict(os.environ, LEXONO_DATA_DIR=str(INST), PYTHONDONTWRITEBYTECODE="1",
           HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9", NO_PROXY="127.0.0.1,localhost")
for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "DATABASE_URL"):
    env.pop(k, None)
server = subprocess.Popen([PY, str(REPO / "run.py"), "serve", "--no-window"], cwd=str(INST), env=env,
                          stdout=open(INST / "serve.out", "w"), stderr=subprocess.STDOUT)
import atexit  # noqa: E402
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


op, cj = session("qa-pseudo@lexono.test")


def get(path, o=None):
    return (o or op).open(BASE + path, timeout=60).read().decode("utf-8")


def status_of(fn):
    try:
        fn(); return 200
    except urllib.error.HTTPError as e:
        return e.code


def csrf(o=None):
    return re.search(r'name="csrf_token" value="([^"]+)"', get("/dashboard/chat?new=1", o)).group(1)


def post(path, fields, files=None, o=None, follow=True):
    boundary = "----QA" + uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for name, data, ctype in (files or []):
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{name}\"\r\n"
                 f"Content-Type: {ctype}\r\n\r\n").encode() + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(BASE + path, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return (o or op).open(req, timeout=120).read().decode("utf-8")


TOKEN = csrf()


def accounts(cid):
    page = get(f"/dashboard/fibu?client={cid}&tab=buchen")
    sel = page.split('name="account_id"', 1)[1].split("</select>", 1)[0] + page.split('name="contra_account_id"', 1)[1].split("</select>", 1)[0]
    return {m.group(2): m.group(1) for m in re.finditer(r'<option value="([0-9a-f-]{36})"[^>]*>(\d+) ', sel)}


def book(cid, d, nr, text, direction, amount, rate, acc, contra, action="save_post", receipt=""):
    acc_ids = accounts(cid)
    return post("/dashboard/fibu/bookings", {"csrf_token": TOKEN, "client_id": cid, "year": d[:4], "action": action,
                                             "booking_date": d, "receipt_number": nr, "description": text,
                                             "direction": direction, "amount": amount, "tax_rate": str(rate),
                                             "account_id": acc_ids[acc], "contra_account_id": acc_ids[contra],
                                             "receipt_id": receipt})


def pdf_bytes(lines):
    import pymupdf
    doc = pymupdf.open(); page = doc.new_page(); y = 80
    for line in lines:
        page.insert_text((60, y), line, fontsize=12); y += 22
    data = doc.tobytes(); doc.close(); return data


def png_bytes():
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (600, 400), "white"); dr = ImageDraw.Draw(img)
    dr.text((40, 40), "QA-SYNTH Quittung Reisekosten Berlin 320,45 EUR", fill="black")
    b = io.BytesIO(); img.save(b, format="PNG"); return b.getvalue()


# ---- Stammdaten, Konten, Belege, Buchungen (synthetisch, ueber die echten Routen)
for cid in (CLIENT_A, CLIENT_B):
    post("/dashboard/fibu/accounts/seed", {"csrf_token": TOKEN, "client_id": cid, "year": "2026"})
post("/dashboard/fibu/profile", {"csrf_token": TOKEN, "client_id": CLIENT_A, "year": "2026", "legal_form": "GmbH",
                                 "tax_number": "123/456/78901", "vat_id": "DE123456789"})
post("/dashboard/fibu/profile", {"csrf_token": TOKEN, "client_id": CLIENT_B, "year": "2026", "legal_form": "KG",
                                 "tax_number": "987/654/32100", "vat_id": ""})
post("/dashboard/fibu/receipts", {"csrf_token": TOKEN, "client_id": CLIENT_A, "year": "2026", "return_tab": "belege"},
     files=[("QA-SYNTH-Rechnung-RE-2026-105.pdf", pdf_bytes(["QA-SYNTH Rechnung RE-2026-105", "Beratung Projekt XY", "1.250,00 EUR brutto"]), "application/pdf"),
            ("QA-SYNTH-Quittung-Reisekosten.png", png_bytes(), "image/png")])
receipt_ids = re.findall(r'/dashboard/fibu/receipts/([0-9a-f-]{36})/file\?client=' + CLIENT_A + '"', get(f"/dashboard/fibu?client={CLIENT_A}&tab=belege"))
R["funktion"]["belege_angelegt"] = len(set(receipt_ids))

rows_a = [
    ("2026-01-20", "RE-2026-090", "Beratung Jahresplanung", "income", "18.500,00", 19, "8400", "1000"),
    ("2026-02-14", "RE-2026-091", "Projektpauschale Q1", "income", "24.000,00", 19, "8400", "1000"),
    ("2026-03-30", "BE-2026-410", "Miete Büro Q1", "expense", "21.000,00", 0, "4950", "1000"),
    ("2026-04-18", "RE-2026-095", "Gutachten Vertragsprüfung", "income", "31.230,50", 19, "8400", "1000"),
    ("2026-05-22", "BE-2026-420", "IT-Dienstleister Jahreslizenz", "expense", "34.200,00", 19, "4980", "1600"),
    ("2026-06-30", "RE-2026-098", "Beratung Restrukturierung", "income", "26.000,00", 19, "8400", "1200"),
    ("2026-08-12", "BE-2026-440", "Fachliteratur", "expense", "1.850,00", 7, "4930", "1000"),
    ("2026-09-15", "RE-2026-101", "Workshop Compliance", "income", "21.070,00", 19, "8400", "1000"),
    ("2026-09-28", "BE-2026-450", "Reisekosten Hamburg", "expense", "29.345,00", 19, "4960", "1000"),
    ("2026-10-12", "BE-2026-454", "Versicherung", "expense", "215,00", 0, "4950", "1000"),
    ("2026-10-15", "RE-2026-103", "Beratung GmbH", "income", "2.400,00", 19, "8400", "1200"),
    ("2026-10-18", "BE-2026-455", "Reisekosten Berlin", "expense", "320,45", 19, "4960", "1000"),
    ("2026-10-20", "BE-2026-456", "Telefon/Internet", "expense", "86,35", 19, "4940", "1000"),
    ("2026-10-24", "BE-2026-457", "Software Abo", "expense", "119,00", 19, "4980", "1000"),
    ("2026-10-26", "BE-2026-458", "Büromaterial", "expense", "78,40", 19, "4930", "1000"),
    ("2026-10-27", "RE-2026-105", "Beratung Projekt XY", "income", "1.250,00", 19, "8400", "1000"),
]
for d, nr, text, direction, amount, rate, acc, contra in rows_a:
    rid = ""
    if nr == "RE-2026-105":
        rid = receipt_ids[-1] if receipt_ids else ""
    book(CLIENT_A, d, nr, text, direction, amount, rate, acc, contra, receipt=rid)
book(CLIENT_A, "2026-10-22", "RE-2026-104", "Rechtsberatung", "income", "980,00", 19, "8400", "1200", action="save")
book(CLIENT_A, "2026-10-21", "BE-2026-999", "Doppelt erfasste Rechnung", "expense", "500,00", 19, "4930", "1000")
for d, nr, text, direction, amount, rate, acc, contra in [
        ("2025-03-10", "RE-2025-010", "Beratung", "income", "61.000,00", 19, "8400", "1000"),
        ("2025-06-10", "RE-2025-020", "Gutachten", "income", "55.120,00", 19, "8400", "1000"),
        ("2025-07-10", "BE-2025-100", "Miete", "expense", "45.000,00", 0, "4950", "1000"),
        ("2025-09-10", "BE-2025-110", "IT", "expense", "40.000,00", 19, "4980", "1000")]:
    book(CLIENT_A, d, nr, text, direction, amount, rate, acc, contra)
for d, nr, text, direction, amount, rate, acc, contra in [
        ("2026-09-03", "AR-0001", "Montage Heizung", "income", "4.760,00", 19, "8400", "1000"),
        ("2026-10-05", "ER-0001", "Material Großhandel", "expense", "1.190,00", 19, "4930", "1600")]:
    book(CLIENT_B, d, nr, text, direction, amount, rate, acc, contra)

# Storno einer Buchung ueber die Route
journal = get(f"/dashboard/fibu?client={CLIENT_A}&year=2026&tab=buchen&q=BE-2026-999")
dup_id = re.search(r'cancel=([0-9a-f-]{36})', journal).group(1)
post(f"/dashboard/fibu/bookings/{dup_id}/cancel", {"csrf_token": TOKEN, "client_id": CLIENT_A, "year": "2026",
                                                   "reason": "QA: doppelt erfasst"})
for title, due in (("3 offene Eingangsrechnungen prüfen", "2026-10-28"), ("Kontoabstimmung Bank", "2026-10-31"),
                   ("Buchungen Oktober abschließen", "2026-10-31")):
    post("/dashboard/fibu/tasks", {"csrf_token": TOKEN, "client_id": CLIENT_A, "year": "2026", "title": title, "due_date": due})

# ---- Funktions-, Isolations- und Rechtepruefungen (HTTP)
f = R["funktion"]
page_a = get(f"/dashboard/fibu?client={CLIENT_A}&year=2026")
page_b = get(f"/dashboard/fibu?client={CLIENT_B}&year=2026")
kpi = lambda html: re.findall(r'class="fibu-kpi__value">([^<]+)<', html)
f["kennzahlen_mandant_a"] = kpi(page_a)
f["kennzahlen_mandant_b"] = kpi(page_b)
f["mandant_b_zeigt_keine_buchung_von_a"] = "RE-2026-105" not in page_b and "AR-0001" in page_b
a_booking = re.search(r'edit=([0-9a-f-]{36})', get(f"/dashboard/fibu?client={CLIENT_A}&year=2026&tab=buchen&q=RE-2026-104")).group(1)
f["fremde_buchung_ueber_mandant_b_lesen_http"] = status_of(lambda: get(f"/dashboard/fibu?client={CLIENT_B}&year=2026&tab=buchen&edit={a_booking}"))
f["fremde_buchung_ueber_mandant_b_stornieren_http"] = status_of(lambda: post(
    f"/dashboard/fibu/bookings/{a_booking}/cancel", {"csrf_token": TOKEN, "client_id": CLIENT_B, "year": "2026", "reason": "x"}))
f["fremder_beleg_ueber_mandant_b_http"] = status_of(lambda: get(f"/dashboard/fibu/receipts/{receipt_ids[0]}/file?client={CLIENT_B}"))
acc_b = accounts(CLIENT_B)


def _try_post_fail(fields, files=None, path="/dashboard/fibu/bookings", o=None):
    try:
        return 200, post(path, fields, files, o=o)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def alert(html):
    m = re.search(r'role="alert">.*?<span>([^<]+)</span>', html, re.S)
    return m.group(1) if m else None


code, html = _try_post_fail({"csrf_token": TOKEN, "client_id": CLIENT_A, "year": "2026", "action": "save",
                             "booking_date": "2026-10-28", "receipt_number": "X-1", "description": "Fremdkonto",
                             "direction": "expense", "amount": "10,00", "tax_rate": "19",
                             "account_id": acc_b["4930"], "contra_account_id": accounts(CLIENT_A)["1000"]})
f["konto_von_b_in_buchung_von_a"] = [code, alert(html)]
code, html = _try_post_fail({"csrf_token": TOKEN, "client_id": CLIENT_A, "year": "2026", "action": "save",
                             "booking_date": "2026-10-28", "receipt_number": "", "description": "ohne Beleg",
                             "direction": "expense", "amount": "abc", "tax_rate": "19",
                             "account_id": accounts(CLIENT_A)["4930"], "contra_account_id": accounts(CLIENT_A)["1000"]})
f["pflichtfeld_fehlt"] = [code, alert(html)]
code, html = _try_post_fail({"csrf_token": TOKEN, "client_id": CLIENT_A, "year": "2026", "return_tab": "belege"},
                            files=[("QA-SYNTH-Makro.exe", b"MZ\x90\x00", "application/octet-stream")], path="/dashboard/fibu/receipts")
f["ungueltiger_beleg"] = [code, alert(html)]
code, html = _try_post_fail({"csrf_token": TOKEN, "client_id": CLIENT_A, "year": "2026", "return_tab": "belege"},
                            files=[("QA-SYNTH-falsch.pdf", b"kein pdf", "application/pdf")], path="/dashboard/fibu/receipts")
f["pdf_endung_ohne_pdf_inhalt"] = [code, alert(html)]
staff, staff_cj = session("qa-mitarbeiter@lexono.test")
staff_page = get(f"/dashboard/fibu?client={CLIENT_A}&year=2026", staff)
f["mitarbeiter_sieht_zahlen"] = kpi(staff_page) == kpi(page_a)
f["mitarbeiter_ohne_hochladen_knopf"] = "Belege hochladen</span>" not in staff_page
f["mitarbeiter_buchen_http"] = _try_post_fail({"csrf_token": csrf(staff), "client_id": CLIENT_A, "year": "2026", "action": "save",
                                               "booking_date": "2026-10-28", "receipt_number": "M-1", "description": "Mitarbeiter",
                                               "direction": "expense", "amount": "10,00", "tax_rate": "19",
                                               "account_id": accounts(CLIENT_A)["4930"], "contra_account_id": accounts(CLIENT_A)["1000"]}, o=staff)[0]
f["mitarbeiter_datev_http"] = status_of(lambda: get(f"/dashboard/fibu/export/datev-entwurf.csv?client={CLIENT_A}&year=2026", staff))
f["ohne_csrf_http"] = _try_post_fail({"client_id": CLIENT_A, "year": "2026", "title": "x"}, path="/dashboard/fibu/tasks")[0]
datev = get(f"/dashboard/fibu/export/datev-entwurf.csv?client={CLIENT_A}&year=2026")
f["datev_zeilen"] = len(datev.strip().splitlines()) - 1
f["datev_kopf"] = datev.splitlines()[0].lstrip("\ufeff")
f["datev_beispiel"] = [l for l in datev.splitlines() if "RE-2026-105" in l]
f["datev_ohne_offene_und_stornierte"] = "RE-2026-104" not in datev and "BE-2026-999" not in datev
susa = get(f"/dashboard/fibu?client={CLIENT_A}&year=2026&tab=auswertungen&report=susa")
f["susa_summenzeile"] = re.findall(r'<tr class="is-total"><td colspan="2">Summe</td>(.*?)</tr>', susa, re.S)
vat = get(f"/dashboard/fibu?client={CLIENT_A}&year=2026&tab=ust")
f["ust_q4_zeile"] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.search(r"<tr><td>Q4/2026</td>(.*?)</tr>", vat, re.S).group(1))).strip()
f["unbekannter_mandant_http"] = status_of(lambda: get("/dashboard/fibu?client=00000000-0000-0000-0000-000000000000"))

STATE = r"""(() => { const box = e => { if (!e) return null; const r = e.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; };
 const over = [...document.querySelectorAll('.fibu-kpi__value, .fibu-card h2, .fibu-tabs__tab, .fibu-btn, .fibu-quick__item, .fibu-tile')].filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.className + ':' + e.textContent.trim().slice(0, 30));
 return {fenster: [innerWidth, innerHeight], querscroll: document.documentElement.scrollWidth > innerWidth,
  seitenhoehe: document.documentElement.scrollHeight, kpis: [...document.querySelectorAll('.fibu-kpi__value')].map(e => e.textContent.trim()),
  zeilen: document.querySelectorAll('.fibu-table tbody tr').length, ueberlauf: over, aktiver_reiter: (document.querySelector('.fibu-tabs__tab.is-active') || {}).textContent,
  nav_aktiv: (document.querySelector('.sidebar__group-summary--active .sidebar__group-label') || {}).textContent,
  neu_badge: !!document.querySelector('.sidebar__new-badge'), kopf: box(document.querySelector('.fibu-head')), js_fehler: window.__errs || []}; })()"""


async def main():
    cookies = [{"name": c.name, "value": c.value, "domain": "127.0.0.1", "path": "/"} for c in cj]
    edge = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={CDP}", f"--user-data-dir={T / 'edge_profile_fibu'}",
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
                res = await cdp("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
                return res.get("result", {}).get("value")

            async def shot(name, full=False):
                await asyncio.sleep(0.6)
                R["aufnahmen"][name] = await js(STATE)
                w, h = await js("innerWidth"), await js("innerHeight")
                if full:
                    # Inhalt scrollt in einem inneren Container -> Fenster fuer die Aufnahme auf volle Hoehe strecken
                    extra = await js("Math.max(0, ...[...document.querySelectorAll('*')].filter(e => ['auto', 'scroll'].includes(getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight + 1 && e.clientHeight > innerHeight * 0.5).map(e => e.scrollHeight - e.clientHeight))")
                    await cdp("Emulation.setDeviceMetricsOverride", width=w, height=h + int(extra or 0), deviceScaleFactor=1, mobile=False)
                    await asyncio.sleep(0.8)
                data = base64.b64decode((await cdp("Page.captureScreenshot", format="png"))["data"])
                if full:
                    await cdp("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
                (OUT / f"{name}.png").write_bytes(data)
                print(name, flush=True)

            async def go(path):
                await cdp("Page.navigate", url=BASE + path); await asyncio.sleep(1.8)

            await cdp("Page.enable"); await cdp("Network.enable"); await cdp("Runtime.enable")
            await cdp("Page.addScriptToEvaluateOnNewDocument", source="window.__errs = []; addEventListener('error', e => window.__errs.push(String(e.message)));")
            for c in cookies:
                await cdp("Network.setCookie", **c)
            await go("/dashboard")
            q = f"client={CLIENT_A}&year=2026"
            for dark in (False, True):
                tn = "dunkel" if dark else "hell"
                await js(f"localStorage.setItem('lexono_theme', '{'dark' if dark else 'light'}')")
                for w, h in ((1920, 1080), (1280, 720), (960, 600)):
                    p = f"{w}x{h}-{tn}"
                    await cdp("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
                    await go(f"/dashboard/fibu?{q}")
                    await shot(f"{p}-01-uebersicht")
                    if w == 1920 or not dark:
                        await shot(f"{p}-01b-uebersicht-ganze-seite", full=True)
                    await go(f"/dashboard/fibu?{q}&tab=buchen")
                    await shot(f"{p}-02-buchen")
            await js("localStorage.setItem('lexono_theme', 'light')")
            await cdp("Emulation.setDeviceMetricsOverride", width=1280, height=720, deviceScaleFactor=1, mobile=False)
            for name, path in (("03-belege", "tab=belege"), ("04-konten", "tab=konten"), ("05-ust", "tab=ust"),
                               ("06-bwa", "tab=auswertungen&report=bwa"), ("07-susa", "tab=auswertungen&report=susa"),
                               ("08-kontenblatt", "tab=auswertungen&report=konto"), ("09-op-liste", "tab=auswertungen&report=op"),
                               ("10-datev-entwurf", "tab=auswertungen&report=datev"), ("11-einstellungen", "tab=einstellungen")):
                await go(f"/dashboard/fibu?{q}&{path}")
                await shot(f"1280x720-hell-{name}", full=True)
            await go(f"/dashboard/fibu?{q}&tab=buchen&q=RE-2026-104")
            edit = await js("(document.querySelector('a[href*=\"edit=\"]') || {}).href")
            await go(edit.replace(BASE, ""))
            await shot("1280x720-hell-12-buchung-bearbeiten-verlauf", full=True)
            await go(f"/dashboard/fibu?{q}&tab=buchen&q=BE-2026-999")
            cancel = await js("(document.querySelector('a[href*=\"cancel=\"]') || {}).href")
            await go(cancel.replace(BASE, ""))
            await shot("1280x720-hell-13-stornierte-buchung-verlauf", full=True)
            await go(f"/dashboard/fibu?{q}")
            await js("document.querySelector('#fibu-client').value = " + json.dumps(CLIENT_B) + "; document.querySelector('#fibu-client').dispatchEvent(new Event('change'));")
            await asyncio.sleep(2.2)
            R["funktion"]["mandantenwechsel_url"] = await js("location.search")
            await shot("1280x720-hell-14-mandantenwechsel-handwerk-kg")
            await js("document.querySelector('#fibu-year').value = '2025'; document.querySelector('#fibu-year').dispatchEvent(new Event('change'));")
            await asyncio.sleep(2.2)
            await shot("1280x720-hell-15-jahreswechsel-2025-mandant-b-leer")
            await go(f"/dashboard/fibu?{q}&tab=buchen&q=RE-2026-104")
            await js("document.querySelector('.fibu-menu summary').click()")
            await asyncio.sleep(0.4)
            await shot("1280x720-hell-16-zeilenmenue")
            await go(f"/dashboard/fibu?{q}")
            await js("document.querySelector('.fibu-split__more summary').click()")
            await asyncio.sleep(0.4)
            await shot("1280x720-hell-17-primaerknopf-menue")
            # Mitarbeiter-Sicht
            await cdp("Network.clearBrowserCookies")
            for c in staff_cj:
                await cdp("Network.setCookie", name=c.name, value=c.value, domain="127.0.0.1", path="/")
            await go(f"/dashboard/fibu?{q}&tab=buchen")
            await shot("1280x720-hell-18-mitarbeiter-nur-lesen")
    finally:
        edge.terminate()
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(server.pid)], capture_output=True)


asyncio.run(main())
(OUT / "messwerte.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
print(json.dumps(R["funktion"], ensure_ascii=False, indent=1, default=str))
