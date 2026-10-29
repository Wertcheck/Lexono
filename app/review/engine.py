"""ReviewEngine – siehe __init__.py fuer die Gesamteinordnung und die
Unabhaengigkeits-Anforderung.

WICHTIGSTER PUNKT: `Draft.content` enthaelt zum Zeitpunkt der Pruefung
bereits REKONSTRUIERTE, echte Mandantendaten (der `DraftingService`
rekonstruiert vor dem Speichern, siehe app/drafting/service.py). Fuer den
Review-Aufruf an Claude wird der Entwurf daher wie neuer, ungeprüfter
Text behandelt: erneute Pseudonymisierung + Security-Check ueber denselben
`ClaudePrivacyGateway` wie beim Drafting - kein Sonderweg, keine Abkuerzung.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai_providers.local_ai_provider import LocalAIProvider
from app.cost_control import CostControlService
from app.models import AuditEvent, Draft, Matter, ReviewFinding
from app.privacy.api_logger import ApiCallLogger
from app.privacy.decisions import load_decisions
from app.privacy.gateway import ClaudePrivacyGateway
from app.research.service import LegalResearchService
from app.review.provider import ClaudeReviewProvider
from app.review.schema import Finding, ReviewOutcome

_REVIEW_PURPOSE = "review_draft"


class ReviewEngine:
    def __init__(
        self,
        local_ai: LocalAIProvider,
        research_service: LegalResearchService,
        gateway: ClaudePrivacyGateway,
        review_provider: ClaudeReviewProvider,
        *,
        api_logger: ApiCallLogger | None = None,
        cost_control: CostControlService | None = None,
        model_name: str = "unknown",
    ) -> None:
        self.local_ai = local_ai
        self.research_service = research_service
        self.gateway = gateway
        self.review_provider = review_provider
        self.api_logger = api_logger if api_logger is not None else ApiCallLogger()
        self.cost_control = cost_control if cost_control is not None else CostControlService()
        self.model_name = model_name

    def review_draft(
        self, draft_id: str, db: Session, *, actor: str = "system"
    ) -> ReviewOutcome:
        if not draft_id:
            raise ValueError("draft_id ist erforderlich")

        draft = db.query(Draft).filter_by(id=draft_id).first()
        if draft is None:
            raise ValueError(f"Draft {draft_id} nicht gefunden")
        if not draft.matter_id:
            raise ValueError("Draft ohne Aktenbezug kann nicht geprueft werden")

        matter = db.query(Matter).filter_by(id=draft.matter_id).first()
        if matter is None:
            raise ValueError(f"Matter {draft.matter_id} nicht gefunden")

        # Nur fuer bekannte Entitaeten (Namen etc.) - dieselbe Quelle wie
        # beim urspruenglichen Drafting, damit dieselben Platzhalter
        # (z. B. [MANDANT_01]) konsistent wiederverwendet werden koennen.
        preparation = self.local_ai.prepare_draft_context(matter.id, db)

        quellen_texte = self._gather_available_sources(matter, db, actor=actor)

        gateway_result = self.gateway.prepare_request(
            purpose=_REVIEW_PURPOSE,
            sachverhalt=draft.content,
            quellenverweise=quellen_texte,
            known_entities=preparation.known_entities,
            # §51: der Entwurf ist re-identifiziert - ein manuell markierter bzw. als Person entschiedener Name, den die
            # Erkennung nicht findet ("Richter Zweigelt"), ginge hier sonst im Klartext hinaus.
            privacy_decisions=load_decisions(db, matter_id=matter.id, conversation_id=None),
        )

        if not gateway_result.allowed:
            self.api_logger.log_blocked(
                db,
                workflow_id=matter.id,
                model=self.model_name,
                purpose=_REVIEW_PURPOSE,
                reasons=gateway_result.reasons,
            )
            return ReviewOutcome(
                success=False, draft_id=draft_id, blocked_reasons=gateway_result.reasons
            )

        budget_check = self.cost_control.check_before_call(db)
        if not budget_check.allowed:
            self.api_logger.log_blocked(
                db,
                workflow_id=matter.id,
                model=self.model_name,
                purpose=_REVIEW_PURPOSE,
                reasons=[budget_check.reason or "Kostenlimit erreicht"],
            )
            return ReviewOutcome(
                success=False,
                draft_id=draft_id,
                blocked_reasons=[budget_check.reason or "Kostenlimit erreicht"],
            )

        try:
            review_result = self.review_provider.review(gateway_result.payload)
        except Exception:
            self.api_logger.log_error(
                db,
                workflow_id=matter.id,
                model=self.model_name,
                purpose=_REVIEW_PURPOSE,
                payload=gateway_result.payload,
            )
            return ReviewOutcome(
                success=False,
                draft_id=draft_id,
                blocked_reasons=["Interner Fehler bei der Entwurfsprüfung"],
            )

        self.api_logger.log_success(
            db,
            workflow_id=matter.id,
            model=self.model_name,
            purpose=_REVIEW_PURPOSE,
            payload=gateway_result.payload,
            input_tokens=review_result.input_tokens,
            output_tokens=review_result.output_tokens,
        )

        reconstructed_findings = [
            Finding(
                category=finding.category,
                severity=finding.severity,
                description=self.gateway.reconstruct_response(
                    finding.description, gateway_result.mappings
                ),
            )
            for finding in review_result.findings
        ]
        reconstructed_assessment = self.gateway.reconstruct_response(
            review_result.overall_assessment, gateway_result.mappings
        )

        self._persist_findings(draft, reconstructed_findings, db, actor=actor)

        return ReviewOutcome(
            success=True,
            draft_id=draft_id,
            findings=reconstructed_findings,
            overall_assessment=reconstructed_assessment,
        )

    def _gather_available_sources(
        self, matter: Matter, db: Session, *, actor: str
    ) -> list[str]:
        research_results = self.research_service.research_for_matter(
            matter, db, actor=actor
        )
        texts: list[str] = []
        for result in research_results:
            for finding in result.findings:
                texts.append(
                    f"{finding.title} ({finding.reference or 'ohne Fundstelle'})"
                )
        return texts

    def _persist_findings(
        self,
        draft: Draft,
        findings: list[Finding],
        db: Session,
        *,
        actor: str,
    ) -> None:
        for finding in findings:
            db.add(
                ReviewFinding(
                    draft_id=draft.id,
                    category=finding.category,
                    severity=finding.severity,
                    description=finding.description,
                )
            )

        draft.status = "legal_review"

        severity_counts = {"hoch": 0, "mittel": 0, "niedrig": 0}
        for finding in findings:
            severity_counts[finding.severity] += 1

        db.add(
            AuditEvent(
                entity_type="Draft",
                entity_id=draft.id,
                event_type="draft_reviewed",
                actor=actor,
                details=(
                    f"{len(findings)} Finding(s): {severity_counts['hoch']} hoch, "
                    f"{severity_counts['mittel']} mittel, {severity_counts['niedrig']} niedrig"
                ),
            )
        )
        db.commit()
