"""Test-Hilfen fuer Modul-Lizenzen: erzeugt zur Laufzeit ein Wegwerf-Schluesselpaar (kein Schluessel im Repository),
traegt den oeffentlichen Schluessel per monkeypatch als vertrauenswuerdig ein und signiert synthetische Lizenzen."""

from __future__ import annotations

import base64
from datetime import date, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.orm import Session

from app.modules import licensing
from scripts.lexono_license_tool import build_license


class LicenseFactory:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, trusted: bool = True) -> None:
        self.key = Ed25519PrivateKey.generate()
        raw = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.key_id = licensing.key_id_for(raw)
        if trusted:
            monkeypatch.setattr(licensing, "TRUSTED_PUBLIC_KEYS", {self.key_id: base64.b64encode(raw).decode()})

    def text(self, modules=("finanzen",), *, licensee: str = "QA Kanzlei (synthetisch)",
             valid_from: date | None = None, valid_until: date | None = None, days: int | None = 365) -> str:
        valid_from = valid_from or date.today() - timedelta(days=1)
        if valid_until is None and days is not None:
            valid_until = date.today() + timedelta(days=days)
        return build_license(self.key, licensee=licensee, modules=list(modules), valid_from=valid_from,
                             valid_until=valid_until)

    def activate(self, db: Session, modules=("finanzen",), **kw) -> licensing.LicenseStatus:
        return licensing.import_license(db, self.text(modules, **kw), actor="qa-test")
