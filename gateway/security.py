"""Kanzlei-Credential-Erzeugung/-Prüfung für den Gateway.

Wiederverwendung von `app.auth.security` (Argon2id, bereits geprüfte
Primitive für Nutzerpasswörter) statt einer zweiten Hash-Implementierung -
siehe ARCHITECTURE.md §70. Eine Kanzlei-Credential hat bewusst ein anderes,
klar unterscheidbares Format als ein echter Anthropic-API-Key (dieser
beginnt immer mit `sk-ant-`) - strukturell unmöglich, eine Kanzlei-
Credential mit einem Anthropic-Key zu verwechseln.
"""

from __future__ import annotations

import secrets

from app.auth.security import hash_password, verify_password

#: Praefix macht den Credential-Typ auf den ersten Blick erkennbar (z. B.
#: in einer .env-Datei oder einem Support-Ticket) - analog zu Stripes
#: "sk_live_"-Konvention, ohne mit einem echten Anthropic-Key (`sk-ant-`)
#: verwechselbar zu sein.
_SECRET_PREFIX = "lxg_secret_"


def generate_client_secret() -> str:
    """Erzeugt ein neues, kryptografisch zufälliges Kanzlei-Secret im
    Klartext - existiert nur für die einmalige Anzeige beim Anlegen einer
    Kanzlei-Credential, wird nirgendwo im Klartext gespeichert."""
    return f"{_SECRET_PREFIX}{secrets.token_urlsafe(32)}"


def hash_client_secret(plain_secret: str) -> str:
    return hash_password(plain_secret)


def verify_client_secret(plain_secret: str, secret_hash: str) -> bool:
    return verify_password(plain_secret, secret_hash)


def parse_bearer_credential(authorization_header: str | None) -> tuple[str, str] | None:
    """Zerlegt `Authorization: Bearer <client_id>:<secret>` in
    `(client_id, secret)`. Gibt `None` bei jedem strukturell ungültigen
    Header zurück (fehlender Header, falsches Schema, fehlender Trenner) -
    einheitliche Behandlung, kein Unterschied im Fehlerpfad zwischen
    "Header fehlt" und "Header falsch formatiert" (vermeidet unnötige
    Informationspreisgabe über den genauen Fehlergrund)."""
    if not authorization_header:
        return None
    scheme, _, token = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    client_id, sep, secret = token.partition(":")
    if not sep or not client_id or not secret:
        return None
    return client_id, secret
