"""Finanzbuchhaltung (MVP, 28.10.2026) - Fachlogik fuer Buchungen, Konten, Belege, Aufgaben und Auswertungen.

Grundsaetze:
- **Mandantentrennung:** Jede Funktion verlangt die `client_id` und fragt Datensaetze ausschliesslich ueber
  `id` UND `client_id` ab. Konto, Gegenkonto und Beleg einer Buchung muessen zum selben Mandanten gehoeren, sonst
  wird die Buchung abgelehnt. Fremde IDs verhalten sich wie nicht vorhandene (`FibuNotFound`).
- **Nachvollziehbarkeit:** Anlegen, Aendern, Buchen, Stornieren, Belege, Konten, Stammdaten und Exporte erzeugen ein
  `AuditEvent` (append-only). Gebuchte Buchungen sind nicht mehr aenderbar, nur stornierbar (mit Grund); stornierte
  Buchungen bleiben sichtbar und fliessen in keine Summe ein.
- **Betraege:** ganze Cent, kaufmaennische Rundung (ROUND_HALF_UP). Erfasst wird der Bruttobetrag; Netto und Steuer
  werden aus dem Steuersatz (0/7/19 %) berechnet.

Ausdruecklich NICHT Teil des MVP (siehe Evidenz-README): GoBD-Konformitaet (Festschreibung, Unveraenderbarkeit,
Verfahrensdokumentation), ein gepruefter Kontenrahmen (SKR03/SKR04), steuerlich gepruefte Berechnungen
(Dauerfristverlaengerung, Sonderfaelle, Reverse Charge usw.), ein validiertes DATEV-Format, Zahlungsausgleich
offener Posten und der Jahresabschluss. Alle Auswertungen sind vorbereitende Uebersichten aus den gespeicherten
Buchungen.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import AuditEvent, Client
from app.models.fibu import (
    FIBU_ACCOUNT_KINDS,
    FIBU_TAX_RATES,
    FibuAccount,
    FibuBooking,
    FibuClientProfile,
    FibuReceipt,
    FibuTask,
)

MAX_RECEIPT_BYTES = 20 * 1024 * 1024
MAX_AMOUNT_CENTS = 99_999_999_999  # 999.999.999,99 EUR
DESCRIPTION_MAX = 200
RECEIPT_NUMBER_MAX = 64

STATUS_LABELS = {"open": "Offen", "posted": "Gebucht", "cancelled": "Storniert"}
DIRECTION_LABELS = {"income": "Einnahme", "expense": "Ausgabe"}
KIND_LABELS = {"revenue": "Erlöse", "expense": "Aufwand", "bank": "Bank/Kasse", "receivable": "Forderungen",
               "payable": "Verbindlichkeiten", "other": "Sonstiges"}

#: Erlaubte Belegformate - Dateiendung UND Dateianfang ("magic bytes") muessen passen
_RECEIPT_TYPES = {
    ".pdf": ("application/pdf", (b"%PDF-",)),
    ".jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
}

#: Gegenkonten je Buchungsrichtung (Plausibilitaetspruefung)
_ALLOWED_ACCOUNTS = {
    "income": ({"revenue"}, {"bank", "receivable", "other"}),
    "expense": ({"expense"}, {"bank", "payable", "other"}),
}

#: Startkonten wie in der Designvorlage (Referenzbild). KEIN geprueftes SKR03/SKR04 - nur ein Startpunkt, der erst
#: auf ausdruecklichen Klick je Mandant angelegt wird und danach frei angepasst werden kann.
EXAMPLE_ACCOUNTS = (
    ("1000", "Bank", "bank"),
    ("1200", "Ford. L+L", "receivable"),
    ("1600", "Verb. L+L", "payable"),
    ("4930", "Bürobedarf", "expense"),
    ("4940", "Kommunikation", "expense"),
    ("4950", "Versicherungen", "expense"),
    ("4960", "Reisekosten", "expense"),
    ("4980", "IT-Kosten", "expense"),
    ("8400", "Erlöse", "revenue"),
)


class FibuError(ValueError):
    """Verstaendliche Validierungsmeldung; `field` benennt das betroffene Formularfeld (oder None)."""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


class FibuNotFound(LookupError):
    """Datensatz existiert nicht ODER gehoert zu einem anderen Mandanten - bewusst nicht unterscheidbar."""


# --- Hilfsfunktionen ---------------------------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(db: Session, entity_type: str, entity_id: str, event_type: str, actor: str, details: str) -> None:
    db.add(AuditEvent(entity_type=entity_type, entity_id=entity_id, event_type=event_type, actor=actor[:64],
                      details=details))


def format_cents(cents: int, *, signed: bool = False) -> str:
    """1234567 -> "12.345,67" (deutsches Format); signed: "+ 12.345,67" / "- 12.345,67"."""
    sign = "-" if cents < 0 else "+"
    value = abs(int(cents))
    text = f"{value // 100:,}".replace(",", ".") + f",{value % 100:02d}"
    if signed:
        return f"{sign} {text}"
    return ("-" if cents < 0 else "") + text


def parse_amount(raw: str) -> int:
    """Deutsche und technische Schreibweisen: "1.250,00", "1250,5", "1250.00", "1250" -> Cent. Nur positive Werte."""
    text = (raw or "").strip().replace("€", "").replace(" ", "").replace(" ", "")
    if not text:
        raise FibuError("Bitte einen Betrag eingeben.", "amount")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d{1,2})?", text) or re.fullmatch(r"\d+,\d{1,2}", text):
        text = text.replace(".", "").replace(",", ".")
    elif not re.fullmatch(r"\d+(\.\d{1,2})?", text):
        raise FibuError("Der Betrag ist ungültig. Beispiel: 1.250,00", "amount")
    cents = int((Decimal(text) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if cents <= 0:
        raise FibuError("Der Betrag muss größer als 0 sein.", "amount")
    if cents > MAX_AMOUNT_CENTS:
        raise FibuError("Der Betrag ist zu groß.", "amount")
    return cents


def split_gross(gross_cents: int, tax_rate: int) -> tuple[int, int]:
    """Bruttobetrag -> (Netto, Steuer) in Cent, kaufmaennisch gerundet; Netto + Steuer == Brutto."""
    net = int((Decimal(gross_cents) * 100 / Decimal(100 + tax_rate)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return net, gross_cents - net


def quarter_of(day: date) -> int:
    return (day.month - 1) // 3 + 1


def vat_due_date(year: int, quarter: int) -> date:
    """10. des Folgemonats nach Quartalsende - ohne Dauerfristverlaengerung, ohne Wochenend-/Feiertagsverschiebung."""
    month = quarter * 3 + 1
    return date(year + 1, 1, 10) if month > 12 else date(year, month, 10)


# --- Mandant, Stammdaten -----------------------------------------------------------------------------------------


def list_clients(db: Session) -> list[Client]:
    return db.query(Client).filter(Client.status == "active").order_by(func.lower(Client.name)).all()


def get_client(db: Session, client_id: str | None) -> Client:
    client = db.get(Client, client_id) if client_id else None
    if client is None:
        raise FibuNotFound("Mandant nicht gefunden.")
    return client


def require_writable_client(db: Session, client_id: str) -> Client:
    client = get_client(db, client_id)
    if client.status != "active":
        raise FibuError("Der Mandant ist archiviert - Änderungen sind nicht möglich.")
    return client


def get_profile(db: Session, client_id: str) -> FibuClientProfile | None:
    return db.query(FibuClientProfile).filter_by(client_id=client_id).first()


def save_profile(db: Session, client_id: str, *, legal_form: str, tax_number: str, vat_id: str,
                 actor: str) -> FibuClientProfile:
    require_writable_client(db, client_id)
    values = {"legal_form": legal_form.strip()[:64] or None, "tax_number": tax_number.strip()[:32] or None,
              "vat_id": vat_id.strip().upper()[:32] or None}
    if values["vat_id"] and not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{2,13}", values["vat_id"]):
        raise FibuError("Die USt-IdNr. muss mit dem Länderkürzel beginnen, z. B. DE123456789.", "vat_id")
    profile = get_profile(db, client_id) or FibuClientProfile(client_id=client_id)
    for key, value in values.items():
        setattr(profile, key, value)
    profile.updated_by = actor
    db.add(profile)
    db.flush()
    _audit(db, "FibuClientProfile", profile.id, "fibu_profile_saved", actor, f"Mandant {client_id}")
    db.commit()
    return profile


# --- Konten ------------------------------------------------------------------------------------------------------


def list_accounts(db: Session, client_id: str, *, active_only: bool = False) -> list[FibuAccount]:
    query = db.query(FibuAccount).filter(FibuAccount.client_id == client_id)
    if active_only:
        query = query.filter(FibuAccount.is_active.is_(True))
    return query.order_by(FibuAccount.number).all()


def get_account(db: Session, client_id: str, account_id: str | None) -> FibuAccount:
    account = (db.query(FibuAccount).filter_by(id=account_id, client_id=client_id).first()
               if account_id else None)
    if account is None:
        raise FibuNotFound("Konto nicht gefunden.")
    return account


def create_account(db: Session, client_id: str, *, number: str, name: str, kind: str, actor: str) -> FibuAccount:
    require_writable_client(db, client_id)
    number, name = (number or "").strip(), (name or "").strip()
    if not re.fullmatch(r"\d{1,8}", number):
        raise FibuError("Die Kontonummer besteht aus 1 bis 8 Ziffern.", "number")
    if not name or len(name) > 120:
        raise FibuError("Bitte eine Kontobezeichnung (höchstens 120 Zeichen) eingeben.", "name")
    if kind not in FIBU_ACCOUNT_KINDS:
        raise FibuError("Bitte eine Kontoart wählen.", "kind")
    if db.query(FibuAccount).filter_by(client_id=client_id, number=number).first():
        raise FibuError(f"Das Konto {number} existiert bei diesem Mandanten bereits.", "number")
    account = FibuAccount(client_id=client_id, number=number, name=name, kind=kind)
    db.add(account)
    db.flush()
    _audit(db, "FibuAccount", account.id, "fibu_account_created", actor, f"Mandant {client_id}: {number} {name}")
    db.commit()
    return account


def seed_example_accounts(db: Session, client_id: str, *, actor: str) -> int:
    """Legt die Startkonten der Designvorlage an, soweit die Nummer noch frei ist. Gibt die Anzahl neuer Konten zurueck."""
    require_writable_client(db, client_id)
    existing = {a.number for a in list_accounts(db, client_id)}
    created = 0
    for number, name, kind in EXAMPLE_ACCOUNTS:
        if number not in existing:
            db.add(FibuAccount(client_id=client_id, number=number, name=name, kind=kind))
            created += 1
    db.flush()
    _audit(db, "Client", client_id, "fibu_example_accounts_created", actor, f"{created} Startkonten angelegt")
    db.commit()
    return created


def set_account_active(db: Session, client_id: str, account_id: str, *, active: bool, actor: str) -> FibuAccount:
    require_writable_client(db, client_id)
    account = get_account(db, client_id, account_id)
    account.is_active = active
    _audit(db, "FibuAccount", account.id, "fibu_account_activated" if active else "fibu_account_deactivated", actor,
           f"{account.number} {account.name}")
    db.commit()
    return account


# --- Belege ------------------------------------------------------------------------------------------------------


def store_receipt(db: Session, client_id: str, *, filename: str, content: bytes, storage_dir: Path | str,
                  actor: str) -> FibuReceipt:
    """Prueft Typ (Endung + Dateianfang) und Groesse, speichert unter <storage>/<client_id>/<uuid><endung>. Der
    Originalname landet nur in der Datenbank, nie im Pfad (kein Path Traversal)."""
    require_writable_client(db, client_id)
    safe_name = Path((filename or "").replace("\\", "/")).name.strip()[:255]
    if not safe_name:
        raise FibuError("Die Datei hat keinen Namen.")
    suffix = Path(safe_name).suffix.lower()
    if suffix not in _RECEIPT_TYPES:
        raise FibuError(f"„{safe_name}“: nur PDF, JPG und PNG sind erlaubt.")
    if not content:
        raise FibuError(f"„{safe_name}“ ist leer.")
    if len(content) > MAX_RECEIPT_BYTES:
        raise FibuError(f"„{safe_name}“ ist größer als {MAX_RECEIPT_BYTES // (1024 * 1024)} MB.")
    mime, magics = _RECEIPT_TYPES[suffix]
    if not any(content.startswith(m) for m in magics):
        raise FibuError(f"„{safe_name}“ ist keine gültige {suffix[1:].upper()}-Datei.")
    receipt_id = str(uuid.uuid4())
    target_dir = Path(storage_dir) / client_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{receipt_id}{suffix}"
    target.write_bytes(content)
    receipt = FibuReceipt(id=receipt_id, client_id=client_id, original_filename=safe_name, mime_type=mime,
                          size_bytes=len(content), file_path=str(target),
                          content_hash=hashlib.sha256(content).hexdigest(), uploaded_by=actor)
    db.add(receipt)
    db.flush()
    _audit(db, "FibuReceipt", receipt.id, "fibu_receipt_uploaded", actor,
           f"Mandant {client_id}: {safe_name} ({len(content)} Bytes)")
    db.commit()
    return receipt


def list_receipts(db: Session, client_id: str) -> list[FibuReceipt]:
    return (db.query(FibuReceipt).filter(FibuReceipt.client_id == client_id)
            .order_by(FibuReceipt.created_at.desc()).all())


def get_receipt(db: Session, client_id: str, receipt_id: str | None) -> FibuReceipt:
    receipt = (db.query(FibuReceipt).filter_by(id=receipt_id, client_id=client_id).first()
               if receipt_id else None)
    if receipt is None:
        raise FibuNotFound("Beleg nicht gefunden.")
    return receipt


def receipt_bookings(db: Session, client_id: str) -> dict[str, list[FibuBooking]]:
    rows = (db.query(FibuBooking).filter(FibuBooking.client_id == client_id, FibuBooking.receipt_id.isnot(None))
            .order_by(FibuBooking.booking_date).all())
    out: dict[str, list[FibuBooking]] = {}
    for row in rows:
        out.setdefault(row.receipt_id, []).append(row)
    return out


# --- Buchungen ---------------------------------------------------------------------------------------------------


@dataclass
class BookingInput:
    booking_date: date
    receipt_number: str
    description: str
    direction: str
    gross_cents: int
    tax_rate: int
    account: FibuAccount
    contra_account: FibuAccount
    receipt: FibuReceipt | None


def validate_booking(db: Session, client_id: str, data: dict) -> BookingInput:
    """Pflichtfelder, Betrag, Steuersatz, Datum und Kontenzuordnung - alle Bezuege nur innerhalb des Mandanten."""
    raw_date = (data.get("booking_date") or "").strip()
    try:
        booking_date = date.fromisoformat(raw_date)
    except ValueError:
        raise FibuError("Bitte ein gültiges Datum eingeben.", "booking_date") from None
    if not 2000 <= booking_date.year <= 2100:
        raise FibuError("Das Datum liegt außerhalb des zulässigen Bereichs.", "booking_date")
    receipt_number = (data.get("receipt_number") or "").strip()
    if not receipt_number:
        raise FibuError("Bitte eine Belegnummer eingeben.", "receipt_number")
    if len(receipt_number) > RECEIPT_NUMBER_MAX:
        raise FibuError(f"Die Belegnummer darf höchstens {RECEIPT_NUMBER_MAX} Zeichen haben.", "receipt_number")
    description = (data.get("description") or "").strip()
    if not description:
        raise FibuError("Bitte eine Beschreibung eingeben.", "description")
    if len(description) > DESCRIPTION_MAX:
        raise FibuError(f"Die Beschreibung darf höchstens {DESCRIPTION_MAX} Zeichen haben.", "description")
    direction = data.get("direction") or ""
    if direction not in _ALLOWED_ACCOUNTS:
        raise FibuError("Bitte Einnahme oder Ausgabe wählen.", "direction")
    gross_cents = parse_amount(data.get("amount") or "")
    try:
        tax_rate = int(data.get("tax_rate") or "")
    except ValueError:
        tax_rate = -1
    if tax_rate not in FIBU_TAX_RATES:
        raise FibuError("Bitte einen Steuersatz (0, 7 oder 19 %) wählen.", "tax_rate")
    try:
        account = get_account(db, client_id, data.get("account_id"))
    except FibuNotFound:
        raise FibuError("Bitte ein Konto dieses Mandanten wählen.", "account_id") from None
    try:
        contra = get_account(db, client_id, data.get("contra_account_id"))
    except FibuNotFound:
        raise FibuError("Bitte ein Gegenkonto dieses Mandanten wählen.", "contra_account_id") from None
    if account.id == contra.id:
        raise FibuError("Konto und Gegenkonto müssen verschieden sein.", "contra_account_id")
    if not account.is_active or not contra.is_active:
        raise FibuError("Deaktivierte Konten können nicht bebucht werden.", "account_id")
    account_kinds, contra_kinds = _ALLOWED_ACCOUNTS[direction]
    if account.kind not in account_kinds:
        expected = "ein Erlöskonto" if direction == "income" else "ein Aufwandskonto"
        raise FibuError(f"Für eine {DIRECTION_LABELS[direction]} ist als Konto {expected} nötig.", "account_id")
    if contra.kind not in contra_kinds:
        raise FibuError("Das Gegenkonto passt nicht zur Buchungsrichtung (erwartet: Bank/Kasse, "
                        + ("Forderungen" if direction == "income" else "Verbindlichkeiten") + " oder Sonstiges).",
                        "contra_account_id")
    receipt = None
    if data.get("receipt_id"):
        try:
            receipt = get_receipt(db, client_id, data.get("receipt_id"))
        except FibuNotFound:
            raise FibuError("Der gewählte Beleg gehört nicht zu diesem Mandanten.", "receipt_id") from None
    return BookingInput(booking_date, receipt_number, description, direction, gross_cents, tax_rate, account, contra,
                        receipt)


def _apply(booking: FibuBooking, data: BookingInput) -> None:
    net, tax = split_gross(data.gross_cents, data.tax_rate)
    booking.booking_date = data.booking_date
    booking.fiscal_year = data.booking_date.year
    booking.receipt_number = data.receipt_number
    booking.description = data.description
    booking.direction = data.direction
    booking.gross_cents, booking.net_cents, booking.tax_cents = data.gross_cents, net, tax
    booking.tax_rate = data.tax_rate
    booking.account_id = data.account.id
    booking.contra_account_id = data.contra_account.id
    booking.receipt_id = data.receipt.id if data.receipt else None


def _snapshot(b: FibuBooking) -> dict:
    return {"Datum": b.booking_date.isoformat(), "Beleg": b.receipt_number, "Beschreibung": b.description,
            "Richtung": b.direction, "Brutto": format_cents(b.gross_cents), "USt": f"{b.tax_rate} %",
            "Konto": b.account_id, "Gegenkonto": b.contra_account_id, "Belegdatei": b.receipt_id or "-"}


def create_booking(db: Session, client_id: str, data: dict, *, actor: str, post: bool = False) -> FibuBooking:
    require_writable_client(db, client_id)
    values = validate_booking(db, client_id, data)
    booking = FibuBooking(client_id=client_id, status="open", created_by=actor, updated_by=actor)
    _apply(booking, values)
    if post:
        booking.status, booking.posted_at = "posted", _now()
    db.add(booking)
    db.flush()
    _audit(db, "FibuBooking", booking.id, "fibu_booking_created", actor,
           f"Mandant {client_id}: {booking.receipt_number}, {format_cents(booking.gross_cents)} EUR, "
           f"Status {STATUS_LABELS[booking.status]}")
    db.commit()
    return booking


def get_booking(db: Session, client_id: str, booking_id: str | None) -> FibuBooking:
    booking = (db.query(FibuBooking).filter_by(id=booking_id, client_id=client_id).first()
               if booking_id else None)
    if booking is None:
        raise FibuNotFound("Buchung nicht gefunden.")
    return booking


def update_booking(db: Session, client_id: str, booking_id: str, data: dict, *, actor: str) -> FibuBooking:
    require_writable_client(db, client_id)
    booking = get_booking(db, client_id, booking_id)
    if booking.status != "open":
        raise FibuError("Nur offene Buchungen können bearbeitet werden. Gebuchte Buchungen bitte stornieren und neu "
                        "erfassen.")
    values = validate_booking(db, client_id, data)
    before = _snapshot(booking)
    _apply(booking, values)
    booking.updated_by = actor
    after = _snapshot(booking)
    changes = "; ".join(f"{k}: {before[k]} -> {after[k]}" for k in before if before[k] != after[k])
    _audit(db, "FibuBooking", booking.id, "fibu_booking_updated", actor, changes or "keine Änderung")
    db.commit()
    return booking


def post_booking(db: Session, client_id: str, booking_id: str, *, actor: str) -> FibuBooking:
    require_writable_client(db, client_id)
    booking = get_booking(db, client_id, booking_id)
    if booking.status != "open":
        raise FibuError("Nur offene Buchungen können gebucht werden.")
    booking.status, booking.posted_at, booking.updated_by = "posted", _now(), actor
    _audit(db, "FibuBooking", booking.id, "fibu_booking_posted", actor, booking.receipt_number)
    db.commit()
    return booking


def cancel_booking(db: Session, client_id: str, booking_id: str, *, reason: str, actor: str) -> FibuBooking:
    require_writable_client(db, client_id)
    booking = get_booking(db, client_id, booking_id)
    if booking.status == "cancelled":
        raise FibuError("Die Buchung ist bereits storniert.")
    reason = (reason or "").strip()
    if not reason:
        raise FibuError("Bitte einen Stornogrund angeben.", "reason")
    previous = booking.status
    booking.status, booking.cancelled_at, booking.cancel_reason = "cancelled", _now(), reason[:500]
    booking.updated_by = actor
    _audit(db, "FibuBooking", booking.id, "fibu_booking_cancelled", actor,
           f"{booking.receipt_number} (vorher {STATUS_LABELS[previous]}): {reason[:300]}")
    db.commit()
    return booking


def list_bookings(db: Session, client_id: str, year: int, *, search: str = "", account_id: str = "",
                  include_cancelled: bool = True) -> list[FibuBooking]:
    query = db.query(FibuBooking).filter(FibuBooking.client_id == client_id, FibuBooking.fiscal_year == year)
    if not include_cancelled:
        query = query.filter(FibuBooking.status != "cancelled")
    if account_id:
        query = query.filter(or_(FibuBooking.account_id == account_id, FibuBooking.contra_account_id == account_id))
    term = search.strip()
    if term:
        like = f"%{term}%"
        query = query.filter(or_(FibuBooking.description.ilike(like), FibuBooking.receipt_number.ilike(like)))
    return query.order_by(FibuBooking.booking_date.desc(), FibuBooking.created_at.desc()).all()


def booking_history(db: Session, booking_id: str) -> list[AuditEvent]:
    return (db.query(AuditEvent).filter_by(entity_type="FibuBooking", entity_id=booking_id)
            .order_by(AuditEvent.created_at).all())


def _active(db: Session, client_id: str, year: int) -> list[FibuBooking]:
    return list_bookings(db, client_id, year, include_cancelled=False)


# --- Kennzahlen und Auswertungen ---------------------------------------------------------------------------------


@dataclass
class Kpis:
    revenue_gross: int
    cost_gross: int
    result_net: int
    revenue_change: float | None
    cost_change: float | None
    result_change: float | None
    vat_quarter: int
    vat_year: int
    vat_payable: int
    vat_due: date
    booking_count: int
    open_count: int


def _totals(rows: list[FibuBooking]) -> tuple[int, int, int]:
    rev = sum(b.gross_cents for b in rows if b.direction == "income")
    cost = sum(b.gross_cents for b in rows if b.direction == "expense")
    result = (sum(b.net_cents for b in rows if b.direction == "income")
              - sum(b.net_cents for b in rows if b.direction == "expense"))
    return rev, cost, result


def _change(current: int, previous: int, has_previous: bool) -> float | None:
    if not has_previous or previous == 0:
        return None
    return round((current - previous) / abs(previous) * 100, 1)


def vat_quarter_for(year: int, today: date | None = None) -> int:
    today = today or date.today()
    if year == today.year:
        return quarter_of(today)
    return 4 if year < today.year else 1


def vat_payable(rows: list[FibuBooking], quarter: int | None = None) -> tuple[int, int]:
    """(Umsatzsteuer, Vorsteuer) der uebergebenen Buchungen, optional nur eines Quartals."""
    sel = [b for b in rows if quarter is None or quarter_of(b.booking_date) == quarter]
    return (sum(b.tax_cents for b in sel if b.direction == "income"),
            sum(b.tax_cents for b in sel if b.direction == "expense"))


def compute_kpis(db: Session, client_id: str, year: int, *, today: date | None = None) -> Kpis:
    rows = _active(db, client_id, year)
    prev_rows = _active(db, client_id, year - 1)
    rev, cost, result = _totals(rows)
    p_rev, p_cost, p_result = _totals(prev_rows)
    has_prev = bool(prev_rows)
    quarter = vat_quarter_for(year, today)
    ust, vst = vat_payable(rows, quarter)
    return Kpis(rev, cost, result, _change(rev, p_rev, has_prev), _change(cost, p_cost, has_prev),
                _change(result, p_result, has_prev), quarter, year, ust - vst, vat_due_date(year, quarter),
                len(rows), sum(1 for b in rows if b.status == "open"))


def vat_overview(db: Session, client_id: str, year: int) -> list[dict]:
    """Je Quartal: Bemessungsgrundlagen und Steuer je Satz, Vorsteuer, Zahllast, Faelligkeit (vorbereitend)."""
    rows = _active(db, client_id, year)
    out = []
    for quarter in (1, 2, 3, 4):
        sel = [b for b in rows if quarter_of(b.booking_date) == quarter]
        by_rate = []
        for rate in (19, 7, 0):
            inc = [b for b in sel if b.direction == "income" and b.tax_rate == rate]
            by_rate.append({"rate": rate, "base": sum(b.net_cents for b in inc), "tax": sum(b.tax_cents for b in inc)})
        ust, vst = vat_payable(sel)
        out.append({"quarter": quarter, "by_rate": by_rate, "ust": ust, "vst": vst, "payable": ust - vst,
                    "due": vat_due_date(year, quarter), "count": len(sel)})
    return out


@dataclass
class SusaRow:
    number: str
    name: str
    kind: str
    debit: int = 0
    credit: int = 0
    account_id: str | None = None

    @property
    def balance(self) -> int:
        return self.debit - self.credit


def _ledger_lines(b: FibuBooking) -> list[tuple[str, int, int]]:
    """Doppelte Buchfuehrung je Buchung: (Kontoschluessel, Soll, Haben). Steuer auf berechneten Steuerzeilen."""
    if b.direction == "income":
        lines = [(b.contra_account_id, b.gross_cents, 0), (b.account_id, 0, b.net_cents)]
        if b.tax_cents:
            lines.append((f"ust{b.tax_rate}", 0, b.tax_cents))
    else:
        lines = [(b.account_id, b.net_cents, 0), (b.contra_account_id, 0, b.gross_cents)]
        if b.tax_cents:
            lines.append((f"vst{b.tax_rate}", b.tax_cents, 0))
    return lines


def trial_balance(db: Session, client_id: str, year: int) -> list[SusaRow]:
    """Summen- und Saldenliste: Soll/Haben/Saldo je bebuchtem Konto plus berechnete USt-/Vorsteuer-Zeilen."""
    accounts = {a.id: a for a in list_accounts(db, client_id)}
    rows: dict[str, SusaRow] = {}
    for b in _active(db, client_id, year):
        for key, debit, credit in _ledger_lines(b):
            if key not in rows:
                if key in accounts:
                    a = accounts[key]
                    rows[key] = SusaRow(a.number, a.name, a.kind, account_id=a.id)
                else:
                    label = "Umsatzsteuer" if key.startswith("ust") else "Vorsteuer"
                    rows[key] = SusaRow("", f"{label} {key[3:]} % (berechnet)", "tax")
            rows[key].debit += debit
            rows[key].credit += credit
    return sorted(rows.values(), key=lambda r: (r.number == "", r.number, r.name))


def bwa_overview(db: Session, client_id: str, year: int) -> dict:
    """BWA-aehnliche Uebersicht (netto): Erloese und Aufwand je Konto, Ergebnis, Monatswerte."""
    rows = _active(db, client_id, year)
    accounts = {a.id: a for a in list_accounts(db, client_id)}
    revenue: dict[str, int] = {}
    expense: dict[str, int] = {}
    months = [{"month": m, "revenue": 0, "expense": 0} for m in range(1, 13)]
    for b in rows:
        target = revenue if b.direction == "income" else expense
        target[b.account_id] = target.get(b.account_id, 0) + b.net_cents
        months[b.booking_date.month - 1]["revenue" if b.direction == "income" else "expense"] += b.net_cents

    def lines(source: dict[str, int]) -> list[dict]:
        return sorted(({"number": accounts[k].number, "name": accounts[k].name, "amount": v}
                       for k, v in source.items()), key=lambda x: x["number"])

    total_rev, total_exp = sum(revenue.values()), sum(expense.values())
    for m in months:
        m["result"] = m["revenue"] - m["expense"]
    return {"revenue": lines(revenue), "expense": lines(expense), "total_revenue": total_rev,
            "total_expense": total_exp, "result": total_rev - total_exp, "months": months}


def account_ledger(db: Session, client_id: str, year: int, account_id: str) -> tuple[FibuAccount, list[dict]]:
    """Kontenblatt: alle aktiven Buchungen des Jahres auf diesem Konto mit Soll/Haben und laufendem Saldo."""
    account = get_account(db, client_id, account_id)
    out, balance = [], 0
    for b in sorted(_active(db, client_id, year), key=lambda x: (x.booking_date, x.created_at)):
        for key, debit, credit in _ledger_lines(b):
            if key == account.id:
                balance += debit - credit
                out.append({"booking": b, "debit": debit, "credit": credit, "balance": balance})
    return account, out


def open_items(db: Session, client_id: str, year: int) -> list[FibuBooking]:
    """Vereinfachte OP-Liste: aktive Buchungen gegen Forderungs-/Verbindlichkeitskonten. Ein Zahlungsausgleich wird im
    MVP nicht abgebildet - die Liste zeigt also alle solchen Buchungen, nicht nur unbezahlte."""
    return [b for b in _active(db, client_id, year) if b.contra_account.kind in ("receivable", "payable")]


def _csv_text(value: str) -> str:
    """Schutz vor Formel-Injection beim Oeffnen der CSV in einer Tabellenkalkulation (=, +, -, @ am Anfang)."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def datev_draft_csv(db: Session, client_id: str, year: int, *, actor: str) -> tuple[str, int]:
    """DATEV-aehnlicher CSV-ENTWURF (Semikolon, Windows-1252-kompatible Zeichen nicht erzwungen) mit den Kernspalten
    eines Buchungsstapels. KEIN validiertes DATEV-Format: Kopfzeile (EXTF), BU-Schluessel und Pflichtfelder des
    DATEV-Formats sind nicht umgesetzt; ein Import ist nicht geprueft. Nur GEBUCHTE Buchungen."""
    rows = [b for b in list_bookings(db, client_id, year, include_cancelled=False) if b.status == "posted"]
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Umsatz (ohne Soll/Haben-Kz)", "Soll/Haben-Kennzeichen", "WKZ Umsatz", "Konto",
                     "Gegenkonto (ohne BU-Schlüssel)", "BU-Schlüssel", "Belegdatum", "Belegfeld 1", "Buchungstext",
                     "Steuersatz (Info)"])
    for b in sorted(rows, key=lambda x: (x.booking_date, x.receipt_number)):
        debit, credit = (b.contra_account, b.account) if b.direction == "income" else (b.account, b.contra_account)
        writer.writerow([format_cents(b.gross_cents).replace(".", ""), "S", "EUR", debit.number, credit.number, "",
                         b.booking_date.strftime("%d%m"), _csv_text(b.receipt_number[:36]),
                         _csv_text(b.description[:60]), f"{b.tax_rate} %"])
    _audit(db, "Client", client_id, "fibu_datev_draft_exported", actor, f"Jahr {year}: {len(rows)} Buchungen")
    db.commit()
    return buffer.getvalue(), len(rows)


