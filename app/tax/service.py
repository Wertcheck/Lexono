"""Lexono Steuern - Fachlogik (28.10.2026): Vorgaenge, Teile (Hauptvordruck/Anlagen), Feldwerte, Statusverlauf.

Jede Funktion prueft serverseitig, in dieser Reihenfolge:
1. Modul "steuern" lizenziert (app/modules/licensing.ensure_module) - sonst ModuleLockedError;
2. Berechtigung des Nutzers (PERM_TAX_PREPARE / _APPROVE / _SUBMIT) - sonst PermissionDeniedError;
3. Mandantentrennung: Vorgaenge, Teile und Werte nur ueber ``client_id`` erreichbar - fremd = TaxNotFound.

Statusmodell (Vier-Stufen-Freigabe, angelehnt an § 87d AO: Daten nach Zustimmung unveraendert uebermitteln)::

    draft --(Pruefung anfordern)--> in_review --(freigeben)--> approved --(uebermitteln: NICHT implementiert)
      ^                                 |                          |
      +----(zurueck, mit Grund)---------+-----(Freigabe zuruecknehmen, mit Grund; Freigabe wird geloescht)
    draft/in_review/approved --(stornieren, mit Grund)--> cancelled

Werte sind nur im Status ``draft`` aenderbar; nach einer Freigabe muss der Vorgang zuerst ausdruecklich (mit Grund,
protokolliert) zurueckgenommen werden. ``submit_filing`` lehnt immer ab: ERiC ist nicht angebunden, es wird nichts
uebermittelt und kein Uebermittlungserfolg vorgetaeuscht. Keine Steuerberechnung, keine Plausibilitaetspruefung im
Sinne der Finanzverwaltung - beides folgt erst mit ERiC.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.auth.permissions import PERM_TAX_APPROVE, PERM_TAX_PREPARE, PERM_TAX_SUBMIT, PermissionDeniedError, has_permission
from app.models import AuditEvent, Client, Matter, User
from app.models.tax import TAX_VALUE_SOURCES, TAX_VALUE_TYPES, TaxFieldValue, TaxFiling, TaxFilingPart
from app.modules.catalog import MODULE_STEUERN
from app.modules.licensing import ensure_module
from app.tax.catalog import PERIOD_LIMITS, TAX_TYPES

STATUS_LABELS = {"draft": "Entwurf", "in_review": "In Prüfung", "approved": "Freigegeben", "submitted": "Übermittelt",
                 "accepted": "Angenommen", "rejected": "Abgewiesen", "cancelled": "Storniert"}

#: (von, nach) -> (benoetigte Berechtigung, Grund Pflicht)
_TRANSITIONS = {
    ("draft", "in_review"): (PERM_TAX_PREPARE, False),
    ("in_review", "draft"): (PERM_TAX_PREPARE, True),
    ("in_review", "approved"): (PERM_TAX_APPROVE, False),
    ("approved", "draft"): (PERM_TAX_APPROVE, True),
    ("draft", "cancelled"): (PERM_TAX_PREPARE, True),
    ("in_review", "cancelled"): (PERM_TAX_PREPARE, True),
    ("approved", "cancelled"): (PERM_TAX_APPROVE, True),
}
_FIELD_KEY = re.compile(r"[A-Za-z0-9_.:\-]{1,64}")


class TaxError(ValueError):
    pass


class TaxNotFound(LookupError):
    pass


class SubmissionNotAvailable(TaxError):
    pass


def _guard(db: Session, user: User, permission: str) -> None:
    ensure_module(db, MODULE_STEUERN)
    if not has_permission(user, permission):
        raise PermissionDeniedError()


def _audit(db: Session, filing: TaxFiling, event: str, user: User, details: str) -> None:
    db.add(AuditEvent(entity_type="TaxFiling", entity_id=filing.id, event_type=event, actor=user.email[:64],
                      details=details))


def get_filing(db: Session, user: User, client_id: str, filing_id: str) -> TaxFiling:
    ensure_module(db, MODULE_STEUERN)
    filing = db.query(TaxFiling).filter_by(id=filing_id, client_id=client_id).first() if filing_id else None
    if filing is None:
        raise TaxNotFound("Vorgang nicht gefunden.")
    return filing


def list_filings(db: Session, user: User, client_id: str) -> list[TaxFiling]:
    ensure_module(db, MODULE_STEUERN)
    return (db.query(TaxFiling).filter(TaxFiling.client_id == client_id)
            .order_by(TaxFiling.year.desc(), TaxFiling.created_at.desc()).all())


def create_filing(db: Session, user: User, client_id: str, *, tax_type: str, year: int, period_kind: str,
                  period_no: int | None = None, title: str = "", matter_id: str | None = None,
                  correction_of_id: str | None = None, form_version: str | None = None) -> TaxFiling:
    _guard(db, user, PERM_TAX_PREPARE)
    client = db.get(Client, client_id)
    if client is None:
        raise TaxNotFound("Mandant nicht gefunden.")
    if client.status != "active":
        raise TaxError("Der Mandant ist archiviert.")
    spec = TAX_TYPES.get(tax_type)
    if spec is None:
        raise TaxError("Unbekannte Steuerart.")
    if not 2000 <= int(year) <= 2100:
        raise TaxError("Das Jahr liegt außerhalb des zulässigen Bereichs.")
    if period_kind not in spec.period_kinds:
        raise TaxError(f"Für „{spec.label}“ ist der Zeitraum „{period_kind}“ nicht vorgesehen.")
    limit = PERIOD_LIMITS[period_kind]
    if limit is None:
        period_no = None
    elif period_no is None or not 1 <= int(period_no) <= limit:
        raise TaxError("Bitte einen gültigen Zeitraum (Monat bzw. Quartal) angeben.")
    if matter_id and db.query(Matter).filter_by(id=matter_id, client_id=client_id).first() is None:
        raise TaxError("Die Akte gehört nicht zu diesem Mandanten.")
    if correction_of_id and db.query(TaxFiling).filter_by(id=correction_of_id, client_id=client_id).first() is None:
        raise TaxError("Der berichtigte Vorgang gehört nicht zu diesem Mandanten.")
    filing = TaxFiling(client_id=client_id, matter_id=matter_id, tax_type=tax_type, filing_kind=spec.filing_kind,
                       year=int(year), period_kind=period_kind, period_no=period_no,
                       title=(title.strip() or f"{spec.label} {year}")[:200], form_version=form_version,
                       status="draft", correction_of_id=correction_of_id, created_by=user.email, updated_by=user.email)
    db.add(filing)
    db.flush()
    _audit(db, filing, "tax_filing_created", user, f"{spec.label} {year} ({period_kind} {period_no or ''})".strip())
    db.commit()
    return filing


def add_part(db: Session, user: User, client_id: str, filing_id: str, *, part_code: str, label: str) -> TaxFilingPart:
    _guard(db, user, PERM_TAX_PREPARE)
    filing = get_filing(db, user, client_id, filing_id)
    if filing.status != "draft":
        raise TaxError("Teile können nur im Entwurf ergänzt werden.")
    code = (part_code or "").strip()
    if not _FIELD_KEY.fullmatch(code):
        raise TaxError("Ungültige Teilkennung.")
    instance = 1 + max((p.instance_no for p in filing.parts if p.part_code == code), default=0)
    part = TaxFilingPart(filing_id=filing.id, part_code=code, instance_no=instance, label=(label.strip() or code)[:200])
    db.add(part)
    db.flush()
    _audit(db, filing, "tax_part_added", user, f"{code} #{instance}")
    db.commit()
    return part


def _normalize(value_type: str, raw: str | None) -> str | None:
    if raw is None or str(raw).strip() == "":
        return None
    text = str(raw).strip()
    if value_type == "text":
        if len(text) > 2000:
            raise TaxError("Text ist zu lang (höchstens 2000 Zeichen).")
        return text
    if value_type in ("amount_cents", "integer"):
        if not re.fullmatch(r"-?\d{1,15}", text):
            raise TaxError("Bitte eine ganze Zahl (Beträge in Cent) eingeben.")
        return str(int(text))
    if value_type == "date":
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            raise TaxError("Bitte ein gültiges Datum (JJJJ-MM-TT) eingeben.") from None
    if value_type == "boolean":
        if text.lower() not in ("true", "false", "1", "0"):
            raise TaxError("Bitte ja/nein angeben.")
        return "true" if text.lower() in ("true", "1") else "false"
    raise TaxError("Unbekannter Werttyp.")


def set_value(db: Session, user: User, client_id: str, part_id: str, *, field_key: str, value: str | None,
              value_type: str = "text", source: str = "manual") -> TaxFieldValue | None:
    _guard(db, user, PERM_TAX_PREPARE)
    part = (db.query(TaxFilingPart).join(TaxFiling, TaxFilingPart.filing_id == TaxFiling.id)
            .filter(TaxFilingPart.id == part_id, TaxFiling.client_id == client_id).first() if part_id else None)
    if part is None:
        raise TaxNotFound("Formularteil nicht gefunden.")
    filing = part.filing
    if filing.status != "draft":
        raise TaxError("Werte sind nur im Entwurf änderbar. Freigegebene Vorgänge zuerst ausdrücklich zurücknehmen.")
    if not _FIELD_KEY.fullmatch(field_key or ""):
        raise TaxError("Ungültige Feldkennung.")
    if value_type not in TAX_VALUE_TYPES or source not in TAX_VALUE_SOURCES:
        raise TaxError("Unbekannter Werttyp oder unbekannte Herkunft.")
    normalized = _normalize(value_type, value)
    row = db.query(TaxFieldValue).filter_by(part_id=part.id, field_key=field_key).first()
    old = row.value_text if row else None
    if normalized is None:
        if row is not None:
            db.delete(row)
        row = None
    else:
        row = row or TaxFieldValue(part_id=part.id, field_key=field_key)
        row.value_type, row.value_text, row.source, row.updated_by = value_type, normalized, source, user.email
        db.add(row)
    filing.updated_by = user.email
    _audit(db, filing, "tax_value_changed", user,
           f"{part.part_code}#{part.instance_no}.{field_key}: {old if old is not None else '-'} -> "
           f"{normalized if normalized is not None else '-'} ({source})")
    db.commit()
    return row


def transition(db: Session, user: User, client_id: str, filing_id: str, target: str, *, reason: str = "") -> TaxFiling:
    _guard(db, user, PERM_TAX_PREPARE)  # ohne Grundrecht keine Auskunft ueber zulaessige Statuswechsel
    filing = get_filing(db, user, client_id, filing_id)
    rule = _TRANSITIONS.get((filing.status, target))
    if rule is None:
        raise TaxError(f"Statuswechsel {STATUS_LABELS.get(filing.status, filing.status)} → "
                       f"{STATUS_LABELS.get(target, target)} ist nicht zulässig.")
    permission, reason_required = rule
    _guard(db, user, permission)
    reason = (reason or "").strip()
    if reason_required and not reason:
        raise TaxError("Bitte einen Grund angeben.")
    previous = filing.status
    filing.status, filing.updated_by = target, user.email
    if target == "approved":
        filing.approved_by, filing.approved_at = user.email, datetime.now(timezone.utc)
    elif previous == "approved":
        filing.approved_by, filing.approved_at = None, None
    _audit(db, filing, "tax_status_changed", user,
           f"{STATUS_LABELS[previous]} -> {STATUS_LABELS[target]}" + (f": {reason[:300]}" if reason else ""))
    db.commit()
    return filing


def submit_filing(db: Session, user: User, client_id: str, filing_id: str, *, environment: str = "test") -> None:
    """Uebermittlung ueber ELSTER/ERiC - NICHT implementiert. Prueft dennoch zuerst Modul, Recht und Status, damit
    die Reihenfolge der Schutzmechanismen schon jetzt testbar feststeht; danach immer Ablehnung."""
    filing = get_filing(db, user, client_id, filing_id)
    _guard(db, user, PERM_TAX_SUBMIT)
    if filing.status != "approved":
        raise TaxError("Nur freigegebene Vorgänge können übermittelt werden.")
    raise SubmissionNotAvailable("Die elektronische Übermittlung (ELSTER/ERiC) ist noch nicht verfügbar. Es wurde "
                                 "nichts übermittelt.")
