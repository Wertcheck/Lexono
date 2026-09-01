"""Lexono-Gateway - FastAPI-Anwendung (ARCHITECTURE.md §70).

Ein einziger funktionaler Endpunkt (`POST /v1/relay/messages`) plus
`/health`. Läuft als eigener Prozess, eigene Infrastruktur, eigenes
`.env.gateway` - siehe gateway/__init__.py."""

from __future__ import annotations

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
from gateway.security import parse_bearer_credential, verify_client_secret

_rate_limiter = SlidingWindowRateLimiter()


@lru_cache
def _engine():
    return build_engine(get_gateway_settings())


@lru_cache
def _session_factory():
    return build_session_factory(_engine())


def get_db() -> Session:
    yield from iter_session(_session_factory())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db(_engine())
    yield


app = FastAPI(title="Lexono Gateway", lifespan=lifespan)


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