# --- Aufgaben ----------------------------------------------------------------------------------------------------


def list_tasks(db: Session, client_id: str, *, include_done: bool = False) -> list[FibuTask]:
    query = db.query(FibuTask).filter(FibuTask.client_id == client_id)
    if not include_done:
        query = query.filter(FibuTask.status == "open")
    return query.order_by(FibuTask.status, FibuTask.due_date.is_(None), FibuTask.due_date, FibuTask.created_at).all()


def create_task(db: Session, client_id: str, *, title: str, due_date: str, actor: str) -> FibuTask:
    require_writable_client(db, client_id)
    title = (title or "").strip()
    if not title or len(title) > 200:
        raise FibuError("Bitte einen Aufgabentitel (höchstens 200 Zeichen) eingeben.", "title")
    due = None
    if (due_date or "").strip():
        try:
            due = date.fromisoformat(due_date.strip())
        except ValueError:
            raise FibuError("Bitte ein gültiges Fälligkeitsdatum eingeben.", "due_date") from None
    task = FibuTask(client_id=client_id, title=title, due_date=due, status="open", created_by=actor)
    db.add(task)
    db.flush()
    _audit(db, "FibuTask", task.id, "fibu_task_created", actor, f"Mandant {client_id}: {title[:120]}")
    db.commit()
    return task


def set_task_done(db: Session, client_id: str, task_id: str, *, done: bool, actor: str) -> FibuTask:
    require_writable_client(db, client_id)
    task = db.query(FibuTask).filter_by(id=task_id, client_id=client_id).first()
    if task is None:
        raise FibuNotFound("Aufgabe nicht gefunden.")
    task.status = "done" if done else "open"
    _audit(db, "FibuTask", task.id, "fibu_task_done" if done else "fibu_task_reopened", actor, task.title[:120])
    db.commit()
    return task


@dataclass
class DashboardData:
    kpis: Kpis
    recent: list[FibuBooking] = field(default_factory=list)
    tasks: list[FibuTask] = field(default_factory=list)
    unassigned_receipts: int = 0
