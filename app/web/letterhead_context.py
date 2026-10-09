"""Template-Kontext fuer den Briefkopf eines Entwurfs (Editor, Entwurfsdetail) - an EINER Stelle.

Der Briefkopf stammt aus dem Briefkopfprofil des Entwurfs (`Draft.letterhead_ref`, siehe
app/firm_profile/letterheads.py) und wird mit denselben Helfern wie im Export aufgebaut."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.export.letterhead import (
    address_and_contact_lines,
    has_letterhead_content,
    has_signature_content,
    image_exists,
)
from app.firm_profile.letterheads import (
    FIRM_LETTERHEAD_REF,
    letterhead_name,
    list_letterheads,
    resolve_letterhead,
)
from app.models import Draft, Letterhead


def letterhead_urls(letterhead: object) -> tuple[str, str]:
    """(Logo-URL, Unterschrift-URL) - der Profil-Briefkopf behaelt die bisherigen Adressen."""
    if isinstance(letterhead, Letterhead):
        base = f"/dashboard/settings/letterheads/{letterhead.id}"
        return f"{base}/logo-file", f"{base}/signature-file"
    return "/dashboard/settings/profile/logo-file", "/dashboard/settings/profile/signature-file"


def draft_letterhead_context(db: Session, draft: Draft) -> dict:
    letterhead = resolve_letterhead(db, draft.letterhead_ref)
    logo_url, signature_url = letterhead_urls(letterhead)
    ref = letterhead.id if isinstance(letterhead, Letterhead) else FIRM_LETTERHEAD_REF
    return {
        "letterhead": letterhead,
        "letterhead_ref": ref,
        "letterhead_display_name": letterhead_name(db, draft.letterhead_ref),
        "letterhead_choices": list_letterheads(db),
        "show_letterhead": has_letterhead_content(letterhead),
        "show_signature_block": has_signature_content(letterhead),
        "firm_logo_exists": image_exists(letterhead.logo_path),
        "firm_signature_exists": image_exists(letterhead.signature_path),
        "firm_contact_lines": address_and_contact_lines(letterhead),
        "letterhead_logo_url": logo_url,
        "letterhead_signature_url": signature_url,
    }
