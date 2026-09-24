"""App-Einstiegspunkt (Prompt 02 – Repository-Grundgeruest, erweitert um
Prompt 03 – Konfigurationssystem, Prompt 21 – FastAPI-Backend,
Prompt 22 – Dashboard-Inbox und Prompt 26 – Rollen & Berechtigungen).

Bindet ab Prompt 21 das lesende REST-Backend (`app/api/`) ein, das alle
acht im Konzept geforderten Dashboard-Bereiche abdeckt: Inbox, Akten,
Dokumente, Entwuerfe, Quellen (+ Kanzlei-Wissen), Aufgaben, Einstellungen,
Audit. Siehe app/api/schemas.py fuer die Allowlist-Grundsaetze.

Ab Prompt 22 zusaetzlich das serverseitig gerenderte Dashboard
(`app/web/`, Jinja2 + HTMX) unter `/dashboard`, plus dessen statische
Assets unter `/dashboard/static`.

Ab Prompt 26: ALLE `/api/...`-Routen erfordern eine gueltige Session
(`api_router`-weite Dependency, siehe app/api/__init__.py) - kein
Endpunkt ist mehr ungeschuetzt erreichbar. Zwei Exception-Handler
uebersetzen Auth-Fehler in fuer Menschen im Browser sinnvolle Antworten
(Redirect statt rohem 401/JSON) - siehe app/auth/permissions.py fuer die
zugrunde liegenden Exception-Klassen.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.ai_providers.factory import build_local_llm_provider
from app.api import api_router
from app.auth.permissions import AppLockedError, ForcePasswordChangeError, NotAuthenticatedError
from app.config import Settings, get_settings
from app.db.session import SessionLocal
from app.documents.service import DocumentProcessingService
from app.local_ai.setup_orchestrator import LocalAiSetupService, LocalAiState
from app.mail.factory import build_mail_provider
from app.mail.service import MailIngestionService
from app.matching.matcher import MatterMatchingService
from app.matching.service import MatterAssignmentService
from app.observability import configure_logging
from app.updater.checker import UpdateCheckResult, check_for_update
from app.web.account_router import router as account_web_router
from app.web.auth_router import router as auth_web_router
from app.web.backup_router import router as backup_web_router
from app.web.chat_router import router as chat_web_router
from app.web.clients_router import router as clients_web_router
from app.web.document_generator_router import router as document_generator_web_router
from app.web.document_templates_router import router as document_templates_web_router
from app.web.drafts_router import router as drafts_web_router
from app.web.feedback_router import router as feedback_web_router
from app.web.lock_router import router as lock_web_router
from app.web.deadline_actions_router import router as deadline_actions_web_router
from app.web.document_actions_router import router as document_actions_web_router
from app.web.matters_router import router as matters_web_router
from app.web.note_actions_router import (
    clients_router as client_notes_web_router,
    matters_router as note_actions_web_router,
)
from app.web.parties_router import router as parties_web_router
from app.web.prompt_library_router import router as prompt_library_web_router
from app.web.quality_router import router as quality_web_router
from app.web.schriftsatz_router import router as schriftsatz_web_router
from app.web.settings_router import router as settings_web_router
from app.web.tasks_router import router as tasks_web_router
from app.web.template_paths import STATIC_DIR
from app.web.errors_router import router as errors_web_router
from app.web.global_search_router import router as global_search_web_router
from app.web.knowledge_router import router as knowledge_web_router
from app.web.laws_router import router as laws_web_router
from app.web.monitoring_router import router as monitoring_web_router
from app.web.outbox_router import router as outbox_web_router
from app.web.placeholder_router import router as placeholder_web_router
from app.web.router import router as web_router
from app.web.users_router import router as users_web_router

logger = logging.getLogger(__name__)


async def _run_silent_update_check(app: FastAPI, manifest_url: str | None) -> None:
    """Führt die Update-Prüfung in einem Hintergrund-Thread aus (Schritt 3)
    - blockiert den App-Start nicht ("stumme" Prüfung). `check_for_update`
    fängt selbst jeden Fehler ab, daher hier kein zusätzliches try/except
    nötig."""
    result = await asyncio.to_thread(check_for_update, manifest_url)
    app.state.update_check = result
    if result.update_available:
        logger.info("Update verfügbar: %s", result.latest_version)


async def _run_silent_local_ai_check(app: FastAPI, settings: Settings) -> None:
    """Ermittelt den Local-AI-Status beim Start in einem Hintergrund-Thread
    (analog zu `_run_silent_update_check` oben) - blockiert den App-Start
    nicht. Ruft AUSSCHLIESSLICH bereits bestehende Logik auf
    (`LocalAiSetupService.get_status`/`OllamaInstaller.ensure_running`,
    ARCHITECTURE.md §68) - keine neue Status- oder Startlogik, keine neue
    Installations-/Modell-Download-Logik.

    `get_status()` liefert bereits `LocalAiState.DISABLED`, wenn
    `settings.local_ai_enabled=False` ist, OHNE dabei einen Netzwerkaufruf
    auszulösen - deshalb hier bewusst KEINE eigene "ist aktiviert"-Prüfung
    (keine Dopplung der bereits in `get_status()` vorhandenen
    Kurzschlusslogik).

    Nur im Fall `RUNTIME_UNREACHABLE` (Ollama-Runtime ist installiert, aber
    gerade nicht erreichbar - typischer Fall nach einem Systemneustart,
    bevor der vom offiziellen Installer registrierte Autostart-/
    Tray-Prozess den API-Port gebunden hat) wird die dafür bereits
    vorgesehene `ensure_running()`-Logik genutzt; danach wird der Status
    genau EIN weiteres Mal frisch ermittelt - der tatsächliche Endzustand
    kommt IMMER aus `get_status()` selbst, nie aus dem reinen
    `ensure_running()`-Rückgabewert, damit niemals ein falscher `READY`-
    Status vorgetäuscht wird. `RUNTIME_MISSING` (gar nicht installiert) und
    `MODEL_MISSING` (Runtime läuft, Modell fehlt) werden unverändert
    übernommen - ein Startversuch würde daran nichts ändern; Installation
    bzw. Modell-Download bleiben Aufgabe des separaten, admin-ausgelösten
    Setup-Assistenten (§68), nicht dieses stillen Startchecks."""
    service = LocalAiSetupService()
    status = await asyncio.to_thread(service.get_status, settings)

    if status.state == LocalAiState.RUNTIME_UNREACHABLE:
        provider = build_local_llm_provider(settings)
        if provider is not None:
            await asyncio.to_thread(
                service.ollama_installer.ensure_running,
                is_reachable=lambda: provider.check_health().reachable,
                wait_seconds=3.0,
            )
            status = await asyncio.to_thread(service.get_status, settings)

    app.state.local_ai_status = status
    if status.state == LocalAiState.READY:
        logger.info("Lokale KI bereit (Modell '%s').", status.configured_model)
    elif status.state != LocalAiState.DISABLED:
        logger.warning(
            "Lokale KI nicht bereit (Status %s): %s", status.state.value, status.detail
        )


_MAIL_POLL_INTERVAL_SECONDS = 300.0


async def _run_periodic_mail_ingestion(settings: Settings) -> None:
    """ECHTER FUND (14.09., "AUTONOMOUS PRODUCT COMPLETION MASTER
    DIRECTIVE" - Posteingang-Untersuchung): `MailIngestionService`
    (E-Mail-Abruf, Prompt 07) UND `MatterAssignmentService`
    (automatische Aktenzuordnung, Prompt 09) waren beide vollstaendig
    implementiert und getestet, wurden aber an KEINER Stelle der
    laufenden Anwendung jemals aufgerufen - ein konfiguriertes Postfach
    (`MAIL_*` in .env, echte Einstellungsseite unter /dashboard/settings)
    hatte in der Praxis NIE eine Wirkung, der Posteingang blieb immer
    leer/manuell. Dies schliesst die Luecke: ist `settings.mail_provider`
    NICHT gesetzt (Standard), kehrt diese Funktion sofort zurueck, OHNE
    jemals eine Verbindung zu versuchen - identisches Prinzip wie
    `build_local_llm_provider`/`_run_silent_local_ai_check` oben. Ist ein
    Postfach konfiguriert, wird es alle 5 Minuten abgefragt und JEDE neu
    erfasste Nachricht SOFORT ueber `MatterMatchingService`/
    `MatterAssignmentService` bewertet - bei eindeutigem Treffer
    automatisch zugeordnet, sonst bleibt sie unzugeordnet und erscheint
    im Posteingang mit einem Zuordnungsvorschlag (siehe
    app/web/router.py::_build_match_suggestion). Ein einzelner
    fehlgeschlagener Abruf (z. B. Postfach kurzzeitig nicht erreichbar)
    bricht die Schleife NICHT ab - naechster Versuch nach der Wartezeit,
    analog zum bewusst fehlertoleranten `IntakeWatcher` (ARCHITECTURE.md
    §Prompt 05: "Fehler bei einzelnen Dateien brechen die Ueberwachung
    der uebrigen nicht ab").

    ECHTER FUND (14.09., Nachtrag noch im selben Zyklus): `MailIngestionService`
    legt Anhaenge bisher NUR als `Document`-Datensatz an (Datei + Zeile),
    ruft aber NIE `DocumentProcessingService.process_document` auf (anders
    als der Chat- und der Schriftsatz-Upload-Pfad, siehe app/chat/service.py
    bzw. app/web/schriftsatz_router.py) - ein E-Mail-Anhang hätte daher
    dauerhaft `extracted_text=None` gehabt, in der Posteingang-Detailansicht
    für immer "(kein Inhalt extrahiert)" gezeigt. Ergänzt: jedes neu
    erfasste Dokument wird jetzt VOR der Zuordnungsbewertung durch
    dieselbe, bereits bestehende `DocumentProcessingService` geschickt
    (identische Konfiguration wie die beiden anderen Upload-Pfade).
    **Bewusst NICHT in diesem Zyklus ergänzt** (echter, aber SEPARATER,
    bereits vorher bestehender Befund, betrifft alle drei Upload-Pfade
    gleich, nicht nur Mail - siehe OPEN_ISSUES.md): `ClassificationService`
    wird von KEINEM der drei Pfade aufgerufen, wodurch
    `classification_confidence` nie gesetzt wird und
    `MatterAssignmentService._classification_is_sufficient` bei JEDER
    Nachricht mit Anhang konservativ `False` liefert (auto_assigned bleibt
    dadurch auf anhangfreie Nachrichten beschränkt, needs_review bleibt
    der Regelfall bei Anhang) - keine Verschlechterung durch diese
    Aenderung, nur keine Verbesserung dieses spezifischen Punktes."""
    provider = build_mail_provider(settings)
    if provider is None:
        return

    ingestion_service = MailIngestionService(provider, settings.mail_attachment_storage_dir)
    document_processor = DocumentProcessingService(
        ocr_enabled=settings.ocr_enabled,
        ocr_languages=settings.ocr_languages,
        tesseract_cmd=settings.tesseract_cmd,
    )
    matcher = MatterMatchingService(
        auto_assign_threshold=settings.matching_auto_assign_threshold,
        review_threshold=settings.matching_review_threshold,
    )
    assignment_service = MatterAssignmentService(
        matcher,
        classification_low_confidence_threshold=settings.classification_low_confidence_threshold,
    )

    while True:
        try:
            db = SessionLocal()
            try:
                new_messages = await asyncio.to_thread(
                    ingestion_service.ingest_new_messages, db
                )
                for message in new_messages:
                    for document in message.documents:
                        await asyncio.to_thread(
                            document_processor.process_document, document, db, actor="system"
                        )
                    result = await asyncio.to_thread(
                        assignment_service.assign_matter, message, db
                    )
                    logger.info(
                        "E-Mail erfasst und bewertet (Entscheidung: %s).", result.decision
                    )
            finally:
                db.close()
        except Exception:  # noqa: BLE001 - siehe Docstring: darf die Schleife nicht beenden
            logger.exception(
                "Automatischer E-Mail-Abruf fehlgeschlagen - naechster Versuch in %ss.",
                _MAIL_POLL_INTERVAL_SECONDS,
            )
        await asyncio.sleep(_MAIL_POLL_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Laedt die validierte Konfiguration beim Start und konfiguriert das
    zentrale Logging (Prompt 32).

    Bewusst werden hier keine Werte geloggt, die Secrets enthalten koennten
    (mail_password, anthropic_api_key, session_secret_key). Nur
    unkritische Metadaten wie app_env werden zu Diagnosezwecken in
    app.state abgelegt UND geloggt.

    Seit Schritt 3 zusätzlich: eine stumme, nicht-blockierende
    Update-Prüfung im Hintergrund (siehe app/updater/checker.py) - läuft
    nebenläufig zum eigentlichen Start, verzögert ihn also nicht, und
    scheitert niemals hart (Standardwert `checked=False`, solange die
    Prüfung noch läuft oder deaktiviert ist).

    Analog dazu (§68-Nachtrag): ein stummer, nicht-blockierender Local-AI-
    Statuscheck im Hintergrund (siehe `_run_silent_local_ai_check` oben) -
    verbindet die bereits bestehende `LocalAiSetupService`/`OllamaInstaller`-
    Logik erstmals mit dem tatsächlichen Anwendungsstart, ohne diesen zu
    verzögern. Solange der Task noch läuft, ist `app.state.local_ai_status`
    schlicht noch nicht gesetzt (kein erfundener Zwischenzustand) - es gibt
    aktuell noch keine UI/Route, die diesen Wert läse.

    Seit 14.09. zusätzlich: `_run_periodic_mail_ingestion` (siehe dort für
    den vollen Befund) - schließt die Lücke, dass ein konfiguriertes
    Postfach bisher nie automatisch abgerufen wurde.
    """
    settings = get_settings()
    configure_logging(log_level=settings.log_level, log_file_path=settings.log_file_path)
    app.state.settings = settings
    app.state.app_env = settings.app_env
    app.state.update_check = UpdateCheckResult(checked=False, update_available=False)
    logger.info("Anwendung gestartet (app_env=%s)", settings.app_env)
    update_task = asyncio.create_task(
        _run_silent_update_check(app, settings.update_manifest_url)
    )
    local_ai_task = asyncio.create_task(_run_silent_local_ai_check(app, settings))
    mail_ingestion_task = asyncio.create_task(_run_periodic_mail_ingestion(settings))
    yield
    update_task.cancel()
    local_ai_task.cancel()
    mail_ingestion_task.cancel()
    logger.info("Anwendung wird beendet")


app = FastAPI(
    title="Lexono-Pipeline",
    description=(
        "Konfigurierbare KI-gestuetzte Workflow-Plattform fuer eine "
        "Anwaltskanzlei (frueher Entwicklungsstand)."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(NotAuthenticatedError)
def handle_not_authenticated(request: Request, exc: NotAuthenticatedError) -> RedirectResponse:
    return RedirectResponse(url=f"/dashboard/login?next={exc.next_path}", status_code=303)


@app.exception_handler(ForcePasswordChangeError)
def handle_force_password_change(
    request: Request, exc: ForcePasswordChangeError
) -> RedirectResponse:
    return RedirectResponse(url="/dashboard/change-password", status_code=303)


@app.exception_handler(AppLockedError)
def handle_app_locked(request: Request, exc: AppLockedError) -> RedirectResponse:
    return RedirectResponse(url="/dashboard/unlock", status_code=303)


@app.get("/health")
def health() -> dict[str, str]:
    """Einfacher Smoke-Test-Endpunkt: bestaetigt nur, dass die App laeuft.
    Bewusst OHNE Login-Pflicht - wird u. a. fuer reine
    Infrastruktur-Healthchecks benoetigt."""
    return {"status": "ok"}


app.include_router(api_router)
app.include_router(web_router)
app.include_router(chat_web_router)
app.include_router(drafts_web_router)
app.include_router(schriftsatz_web_router)
app.include_router(outbox_web_router)
app.include_router(auth_web_router)
app.include_router(users_web_router)
app.include_router(errors_web_router)
app.include_router(monitoring_web_router)
app.include_router(backup_web_router)
app.include_router(clients_web_router)
app.include_router(global_search_web_router)
app.include_router(knowledge_web_router)
app.include_router(laws_web_router)
app.include_router(document_templates_web_router)
app.include_router(document_generator_web_router)
app.include_router(quality_web_router)
app.include_router(account_web_router)
app.include_router(settings_web_router)
app.include_router(tasks_web_router)
app.include_router(feedback_web_router)
app.include_router(lock_web_router)
app.include_router(prompt_library_web_router)
app.include_router(matters_web_router)
app.include_router(parties_web_router)
app.include_router(document_actions_web_router)
app.include_router(deadline_actions_web_router)
app.include_router(note_actions_web_router)
app.include_router(client_notes_web_router)
app.include_router(placeholder_web_router)
app.mount(
    "/dashboard/static",
    StaticFiles(directory=STATIC_DIR),
    name="dashboard-static",
)
