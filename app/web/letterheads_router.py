"""Einstellungen -> Briefkoepfe: mehrere Briefkoepfe pro Kanzlei verwalten.

Eine Seite statt mehrerer Dialoge (einfach und eindeutig): Liste aller Briefkoepfe (Briefkopf des
Kanzlei-Profils zuerst), Anlegen/Bearbeiten, Standard festlegen, Loeschen (nicht solange Entwuerfe
den Briefkopf verwenden), Logo/Unterschrift je zusaetzlichem Briefkopf. Der Briefkopf des
Kanzlei-Profils behaelt seine bisherigen Dialoge (Kanzleiinformationen/Branding) fuer Logo und
Unterschrift. Logik und Regeln: app/firm_profile/letterheads.py."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth.permissions import require_login, require_role
from app.config import get_settings
from app.db.session import get_db
from app.firm_profile.letterheads import (
    FIRM_LETTERHEAD_REF,
    LetterheadError,
    create_letterhead,
    delete_letterhead,
    list_letterheads,
    set_default,
    update_letterhead,
    usage_count,
)
from app.models import Letterhead, User
from app.web.settings_router import (
    _delete_if_exists,
    _require_admin,
    _store_profile_image,
    _validate_image_upload,
)
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard/settings/letterheads", tags=["dashboard-letterheads"])
templates = Jinja2Templates(directory=TEMPLATES_DIR)

_FIELD_NAMES = (
    "firm_name",
    "legal_form",
    "street",
    "address_addition",
    "postal_code",
    "city",
    "phone",
    "email",
    "website",
    "signatory_name",
)


def _redirect(*, success: str | None = None, error: str | None = None, edit: str | None = None) -> RedirectResponse:
    parts = []
    if success:
        parts.append(f"success={quote(success)}")
    if error:
        parts.append(f"error={quote(error)}")
    if edit:
        parts.append(f"edit={quote(edit)}")
    return RedirectResponse(
        url="/dashboard/settings/letterheads" + (("?" + "&".join(parts)) if parts else ""), status_code=303
    )


@router.get("", response_class=HTMLResponse)
def letterheads_page(
    request: Request,
    success: str | None = None,
    error: str | None = None,
    edit: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(_require_admin),
) -> HTMLResponse:
    choices = list_letterheads(db)
    rows = [
        {
            "choice": choice,
            "used_by": usage_count(db, choice.ref),
            "can_delete": choice.ref != FIRM_LETTERHEAD_REF,
        }
        for choice in choices
    ]
    editing = next((choice for choice in choices if choice.ref == edit), None)
    context = {
        "request": request,
        "active_nav": "Einstellungen",
        "current_user": current_user,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "rows": rows,
        "editing": editing,
        "FIRM_LETTERHEAD_REF": FIRM_LETTERHEAD_REF,
        "success": success,
        "error": error,
    }
    return templates.TemplateResponse(request, "letterheads.html", context)


def _fields(**values: str) -> dict[str, str]:
    return {key: values.get(key, "") for key in _FIELD_NAMES}


@router.post("/create")
def create(
    name: str = Form(""),
    firm_name: str = Form(""),
    legal_form: str = Form(""),
    street: str = Form(""),
    address_addition: str = Form(""),
    postal_code: str = Form(""),
    city: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    website: str = Form(""),
    signatory_name: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    try:
        row = create_letterhead(
            db,
            name=name,
            actor=current_user.email,
            **_fields(
                firm_name=firm_name, legal_form=legal_form, street=street, address_addition=address_addition,
                postal_code=postal_code, city=city, phone=phone, email=email, website=website,
                signatory_name=signatory_name,
            ),
        )
    except LetterheadError as exc:
        return _redirect(error=str(exc))
    return _redirect(success=f'Briefkopf "{row.name}" angelegt', edit=row.id)


@router.post("/{ref}/update")
def update(
    ref: str,
    name: str = Form(""),
    firm_name: str = Form(""),
    legal_form: str = Form(""),
    street: str = Form(""),
    address_addition: str = Form(""),
    postal_code: str = Form(""),
    city: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    website: str = Form(""),
    signatory_name: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    try:
        update_letterhead(
            db,
            ref,
            name=name,
            actor=current_user.email,
            **_fields(
                firm_name=firm_name, legal_form=legal_form, street=street, address_addition=address_addition,
                postal_code=postal_code, city=city, phone=phone, email=email, website=website,
                signatory_name=signatory_name,
            ),
        )
    except LetterheadError as exc:
        return _redirect(error=str(exc), edit=ref)
    return _redirect(success="Briefkopf gespeichert")


@router.post("/{ref}/default")
def make_default(
    ref: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    try:
        set_default(db, ref)
    except LetterheadError as exc:
        return _redirect(error=str(exc))
    return _redirect(success="Standardbriefkopf festgelegt")


@router.post("/{ref}/delete")
def delete(
    ref: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    try:
        delete_letterhead(db, ref)
    except LetterheadError as exc:
        return _redirect(error=str(exc))
    return _redirect(success="Briefkopf gelöscht")


def _get_row(db: Session, ref: str) -> Letterhead:
    row = db.get(Letterhead, ref)
    if row is None:
        raise HTTPException(status_code=404, detail="Briefkopf nicht gefunden")
    return row


@router.post("/{ref}/logo")
def upload_logo(
    ref: str,
    logo: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    row = _get_row(db, ref)
    error = _validate_image_upload(logo)
    if error:
        return _redirect(error=error, edit=ref)
    try:
        new_path = _store_profile_image(logo, Path(get_settings().firm_profile_asset_storage_dir))
    except ValueError as exc:
        return _redirect(error=str(exc), edit=ref)
    old_path = row.logo_path
    row.logo_path = new_path
    row.logo_original_filename = logo.filename
    row.updated_by_actor = current_user.email
    db.commit()
    if old_path != new_path:
        _delete_if_exists(old_path)
    return _redirect(success="Logo hochgeladen", edit=ref)


@router.post("/{ref}/logo/remove")
def remove_logo(
    ref: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    row = _get_row(db, ref)
    old_path = row.logo_path
    row.logo_path = None
    row.logo_original_filename = None
    row.updated_by_actor = current_user.email
    db.commit()
    _delete_if_exists(old_path)
    return _redirect(success="Logo entfernt", edit=ref)


@router.post("/{ref}/signature")
def upload_signature(
    ref: str,
    signature: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    row = _get_row(db, ref)
    error = _validate_image_upload(signature)
    if error:
        return _redirect(error=error, edit=ref)
    try:
        new_path = _store_profile_image(signature, Path(get_settings().firm_profile_asset_storage_dir))
    except ValueError as exc:
        return _redirect(error=str(exc), edit=ref)
    old_path = row.signature_path
    row.signature_path = new_path
    row.signature_original_filename = signature.filename
    row.updated_by_actor = current_user.email
    db.commit()
    if old_path != new_path:
        _delete_if_exists(old_path)
    return _redirect(success="Unterschrift hochgeladen", edit=ref)


@router.post("/{ref}/signature/remove")
def remove_signature(
    ref: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    row = _get_row(db, ref)
    old_path = row.signature_path
    row.signature_path = None
    row.signature_original_filename = None
    row.updated_by_actor = current_user.email
    db.commit()
    _delete_if_exists(old_path)
    return _redirect(success="Unterschrift entfernt", edit=ref)


@router.get("/{ref}/logo-file")
def logo_file(
    ref: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> FileResponse:
    """Wie `firm_logo_file`: fuer jede angemeldete Rolle (Editor/Vorschau zeigen das Logo)."""
    row = _get_row(db, ref)
    if not row.logo_path or not Path(row.logo_path).exists():
        raise HTTPException(status_code=404, detail="Kein Logo hinterlegt")
    return FileResponse(row.logo_path)


@router.get("/{ref}/signature-file")
def signature_file(
    ref: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> FileResponse:
    row = _get_row(db, ref)
    if not row.signature_path or not Path(row.signature_path).exists():
        raise HTTPException(status_code=404, detail="Keine Unterschrift hinterlegt")
    return FileResponse(row.signature_path)
