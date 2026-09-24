"""Unit-Tests für app/auth/security.py und app/auth/session.py (Prompt 26).

Deckt aus der geforderten Testliste insbesondere ab:
- #17 Session läuft nach Ablauf ab
- #18 Passwort wird niemals als Klartext gespeichert
"""

from __future__ import annotations

import time

import pytest

from app.auth.security import hash_password, needs_rehash, verify_password
from app.auth.session import create_session_token, read_session_token
from app.config import Settings


def _dev_settings(**overrides) -> Settings:
    return Settings(app_env="development", **overrides)


# --- Passwort-Hashing (#18) ---


def test_hash_password_never_equals_plaintext() -> None:
    hashed = hash_password("MeinSicheresPasswort123")
    assert hashed != "MeinSicheresPasswort123"
    assert "MeinSicheresPasswort123" not in hashed


def test_hash_password_produces_argon2_hash() -> None:
    hashed = hash_password("MeinSicheresPasswort123")
    assert hashed.startswith("$argon2")


def test_hash_password_is_salted_different_each_time() -> None:
    """Zwei Hashes desselben Passworts müssen sich unterscheiden (Salt) -
    verhindert, dass gleiche Passwörter an gleichen Hashes erkennbar sind."""
    h1 = hash_password("MeinSicheresPasswort123")
    h2 = hash_password("MeinSicheresPasswort123")
    assert h1 != h2


def test_verify_password_accepts_correct_password() -> None:
    hashed = hash_password("MeinSicheresPasswort123")
    assert verify_password("MeinSicheresPasswort123", hashed) is True


def test_verify_password_rejects_wrong_password() -> None:
    hashed = hash_password("MeinSicheresPasswort123")
    assert verify_password("FalschesPasswort", hashed) is False


def test_verify_password_rejects_none_hash_without_crashing() -> None:
    """Ein Nutzer ohne password_hash (z. B. zukünftiger SSO-Nutzer) darf
    NIE zu einem Absturz führen - immer sauber False."""
    assert verify_password("irgendwas", None) is False


def test_verify_password_rejects_malformed_hash_without_crashing() -> None:
    assert verify_password("irgendwas", "kein-echter-argon2-hash") is False


def test_hash_password_rejects_empty_password() -> None:
    with pytest.raises(ValueError):
        hash_password("")


def test_needs_rehash_false_for_freshly_created_hash() -> None:
    hashed = hash_password("MeinSicheresPasswort123")
    assert needs_rehash(hashed) is False


# --- Session-Tokens (#17) ---


def test_session_token_roundtrip_returns_correct_user_id() -> None:
    settings = _dev_settings()
    token, csrf = create_session_token("user-123", settings)

    payload = read_session_token(token, settings)

    assert payload is not None
    assert payload["user_id"] == "user-123"
    assert payload["csrf"] == csrf


def test_session_token_rejects_tampered_signature() -> None:
    settings = _dev_settings()
    token, _csrf = create_session_token("user-123", settings)
    tampered = token[:-4] + ("0" * 4)

    assert read_session_token(tampered, settings) is None


def test_session_expires_after_max_age() -> None:
    """Kern der Anforderung #17: eine Session, die älter als
    `session_max_age_seconds` ist, wird als ungültig behandelt - geprüft
    über eine sehr kleine max_age (0 Sekunden), damit der Test nicht 8
    Stunden warten muss."""
    settings = _dev_settings(session_max_age_seconds=1)
    token, _csrf = create_session_token("user-123", settings)

    time.sleep(1.2)

    expired_settings = _dev_settings(session_max_age_seconds=1)
    assert read_session_token(token, expired_settings) is None


def test_session_still_valid_within_max_age() -> None:
    settings = _dev_settings(session_max_age_seconds=3600)
    token, _csrf = create_session_token("user-123", settings)

    assert read_session_token(token, settings) is not None


def test_each_session_gets_a_different_csrf_token() -> None:
    settings = _dev_settings()
    _token1, csrf1 = create_session_token("user-123", settings)
    _token2, csrf2 = create_session_token("user-123", settings)
    assert csrf1 != csrf2


def test_read_session_token_rejects_garbage_input() -> None:
    settings = _dev_settings()
    assert read_session_token("völlig-ungültiges-token", settings) is None


# --- `issued_at`-Praezision (24.09., ECHTER FUND) ---
#
# itsdangerous' EIGENE Signaturzeit ist nur sekundengenau - verglichen mit
# dem mikrosekundengenauen `User.sessions_invalidated_after`
# (app/auth/service.py) fuehrte das dazu, dass eine Anmeldung INNERHALB
# DERSELBEN Sekunde wie eine vorangegangene Passwortaenderung faelschlich
# sofort wieder abgemeldet wurde (real gegen die installierte Anwendung
# reproduziert, siehe DECISIONS.md und
# tests/test_rate_limiting_and_session_revocation.py fuer den vollen
# End-to-End-Beweis). Fix: `issued_at` ist jetzt ein eigenes,
# mikrosekundengenaues Feld im signierten Payload selbst.


def test_session_token_issued_at_is_a_timezone_aware_datetime() -> None:
    settings = _dev_settings()
    token, _csrf = create_session_token("user-123", settings)

    payload = read_session_token(token, settings)

    assert payload is not None
    assert payload["issued_at"].tzinfo is not None


def test_session_token_issued_at_reflects_the_real_creation_instant_not_a_truncated_one() -> None:
    """Der eigentliche Kern des Fixes: `issued_at` muss der tatsaechliche,
    mikrosekundengenaue Erzeugungszeitpunkt sein - NICHT itsdangerous'
    eigene, nur sekundengenaue Signaturzeit. Geprueft durch einen fixierten
    Zeitpunkt mit einer Mikrosekunde ungleich 0: mit der alten Implemen-
    tierung (itsdangerous-Signaturzeit) waere dieser Wert immer auf 0
    abgeschnitten worden."""
    from unittest.mock import patch

    from app.auth import session as session_module

    fixed_instant = session_module.datetime(2027, 1, 1, 12, 0, 0, 654_321, tzinfo=session_module.timezone.utc)
    with patch.object(session_module, "datetime") as mock_datetime:
        mock_datetime.now.return_value = fixed_instant
        settings = _dev_settings()
        token, _csrf = create_session_token("user-123", settings)

    payload = read_session_token(token, settings)

    assert payload is not None
    assert payload["issued_at"] == fixed_instant


def test_read_session_token_falls_back_to_coarse_timestamp_for_pre_fix_tokens() -> None:
    """Ein VOR diesem Fix ausgestelltes Token (kein `issued_at`-Feld im
    Payload) darf nicht abgelehnt werden - dasselbe etablierte
    Rueckwaertskompatibilitaets-Prinzip wie bei der KanzleiAI→Lexono-
    Umbenennung (siehe session.py-Kommentar): ein bestehendes Cookie
    bleibt nutzbar, nur mit der alten, groeberen Genauigkeit, bis zum
    naechsten eigenen Neu-Login."""
    from app.auth.session import _serializer

    settings = _dev_settings()
    old_format_token = _serializer(settings).dumps({"user_id": "user-123", "csrf": "abc"})

    payload = read_session_token(old_format_token, settings)

    assert payload is not None
    assert payload["user_id"] == "user-123"
    assert payload["issued_at"] is not None
