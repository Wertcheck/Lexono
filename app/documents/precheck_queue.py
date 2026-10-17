"""§64 P1: lokale Vorab-Pruefung der Dokumentfreigabe (Modus C) im Hintergrund.

Bisher (§63) lief die Vorab-Pruefung synchron am Ende von `process_document` - der Upload wartete auf die lokale
Erkennung (erster Upload: Kaltstart, grosse Texte: Sekunden), und zwei Faelle wurden gar nicht vorgeprueft:

- Mail-Anhaenge: bei der Verarbeitung noch ohne Akte; die Zuordnung (automatisch oder "Übernehmen") folgt danach.
- Kontextaenderungen (aktenweite Datenschutz-Entscheidung, Beteiligter, Mandantenname): sie machen alle gespeicherten
  Pruefungen der Akte ungueltig - bis zu 120 Texte wurden dann beim naechsten Versand synchron neu geprueft.

Jetzt meldet ein Session-Listener NACH dem Commit, welche Dokumente bzw. Akten betroffen sind; ein einzelner lokaler
Hintergrund-Thread prueft sie mit `release.check_document` (dieselbe zentrale Pruefung, an Textstand, Kontext und
Datenschutz-Codeversion gebunden). Die Vorab-Pruefung ist NUR ein Vorwaermen:

- Die Durchsetzung beim Versand (`released_document_ids` -> `processing_state`) bleibt unveraendert verbindlich und
  prueft selbst, wenn kein passendes Ergebnis vorliegt. Ein noch laufender, abgebrochener oder fehlgeschlagener
  Hintergrundauftrag bewirkt daher NIE eine Freigabe.
- Ein Fehler wird nicht gespeichert ("fehlgeschlagen" wird nie zwischengespeichert) - der Versand prueft neu und
  blockiert fail-closed.
- Kein KI-, Cloud- oder Netzaufruf: `check_document` ruft ausschliesslich die lokale Erkennung auf.
- Nur in Modus C (bei jedem Auftrag frisch gelesen, ohne das Kanzleiprofil anzulegen).

Ohne eigene Verbindung (SQLite im Arbeitsspeicher, nur in Tests: alle Sitzungen teilen EINE Verbindung) laeuft die
Pruefung direkt im Commit-Hook statt im Thread - sonst wuerden zwei Threads dieselbe Verbindung nutzen.
"""

from __future__ import annotations

import logging
import queue
import threading

from sqlalchemy import event, func, inspect
from sqlalchemy.orm import Session, sessionmaker

from app.models import Client, Document, Matter, Party, PrivacyDecision

logger = logging.getLogger(__name__)

# wie die Suchgrenze der Kontextauswahl (4 x 30, app/ai_providers/local_ai_provider.py): mehr wird dort nie geprueft
MAX_DOCUMENTS_PER_MATTER = 120
MAX_PENDING = 1000
_INFO_KEY = "lexono_precheck"

_lock = threading.Lock()
_pending: set[tuple[str, str]] = set()
_queue: queue.Queue = queue.Queue()
_worker: threading.Thread | None = None
_idle = threading.Event()
_idle.set()


# --- Erfassung im Commit -------------------------------------------------------------------------------------------


def _changed(obj, *names: str) -> bool:
    state = inspect(obj)
    return any(state.attrs[name].history.has_changes() for name in names)


def _collect(session: Session, flush_context) -> None:
    """Nach jedem Flush: betroffene Dokumente/Akten merken (Zustand und Historie sind hier noch vor dem Flush). Ein
    Fehler hier darf den Flush des Aufrufers nie abbrechen (Review §64) - dann fehlt nur die Vorwaermung."""
    try:
        _collect_changes(session)
    except Exception as exc:  # noqa: BLE001 - der Versand prueft verbindlich
        logger.debug("Vorab-Pruefung nicht eingeplant (%s).", type(exc).__name__)


def _collect_changes(session: Session) -> None:
    documents: set[str] = set()
    matters: set[str] = set()
    for obj in session.dirty:
        # nur AENDERUNGEN bestehender Dokumente (Verarbeitung setzt den Text, Zuordnung die Akte) - neu angelegte
        # Dokumente erhalten ihren Text erst in der Verarbeitung
        if isinstance(obj, Document) and _changed(obj, "extracted_text", "matter_id"):
            documents.add(obj.id)
        elif isinstance(obj, Client) and _changed(obj, "name"):
            matters.update(m.id for m in obj.matters)
        elif isinstance(obj, Matter) and _changed(obj, "client_id"):
            matters.add(obj.id)
    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        if isinstance(obj, Party) and obj.matter_id:
            matters.add(obj.matter_id)
        elif isinstance(obj, PrivacyDecision) and obj.matter_id and obj.conversation_id is None:
            matters.add(obj.matter_id)  # nur aktenweite Entscheidungen gehoeren zum Pruefkontext
    if documents or matters:
        pending = session.info.setdefault(_INFO_KEY, (set(), set()))
        pending[0].update(documents)
        pending[1].update(matters)


