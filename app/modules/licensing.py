"""Zentrale Modul-Freischaltung ueber signierte Lizenzen (Ed25519), 28.10.2026.

Lizenzformat (eine Textdatei, z. B. ``kanzlei.lexono-license``)::

    {"format": 1, "key_id": "<16 hex>", "payload": "<base64url(JSON)>", "signature": "<base64url(Ed25519)>"}

Payload-JSON: ``license_id``, ``licensee`` (Kanzlei), ``modules`` (Liste aus app/modules/catalog.LICENSED_MODULES),
``valid_from``/``valid_until`` (ISO-Datum, ``valid_until`` optional), ``issued_at``.

Sicherheitsprinzipien:
- Lexono enthaelt NUR oeffentliche Schluessel (``TRUSTED_PUBLIC_KEYS``). Der private Schluessel liegt beim Hersteller
  und wird mit ``scripts/lexono_license_tool.py`` verwendet - niemals im Repository, in Logs oder in der App.
- ``TRUSTED_PUBLIC_KEYS`` ist derzeit LEER: Der produktive Herstellerschluessel ist noch nicht erzeugt (Owner-
  Entscheidung 28.10.2026). Bis dahin ist in echten Installationen kein Zusatzmodul freischaltbar - fail-closed.
- Jede Pruefung ist fail-closed: unbekannter Schluessel, falsche Signatur, kaputtes Format, abgelaufene oder noch
  nicht gueltige Lizenz, unbekannte Module -> das Modul gilt als NICHT freigeschaltet.
- Das Basispaket ("kanzlei") braucht keine Lizenz und haengt von keinem Zusatzmodul ab.
- Ablauf/Entfernen einer Lizenz loescht keine Daten; das Modul ist dann nur gesperrt.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import AuditEvent
from app.models.module_license import ModuleLicense
from app.modules.catalog import BASE_MODULES, CATALOG, LICENSED_MODULES

#: key_id -> roher Ed25519-Public-Key (32 Byte, base64). Leer, bis der Owner das produktive Schluesselpaar erzeugt
#: hat (Anleitung: MODULARCHITEKTUR.md). Tests setzen hier per monkeypatch einen Testschluessel.
TRUSTED_PUBLIC_KEYS: dict[str, str] = {}

MAX_LICENSE_BYTES = 16 * 1024


def key_id_for(public_key_raw: bytes) -> str:
    return hashlib.sha256(public_key_raw).hexdigest()[:16]


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass
class LicenseStatus:
    valid: bool
    reason: str
    modules: frozenset[str] = frozenset()
    licensee: str = ""
    license_id: str = ""
    valid_from: date | None = None
    valid_until: date | None = None
    unknown_modules: list[str] = field(default_factory=list)


def verify_license_text(text: str, *, today: date | None = None) -> LicenseStatus:
    """Prueft eine Lizenz vollstaendig. Gibt immer einen Status zurueck (nie eine Ausnahme) - fail-closed."""
    today = today or date.today()
    if not text or len(text.encode("utf-8")) > MAX_LICENSE_BYTES:
        return LicenseStatus(False, "Die Lizenzdatei ist leer oder zu groß.")
    try:
        envelope = json.loads(text)
        if envelope.get("format") != 1:
            return LicenseStatus(False, "Unbekanntes Lizenzformat.")
        key_b64 = TRUSTED_PUBLIC_KEYS.get(str(envelope.get("key_id", "")))
        if not key_b64:
            return LicenseStatus(False, "Die Lizenz ist nicht mit einem bekannten Lexono-Herstellerschlüssel signiert.")
        payload_bytes = _b64decode(str(envelope["payload"]))
        Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64)).verify(_b64decode(str(envelope["signature"])),
                                                                              payload_bytes)
        payload = json.loads(payload_bytes.decode("utf-8"))
    except InvalidSignature:
        return LicenseStatus(False, "Die Signatur der Lizenz ist ungültig (Datei verändert oder nicht vom Hersteller).")
    except (ValueError, KeyError, TypeError, AttributeError, binascii.Error):
        return LicenseStatus(False, "Die Lizenzdatei ist beschädigt oder unvollständig.")
    try:
        valid_from = date.fromisoformat(payload["valid_from"])
        valid_until = date.fromisoformat(payload["valid_until"]) if payload.get("valid_until") else None
        requested = [str(m) for m in payload["modules"]]
        licensee, license_id = str(payload["licensee"]), str(payload["license_id"])
    except (ValueError, KeyError, TypeError):
        return LicenseStatus(False, "Die Lizenzdaten sind unvollständig.")
    unknown = [m for m in requested if m not in LICENSED_MODULES]
    base = dict(licensee=licensee, license_id=license_id, valid_from=valid_from, valid_until=valid_until,
                unknown_modules=unknown)
    if today < valid_from:
        return LicenseStatus(False, f"Die Lizenz gilt erst ab {valid_from.strftime('%d.%m.%Y')}.", **base)
    if valid_until and today > valid_until:
        return LicenseStatus(False, f"Die Lizenz ist am {valid_until.strftime('%d.%m.%Y')} abgelaufen.", **base)
    modules = frozenset(m for m in requested if m in LICENSED_MODULES)
    if not modules:
        return LicenseStatus(False, "Die Lizenz enthält kein bekanntes Zusatzmodul.", **base)
    return LicenseStatus(True, "Gültig", modules=modules, **base)


def active_license(db: Session) -> ModuleLicense | None:
    return (db.query(ModuleLicense).filter(ModuleLicense.is_active.is_(True))
            .order_by(ModuleLicense.created_at.desc()).first())


def current_status(db: Session, *, today: date | None = None) -> LicenseStatus:
    row = active_license(db)
    if row is None:
        return LicenseStatus(False, "Keine Lizenz für Zusatzmodule importiert.")
    return verify_license_text(row.license_text, today=today)


def enabled_modules(db: Session, *, today: date | None = None) -> frozenset[str]:
    status = current_status(db, today=today)
    return BASE_MODULES | (status.modules if status.valid else frozenset())


def is_enabled(db: Session, module: str) -> bool:
    if module not in CATALOG:
        return False
    return module in enabled_modules(db)


class LicenseImportError(ValueError):
    pass


def import_license(db: Session, text: str, *, actor: str) -> LicenseStatus:
    """Nur eine GUELTIGE Lizenz wird uebernommen; sie ersetzt die bisherige (alte Zeile bleibt inaktiv erhalten)."""
    text = (text or "").strip()
    status = verify_license_text(text)
    if not status.valid:
        raise LicenseImportError(status.reason)
    for row in db.query(ModuleLicense).filter(ModuleLicense.is_active.is_(True)):
        row.is_active = False
    row = ModuleLicense(license_text=text, license_id=status.license_id[:64], is_active=True, imported_by=actor)
    db.add(row)
    db.flush()
    db.add(AuditEvent(entity_type="ModuleLicense", entity_id=row.id, event_type="module_license_imported",
                      actor=actor[:64], details=f"Lizenz {status.license_id} für {status.licensee}: "
                      f"{', '.join(sorted(status.modules))}; gültig bis "
                      f"{status.valid_until.isoformat() if status.valid_until else 'unbefristet'}"))
    db.commit()
    return status


def remove_license(db: Session, *, actor: str) -> bool:
    row = active_license(db)
    if row is None:
        return False
    row.is_active = False
    db.add(AuditEvent(entity_type="ModuleLicense", entity_id=row.id, event_type="module_license_removed",
                      actor=actor[:64], details=f"Lizenz {row.license_id or '?'} deaktiviert (Daten bleiben erhalten)"))
    db.commit()
    return True


class ModuleLockedError(HTTPException):
    """Zusatzmodul nicht freigeschaltet - 403; app/main.py rendert fuer Dashboard-Seiten eine verstaendliche Seite."""

    def __init__(self, module: str) -> None:
        super().__init__(status_code=403, detail=f"Das Modul „{CATALOG[module].name}“ ist nicht freigeschaltet.")
        self.module = module


def require_module(module: str):
    """Router-/Endpunkt-Dependency: serverseitige Sperre eines Zusatzmoduls (unabhaengig von der Navigation)."""
    if module not in CATALOG:
        raise ValueError(f"Unbekanntes Modul: {module}")

    from app.auth.permissions import require_login  # lokal: vermeidet Importzyklus beim App-Start

    def dependency(request: Request, db: Session = Depends(get_db), _user=Depends(require_login)) -> None:
        # Anmeldung zuerst (Nicht-Angemeldete -> Login), danach die Modulsperre fuer ALLE Rollen
        if not is_enabled(db, module):
            # Rolle jetzt laden: der Fehlerhandler (app/main.py) rendert erst NACH dem Schliessen der DB-Sitzung
            request.state.locked_user_is_admin = bool(_user.role and _user.role.name.strip().lower() == "admin")
            raise ModuleLockedError(module)

    return dependency


def ensure_module(db: Session, module: str) -> None:
    """Dieselbe Sperre fuer Service-Funktionen (z. B. Steuern ohne eigene Web-Routen)."""
    if not is_enabled(db, module):
        raise ModuleLockedError(module)
