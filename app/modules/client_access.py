"""Mandantenzugriff in den Zusatzmodulen (28.10.2026).

Regel (deny by default):
- Rollen mit ``PERM_CLIENT_MANAGE`` (Admin, Anwalt) haben Zugriff auf alle Mandanten - unveraendert zum Basispaket.
- Alle anderen Rollen (Mitarbeiter) nur auf Mandanten mit ausdruecklicher Zuweisung in ``client_user_access``.
- Inaktive Nutzer oder Nutzer ohne Rolle: kein Zugriff.

Gilt fuer Lexono Finanzen und Lexono Steuern. Das Basispaket (Mandanten, Akten, Chat) bleibt unveraendert.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.auth.permissions import PERM_CLIENT_MANAGE, has_permission
from app.models import AuditEvent, Client, User
from app.models.client_access import ClientUserAccess


def has_full_client_access(user: User) -> bool:
    return has_permission(user, PERM_CLIENT_MANAGE)


def can_access_client(db: Session, user: User, client_id: str | None) -> bool:
    if not client_id or user is None or not user.is_active or user.role is None:
        return False
    if has_full_client_access(user):
        return True
    return db.query(ClientUserAccess).filter_by(client_id=client_id, user_id=user.id).first() is not None


def accessible_client_ids(db: Session, user: User) -> set[str] | None:
    """None = alle Mandanten (volle Rechte); sonst die zugewiesenen IDs (ggf. leer)."""
    if has_full_client_access(user):
        return None
    return {row.client_id for row in db.query(ClientUserAccess).filter_by(user_id=user.id)}


def assigned_user_ids(db: Session, client_id: str) -> set[str]:
    return {row.user_id for row in db.query(ClientUserAccess).filter_by(client_id=client_id)}


class AccessChangeError(ValueError):
    pass


def set_staff_access(db: Session, client_id: str, user_ids: set[str], *, actor: User) -> tuple[set[str], set[str]]:
    """Setzt die Zuweisungen eines Mandanten auf genau ``user_ids`` (nur Nutzer OHNE vollen Zugriff, aktiv).
    Rueckgabe: (hinzugefuegt, entzogen). Nur fuer Rollen mit PERM_CLIENT_MANAGE."""
    if not has_full_client_access(actor):
        raise AccessChangeError("Keine Berechtigung, Mandantenzugriffe zu verwalten.")
    if db.get(Client, client_id) is None:
        raise AccessChangeError("Mandant nicht gefunden.")
    users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids))} if user_ids else {}
    invalid = [uid for uid in user_ids if uid not in users or not users[uid].is_active
               or has_full_client_access(users[uid])]
    if invalid:
        raise AccessChangeError("Zuweisbar sind nur aktive Nutzer ohne kanzleiweiten Zugriff (z. B. Mitarbeiter).")
    current = assigned_user_ids(db, client_id)
    added, removed = set(user_ids) - current, current - set(user_ids)
    for uid in added:
        db.add(ClientUserAccess(client_id=client_id, user_id=uid, granted_by=actor.email))
    if removed:
        db.query(ClientUserAccess).filter(ClientUserAccess.client_id == client_id,
                                          ClientUserAccess.user_id.in_(removed)).delete(synchronize_session=False)
    if added or removed:
        emails = {u.id: u.email for u in db.query(User).filter(User.id.in_(added | removed))}
        db.add(AuditEvent(entity_type="Client", entity_id=client_id, event_type="client_access_changed",
                          actor=actor.email[:64],
                          details=f"zugewiesen: {', '.join(sorted(emails[u] for u in added)) or '-'}; "
                                  f"entzogen: {', '.join(sorted(emails[u] for u in removed)) or '-'}"))
    db.commit()
    return added, removed