def _dispatch(session: Session) -> None:
    """Nach dem Commit: einplanen. Die Daten sind bereits gespeichert - ein Fehler hier (z. B. kein neuer Thread beim
    Beenden) darf den Aufrufer nicht erreichen (Review §64), sonst saehe ein erfolgreicher Commit wie ein Fehler aus."""
    try:
        _dispatch_pending(session)
    except Exception as exc:  # noqa: BLE001 - der Versand prueft verbindlich
        logger.debug("Vorab-Pruefung nicht eingeplant (%s).", type(exc).__name__)


def _dispatch_pending(session: Session) -> None:
    pending = session.info.pop(_INFO_KEY, None)
    if not pending:
        return
    jobs = [("document", i) for i in pending[0] if i] + [("matter", i) for i in pending[1] if i]
    try:
        engine = session.get_bind()
    except Exception:  # noqa: BLE001 - ohne Verbindung keine Vorab-Pruefung (der Versand prueft verbindlich)
        return
    if _shares_one_connection(engine):
        for kind, ident in jobs:
            _run(engine, kind, ident)
        return
    with _lock:
        for job in jobs:
            if job in _pending or len(_pending) >= MAX_PENDING:
                continue  # schon eingeplant bzw. Obergrenze: dann prueft der Versand
            _pending.add(job)
            _queue.put((engine, *job))
            _idle.clear()
        _ensure_worker()


def _discard(session: Session, previous_transaction=None) -> None:
    session.info.pop(_INFO_KEY, None)


event.listen(Session, "after_flush", _collect)
event.listen(Session, "after_commit", _dispatch)
event.listen(Session, "after_rollback", _discard)


# --- Ausfuehrung ----------------------------------------------------------------------------------------------------


def _shares_one_connection(engine) -> bool:
    url = engine.url
    return url.get_backend_name() == "sqlite" and url.database in (None, "", ":memory:")


def _ensure_worker() -> None:
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_work, name="lexono-document-precheck", daemon=True)
        _worker.start()


def _work() -> None:
    while True:
        engine, kind, ident = _queue.get()
        with _lock:
            _pending.discard((kind, ident))  # vor der Pruefung: eine erneute Aenderung plant neu ein
        try:
            _run(engine, kind, ident)
        finally:
            _queue.task_done()
            with _lock:
                if not _pending and _queue.unfinished_tasks == 0:
                    _idle.set()


def _run(engine, kind: str, ident: str) -> None:
    """Eine Vorab-Pruefung in eigener Sitzung. Fehler werden verschluckt (ohne Inhalte zu protokollieren)."""
    from app.documents import release

    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        if release._stored_mode(session) != release.MODE_AUTO_REVIEW:
            return
        if kind == "document":
            document = session.get(Document, ident)
            documents = [document] if document is not None and document.deleted_at is None else []
        else:
            documents = (
                session.query(Document)
                .filter(Document.matter_id == ident, Document.deleted_at.is_(None),
                        func.length(func.trim(Document.extracted_text)) > 0)
                .order_by(Document.created_at.desc(), Document.id)
                .limit(MAX_DOCUMENTS_PER_MATTER)
                .all()
            )
        contexts: dict = {}
        for document in documents:
            if not document.matter_id or not (document.extracted_text or "").strip():
                continue
            matter = session.get(Matter, document.matter_id)
            if matter is None or matter.deleted_at is not None:
                continue
            if document.matter_id not in contexts:
                contexts[document.matter_id] = release._context(session, matter)
            release.check_document(session, document, context=contexts[document.matter_id])
    except Exception as exc:  # noqa: BLE001 - Vorwaermen; der Versand prueft verbindlich und blockiert fail-closed
        logger.debug("Vorab-Pruefung der Dokumentfreigabe uebersprungen (%s).", type(exc).__name__)
    finally:
        session.close()


def wait_idle(timeout: float = 30.0) -> bool:
    """Wartet, bis alle eingeplanten Vorab-Pruefungen abgearbeitet sind (Tests, geordnetes Beenden)."""
    return _idle.wait(timeout)
