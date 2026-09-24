"""AuthService (Login/Authentifizierung) und UserService (Nutzerverwaltung,
Admin-only) – Prompt 26.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.auth.security import hash_password, verify_password
from app.models import AuditEvent, Role, User


class AuthService:
    def authenticate(self, email: str, plain_password: str, db: Session) -> User | None:
        """Prüft E-Mail + Passwort. Gibt `None` zurück bei jedem Fehler
        (unbekannte E-Mail, falsches Passwort, deaktivierter Nutzer,
        Nutzer ohne Passwort-Hash) - bewusst KEIN unterschiedliches
        Verhalten je nach Fehlerursache (verhindert User-Enumeration).
        Schreibt in JEDEM Fall (Erfolg wie Fehlschlag) ein Audit-Event."""
        normalized_email = email.strip().lower()
        user = db.query(User).filter(User.email == normalized_email).first()

        if user is None or not user.is_active:
            db.add(
                AuditEvent(
                    entity_type="User",
                    entity_id=normalized_email,
                    event_type="login_failed",
                    actor=normalized_email,
                    details="Unbekannte E-Mail oder deaktivierter Nutzer",
                )
            )
            db.commit()
            return None

        if not verify_password(plain_password, user.password_hash):
            db.add(
                AuditEvent(
                    entity_type="User",
                    entity_id=user.id,
                    event_type="login_failed",
                    actor=normalized_email,
                    details="Falsches Passwort",
                )
            )
            db.commit()
            return None

        db.add(
            AuditEvent(
                entity_type="User",
                entity_id=user.id,
                event_type="login_succeeded",
                actor=user.email,
                details=None,
            )
        )
        db.commit()
        return user


class UserAlreadyExistsError(Exception):
    pass


class UserService:
    """Nutzerverwaltung - JEDE Methode hier ist ausschließlich für Admins
    gedacht (Berechtigungsprüfung erfolgt in der aufrufenden Router-Schicht
    über `require_role("admin")`, siehe app/auth/permissions.py - dieser
    Service selbst prüft keine Rollen, um Zuständigkeiten sauber zu
    trennen: WER etwas darf, entscheidet der Router; WAS die Aktion tut,
    dieser Service)."""

    def list_users(self, db: Session) -> list[User]:
        return db.query(User).order_by(User.email).all()

    def create_user(
        self,
        db: Session,
        *,
        email: str,
        role_name: str,
        actor: str,
        initial_password: str | None = None,
    ) -> tuple[User, str]:
        """Legt einen neuen Nutzer an. Gibt (User, initiales Klartext-
        Passwort) zurück - das Klartext-Passwort existiert NUR in diesem
        Rückgabewert für die einmalige Anzeige im Dashboard, wird an
        keiner Stelle geloggt oder gespeichert. `must_change_password`
        wird immer auf True gesetzt."""
        normalized_email = email.strip().lower()
        if db.query(User).filter(User.email == normalized_email).first() is not None:
            raise UserAlreadyExistsError(f"Nutzer {normalized_email} existiert bereits")

        role = db.query(Role).filter(Role.name == role_name).first()
        if role is None:
            raise ValueError(f"Rolle '{role_name}' existiert nicht")

        password = initial_password or secrets.token_urlsafe(16)
        user = User(
            email=normalized_email,
            role_id=role.id,
            is_active=True,
            password_hash=hash_password(password),
            must_change_password=True,
        )
        db.add(user)
        db.flush()

        db.add(
            AuditEvent(
                entity_type="User",
                entity_id=user.id,
                event_type="user_created",
                actor=actor,
                details=f"Nutzer {normalized_email} mit Rolle '{role_name}' angelegt",
            )
        )
        db.commit()
        db.refresh(user)
        return user, password

    def set_role(self, db: Session, user: User, role_name: str, *, actor: str) -> User:
        role = db.query(Role).filter(Role.name == role_name).first()
        if role is None:
            raise ValueError(f"Rolle '{role_name}' existiert nicht")
        old_role_name = user.role.name if user.role else None
        user.role_id = role.id
        db.add(
            AuditEvent(
                entity_type="User",
                entity_id=user.id,
                event_type="user_role_changed",
                actor=actor,
                details=f"Rolle geändert: {old_role_name} -> {role_name}",
            )
        )
        db.commit()
        db.refresh(user)
        return user

    def set_active(self, db: Session, user: User, is_active: bool, *, actor: str) -> User:
        user.is_active = is_active
        if not is_active:
            # Deaktivierung wirkt ohnehin sofort (jede Anfrage laedt den
            # Nutzer frisch, siehe app/auth/permissions.py), aber
            # `sessions_invalidated_after` zusaetzlich zu setzen macht die
            # Absicht explizit nachvollziehbar und ist unschaedlich.
            user.sessions_invalidated_after = datetime.now(timezone.utc)
        db.add(
            AuditEvent(
                entity_type="User",
                entity_id=user.id,
                event_type="user_activated" if is_active else "user_deactivated",
                actor=actor,
                details=None,
            )
        )
        db.commit()
        db.refresh(user)
        return user

    def force_logout(self, db: Session, user: User, *, actor: str) -> User:
        """Beendet ALLE aktuell laufenden Sessions eines Nutzers sofort,
        OHNE das Passwort zu ändern (Prompt 29) - z. B. bei einem
        gestohlenen/verlorenen Gerät, wenn eine Passwortänderung allein
        (noch) nicht nötig erscheint, aber Vorsicht geboten ist."""
        user.sessions_invalidated_after = datetime.now(timezone.utc)
        db.add(
            AuditEvent(
                entity_type="User",
                entity_id=user.id,
                event_type="sessions_force_logged_out",
                actor=actor,
                details=None,
            )
        )
        db.commit()
        db.refresh(user)
        return user

    def change_password(
        self, db: Session, user: User, new_password: str, *, actor: str
    ) -> User:
        """Setzt ein neues Passwort UND löscht `must_change_password`.
        `new_password` existiert nur für die Dauer dieses Aufrufs als
        Klartext - wird nicht geloggt (auch nicht im Audit-`details`).

        Widerruft zusätzlich ALLE bestehenden Sessions dieses Nutzers
        (`sessions_invalidated_after`, Prompt 29) - schließt die Lücke,
        dass ein bereits gestohlenes Session-Cookie eine Passwortänderung
        sonst unbeeindruckt überlebt hätte."""
        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        user.sessions_invalidated_after = datetime.now(timezone.utc)
        db.add(
            AuditEvent(
                entity_type="User",
                entity_id=user.id,
                event_type="password_changed",
                actor=actor,
                details=None,
            )
        )
        db.commit()
        db.refresh(user)
        return user

    def reset_password(self, db: Session, user: User, *, actor: str) -> tuple[User, str]:
        """Admin-ausgeloester Passwort-Reset fuer EINEN BELIEBIGEN Nutzer
        (19.09., echter Fund: `scripts/reset_admin_password.py` wirkt
        ausschließlich auf Admin-Konten - ein Anwalt/Mitarbeiter, der sein
        Passwort vergisst, hatte projektweit KEINEN Weg zurück ins Konto).
        Bewusst dieselbe Kombination aus zwei bereits bewährten Mustern,
        keine neue Architektur: das Zufallspasswort+Einmal-Anzeige-Muster
        aus `create_user` oben, kombiniert mit `must_change_password=True`
        + `sessions_invalidated_after` aus `reset_admin_password.py`
        (dort dieselbe Begründung: das gesetzte Passwort gilt nur für den
        nächsten Login, ein evtl. gestohlenes altes Session-Cookie wird
        ungültig). Gibt (User, Klartext-Passwort) zurück - existiert NUR
        für die einmalige Anzeige im Dashboard, wird nicht geloggt."""
        password = secrets.token_urlsafe(16)
        user.password_hash = hash_password(password)
        user.must_change_password = True
        user.sessions_invalidated_after = datetime.now(timezone.utc)
        db.add(
            AuditEvent(
                entity_type="User",
                entity_id=user.id,
                event_type="password_reset_by_admin",
                actor=actor,
                details=f"Passwort für {user.email} durch Admin zurückgesetzt",
            )
        )
        db.commit()
        db.refresh(user)
        return user, password
