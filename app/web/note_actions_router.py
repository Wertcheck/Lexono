"""Dashboard-Router für das Anlegen einer Akte- oder Mandanten-Notiz
(19.09., UI/UX-Referenzabgleich - mehrere Referenzbilder zeigen
konsequent einen eigenen "Notizen"-Tab, sowohl in der Akte- als auch in
der Mandanten-Detailansicht, siehe app/models/note.py).

Bewusst NUR Erstellen + Anzeigen im ersten Schnitt, KEIN Bearbeiten/
Löschen: eine Notiz ist ihrem Wesen nach ein append-only Vermerk ("was
wurde wann festgehalten") - das nachträgliche Verändern/Entfernen wirft
echte, noch offene Fragen auf (darf nur der Autor löschen? bleibt eine
gelöschte Notiz im Verlauf sichtbar? siehe CLAUDE.md §"Bei unklaren
fachlichen Entscheidungen stoppen"), waehrend Erstellen/Anzeigen keine
davon beruehrt. Owner-Direktive §8 (Decompose): der Erstellen/Anzeigen-
Teil ist decision-independent und wird hier gebaut; Bearbeiten/Löschen
bleibt zurückgestellt.

Eigener, kleiner Router (analog zu deadline_actions_router.py/
document_actions_router.py/parties_router.py) statt Erweiterung von
matters_router.py/clients_router.py. Zwei eigenständige Endpunkte
(Akte/Mandant) statt eines gemeinsamen, da `get_or_404` je Entität einen
anderen Modelltyp/eine andere Fehlermeldung braucht - beide teilen sich
aber dieselbe kleine `_create_note`-Hilfsfunktion.

Aktenisolation (CLAUDE.md) bleibt gewahrt: jede Notiz ist über GENAU
EINES von `matter_id`/`client_id` an genau eine Akte/einen Mandanten
gebunden - siehe app/models/note.py für die volle Begründung der
Modellierung."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.auth.permissions import require_role
from app.db.session import get_db
from app.models import AuditEvent, Client, Matter, Note, User

matters_router = APIRouter(prefix="/dashboard/matters", tags=["dashboard-matters-notes"])
clients_router = APIRouter(prefix="/dashboard/clients", tags=["dashboard-clients-notes"])

# Rueckwaertskompatibler Name (main.py importierte bisher `router`, siehe
# vorherige Fassung dieser Datei) - zeigt weiterhin auf den Akte-Router.
router = matters_router


def _create_note(db: Session, *, text: str, current_user: User, **note_kwargs: str) -> Note:
    clean_text = text.strip()
    if not clean_text:
        raise HTTPException(status_code=400, detail="Bitte einen Notiztext eingeben.")

    note = Note(text=clean_text, author=current_user.email, **note_kwargs)
    db.add(note)
    db.flush()
    db.add(
        AuditEvent(
            entity_type="Note",
            entity_id=note.id,
            event_type="note_added",
            actor=current_user.email,
            details="Notiz hinzugefügt",
        )
    )
    db.commit()
    return note


@matters_router.post("/{matter_id}/notes")
def create_matter_note(
    matter_id: str,
    text: str = Form(...),
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    matter = get_or_404(db, Matter, matter_id, "Akte")
    _create_note(db, text=text, current_user=current_user, matter_id=matter.id)
    return RedirectResponse(url=f"/dashboard/matters/{matter.id}#notizen", status_code=303)


@clients_router.post("/{client_id}/notes")
def create_client_note(
    client_id: str,
    text: str = Form(...),
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    client = get_or_404(db, Client, client_id, "Mandant")
    _create_note(db, text=text, current_user=current_user, client_id=client.id)
    return RedirectResponse(url=f"/dashboard/clients/{client.id}#notizen", status_code=303)
