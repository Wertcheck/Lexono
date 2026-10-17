"""DraftGenerationOrchestrator – verbindet alle Abstraktionen zum
vollständigen Ablauf aus der Architekturvorgabe:

LOCAL DATA -> Local AI (`LocalAIProvider`) -> Draft Preparation
  -> Privacy Gateway (`ClaudePrivacyGateway`, Schritt 1-3)
  -> ClaudeWritingProvider (Schritt 4, Protocol)
  -> lokale Rückführung (`ClaudePrivacyGateway.reconstruct_response`)

WICHTIG: Diese Klasse hängt ausschließlich von den drei Protocols/
Abstraktionen ab (`LocalAIProvider`, `ClaudePrivacyGateway`,
`ClaudeWritingProvider`) - sie importiert und kennt kein konkretes
Claude-SDK, keinen API-Key, keine HTTP-Bibliothek. Das ist die technische
Umsetzung von Vorgabe-Punkt 11 ("Der Workflow darf nicht direkt von
Claude abhängig sein").
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.ai_providers.claude_writing_provider import ClaudeWritingProvider
from app.ai_providers.local_ai_provider import LocalAIProvider
from app.privacy.api_logger import ApiCallLogger
from app.privacy.decisions import load_decisions
from app.privacy.gateway import ClaudePrivacyGateway


@dataclass
class DraftGenerationResult:
    success: bool
    text: str | None = None
    blocked_reasons: list[str] = field(default_factory=list)


class DraftGenerationOrchestrator:
    def __init__(
        self,
        local_ai: LocalAIProvider,
        gateway: ClaudePrivacyGateway,
        writing_provider: ClaudeWritingProvider,
        *,
        api_logger: ApiCallLogger | None = None,
        model_name: str = "unknown",
    ) -> None:
        self.local_ai = local_ai
        self.gateway = gateway
        self.writing_provider = writing_provider
        # Schritt 5: Protokollierung ist standardmaessig aktiv (sicherer
        # Default) - kann fuer isolierte Tests explizit deaktiviert werden.
        self.api_logger = api_logger if api_logger is not None else ApiCallLogger()
        self.model_name = model_name

    def generate_draft_text(
        self,
        matter_id: str,
        purpose: str,
        db: Session,
        *,
        stil: str | None = None,
        vorlage: str | None = None,
    ) -> DraftGenerationResult:
        preparation = self.local_ai.prepare_draft_context(matter_id, db)

        gateway_result = self.gateway.prepare_request(
            purpose=purpose,
            sachverhalt=preparation.sachverhalt,
            argumentationspunkte=preparation.argumentationspunkte,
            quellenverweise=preparation.quellenverweise,
            stil=stil,
            vorlage=vorlage,
            known_entities=preparation.known_entities,
            privacy_decisions=load_decisions(db, matter_id=matter_id, conversation_id=None),  # §51, wie Drafting
        )

        if not gateway_result.allowed:
            self.api_logger.log_blocked(
                db,
                workflow_id=matter_id,
                model=self.model_name,
                purpose=purpose,
                reasons=gateway_result.reasons,
            )
            return DraftGenerationResult(
                success=False, text=None, blocked_reasons=gateway_result.reasons
            )

        try:
            writing_result = self.writing_provider.write(gateway_result.payload)
        except Exception:
            self.api_logger.log_error(
                db,
                workflow_id=matter_id,
                model=self.model_name,
                purpose=purpose,
                payload=gateway_result.payload,
            )
            return DraftGenerationResult(
                success=False,
                text=None,
                blocked_reasons=["Interner Fehler bei der Textproduktion"],
            )

        self.api_logger.log_success(
            db,
            workflow_id=matter_id,
            model=self.model_name,
            purpose=purpose,
            payload=gateway_result.payload,
            token_count=writing_result.token_count,
        )

        reconstructed_text = self.gateway.reconstruct_response(
            writing_result.text, gateway_result.mappings
        )

        return DraftGenerationResult(success=True, text=reconstructed_text)
