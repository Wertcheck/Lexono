"""Lexono-Gateway - FastAPI-Anwendung (ARCHITECTURE.md §70).

Ein einziger funktionaler Endpunkt (`POST /v1/relay/messages`) plus
`/health`. Läuft als eigener Prozess, eigene Infrastruktur, eigenes
`.env.gateway` - siehe gateway/__init__.py."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy.orm import Session

from gateway.config import GatewaySettings, get_gateway_settings
from gateway.db import build_engine, build_session_factory, init_db, iter_session
from gateway.logging_utils import log_relay_request
from gateway.models import Tenant
from gateway.rate_limiter import SlidingWindowRateLimiter
from gateway.relay import RelayError, call_anthropic
from gateway.schemas import RelayRequest, RelayResponse
from gateway.security import hash_client_secret, parse_bearer_credential, verify_client_secret

_rate_limiter = SlidingWindowRateLimiter()

# Release-Review-Befund: Timing-Seitenkanal in require_tenant(). Ohne diesen
# Dummy-Hash kehrte die Funktion bei unbekannter/deaktivierter client_id
# SOFORT zurueck (nur ein DB-Lookup, keine Argon2-Pruefung), waehrend eine
# bekannte, aktive client_id mit falschem Secret zusaetzlich einen echten
# Argon2id-Verify durchlief (~60-70ms auf typischer Server-Hardware, siehe
# Review-Notiz) - dieser Laufzeitunterschied laesst sich messen und erlaubt,
# gueltige/aktive client_id-Werte zu enumerieren, OHNE das zugehoerige
# Secret zu kennen (CWE-208). client_id ist zwar bewusst kein Geheimnis fuer
# sich allein (siehe gateway/models.py), aber die Existenz/den Aktivstatus
# einer Kanzlei-Installation sollte ein nicht authentifizierter Aufrufer
# trotzdem nicht per Zeitmessung erfahren koennen. Fixer, einmalig beim
# Modul-Import berechneter Dummy-Hash (kein echtes Secret, keine
# Persistenz) - wird im "Tenant nicht gefunden"-Pfad gegengeprueft, um dort
# dieselbe Argon2-Laufzeit wie im echten Pruefpfad zu erzeugen.
_DUMMY_SECRET_HASH = hash_client_secret("lxg_secret_dummy-value-fuer-timing-schutz-nur")


@lru_cache
def _engine():
    return build_engine(get_gateway_settings())


@lru_cache
def _session_factory():
    return build_session_factory(_engine())


def get_db() -> Session:
    yield from iter_session(_session_factory())


def _configure_logging(settings: GatewaySettings) -> None:
    """Release-Review-Befund (01.09.): OHNE diese Konfiguration erreichen die
    INFO-Log-Zeilen aus `log_relay_request` (request_id/tenant_id/duration_ms/
    status/error_category/input_tokens/output_tokens) NIE einen Handler -
    Pythons Root-Logger hat ohne explizite Konfiguration Level WARNING und
    keine Handler, `.info(...)`-Aufrufe werden dann still verworfen. Empirisch
    bestaetigt: in einem so gestarteten Prozess zeigte `journalctl` bisher NUR
    uvicorns eigene Access-Log-Zeilen ("POST ... 200 OK"), nie die
    strukturierten Zeilen aus `gateway/logging_utils.py` - die gesamte
    Nutzungs-/Audit-Logging-Funktionalitaet war dadurch faktisch unsichtbar.
    `force=True` sorgt fuer deterministisches Verhalten unabhaengig davon, ob
    zuvor bereits (z. B. durch ein aufrufendes Test-/Entwicklungs-Tool) andere
    Root-Handler gesetzt wurden - dieses Modul ist der Prozesseinstiegspunkt
    des Gateway, eine eigene, vollstaendige Kontrolle ueber die Logging-
    Konfiguration ist hier sachgerecht."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _configure_logging(get_gateway_settings())
    init_db(_engine())
    yield


