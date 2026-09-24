"""Signierte, zeitgestempelte Session-Cookies (Prompt 26).

Bewusst KEIN Server-seitiger Session-Store (keine zusätzliche Tabelle
"sessions") - der Cookie-Inhalt selbst trägt die Nutzer-ID und ein
signiertes Ausstellungsdatum (`itsdangerous.URLSafeTimedSerializer`).
Das Ablaufen wird beim VERIFIZIEREN geprüft (`max_age`), nicht nur über
das Cookie-`Max-Age`-Attribut im Browser - ein manuell verlängertes/
manipuliertes Cookie schlägt an der Signaturprüfung fehl, ein technisch
gültiges, aber zu altes Cookie schlägt an der `max_age`-Prüfung fehl.

Der Session-Payload enthält zusätzlich einen zufälligen CSRF-Token (siehe
app/auth/permissions.py: `verify_csrf`) - an die Session gebunden, ändert
sich bei jedem neuen Login.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import Settings

# Produktidentitaets-Bereinigung (KanzleiAI -> Lexono): Nebeneffekt einer
# Aenderung hier ist, dass jede bereits aktive Session beim naechsten
# Update einmalig neu angemeldet werden muss (das alte Cookie wird nicht
# mehr erkannt bzw. die Signatur nicht mehr validiert) - akzeptiert, kein
# Datenverlust, nur eine einmalige erneute Anmeldung mit dem bereits
# bekannten Passwort.
SESSION_COOKIE_NAME = "lexono_session"
_SALT = "lexono-session-v1"


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.resolved_session_secret_key, salt=_SALT)


def create_session_token(user_id: str, settings: Settings) -> tuple[str, str]:
    """Erzeugt ein neues, signiertes Session-Token für `user_id`.

    Gibt (token, csrf_token) zurück - `csrf_token` ist Teil des
    signierten Payloads UND wird separat zurückgegeben, damit der
    aufrufende Login-Handler ihn direkt in die Antwort (z. B. Redirect-
    Kontext) einbetten kann, ohne das Token erneut zu entschlüsseln.

    `issued_at` wird als eigenes, MIKROSEKUNDENGENAUES Feld im Payload
    selbst mitgeschickt (ISO-8601, UTC) - ECHTER FUND (24.09., beim Live-
    E2E-Test von Passwortaenderung + sofortigem Neu-Login reproduziert,
    siehe DECISIONS.md): itsdangerous' eigene, in `read_session_token`
    frueher dafuer genutzte Signaturzeit ist NUR sekundengenau. Verglichen
    mit dem mikrosekundengenauen `User.sessions_invalidated_after`
    (`datetime.now(timezone.utc)`, app/auth/service.py) fuehrte das bei
    einer Anmeldung INNERHALB DERSELBEN Sekunde wie eine vorangegangene
    Passwortaenderung dazu, dass das neue, korrekte Token faelschlich als
    "davor ausgestellt" verworfen wurde. Der Client kann `issued_at`
    trotzdem nicht faelschen - der gesamte Payload ist signiert, ein
    manipulierter Wert macht die Signatur ungueltig."""
    csrf_token = secrets.token_urlsafe(32)
    token = _serializer(settings).dumps(
        {
            "user_id": user_id,
            "csrf": csrf_token,
            "issued_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return token, csrf_token


def read_session_token(token: str, settings: Settings) -> dict | None:
    """Liest und verifiziert ein Session-Token.

    Gibt `None` zurück bei fehlender/falscher Signatur ODER abgelaufener
    Session (älter als `session_max_age_seconds`) - beide Fälle werden
    hier bewusst gleich behandelt (kein Unterschied für den Aufrufer, der
    ohnehin nur "eingeloggt oder nicht" braucht).

    `payload["issued_at"]` ist ein `datetime` (UTC, mikrosekundengenau) -
    Grundlage für den Session-Widerruf bei Passwortänderung (siehe
    app/auth/permissions.py: `_load_user_from_session`). Für ein VOR
    diesem Fix ausgestelltes Token (fehlendes `issued_at`-Feld im Payload)
    wird auf itsdangerous' eigene, nur sekundengenaue Signaturzeit
    zurückgefallen - dasselbe, bereits etablierte Muster wie bei der
    KanzleiAI→Lexono-Umbenennung oben: ein bestehendes Cookie bleibt
    nutzbar, kein erzwungener Neu-Login nötig, nur mit der alten,
    gröberen Genauigkeit bis zum nächsten eigenen Neu-Login."""
    try:
        payload, coarse_timestamp = _serializer(settings).loads(
            token, max_age=settings.session_max_age_seconds, return_timestamp=True
        )
    except (BadSignature, SignatureExpired):
        return None
    if "issued_at" in payload:
        payload["issued_at"] = datetime.fromisoformat(payload["issued_at"])
    else:
        payload["issued_at"] = coarse_timestamp
    return payload
