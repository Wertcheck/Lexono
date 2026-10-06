"""EditorService – Backend für den Rich-Text-Dokumenten-Editor (04.10.,
Owner-Direktive "LEXONO - Dokumenten-Editor produktionsnah implementieren
und vollständig in den Chat-Workflow integrieren").

Bewusst KEIN neuer Privacy-/KI-Pfad: jede KI-Bearbeitung läuft über
`AttorneyInstructionService.apply_instruction` (app/attorney_instructions/
service.py) - exakt derselbe, bereits produktive Weg wie die Anmerkungs-
/Anweisungs-Felder in `draft_detail.html` ("Änderungen übernehmen & neu
formulieren"), inklusive vollständigem Privacy-Gateway-Durchlauf,
Platzhalter-Integritätsprüfung und Kostenkontrolle. Dieser Service fügt
NUR hinzu:

1. Autosave (`autosave_draft`): reine In-Place-Aktualisierung der
   AKTUELLEN, noch nicht freigegebenen Zeile (siehe app/models/draft.py-
   Moduldocstring "nur die jeweils aktuelle/neueste Zeile ... Status-
   Updates ohne Versionssprung") - KEINE neue Version pro Tastendruck/
   Debounce-Intervall. Läuft NIE, wenn `draft.status != "draft"`
   (approved/rejected/legal_review sind eingefroren).
2. Auswahlbewusste KI-Bearbeitung (`apply_ai_suggestion`): baut bei
   vorhandener Textauswahl eine auf genau diesen Ausschnitt begrenzte
   Anweisung, STATT immer den gesamten Entwurf neu zu formulieren - der
   bestehende `AttorneyInstructionService` kennt den Begriff "Auswahl"
   nicht, braucht dafür keine Änderung (reiner Text-Baustein in der
   Anweisung selbst).
3. Verwerfen eines KI-Vorschlags (`discard_ai_suggestion`, delegiert an
   app/drafting/versioning.py) - macht KEINE neue Version rückgängig,
   markiert nur `status`.
4. "Als Vorlage speichern" (`save_as_template`): echte Wiederverwendung
   von `DocumentTemplateService` (app/document_generator/template_service.py,
   bisher NUR vom eigenständigen Dokumenten-Generator genutzt) - der neue
   Editor erzeugt dieselbe `DocumentTemplate`-Zeile, keine zweite
   Vorlagenablage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.attorney_instructions.schema import ApplyInstructionResult, AttorneyInstructionInput
from app.attorney_instructions.service import AttorneyInstructionService
from app.document_generator.schema import DocumentTemplateInput
from app.document_generator.template_service import DocumentTemplateService
from app.drafting.html_sanitizer import sanitize_editor_html
from app.drafting.versioning import discard_ai_suggestion
from app.models import AuditEvent, Draft, DocumentTemplate

#: Feste KI-Vorschläge im Editor-Sidebar ("KI-Assistent"-Tab, siehe
#: Referenzbild 12_dokument_editor.png) - identisches Prinzip wie die
#: bereits bestehenden 4 Vorschlagsknöpfe in draft_detail.html
#: (`data-prefill`), hier als Backend-seitige, feste Anweisungstexte statt
#: reinem Vorausfüllen, weil der Editor sie direkt (ohne Zwischenschritt
#: im Textfeld) anwendbar machen soll. Jeder Eintrag nutzt EINEN der
#: bereits in `ALLOWED_PURPOSES` (app/privacy/security_check.py)
#: zugelassenen Zwecke - kein neuer Zweck wird erfunden.
AI_SUGGESTIONS: list[dict[str, str]] = [
    {
        "id": "precise_wording",
        "label": "Formulierung präzisieren",
        "purpose": "improve_clarity",
        "instruction_text": (
            "Formuliere den folgenden Text präziser und eindeutiger, ohne "
            "den rechtlichen Inhalt zu verändern."
        ),
    },
    {
        "id": "shorten",
        "label": "Text kürzen",
        "purpose": "optimize_style",
        "instruction_text": (
            "Kürze den folgenden Text auf das Wesentliche, ohne "
            "rechtlich relevante Aussagen zu verlieren."
        ),
    },
    {
        "id": "legal_review",
        "label": "Rechtliche Prüfung",
        "purpose": "review_draft",
        "instruction_text": (
            "Prüfe den folgenden Text auf rechtliche Unstimmigkeiten oder "
            "Lücken und schlage konkrete Verbesserungen vor."
        ),
    },
    {
        "id": "alternative_wording",
        "label": "Alternative Formulierungen",
        "purpose": "improve_draft",
        "instruction_text": (
            "Formuliere den folgenden Text alternativ, mit demselben "
            "rechtlichen Inhalt, aber anderem Ausdruck."
        ),
    },
]

#: Siehe AI_SUGGESTIONS-Docstring: "Rechtliche Prüfung" nutzt bewusst
#: denselben Zweck wie die unabhängige Review-Engine - ist aber hier KEIN
#: Ersatz für eine rechtliche Freigabe (siehe `LEGAL_REVIEW_DISCLAIMER`
#: unten, in der UI sichtbar auszugeben).
LEGAL_REVIEW_DISCLAIMER = (
    "KI-gestützte Analyse, keine Rechtsfreigabe - die Verantwortung für den "
    "Inhalt verbleibt bei der Anwältin/dem Anwalt."
)


@dataclass
class SelectionScopedInstruction:
    """Ergebnis von `_build_instruction_text` - reine Textbausteine, kein
    neuer Zweck/keine neue Privacy-Ausnahme."""

    instruction_text: str
    is_selection_scoped: bool


def _build_instruction_text(base_instruction: str, selected_text: str | None) -> SelectionScopedInstruction:
    """Baut die tatsächlich an `AttorneyInstructionService` übergebene
    Anweisung.

    EHRLICHE GRENZE (bereits bestehende, dokumentierte Einschränkung -
    siehe AttorneyInstructionService.apply_instruction-Docstring: "Die
    Neugenerierung baut den Aktenkontext ... unverändert aus den
    AKTUELLEN Aktendaten neu auf - sie erhält NICHT zusätzlich den Text
    der vorherigen Draft-Version als Eingabe"): `DraftingService.
    create_draft` erzeugt IMMER einen vollständigen neuen Entwurfstext
    aus dem Aktenkontext, NIE eine chirurgische Ersetzung nur eines
    Ausschnitts - diese Funktion kann eine Textauswahl deshalb nur als
    FOKUS innerhalb der Anweisung mitgeben (Claude bekommt den
    markierten Ausschnitt als Kontext, worauf sich die Überarbeitung
    konzentrieren soll), NICHT als Garantie, dass nur dieser Ausschnitt
    verändert wird. Der Editor zeigt das Ergebnis deshalb konsequent als
    VOLLSTÄNDIGEN neuen Versionsvorschlag zum Annehmen/Verwerfen an
    (siehe EditorService.apply_ai_suggestion/app_draft_editor.js),
    niemals als stille Ersetzung nur der Auswahl. Dieselbe, bereits
    bestehende Privacy-Gateway-Pseudonymisierung läuft unverändert über
    den gesamten `instruction_text` (siehe AttorneyInstructionService-
    Moduldocstring: "AttorneyInstruction darf niemals ungeprüft an
    Claude gehen")."""
    selected_text = (selected_text or "").strip()
    if not selected_text:
        return SelectionScopedInstruction(instruction_text=base_instruction, is_selection_scoped=False)
    instruction_text = (
        f"{base_instruction}\n\n"
        "Konzentriere die Überarbeitung besonders auf den folgenden, von "
        "der Anwältin/dem Anwalt im Editor markierten Ausschnitt des "
        "bisherigen Entwurfs (der restliche, nicht markierte Text ist dir "
        "nicht bekannt - formuliere das gesamte Schreiben stimmig neu):\n"
        "---MARKIERTER AUSSCHNITT ANFANG---\n"
        f"{selected_text}\n"
        "---MARKIERTER AUSSCHNITT ENDE---"
    )
    return SelectionScopedInstruction(instruction_text=instruction_text, is_selection_scoped=True)


class EditorService:
    def __init__(self, attorney_instruction_service: AttorneyInstructionService) -> None:
        """`attorney_instruction_service` wird injiziert (siehe
        app/web/service_factory.py) statt hier selbst aufgebaut -
        identisches Prinzip wie `AttorneyInstructionService.__init__`
        (ein ungenutzter `DraftingService` bei reinem Autosave/Discard
        wäre unnötiger Aufwand, siehe dortiger Docstring)."""
        self.attorney_instruction_service = attorney_instruction_service

    def autosave_draft(
        self,
        db: Session,
        *,
        draft: Draft,
        content: str,
        subject: str | None,
        recipient: str | None,
        content_format: str,
        actor: str,
    ) -> Draft:
        """In-Place-Aktualisierung der aktuellen Zeile (siehe Moduldocstring
        Punkt 1). Gibt den UNVERÄNDERTEN `draft` zurück (kein neuer),
        wenn `status != "draft"` - der Aufrufer (Router) muss diesen Fall
        dem Editor ehrlich als "nicht gespeichert, Entwurf eingefroren"
        melden, NICHT stillschweigend verwerfen."""
        if draft.status != "draft":
            return draft
        # Sanitisierung NUR fuer "html" (siehe app/drafting/html_sanitizer.py-
        # Moduldocstring: gespeichertes XSS, falls der Request manipuliert
        # wurde - document.execCommand im Browser ist KEIN Schutz dagegen).
        # "text" bleibt unveraendert Klartext, wie vor dieser Erweiterung.
        draft.content = sanitize_editor_html(content) if content_format == "html" else content
        draft.subject = subject
        draft.recipient = recipient
        draft.content_format = content_format
        draft.last_autosaved_at = datetime.now(timezone.utc)
        db.add(draft)
        db.commit()
        db.refresh(draft)
        return draft

    def apply_ai_suggestion(
        self,
        db: Session,
        *,
        draft: Draft,
        instruction_text: str,
        purpose: str,
        actor: str,
        selected_text: str | None = None,
    ) -> ApplyInstructionResult:
        """Wendet EINE KI-Bearbeitung an - kombiniert `create_instruction`
        + `apply_instruction` (identisch zu `/instructions/apply` in
        app/web/drafts_router.py, hier als EIN Aufruf für den Editor, der
        keine separate "nur speichern"-Aktion anbietet)."""
        scoped = _build_instruction_text(instruction_text, selected_text)
        instruction = self.attorney_instruction_service.create_instruction(
            draft,
            AttorneyInstructionInput(instruction_text=scoped.instruction_text),
            db,
            actor=actor,
        )
        result = self.attorney_instruction_service.apply_instruction(
            instruction, db, purpose=purpose, actor=actor
        )
        if result.new_draft is not None:
            db.add(
                AuditEvent(
                    entity_type="Draft",
                    entity_id=result.new_draft.id,
                    event_type="draft_ai_suggestion_applied",
                    actor=actor,
                    details=(
                        f"Auswahlbezogen: {scoped.is_selection_scoped}"
                        if selected_text
                        else "Gesamter Entwurf"
                    ),
                )
            )
            db.commit()
        return result

    def discard_ai_suggestion(self, db: Session, *, draft: Draft, actor: str) -> Draft:
        return discard_ai_suggestion(db, draft=draft, actor=actor)

    def save_as_template(
        self,
        db: Session,
        *,
        draft: Draft,
        name: str,
        category: str | None,
        actor: str,
    ) -> DocumentTemplate:
        """Speichert den AKTUELLEN Entwurfsinhalt als wiederverwendbare
        `DocumentTemplate` (echte Wiederverwendung des bestehenden
        Dokumenten-Generators, siehe Moduldocstring Punkt 4) - bewusst
        OHNE automatische Platzhalter-Erkennung (würde eine nicht real
        geprüfte "Intelligenz" vorspiegeln, siehe CLAUDE.md "Keine
        Fake-Vollständigkeit") - der Anwalt kann die gespeicherte Vorlage
        anschließend wie jede andere im Dokumenten-Generator
        (app/web/document_templates_router.py) mit Platzhaltern versehen."""
        service = DocumentTemplateService()
        return service.create_template(
            db,
            DocumentTemplateInput(
                name=name,
                category=category,
                description=f"Aus Entwurf v{draft.version} ({draft.matter_id}) gespeichert.",
                content=draft.content,
            ),
            actor=actor,
        )
