"""Widerruft eine bestehende Kanzlei-Credential am Lexono-Gateway (ARCHITECTURE.md §70).

    GATEWAY_TENANT_CLIENT_ID="<client_id>" python -m scripts.revoke_gateway_tenant

WICHTIG: als Modul aufrufen ("python -m scripts.revoke_gateway_tenant"), NICHT
als Datei - siehe Docstring von scripts/create_gateway_tenant.py für die
Begründung (sys.path/ModuleNotFoundError).

Duenner Wrapper um `gateway.tenant_admin.revoke_tenant` (analog zu
`scripts/create_gateway_tenant.py`) - implementiert KEINE eigene
Widerrufslogik, nur den CLI-Aufrufpunkt. Setzt `Tenant.is_active=False` +
`revoked_at` sofort; alle anderen Kanzlei-Credentials und der zentrale
Anthropic-Key bleiben unberuehrt. Laeuft gegen die Gateway-eigene
Datenbank (`gateway/db.py`), NICHT gegen die Kanzlei-Anwendungsdatenbank."""

from __future__ import annotations

import os
import sys

from gateway.config import get_gateway_settings
from gateway.db import build_engine, build_session_factory, init_db
from gateway.tenant_admin import revoke_tenant


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
        revoked = revoke_tenant(db, client_id=client_id)
    finally:
        db.close()

    if not revoked:
        print(f"FEHLER: Keine Kanzlei-Credential mit client_id={client_id!r} gefunden.", file=sys.stderr)
        return 1

    print(f"Kanzlei-Credential widerrufen: client_id={client_id}")
    print("Diese Credential kann ab sofort keine Anfragen mehr am Gateway authentifizieren.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