app = FastAPI(
    title="Lexono Gateway",
    lifespan=lifespan,
    # Release-Review-Befund: ein minimaler, ausschliesslich fuer
    # authentifizierte Kanzlei-Installationen bestimmter Relay braucht keine
    # oeffentlich erreichbare Swagger-UI/OpenAPI-Schema-Exposition -
    # unnoetige Angriffsflaeche/Informationspreisgabe ueber die interne
    # API-Struktur ohne fachlichen Nutzen fuer diesen Dienst.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def _unauthorized() -> HTTPException:
    # Bewusst dieselbe generische Meldung fuer JEDEN Authentifizierungs-
    # fehler (fehlender Header, unbekannte client_id, falsches Secret,
    # widerrufene Kanzlei-Credential) - kein Unterschied im Fehlerpfad,
    # der einem Angreifer verraet, welcher Teil einer Credential korrekt
    # war (Enumeration-Schutz, analog zu app/auth/service.py).
    return HTTPException(status_code=401, detail="Authentifizierung fehlgeschlagen")


def require_tenant(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Tenant:
    parsed = parse_bearer_credential(authorization)
    if parsed is None:
        raise _unauthorized()
    client_id, secret = parsed
    tenant = db.query(Tenant).filter(Tenant.client_id == client_id).first()
    if tenant is None or not tenant.is_active:
        # Dummy-Verify GEGEN EINEN FIXEN HASH (siehe _DUMMY_SECRET_HASH oben) -
        # nicht um "secret" zu pruefen (das Ergebnis wird bewusst verworfen),
        # sondern um dieselbe Argon2-Laufzeit wie im echten Pruefpfad unten zu
        # erzeugen und damit eine client_id-Enumeration per Antwortzeit zu
        # verhindern.
        verify_client_secret(secret, _DUMMY_SECRET_HASH)
        raise _unauthorized()
    if not verify_client_secret(secret, tenant.secret_hash):
        raise _unauthorized()
    return tenant


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/v1/relay/messages", response_model=RelayResponse)
def relay_messages(
    request: RelayRequest,
    tenant: Tenant = Depends(require_tenant),
    settings: GatewaySettings = Depends(get_gateway_settings),
) -> RelayResponse:
    request_id = str(uuid.uuid4())
    started = time.monotonic()

    if not _rate_limiter.allow(
        tenant.id, limit_per_minute=tenant.rate_limit_per_minute
    ):
        log_relay_request(
            request_id=request_id,
            tenant_id=tenant.id,
            duration_ms=(time.monotonic() - started) * 1000,
            status=429,
            error_category="rate_limited",
        )
        raise HTTPException(status_code=429, detail="Rate-Limit überschritten")

    # Größenlimit (Umsetzungsplan Punkt 2): grober, serverseitiger Schutz
    # VOR dem Anthropic-Aufruf - der Payload selbst wird dabei nicht
    # zusaetzlich gespeichert, nur seine Groesse gemessen. Zweite,
    # vorgelagerte Schutzschicht ist das Reverse-Proxy-Bodylimit (siehe
    # deploy/Caddyfile) - diese Pruefung hier ist die Absicherung fuer den
    # Fall, dass der Gateway-Prozess direkt (ohne Proxy davor) erreicht wird.
    payload_size = len(request.model_dump_json().encode("utf-8"))
    if payload_size > settings.max_request_bytes:
        log_relay_request(
            request_id=request_id,
            tenant_id=tenant.id,
            duration_ms=(time.monotonic() - started) * 1000,
            status=413,
            error_category="payload_too_large",
        )
        raise HTTPException(status_code=413, detail="Anfrage überschreitet das Größenlimit")

    if request.model not in settings.allowed_models:
        log_relay_request(
            request_id=request_id,
            tenant_id=tenant.id,
            duration_ms=(time.monotonic() - started) * 1000,
            status=400,
            error_category="model_not_allowed",
        )
        raise HTTPException(status_code=400, detail="Modell nicht zulässig")

    if request.max_tokens > settings.max_tokens_ceiling:
        log_relay_request(
            request_id=request_id,
            tenant_id=tenant.id,
            duration_ms=(time.monotonic() - started) * 1000,
            status=400,
            error_category="max_tokens_exceeded",
        )
        raise HTTPException(status_code=400, detail="max_tokens überschreitet das Limit")

    api_key = (
        settings.anthropic_api_key.get_secret_value()
        if settings.anthropic_api_key is not None
        else None
    )
    if not api_key:
        log_relay_request(
            request_id=request_id,
            tenant_id=tenant.id,
            duration_ms=(time.monotonic() - started) * 1000,
            status=503,
            error_category="gateway_not_configured",
        )
        raise HTTPException(status_code=503, detail="Gateway ist nicht konfiguriert")

    try:
        # `model=settings.default_model`, NICHT `request.model` (Umsetzungsplan
        # Punkt 1): das tatsaechlich verwendete Anthropic-Modell wird
        # ausschliesslich zentral ueber die Gateway-Konfiguration bestimmt.
        # Der Client darf weiterhin ein Modell mitsenden (oben bereits gegen
        # `allowed_models` geprueft, fuer Abwaertskompatibilitaet/Logging),
        # es bestimmt aber nicht mehr die tatsaechliche Modellwahl - ein
        # Modellwechsel ist damit ausschliesslich eine `.env.gateway`-
        # Aenderung + Neustart, kein Client-Rebuild noetig.
        result = call_anthropic(api_key=api_key, model=settings.default_model, request=request)
    except RelayError:
        log_relay_request(
            request_id=request_id,
            tenant_id=tenant.id,
            duration_ms=(time.monotonic() - started) * 1000,
            status=502,
            error_category="upstream_error",
        )
        raise HTTPException(status_code=502, detail="Anthropic-Aufruf fehlgeschlagen") from None

    log_relay_request(
        request_id=request_id,
        tenant_id=tenant.id,
        duration_ms=(time.monotonic() - started) * 1000,
        status=200,
        error_category=None,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    return result
