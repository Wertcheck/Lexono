"""User – Benutzer.

Passwort-Handling (Prompt 26): `password_hash` speichert AUSSCHLIESSLICH
einen Argon2-Hash (siehe app/auth/security.py) - an keiner Stelle im
Projekt wird ein Klartext-Passwort persistiert oder geloggt. Nullable,
weil ein Nutzer OHNE Hash sich schlicht nicht anmelden kann (siehe
AuthService.authenticate) - technisch nullable gehalten, um zukünftige
SSO-/Passkey-Nutzer ohne lokales Passwort nicht von vornherein
auszuschließen.

Session-Widerruf (Prompt 29, Nachtrag zum Security Review Prompt 27):
`is_active=False` wirkt bereits SOFORT (jede Anfrage lädt den Nutzer neu
aus der DB, siehe app/auth/permissions.py: `_load_user_from_session`) -
KEINE Lücke. Die tatsächliche Lücke war enger: ein gestohlenes
Session-Cookie überlebte bislang eine Passwortänderung unverändert, da
das Token nur die Nutzer-ID trägt, keinen Passwort-Stand.
`sessions_invalidated_after` schließt das: bei jeder Passwortänderung
(und optional per Admin-Aktion "Sessions beenden") auf "jetzt" gesetzt -
jedes Session-Token, das VOR diesem Zeitpunkt ausgestellt wurde, gilt ab
sofort als ungültig (siehe app/auth/permissions.py).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role_id: Mapped[str | None] = mapped_column(ForeignKey("roles.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Erzwingt eine Passwortänderung vor dem nächsten Dashboard-Zugriff -
    # insbesondere für den initialen Admin (siehe scripts/create_admin.py)
    # und für von einem Admin neu angelegte Nutzer.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # Nullable: None = noch nie zurückgesetzt, alle bestehenden Sessions
    # bleiben bis zu ihrem natürlichen Ablauf (8h) gültig.
    sessions_invalidated_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # PIN-Sperre / App-Lock (Schritt 3, Teil 2): eigener, KURZER Argon2-Hash
    # (app/auth/pin_lock.py) - bewusst GETRENNT von `password_hash`, da eine
    # PIN ein schwächeres, rein für "kurz weg vom Schreibtisch" gedachtes
    # Geheimnis ist, kein Ersatz für das eigentliche Passwort. `None` = keine
    # PIN eingerichtet - die Sperrfunktion ist dann inaktiv (siehe
    # app/web/lock_router.py: ohne PIN gäbe es keinen Weg, sich wieder zu
    # entsperren).
    pin_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Wird durch manuelle Sperre ODER den clientseitigen Inaktivitäts-Timer
    # gesetzt; `require_login` (app/auth/permissions.py) blockiert JEDEN
    # Dashboard-Zugriff (außer der Entsperr-Seite selbst), solange dieser
    # Wert True ist - bewusst am Nutzer, nicht an der einzelnen Session
    # hängend, damit "sperren" wirklich alle offenen Tabs/Geräte dieses
    # Nutzers gleichzeitig sperrt (Bedrohungsmodell: fremde Person am
    # Schreibtisch, nicht Multi-Device-Isolation).
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # §62: nur die INFORMATIVE Meldung "automatisch freigegeben" - pro Benutzer ausblendbar; Warnungen, Blockaden und
    # erforderliche Freigaben sind davon nie betroffen (app/documents/release.py::show_auto_release_notice)
    show_auto_release_notice: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default="1")

    role: Mapped["Role | None"] = relationship(back_populates="users")
