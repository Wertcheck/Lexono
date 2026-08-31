"""Verwaltung von Kanzlei-Credentials (Anlegen, Widerrufen, Rotieren).

Reine Funktionen ohne CLI-Rahmen - `scripts/create_gateway_tenant.py` ist
der tatsächliche Aufrufpunkt (analog zu `scripts/create_admin.py` für
Nutzer-Accounts der Kanzlei-Anwendung selbst)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from gateway.models import Tenant
from gateway.security import generate_client_secret, hash_client_secret


@dataclass
class NewTenantCredential:
    """Enthält das Klartext-Secret NUR für die einmalige Anzeige direkt
    nach dem Anlegen - wird nirgendwo gespeichert oder geloggt."""

    tenant_id: str
    client_id: str
    client_secret: str


def create_tenant(
    db: Session, *, display_name: str, rate_limit_per_minute: int
) -> NewTenantCredential:
    secret = generate_client_secret()
    tenant = Tenant(
        display_name=display_name,
        secret_hash=hash_client_secret(secret),
        rate_limit_per_minute=rate_limit_per_minute,
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return NewTenantCredential(
        tenant_id=tenant.id, client_id=tenant.client_id, client_secret=secret
    )


def revoke_tenant(db: Session, *, client_id: str) -> bool:
    from datetime import datetime, timezone

    tenant = db.query(Tenant).filter(Tenant.client_id == client_id).first()
    if tenant is None:
        return False
    tenant.is_active = False
    tenant.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return True


def rotate_tenant_secret(db: Session, *, client_id: str) -> str | None:
    """Erzeugt ein neues Secret für eine bestehende Kanzlei-Credential
    (gleiche `client_id`, altes Secret wird sofort ungültig) - gibt das
    neue Klartext-Secret zurück, oder `None`, wenn `client_id` unbekannt
    ist."""
    tenant = db.query(Tenant).filter(Tenant.client_id == client_id).first()
    if tenant is None:
        return None
    new_secret = generate_client_secret()
    tenant.secret_hash = hash_client_secret(new_secret)
    db.commit()
    return new_secret
