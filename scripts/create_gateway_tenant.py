"""Legt eine neue Kanzlei-Credential am Lexono-Gateway an (ARCHITECTURE.md §70).

    GATEWAY_TENANT_NAME="Kanzlei Mustermann" python scripts/create_gateway_tenant.py

Liest den Anzeigenamen aus `GATEWAY_TENANT_NAME` (Pflicht) und optional
`GATEWAY_TENANT_RATE_LIMIT` (Default: `default_rate_limit_per_minute` aus
`.env.gateway`). Gibt `client_id` und das Klartext-Secret EINMALIG auf der
Konsole aus - genau wie `scripts/create_admin.py` für Kanzlei-Nutzer-
Accounts. Läuft gegen die Gateway-eigene Datenbank (`gateway/db.py`),
NICHT gegen die Kanzlei-Anwendungsdatenbank."""

from __future__ import annotations

import os
import sys

from gateway.config import get_gateway_settings
from gateway.db import build_engine, build_session_factory, init_db
from gateway.tenant_admin import create_tenant


def main() -> int:
    display_name = os.environ.get("GATEWAY_TENANT_NAME")
    if not display_name:
        print(
            "FEHLER: Umgebungsvariable GATEWAY_TENANT_NAME ist nicht gesetzt.",
            file=sys.stderr,
        )
        return 1

    settings = get_gateway_settings()
    rate_limit = int(
        os.environ.get("GATEWAY_TENANT_RATE_LIMIT", settings.default_rate_limit_per_minute)
    )

    engine = build_engine(settings)
    init_db(engine)
    db = build_session_factory(engine)()
    try:
        credential = create_tenant(
            db, display_name=display_name, rate_limit_per_minute=rate_limit
        )
    finally:
        db.close()

    print(f"Kanzlei-Credential angelegt: {display_name}")
    print(f"  client_id:     {credential.client_id}")
    print("  client_secret (wird NUR JETZT angezeigt):")
    print(f"    {credential.client_secret}")
    print(
        "In der Kanzlei-.env eintragen: LEXONO_GATEWAY_CLIENT_ID und "
        "LEXONO_GATEWAY_CLIENT_SECRET."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
