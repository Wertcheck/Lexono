"""Dashboard-Router "Finanzbuchhaltung" (MVP, 28.10.2026, Referenzbild) - reine UI-Fassade vor app/fibu/service.py.

Rechte: Lesen fuer alle angemeldeten Rollen (`require_login`), jede Aenderung ueber `require_role(permission=
PERM_FIBU_MANAGE)` (Login -> CSRF -> Berechtigung, serverseitig). Der Mandant kommt bei JEDER Anfrage als Parameter
und wird im Service gegen jede ID geprueft; fremde IDs ergeben 404.

Fehler bei Formularen rendern die Seite mit Meldung und den eingegebenen Werten neu (HTTP 400), damit keine Eingaben
oder Dateinamen in URLs landen. Erfolg -> 303-Redirect mit kurzem Meldungscode.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth.permissions import PERM_FIBU_MANAGE, has_permission, require_login, require_role
from app.config.settings import Settings, get_settings
from app.db.session import get_db
from app.fibu import service as fs
from app.models import User
from app.modules.catalog import MODULE_FINANZEN
from app.modules.licensing import require_module
from app.web.template_paths import TEMPLATES_DIR

# Modul "Lexono Finanzen" (28.10.2026): JEDE Route dieses Routers ist ohne gueltige Lizenz gesperrt (403), unabhaengig
# von Rolle und Navigation - siehe app/modules/licensing.py.
router = APIRouter(prefix="/dashboard/fibu", tags=["dashboard-fibu"],
                   dependencies=[Depends(require_module(MODULE_FINANZEN))])
templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.filters["eur"] = fs.format_cents

TABS = (("uebersicht", "Übersicht"), ("buchen", "Buchen"), ("belege", "Belege"), ("konten", "Konten"),
        ("ust", "USt-Voranmeldung"), ("auswertungen", "Auswertungen"), ("jahresabschluss", "Jahresabschluss"),
        ("einstellungen", "Einstellungen"))
_TAB_KEYS = {k for k, _ in TABS} - {"jahresabschluss"}
_PAGE_SIZES = (10, 25, 50)
_REPORTS = ("bwa", "susa", "konto", "op", "datev")
_MONTHS = ("Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez")
_OK_MESSAGES = {
    "booking_created": "Buchung erfasst.", "booking_posted": "Buchung gebucht.", "booking_updated": "Buchung geändert.",
    "booking_cancelled": "Buchung storniert.", "receipts_uploaded": "Beleg(e) gespeichert.",
    "account_created": "Konto angelegt.", "accounts_seeded": "Startkonten angelegt.",
    "account_toggled": "Konto aktualisiert.", "task_created": "Aufgabe angelegt.", "task_toggled": "Aufgabe aktualisiert.",
    "profile_saved": "Stammdaten gespeichert.",
}


def _storage_dir(settings: Settings) -> Path:
    return Path(settings.fibu_receipt_storage_dir)


def _year(raw: str | int | None) -> int:
    try:
        year = int(raw) if raw not in (None, "") else date.today().year
    except (TypeError, ValueError):
        year = date.today().year
    return year if 2000 <= year <= 2100 else date.today().year


def _url(client_id: str, year: int, tab: str = "uebersicht", **extra: str) -> str:
    from urllib.parse import urlencode
    params = {"client": client_id, "year": year, "tab": tab, **{k: v for k, v in extra.items() if v}}
    return "/dashboard/fibu?" + urlencode(params)


def _client_or_404(db: Session, client_id: str | None):
    try:
        return fs.get_client(db, client_id)
    except fs.FibuNotFound:
        raise HTTPException(status_code=404, detail="Mandant nicht gefunden") from None


def _render(request: Request, db: Session, user: User, *, client_id: str | None, year: int, tab: str = "uebersicht",
            error: str | None = None, error_field: str | None = None, form: dict | None = None, status: int = 200,
            params: dict | None = None) -> HTMLResponse:
    params = params or {}
    clients = fs.list_clients(db)
    client = None
    if client_id:
        client = _client_or_404(db, client_id)
    elif clients:
        client = clients[0]
    tab = tab if tab in _TAB_KEYS else "uebersicht"
    ctx: dict = {
        "request": request, "current_user": user, "active_nav": "Finanzbuchhaltung", "hide_back_link": True,
        "csrf_token": getattr(request.state, "csrf_token", ""), "clients": clients, "client": client, "year": year,
        "tab": tab, "tabs": TABS, "error": error, "error_field": error_field, "form": form or {},
        "ok": _OK_MESSAGES.get(params.get("ok", "")), "can_manage": has_permission(user, PERM_FIBU_MANAGE),
        "status_labels": fs.STATUS_LABELS, "kind_labels": fs.KIND_LABELS, "direction_labels": fs.DIRECTION_LABELS,
        "tax_rates": (19, 7, 0), "page_sizes": _PAGE_SIZES, "months": _MONTHS, "today": date.today(),
    }
    if client is None:
        return templates.TemplateResponse(request, "fibu.html", ctx, status_code=status)
    cid = client.id
    this_year = date.today().year  # Vor- und Folgejahr immer waehlbar (Erfassung eines neuen/alten Jahres)
    years = sorted({year, this_year - 1, this_year, this_year + 1, *(y for (y,) in db.query(fs.FibuBooking.fiscal_year)
                                                  .filter(fs.FibuBooking.client_id == cid).distinct())}, reverse=True)
    accounts = fs.list_accounts(db, cid)
    ctx.update(years=years, accounts=accounts, active_accounts=[a for a in accounts if a.is_active],
               profile=fs.get_profile(db, cid), url=lambda t="uebersicht", **kw: _url(cid, year, t, **kw))
    search, account_filter = params.get("q", "").strip(), params.get("account", "")
    if account_filter and account_filter not in {a.id for a in accounts}:
        account_filter = ""
    ctx.update(search=search, account_filter=account_filter)

    if tab in ("uebersicht", "buchen"):
        default_size = 10 if tab == "uebersicht" else 25
        rows = fs.list_bookings(db, cid, year, search=search, account_id=account_filter)
        try:
            page_size = int(params.get("page_size") or default_size)
        except ValueError:
            page_size = default_size
        page_size = page_size if page_size in _PAGE_SIZES else default_size
        pages = max(1, -(-len(rows) // page_size))
        try:
            page = min(max(1, int(params.get("page") or 1)), pages)
        except ValueError:
            page = 1
        ctx.update(bookings=rows[(page - 1) * page_size: page * page_size], booking_total=len(rows), page=page,
                   pages=pages, page_size=page_size)
    if tab == "uebersicht":
        receipts = fs.list_receipts(db, cid)
        linked = fs.receipt_bookings(db, cid)
        ctx.update(kpis=fs.compute_kpis(db, cid, year), tasks=fs.list_tasks(db, cid),
                   unassigned_receipts=sum(1 for r in receipts if r.id not in linked),
                   report_month=_MONTHS[(date.today().month if year == date.today().year else 12) - 1])
    elif tab == "buchen":
        edit = cancel = None
        try:
            if params.get("edit"):
                edit = fs.get_booking(db, cid, params["edit"])
            if params.get("cancel"):
                cancel = fs.get_booking(db, cid, params["cancel"])
        except fs.FibuNotFound:
            raise HTTPException(status_code=404, detail="Buchung nicht gefunden") from None
        selected = edit or cancel
        ctx.update(edit=edit, cancel=cancel, receipts=fs.list_receipts(db, cid),
                   history=fs.booking_history(db, selected.id) if selected else [],
                   preset_receipt=params.get("receipt", ""))
    elif tab == "belege":
        ctx.update(receipts=fs.list_receipts(db, cid), receipt_links=fs.receipt_bookings(db, cid))
    elif tab == "konten":
        ctx.update(susa={r.account_id: r for r in fs.trial_balance(db, cid, year) if r.account_id})
    elif tab == "ust":
        ctx.update(vat=fs.vat_overview(db, cid, year))
    elif tab == "auswertungen":
        report = params.get("report") if params.get("report") in _REPORTS else "bwa"
        ctx.update(report=report)
        if report == "bwa":
            ctx.update(bwa=fs.bwa_overview(db, cid, year))
        elif report == "susa":
            rows = fs.trial_balance(db, cid, year)
            ctx.update(susa_rows=rows, susa_debit=sum(r.debit for r in rows), susa_credit=sum(r.credit for r in rows))
        elif report == "konto":
            chosen = params.get("konto") or (accounts[0].id if accounts else "")
            if chosen:
                try:
                    ledger_account, ledger = fs.account_ledger(db, cid, year, chosen)
                except fs.FibuNotFound:
                    raise HTTPException(status_code=404, detail="Konto nicht gefunden") from None
                ctx.update(ledger_account=ledger_account, ledger=ledger)
        elif report == "op":
            ctx.update(op_rows=fs.open_items(db, cid, year))
        elif report == "datev":
            ctx.update(datev_count=sum(1 for b in fs.list_bookings(db, cid, year, include_cancelled=False)
                                       if b.status == "posted"))
    return templates.TemplateResponse(request, "fibu.html", ctx, status_code=status)


@router.get("", response_class=HTMLResponse)
def fibu_page(request: Request, client: str | None = None, year: str | None = None, tab: str = "uebersicht",
              db: Session = Depends(get_db), current_user: User = Depends(require_login)) -> HTMLResponse:
    return _render(request, db, current_user, client_id=client, year=_year(year), tab=tab,
                   params=dict(request.query_params))


def _fail(request: Request, db: Session, user: User, client_id: str, year: int, tab: str, exc: fs.FibuError,
          form: dict | None = None, params: dict | None = None) -> HTMLResponse:
    db.rollback()
    return _render(request, db, user, client_id=client_id, year=year, tab=tab, error=str(exc),
                   error_field=getattr(exc, "field", None), form=form, status=400, params=params)


def _done(client_id: str, year: int, tab: str, ok: str, **extra: str) -> RedirectResponse:
    return RedirectResponse(_url(client_id, year, tab, ok=ok, **extra), status_code=303)


_BOOKING_FIELDS = ("booking_date", "receipt_number", "description", "direction", "amount", "tax_rate", "account_id",
                   "contra_account_id", "receipt_id")


async def _booking_form(request: Request) -> dict:
    data = await request.form()
    return {k: str(data.get(k) or "") for k in _BOOKING_FIELDS}


@router.post("/bookings")
async def create_booking(request: Request, client_id: str = Form(...), year: str = Form(""),
                         action: str = Form("save"), db: Session = Depends(get_db),
                         current_user: User = Depends(require_role(permission=PERM_FIBU_MANAGE))):
    _client_or_404(db, client_id)
    form = await _booking_form(request)
    try:
        booking = fs.create_booking(db, client_id, form, actor=current_user.email, post=action == "save_post")
    except fs.FibuError as exc:
        return _fail(request, db, current_user, client_id, _year(year), "buchen", exc, form)
    return _done(client_id, booking.fiscal_year, "buchen", "booking_posted" if action == "save_post" else "booking_created")


@router.post("/bookings/{booking_id}/update")
async def update_booking(request: Request, booking_id: str, client_id: str = Form(...), year: str = Form(""),
                         db: Session = Depends(get_db),
                         current_user: User = Depends(require_role(permission=PERM_FIBU_MANAGE))):
    _client_or_404(db, client_id)
    form = await _booking_form(request)
    try:
        booking = fs.update_booking(db, client_id, booking_id, form, actor=current_user.email)
    except fs.FibuNotFound:
        raise HTTPException(status_code=404, detail="Buchung nicht gefunden") from None
    except fs.FibuError as exc:
        return _fail(request, db, current_user, client_id, _year(year), "buchen", exc, form, {"edit": booking_id})
    return _done(client_id, booking.fiscal_year, "buchen", "booking_updated")


@router.post("/bookings/{booking_id}/post")
def post_booking(request: Request, booking_id: str, client_id: str = Form(...), year: str = Form(""),
                 return_tab: str = Form("buchen"), db: Session = Depends(get_db),
                 current_user: User = Depends(require_role(permission=PERM_FIBU_MANAGE))):
    _client_or_404(db, client_id)
    try:
        fs.post_booking(db, client_id, booking_id, actor=current_user.email)
    except fs.FibuNotFound:
        raise HTTPException(status_code=404, detail="Buchung nicht gefunden") from None
    except fs.FibuError as exc:
        return _fail(request, db, current_user, client_id, _year(year), return_tab, exc)
    return _done(client_id, _year(year), return_tab if return_tab in _TAB_KEYS else "buchen", "booking_posted")


@router.post("/bookings/{booking_id}/cancel")
def cancel_booking(request: Request, booking_id: str, client_id: str = Form(...), year: str = Form(""),
                   reason: str = Form(""), db: Session = Depends(get_db),
                   current_user: User = Depends(require_role(permission=PERM_FIBU_MANAGE))):
    _client_or_404(db, client_id)
    try:
        fs.cancel_booking(db, client_id, booking_id, reason=reason, actor=current_user.email)
    except fs.FibuNotFound:
        raise HTTPException(status_code=404, detail="Buchung nicht gefunden") from None
    except fs.FibuError as exc:
        return _fail(request, db, current_user, client_id, _year(year), "buchen", exc, {"reason": reason},
                     {"cancel": booking_id})
    return _done(client_id, _year(year), "buchen", "booking_cancelled")


@router.post("/receipts")
async def upload_receipts(request: Request, client_id: str = Form(...), year: str = Form(""),
                          return_tab: str = Form("belege"), files: list[UploadFile] = File(default=[]),
                          db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
                          current_user: User = Depends(require_role(permission=PERM_FIBU_MANAGE))):
    _client_or_404(db, client_id)
    tab = return_tab if return_tab in _TAB_KEYS else "belege"
    uploads = [f for f in files if f.filename]
    if not uploads:
        return _fail(request, db, current_user, client_id, _year(year), tab,
                     fs.FibuError("Bitte mindestens eine Datei auswählen."))
    errors, stored = [], 0
    for upload in uploads:
        content = await upload.read(fs.MAX_RECEIPT_BYTES + 1)
        try:
            fs.store_receipt(db, client_id, filename=upload.filename, content=content,
                             storage_dir=_storage_dir(settings), actor=current_user.email)
            stored += 1
        except fs.FibuError as exc:
            db.rollback()
            errors.append(str(exc))
    if errors:
        prefix = f"{stored} Datei(en) gespeichert. " if stored else ""
        return _fail(request, db, current_user, client_id, _year(year), tab,
                     fs.FibuError(prefix + "Nicht gespeichert: " + " ".join(errors)))
    return _done(client_id, _year(year), tab, "receipts_uploaded")


@router.get("/receipts/{receipt_id}/file")
def receipt_file(receipt_id: str, client: str, download: int = 0, db: Session = Depends(get_db),
                 settings: Settings = Depends(get_settings), current_user: User = Depends(require_login)):
    try:
        receipt = fs.get_receipt(db, client, receipt_id)
    except fs.FibuNotFound:
        raise HTTPException(status_code=404, detail="Beleg nicht gefunden") from None
    path = Path(receipt.file_path).resolve()
    root = _storage_dir(settings).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Belegdatei nicht vorhanden")
    disposition = "attachment" if download else "inline"
    return FileResponse(path, media_type=receipt.mime_type, filename=receipt.original_filename,
                        content_disposition_type=disposition)


@router.post("/accounts")
def create_account(request: Request, client_id: str = Form(...), year: str = Form(""), number: str = Form(""),
                   name: str = Form(""), kind: str = Form(""), db: Session = Depends(get_db),
                   current_user: User = Depends(require_role(permission=PERM_FIBU_MANAGE))):
    _client_or_404(db, client_id)
    try:
        fs.create_account(db, client_id, number=number, name=name, kind=kind, actor=current_user.email)
    except fs.FibuError as exc:
        return _fail(request, db, current_user, client_id, _year(year), "konten", exc,
                     {"number": number, "name": name, "kind": kind})
    return _done(client_id, _year(year), "konten", "account_created")


@router.post("/accounts/seed")
def seed_accounts(request: Request, client_id: str = Form(...), year: str = Form(""), db: Session = Depends(get_db),
                  current_user: User = Depends(require_role(permission=PERM_FIBU_MANAGE))):
    _client_or_404(db, client_id)
    try:
        fs.seed_example_accounts(db, client_id, actor=current_user.email)
    except fs.FibuError as exc:
        return _fail(request, db, current_user, client_id, _year(year), "konten", exc)
    return _done(client_id, _year(year), "konten", "accounts_seeded")


@router.post("/accounts/{account_id}/toggle")
def toggle_account(request: Request, account_id: str, client_id: str = Form(...), year: str = Form(""),
                   active: str = Form("0"), db: Session = Depends(get_db),
                   current_user: User = Depends(require_role(permission=PERM_FIBU_MANAGE))):
    _client_or_404(db, client_id)
    try:
        fs.set_account_active(db, client_id, account_id, active=active == "1", actor=current_user.email)
    except fs.FibuNotFound:
        raise HTTPException(status_code=404, detail="Konto nicht gefunden") from None
    except fs.FibuError as exc:
        return _fail(request, db, current_user, client_id, _year(year), "konten", exc)
    return _done(client_id, _year(year), "konten", "account_toggled")


@router.post("/tasks")
def create_task(request: Request, client_id: str = Form(...), year: str = Form(""), title: str = Form(""),
                due_date: str = Form(""), db: Session = Depends(get_db),
                current_user: User = Depends(require_role(permission=PERM_FIBU_MANAGE))):
    _client_or_404(db, client_id)
    try:
        fs.create_task(db, client_id, title=title, due_date=due_date, actor=current_user.email)
    except fs.FibuError as exc:
        return _fail(request, db, current_user, client_id, _year(year), "uebersicht", exc,
                     {"task_title": title, "task_due": due_date})
    return _done(client_id, _year(year), "uebersicht", "task_created")


@router.post("/tasks/{task_id}/toggle")
def toggle_task(request: Request, task_id: str, client_id: str = Form(...), year: str = Form(""),
                done: str = Form("1"), db: Session = Depends(get_db),
                current_user: User = Depends(require_role(permission=PERM_FIBU_MANAGE))):
    _client_or_404(db, client_id)
    try:
        fs.set_task_done(db, client_id, task_id, done=done == "1", actor=current_user.email)
    except fs.FibuNotFound:
        raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden") from None
    except fs.FibuError as exc:
        return _fail(request, db, current_user, client_id, _year(year), "uebersicht", exc)
    return _done(client_id, _year(year), "uebersicht", "task_toggled")


@router.post("/profile")
def save_profile(request: Request, client_id: str = Form(...), year: str = Form(""), legal_form: str = Form(""),
                 tax_number: str = Form(""), vat_id: str = Form(""), db: Session = Depends(get_db),
                 current_user: User = Depends(require_role(permission=PERM_FIBU_MANAGE))):
    _client_or_404(db, client_id)
    try:
        fs.save_profile(db, client_id, legal_form=legal_form, tax_number=tax_number, vat_id=vat_id,
                        actor=current_user.email)
    except fs.FibuError as exc:
        return _fail(request, db, current_user, client_id, _year(year), "einstellungen", exc,
                     {"legal_form": legal_form, "tax_number": tax_number, "vat_id": vat_id})
    return _done(client_id, _year(year), "einstellungen", "profile_saved")


@router.get("/export/datev-entwurf.csv")
def export_datev_draft(client: str, year: str | None = None, db: Session = Depends(get_db),
                       current_user: User = Depends(require_login)) -> Response:
    if not has_permission(current_user, PERM_FIBU_MANAGE):
        raise HTTPException(status_code=403, detail="Keine Berechtigung für diese Aktion")
    target = _client_or_404(db, client)
    y = _year(year)
    content, _count = fs.datev_draft_csv(db, target.id, y, actor=current_user.email)
    filename = f"Lexono_DATEV-Entwurf_{y}.csv"
    return Response(content="﻿" + content, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
