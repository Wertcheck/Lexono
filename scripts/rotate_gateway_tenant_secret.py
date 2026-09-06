"""Rotiert das Secret einer bestehenden Kanzlei-Credential am Lexono-Gateway
(ARCHITECTURE.md §70).

    GATEWAY_TENANT_CLIENT_ID="<client_id>" python -m scripts.rotate_gateway_tenant_secret

WICHTIG: als Modul aufrufen ("python -m scripts.rotate_gateway_tenant_secret"),
NICHT als Datei - siehe Docstring von scripts/create_gateway_tenant.py für die
Begründung (sys.path/ModuleNotFoundError).

Duenner Wrapper um `gateway.tenant_admin.rotate_tenant_secret` (analog zu
`scripts/create_gateway_tenant.py`) - implementiert KEINE eigene
Rotationslogik, nur den CLI-Aufrufpunkt. Die `client_id` bleibt unveraendert,
nur das Secret wird neu erzeugt - das alte Secret ist ab sofort ungueltig.
Gibt das neue Klartext-Secret EINMALIG auf der Konsole aus, genau wie beim
Anlegen. Laeuft gegen die Gateway-eigene Datenbank (`gateway/db.py`), NICHT
gegen die Kanzlei-Anwendungsdatenbank."""

from __future__ import annotations

import os
import sys

from gateway.config import get_gateway_settings
from gateway.db import build_engine, build_session_factory, init_db
from gateway.tenant_admin import rotate_tenant_secret


def main() -> int:
    client_id = os.environ.get("GATEWAY_TENANT_CLIENT_ID")
    if not client_id:
        print(
            "FEHLER: Umgebungsvariable GATEWAY_TENANT_CLIENT_ID ist nicht gesetzt.",
            file=sys.stderr,
        )
        return 1

    settings = get_gateway_settings()
    engine = build_engine(settings)
    init_db(engine)
    db = build_session_factory(engine)()
    try:
        new_secret = rotate_tenant_secret(db, client_id=client_id)
    finally:
        db.close()

    if new_secret is None:
        print(f"FEHLER: Keine Kanzlei-Credential mit client_id={client_id!r} gefunden.", file=sys.stderr)
        return 1

    print(f"Secret rotiert: client_id={client_id}")
    print("Neues client_secret (wird NUR JETZT angezeigt, das alte ist ab sofort ungueltig):")
    print(f"    {new_secret}")
    print(
        "In der betroffenen Kanzlei-.env aktualisieren: LEXONO_GATEWAY_CLIENT_SECRET."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
