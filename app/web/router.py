"""Dashboard-Router (Prompt 22 – Dashboard-Inbox).

Serverseitig gerendert (Jinja2 + HTMX, siehe ARCHITECTURE.md §4/Entscheidung
Prompt 22). Bewusst getrennt von `app/api/` (JSON-API): dieser Router liefert
HTML fuer Menschen im Browser, `app/api/` liefert JSON fuer Programme/
zukuenftige Integrationen. Beide teilen sich dieselbe `get_db`-Abhaengigkeit
und dieselben SQLAlchemy-Modelle, aber keine Code-Duplikation der
Query-Logik ist hier bewusst in Kauf genommen: die Web-Views brauchen andere
Joins (z. B. `Message.matter` fuer die Akten-Tab-Badges) als die schlanken
API-Listen-Endpunkte.

WICHTIG: dieselbe Grundregel wie im gesamten Projekt gilt auch hier - noch
keine Authentifizierung (folgt Prompt 26), siehe Sidebar-Fussnote in
base.html.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_or_404
from app.auth.permissions import require_login, require_role
from app.config import get_settings
from app.db.session import get_db
from app.deadlines.extractor import PlaceholderDeadlineExtractor
from app.deadlines.service import DeadlineAnalysisService
from app.drafting.quick_matter import PLACEHOLDER_CLIENT_NAME
from app.matching.matcher import MatterMatchingService
from app.matching.service import MatterAssignmentService
from app.models import AuditEvent, Client, Document, Matter, Message, User
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

templates = Jinja2Templates(directory=TEMPLATES_DIR)

_FILTER_OPTIONS: list[tuple[str, str]] = [
    ("all", "Alle"),
    ("unmatched", "Nicht zugeordnet"),
    # ECHTER FUND (19.09., UI/UX-Referenzabgleich "04_posteingang_
    # nachricht_detail.png" - dieselbe Referenz, aus der "unmatched"/die
    # Auto-Zuordnungskarte bereits am 16.09. stammen): die Referenz zeigt
    # zusaetzlich "Zugewiesen" und "Mit Anhang" als Filter-Tabs - beide
    # rein lesende Filter auf bereits vorhandenen Daten (`matter_id`/
    # `documents`), keine neue Datenquelle. "Ungelesen"/"beA" AUS DER
    # REFERENZ bewusst NICHT ergaenzt: "Ungelesen" braeuchte ein neues
    # Datenbankfeld UND eine noch offene Entscheidung, WANN eine
    # Nachricht als gelesen gilt (Listenansicht oeffnen? Detailansicht?
    # bereits vor Einfuehrung des Felds bestehende Nachrichten?) - echte
    # Produktentscheidung, nicht nur Verdrahtung (CLAUDE.md §"Bei
    # unklaren fachlichen Entscheidungen stoppen"). "beA" ist eine grosse,
    # eigenstaendige Integration (besonderes elektronisches
    # Anwaltspostfach), bereits an anderer Stelle als zurueckgestellt
    # dokumentiert.
    ("matched", "Zugewiesen"),
    ("with_attachment", "Mit Anhang"),
    ("inbound", "Eingehend"),
    ("outbound", "Ausgehend"),
]
_VALID_FILTER_KEYS = {key for key, _ in _FILTER_OPTIONS}


def _apply_filter(query, filter_key: str):
    """Wendet den gewaehlten Inbox-Filter auf die Query an.

    Unbekannte/fehlende Filter-Keys fallen sicher auf "all" zurueck, statt
    einen Fehler zu werfen - ein manipulierter Query-Parameter darf die
    Ansicht bestenfalls auf "alle Nachrichten" zuruecksetzen, nie zu einem
    Serverfehler fuehren.
    """
    if filter_key == "unmatched":
        return query.filter(Message.matter_id.is_(None))
    if filter_key == "matched":
        return query.filter(Message.matter_id.isnot(None))
    if filter_key == "with_attachment":
        return query.filter(Message.documents.any())
    if filter_key == "inbound":
        return query.filter(Message.direction == "inbound")
    if filter_key == "outbound":
        return query.filter(Message.direction == "outbound")
    return query


def _load_messages(db: Session, filter_key: str, search: str = "") -> list[Message]:
    query = db.query(Message).options(joinedload(Message.matter))
    query = _apply_filter(query, filter_key)
    search = search.strip()
    if search:
        # Suche (18.09., Owner-Direktive "WEITERARBEITEN" §5 "fehlende
        # Aktionen implementieren") - ECHTER FUND: der Posteingang hatte
        # bei wachsender Nachrichtenzahl (die Filter-Tabs allein reichen
        # nicht) projektweit KEIN Suchfeld, nur die vier groben Filter-
        # Tabs. Bewusst serverseitig per LIKE (kein neuer Suchindex noetig,
        # Nachrichtenzahl pro Kanzlei bleibt klein) - Absender/Betreff, die
        # beiden Felder, an denen man eine bestimmte E-Mail typischerweise
        # wiedererkennt.
        needle = f"%{search}%"
        query = query.filter(
            (Message.sender.ilike(needle)) | (Message.subject.ilike(needle))
        )
    return query.order_by(Message.created_at.desc()).limit(100).all()


def _load_assignable_matters(db: Session) -> list[Matter]:
    """Fuer die manuelle "Akte zuordnen"-Auswahl (16.09., UI/UX-Sweep -
    Referenz `04_posteingang_nachricht_detail.png` zeigt eine editierbare
    Mandant-/Akte-Zuordnung, nicht nur die automatische Vorschlagskarte;
    fuer Nachrichten OHNE gefundenen Vorschlag gab es bisher UEBERHAUPT
    keinen Weg, sie manuell einer Akte zuzuordnen, obwohl der dafuer
    noetige Endpunkt - `accept_matter_suggestion` - bereits jede
    existierende `matter_id` akzeptiert, nicht nur die vorgeschlagene).
    Schliesst wie beim Aktenbestand-Fastpath (app/chat/service.py) die
    Schnellentwurf-Sammelakten des Platzhalter-Mandanten aus - das sind
    keine "echten" Akten, denen man eine E-Mail sinnvoll zuordnen wuerde."""
    return (
        db.query(Matter)
        .join(Client, Matter.client_id == Client.id)
        .filter(Client.name != PLACEHOLDER_CLIENT_NAME)
        .order_by(Client.name, Matter.title)
        .all()
    )


def _load_detail_context(db: Session, message_id: str) -> dict:
    message = get_or_404(db, Message, message_id, "Nachricht")
    documents = (
        db.query(Document)
        .filter(Document.message_id == message_id, Document.deleted_at.is_(None))
        .all()
    )
    context = {"message": message, "documents": documents}
    if message.matter_id is None:
        context["match_suggestion"] = _build_match_suggestion(db, message)
        context["assignable_matters"] = _load_assignable_matters(db)
        context["detected_deadline_preview"] = _preview_deadline(message)
    return context


def _preview_deadline(message: Message):
    """Referenzbild `04_posteingang_nachricht_detail.png` zeigt in der
    Zuordnungs-Karte ein DRITTES Feld neben Mandant/Akte: eine erkannte
    Frist (24.09., Owner-Direktive "PRODUCT COMPLETION MODE" §2/§9,
    frischer Referenzbild-Abgleich - bereits am 14.09. als bewusst
    zurueckgestellte Luecke dokumentiert: "kein Frist-Vorschlag in der
    Karte", siehe PROJECT_STATE.md).

    Reine VORSCHAU, kein neuer Schreibpfad: `PlaceholderDeadlineExtractor.
    extract()` ist eine reine Funktion (Text -> Kandidaten, keine DB-
    Schreibzugriffe) - exakt dieselbe Erkennung, die
    `DeadlineAnalysisService.analyze_message` beim tatsaechlichen
    Zuordnen ohnehin ausfuehrt (app/web/router.py::accept_matter_
    suggestion). Die Karte zeigt dem Anwalt damit ehrlich, was nach einem
    Klick auf "Übernehmen" automatisch mit erfasst wird - OHNE eine
    zweite, konkurrierende Erfassung/Bestaetigung einzufuehren (die
    Nachricht hat noch keine Akte, ein `Deadline`-Datensatz kann laut
    Datenmodell aber erst NACH der Zuordnung entstehen, siehe DECISIONS.md
    zum urspruenglichen 20.09.-Fund). Bei mehreren Kandidaten wird nur der
    mit der hoechsten Konfidenz angezeigt (der Reference zeigt ebenfalls
    nur ein einzelnes Feld)."""
    if not message.body_text or not message.body_text.strip():
        return None
    candidates = PlaceholderDeadlineExtractor().extract(message.body_text)
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.confidence)


def _get_assignment_service() -> MatterAssignmentService:
    settings = get_settings()
    matcher = MatterMatchingService(
        auto_assign_threshold=settings.matching_auto_assign_threshold,
        review_threshold=settings.matching_review_threshold,
    )
    return MatterAssignmentService(
        matcher,
        classification_low_confidence_threshold=settings.classification_low_confidence_threshold,
    )


def _build_match_suggestion(db: Session, message: Message) -> dict | None:
    """ECHTER FUND (14.09., "AUTONOMOUS PRODUCT COMPLETION MASTER
    DIRECTIVE" - Posteingang-Untersuchung): `MatterAssignmentService`
    (automatische Aktenzuordnung, Prompt 09) existierte bereits
    vollstaendig implementiert/getestet, wurde aber nie mit dem
    Posteingang verbunden - dessen eigener Schema-Kommentar sagt sogar
    woertlich "damit ein spaeteres Dashboard (Prompt 22) Vorschlaege
    anzeigen kann" (siehe app/matching/schema.py). Nutzt
    `MatterAssignmentService.suggest_matter` (rein lesend, siehe dort -
    wendet NICHTS an, nur das Betrachten dieser Seite darf niemals selbst
    eine Zuordnung bewirken). Liefert `None`, wenn kein Kandidat gefunden
    wurde (`no_match`) - dann zeigt die Seite gar keine Karte, statt eine
    leere/nutzlose anzuzeigen."""
    result = _get_assignment_service().suggest_matter(message, db)
    if not result.candidates:
        return None
    best = result.candidates[0]
    matter = db.query(Matter).filter_by(id=best.matter_id).first()
    if matter is None:
        return None
    return {
        "matter_id": matter.id,
        "matter_title": matter.title,
        "client_name": matter.client.name if matter.client else None,
        "score": best.score,
        "matched_signals": best.matched_signals,
        "decision": result.decision,
    }


@router.get("", response_class=HTMLResponse)
def dashboard_root(
    request: Request, current_user: User = Depends(require_login)
) -> HTMLResponse:
    """`/dashboard` selbst zeigt keine eigene Seite - leitet auf die Chat-
    Startseite weiter (UI-Überarbeitung: Chat ist die zentrale
    Arbeitsoberfläche, siehe app/web/chat_router.py), vormals der
    Posteingang."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/dashboard/chat")


@router.get("/inbox", response_class=HTMLResponse)
def inbox_page(
    request: Request,
    filter: str = "all",  # noqa: A002 - passender, konsistenter Query-Param-Name
    q: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    filter_key = filter if filter in _VALID_FILTER_KEYS else "all"
    messages = _load_messages(db, filter_key, search=q)
    total_count = db.query(Message).count()
    unmatched_count = db.query(Message).filter(Message.matter_id.is_(None)).count()

    context = {
        "request": request,
        "active_nav": "Posteingang",
        "messages": messages,
        "filter_options": _FILTER_OPTIONS,
        "active_filter": filter_key,
        "search": q,
        "total_count": total_count,
        "unmatched_count": unmatched_count,
        "message": None,
        "documents": [],
        "active_message_id": None,
        "current_user": current_user,
        # Fuer partials/onboarding_banner.html (nur bei leerem Posteingang
        # sichtbar) - dessen Formulare posten seit 20.08. echt gegen
        # app/web/settings_router.py, brauchen also einen echten CSRF-Token.
        "csrf_token": getattr(request.state, "csrf_token", ""),
    }
    return templates.TemplateResponse(request, "inbox.html", context)


@router.get("/inbox/list", response_class=HTMLResponse)
def inbox_list_partial(
    request: Request,
    filter: str = "all",  # noqa: A002
    q: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    """HTMX-Partial: nur die gefilterte Nachrichtenliste, fuer den
    Filter-Tab-Wechsel/die Suche ohne vollen Seiten-Reload."""
    filter_key = filter if filter in _VALID_FILTER_KEYS else "all"
    messages = _load_messages(db, filter_key, search=q)
    context = {
        "request": request,
        "messages": messages,
        "active_message_id": None,
    }
    return templates.TemplateResponse(request, "partials/message_list.html", context)


@router.get("/inbox/{message_id}", response_class=HTMLResponse)
def inbox_message_page(
    request: Request,
    message_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    """Volle Seite mit vorausgewaehlter Nachricht - ermoeglicht direktes
    Verlinken/Neuladen eines einzelnen Postfach-Eintrags (siehe
    `hx-push-url` in partials/message_row.html)."""
    detail_context = _load_detail_context(db, message_id)
    messages = _load_messages(db, "all")
    total_count = db.query(Message).count()
    unmatched_count = db.query(Message).filter(Message.matter_id.is_(None)).count()

    context = {
        "request": request,
        "active_nav": "Posteingang",
        "messages": messages,
        "filter_options": _FILTER_OPTIONS,
        "active_filter": "all",
        "total_count": total_count,
        "unmatched_count": unmatched_count,
        "active_message_id": message_id,
        "current_user": current_user,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        **detail_context,
    }
    return templates.TemplateResponse(request, "inbox.html", context)


@router.get("/inbox/{message_id}/detail", response_class=HTMLResponse)
def inbox_message_detail_partial(
    request: Request,
    message_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    """HTMX-Partial: nur das Detail-Panel, fuer den Klick auf eine
    Nachrichten-Zeile ohne vollen Seiten-Reload."""
    detail_context = _load_detail_context(db, message_id)
    context = {
        "request": request,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        **detail_context,
    }
    return templates.TemplateResponse(
        request, "partials/message_detail.html", context
    )


@router.post("/inbox/{message_id}/assign-matter")
def accept_matter_suggestion(
    request: Request,
    message_id: str,
    matter_id: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role()),
) -> RedirectResponse:
    """"Übernehmen"-Aktion der "Automatische Zuordnung (Vorschlag)"-Karte
    (14.09., siehe `_build_match_suggestion` fuer den vollen Befund, den
    diese Route schliesst) - wendet den vorgeschlagenen Kandidaten
    tatsaechlich an, GENAU wie die bereits bestehende automatische
    Zuordnung (`MatterAssignmentService.assign_matter`), nur explizit
    vom Anwalt bestaetigt statt automatisch bei Score >= Schwelle.
    Bewusst dieselbe `Matter`-Existenzpruefung wie ueberall sonst
    (`get_or_404`) statt dem Formularwert blind zu vertrauen - ein
    manipulierter `matter_id`-Wert darf hoechstens einen 404 ausloesen,
    nie eine Zuordnung zu einer nicht existierenden/fremden Akte.

    Fristenanalyse NACH der Zuordnung (20.09., ECHTER FUND: siehe
    Moduldocstring von app/deadlines/service.py) - zwei bisher uebersehene
    Faelle werden hier nachgeholt: (1) der Nachrichtentext selbst
    (`Message.body_text`) wurde nie auf Fristen untersucht, nur Anhaenge;
    (2) bereits VOR der Zuordnung verarbeitete Anhaenge wurden bei ihrer
    ersten (erfolglosen) Analyse nur uebersprungen, nie erneut versucht -
    `DeadlineAnalysisService` ist fuer beide Faelle idempotent (kein
    Duplikat, falls schon einmal erfolgreich analysiert)."""
    message = get_or_404(db, Message, message_id, "Nachricht")
    target_matter = get_or_404(db, Matter, matter_id, "Akte")

    previous_matter_id = message.matter_id
    message.matter_id = target_matter.id
    for document in message.documents:
        document.matter_id = target_matter.id
    db.add(
        AuditEvent(
            entity_type="Message",
            entity_id=message.id,
            event_type="matter_match_accepted_by_user",
            actor=current_user.email,
            details=(
                f"Zuordnungsvorschlag von {current_user.email} bestaetigt: "
                f"Akte {previous_matter_id or '(keine)'} -> {target_matter.id}"
            ),
        )
    )
    db.commit()

    deadline_service = DeadlineAnalysisService(PlaceholderDeadlineExtractor())
    deadline_service.analyze_message(message, db)
    for document in message.documents:
        deadline_service.analyze_document(document, db)

    return RedirectResponse(url=f"/dashboard/inbox/{message.id}", status_code=303)
