"""SystemHealthService – Selbstdiagnose für die admin-only Systemstatus-
Seite (Schritt 3, Teil 2).

Grundsatz wie beim bestehenden Systemstatus (Prompt 32): NIEMALS
Mandanteninhalte oder Secrets, nur technische Ja/Nein-/Zahlwerte.

Die Claude-API-Erreichbarkeitsprüfung ist nicht Teil des normalen
Seitenaufrufs (kein automatischer Aufruf bei jedem Laden der
Systemstatus-Seite) - Claude kostet laut Anthropic-Dokumentation zwar
keine Token (`GET /v1/models` ist ein reiner Metadaten-Abruf), aber ein
automatischer Hintergrundaufruf bei jedem Seitenaufruf wäre trotzdem eine
unnötige, unkontrollierte Verbindung - konsistent mit der übrigen
Kostenkontroll-Disziplin des Projekts (Prompt 33) wird sie nur auf
ausdrücklichen Admin-Klick ausgeführt (siehe app/web/monitoring_router.py:
check_api_reachability)."""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class DiskSpaceStatus:
    checked: bool
    path: str | None = None
    free_gb: float | None = None
    total_gb: float | None = None
    percent_free: float | None = None
    is_low: bool = False
    error: str | None = None


@dataclass(frozen=True)
class DatabaseStatus:
    checked: bool
    kind: str  # "sqlite" / "postgresql" / "other"
    file_exists: bool | None = None
    size_mb: float | None = None
    integrity_ok: bool | None = None
    error: str | None = None


@dataclass(frozen=True)
class ApiReachabilityResult:
    checked: bool
    reachable: bool
    latency_ms: float | None = None
    error: str | None = None


#: Unterhalb dieses Anteils freien Speicherplatzes gilt der Wert als knapp
#: - keine wissenschaftliche Herleitung, ein pragmatischer Schwellenwert
#: für eine lokale Kanzlei-Installation mit begrenztem Dokumentenaufkommen.
_LOW_DISK_SPACE_THRESHOLD_PERCENT = 10.0


class SystemHealthService:
    def check_disk_space(self, path: str | Path) -> DiskSpaceStatus:
        try:
            resolved = Path(path)
            resolved.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(resolved)
        except OSError as exc:
            return DiskSpaceStatus(checked=False, error=str(exc))

        percent_free = round((usage.free / usage.total) * 100, 1) if usage.total else 0.0
        return DiskSpaceStatus(
            checked=True,
            path=str(resolved),
            free_gb=round(usage.free / (1024**3), 2),
            total_gb=round(usage.total / (1024**3), 2),
            percent_free=percent_free,
            is_low=percent_free < _LOW_DISK_SPACE_THRESHOLD_PERCENT,
        )

    def check_database_status(self, db: Session, database_url: str) -> DatabaseStatus:
        if database_url.startswith("sqlite:///"):
            return self._check_sqlite_status(db, database_url)
        if database_url.startswith("postgresql"):
            return self._check_generic_liveness(db, kind="postgresql")
        return self._check_generic_liveness(db, kind="other")

    def _check_sqlite_status(self, db: Session, database_url: str) -> DatabaseStatus:
        db_path = Path(database_url.removeprefix("sqlite:///"))
        file_exists = db_path.exists()
        size_mb = round(db_path.stat().st_size / (1024**2), 2) if file_exists else None

        integrity_ok: bool | None = None
        error: str | None = None
        if file_exists:
            try:
                result = db.execute(text("PRAGMA integrity_check")).scalar()
                integrity_ok = result == "ok"
            except Exception as exc:  # noqa: BLE001 - Diagnose darf nie die Seite crashen
                error = f"Integritätsprüfung fehlgeschlagen: {type(exc).__name__}"

        return DatabaseStatus(
            checked=True,
            kind="sqlite",
            file_exists=file_exists,
            size_mb=size_mb,
            integrity_ok=integrity_ok,
            error=error,
        )

    def _check_generic_liveness(self, db: Session, *, kind: str) -> DatabaseStatus:
        try:
            db.execute(text("SELECT 1"))
            return DatabaseStatus(checked=True, kind=kind, integrity_ok=True)
        except Exception as exc:  # noqa: BLE001
            return DatabaseStatus(
                checked=True, kind=kind, integrity_ok=False, error=type(exc).__name__
            )

    def check_claude_api_reachability(
        self, api_key: str | None, *, timeout_seconds: float = 5.0
    ) -> ApiReachabilityResult:
        """Führt EINEN einzigen, nur auf Admin-Klick ausgeführten
        Metadaten-Abruf aus (`GET /v1/models`, laut Anthropic-Dokumentation
        ohne Token-Kosten) - kein Chat-Aufruf, keine Mandantendaten
        beteiligt."""
        if not api_key or not api_key.strip():
            return ApiReachabilityResult(
                checked=False, reachable=False, error="Kein API-Schlüssel konfiguriert"
            )

        import anthropic

        started = time.monotonic()
        try:
            # Ziel nach app/ai_providers/endpoint_policy.py (sonst ginge der API-Schluessel an ANTHROPIC_BASE_URL)
            from app.ai_providers.endpoint_policy import resolve_anthropic_base_url

            client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds, base_url=resolve_anthropic_base_url())
            client.models.list(limit=1)
            latency_ms = round((time.monotonic() - started) * 1000, 1)
            return ApiReachabilityResult(checked=True, reachable=True, latency_ms=latency_ms)
        except Exception as exc:  # noqa: BLE001 - Diagnose darf nie die Seite crashen
            return ApiReachabilityResult(
                checked=True, reachable=False, error=type(exc).__name__
            )

