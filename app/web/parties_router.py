"""Dashboard-Router für Aktenbeteiligte (Party) – Gegner/gegnerische
Anwälte/Gericht/sonstige Beteiligte einer Akte (17.09., Owner-Direktive
§5/§6/§10 "technisch lösbare Lücke statt Produktentscheidung").

ECHTER FUND: `app.models.Party` (Prompt 04) existiert bereits vollständig
als Datenmodell und wird bereits produktiv GELESEN -
`RuleBasedLocalAIProvider._build_known_entities`
(app/ai_providers/local_ai_provider.py) klassifiziert jede `Party`-Zeile
per Rollen-Schlüsselwort in "gegner"/"anwalt"/"gericht"/"beteiligter" und
speist das Ergebnis direkt in die Pseudonymisierung sowie den
risikobasierten Fast-Path (CHAT-04, `_should_skip_llm_privacy_layers")
ein. Es gab aber PROJEKTWEIT keinen einzigen Schreibpfad, der jemals eine
`Party`-Zeile anlegt - der Lesepfad lief dadurch strukturell immer leer,
und jede Erwähnung eines Gegners/Gerichts in einer Chat-Nachricht wurde
zwangsläufig als "unbekannte neue Entität" behandelt (volle, langsamere
Pipeline statt des schnellen Pfads) UND lief Gefahr, von der
deterministischen "nicht erkannte Namen"-Heuristik (siehe
security_check.py) blockiert zu werden.

Bewusst NICHT in `matters_router.py` (dort ausdrücklich "rein LESEND" -
Akten selbst entstehen weiterhin ausschließlich über die bestehenden Wege,
siehe dortiges Moduldocstring) - eigener, kleiner Router für genau diesen
einen Unter-Ressourcentyp, analog zur bereits etablierten Trennung
(`chat_router.py` hostet z. B. auch die "aus einer Nachricht/einem
Dokument einen Chat starten"-Aktionen, obwohl diese Seiten von anderen
Routern stammen).

Aktenisolation (CLAUDE.md) bleibt gewahrt: jede Partei ist über
`matter_id` an genau eine Akte gebunden, jede Abfrage/Löschung prüft dies
explizit."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.auth.permissions import require_role
from app.db.session import get_db
from app.models import AuditEvent, Matter, Party, User

router = APIRouter(prefix="/dashboard/matters", tags=["dashboard-matters-parties"])


@router.post("/{matter_id}/parties")
def create_party(
    matter_id: str,
    name: str = Form(...),
    role: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Legt eine neue Aktenbeteiligte/einen neuen Aktenbeteiligten an
    (Gegner/gegnerische:r Anwält:in/Gericht/Sonstige:r) - `role` ist
    bewusst Freitext (identisch zum bestehenden Modell-Feld, siehe
    Moduldocstring dort), NICHT auf eine feste Liste beschränkt, damit die
    bereits etablierte, tolerante Schlüsselwort-Zuordnung
    (`_OPPONENT_ROLE_KEYWORDS`/`_COURT_ROLE_KEYWORDS`/
    `_LAWYER_ROLE_KEYWORDS`) unverändert funktioniert, unabhängig von der
    genauen Formulierung ("Gegner", "gegnerischer Anwalt",
    "Prozessbevollmächtigter" ...)."""
    matter = get_or_404(db, Matter, matter_id, "Akte")
    name = name.strip()
    if not name:
        return RedirectResponse(url=f"/dashboard/matters/{matter.id}", status_code=303)

    party = Party(
        matter_id=matter.id,
        name=name,
        role=role.strip() or None,
        email=email.strip() or None,
        phone=phone.strip() or None,
    )
    db.add(party)
    db.flush()
    db.add(
        AuditEvent(
            entity_type="Party",
            entity_id=party.id,
            event_type="party_added",
            actor=current_user.email,
            details=f"Aktenbeteiligte(r) hinzugefügt (Rolle: {party.role or 'unbekannt'})",
        )
    )
    db.commit()

    return RedirectResponse(url=f"/dashboard/matters/{matter.id}", status_code=303)


@router.post("/{matter_id}/parties/{party_id}/delete")
def delete_party(
    matter_id: str,
    party_id: str,
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    matter = get_or_404(db, Matter, matter_id, "Akte")
    party = (
        db.query(Party)
        .filter(Party.id == party_id, Party.matter_id == matter.id)
        .first()
    )
    if party is not None:
        db.add(
            AuditEvent(
                entity_type="Party",
                entity_id=party.id,
                event_type="party_removed",
                actor=current_user.email,
                details=f"Aktenbeteiligte(r) entfernt (Rolle: {party.role or 'unbekannt'})",
            )
        )
        db.delete(party)
        db.commit()

    return RedirectResponse(url=f"/dashboard/matters/{matter.id}", status_code=303)
