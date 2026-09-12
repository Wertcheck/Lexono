"""Recovery-Skript: setzt das Passwort eines BESTEHENDEN Admin-Nutzers neu
(für den Fall, dass das ursprünglich beim Setup vergebene Passwort verloren
gegangen ist - z. B. weil `Lexono.exe setup` nicht interaktiv lief und
das einmalig ausgegebene Zufallspasswort nirgendwo gesichert wurde).

Bewusst GETRENNT von `scripts/create_admin.py` (das legt NUR den allerersten
Admin an und bricht bei bereits vorhandenem Admin ab) - dieses Skript ist
der explizite Gegenpart für den "Admin existiert, Passwort ist unbekannt"-
Fall. Wird NIEMALS automatisch beim App-Start ausgeführt (anders als der
Auftrag es implizit nahelegte) - ein Passwort-Reset ist eine bewusste,
manuell ausgeloeste administrative Aktion, kein Nebeneffekt eines Neustarts
(sonst könnte jede Person mit Zugriff auf den Server-Prozess/-Start das
Admin-Passwort stillschweigend zurücksetzen).

Aufruf (installierte Anwendung, kein Python/keine Shell-Kenntnisse jenseits
dieses einen Befehls nötig - siehe run.py::cmd_reset_admin_password):

    set ADMIN_EMAIL=admin@kanzlei.de
    Lexono.exe reset-admin-password

Aufruf (Entwicklungsbetrieb, äquivalent):
    ADMIN_EMAIL=admin@kanzlei.de RESET_PASSWORD=<neues Passwort> \\
        python scripts/reset_admin_password.py

`RESET_PASSWORD` ist optional - fehlt es, wird (wie bei create_admin.py)
ein kryptografisch sicheres Zufallspasswort erzeugt und EINMALIG auf der
Konsole ausgegeben, nirgendwo sonst gespeichert oder geloggt. Das neue
Passwort landet NIE im Quellcode oder in einer Konfigurationsdatei.

Erzwingt `must_change_password=True`: das hier gesetzte Passwort ist NUR
für den nächsten Login gültig, direkt danach muss ein eigenes Passwort
vergeben werden - dieselbe Konvention wie beim initialen Admin
(scripts/create_admin.py). Widerruft zusätzlich alle bestehenden Sessions
dieses Nutzers (`sessions_invalidated_after`), falls ein gestohlenes/
veraltetes Session-Cookie im Umlauf sein sollte."""

from __future__ import annotations

import os
import secrets
import sys
from datetime import datetime, timezone

from app.auth.security import hash_password
from app.db.session import SessionLocal
from app.models import AuditEvent, User


def main() -> int:
    email = os.environ.get("ADMIN_EMAIL")
    if not email:
        print(
            "FEHLER: Umgebungsvariable ADMIN_EMAIL ist nicht gesetzt.\n"
            "Beispiel (installierte Anwendung): set ADMIN_EMAIL=admin@kanzlei.de "
            "&& Lexono.exe reset-admin-password",
            file=sys.stderr,
        )
        return 1

    password = os.environ.get("RESET_PASSWORD")
    generated = password is None
    if generated:
        password = secrets.token_urlsafe(18)

    db = SessionLocal()
    try:
        normalized_email = email.strip().lower()
        user = db.query(User).filter(User.email == normalized_email).first()
        if user is None:
            print(
                f"FEHLER: Kein Nutzer mit E-Mail '{normalized_email}' gefunden.",
                file=sys.stderr,
            )
            return 1
        if user.role is None or user.role.name.strip().lower() != "admin":
            print(
                f"FEHLER: '{normalized_email}' ist kein Admin-Nutzer - dieses "
                "Skript ist ausschließlich für die Admin-Wiederherstellung "
                "gedacht.",
                file=sys.stderr,
            )
            return 1

        user.password_hash = hash_password(password)
        user.must_change_password = True
        user.is_active = True
        user.sessions_invalidated_after = datetime.now(timezone.utc)
        db.add(
            AuditEvent(
                entity_type="User",
                entity_id=user.id,
                event_type="admin_password_reset_via_script",
                actor="cli:reset_admin_password",
                details=f"Passwort für {normalized_email} über Recovery-Skript zurückgesetzt",
            )
        )
        db.commit()

        print(f"Passwort zurückgesetzt für: {normalized_email}")
        if generated:
            print("Generiertes Passwort (wird NUR JETZT angezeigt):")
            print(f"  {password}")
        print(
            "Beim nächsten Login wird eine Passwortänderung erzwungen "
            "(must_change_password=True). Alle bestehenden Sessions dieses "
            "Nutzers wurden widerrufen."
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
