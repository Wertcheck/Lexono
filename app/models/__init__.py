"""Datenmodelle (Prompt 04).

Alle Modelle werden hier importiert, damit `Base.metadata` vollstaendig ist
(wichtig fuer Alembics Autogenerate und fuer `Base.metadata.create_all()`
in Tests). Reihenfolge der Importe spielt dank SQLAlchemys
String-Foreign-Key-Aufloesung keine Rolle.
"""

from app.models.api_call_log import ApiCallLog
from app.models.attorney_instruction import (
    VALID_ATTORNEY_INSTRUCTION_STATUSES,
    AttorneyInstruction,
)
from app.models.audit_event import AuditEvent, AuditLogImmutableError
from app.models.base import Base
from app.models.chat_conversation import ChatConversation
from app.models.chat_message import VALID_CHAT_MESSAGE_ROLES, ChatMessage, ChatMessageDocument
from app.models.client import VALID_CLIENT_STATUSES, Client
from app.models.deadline import Deadline
from app.models.document import Document
from app.models.document_template import DocumentTemplate
from app.models.draft import Draft
from app.models.draft_feedback import DraftFeedback
from app.models.draft_quality_rating import DraftQualityRating
from app.models.draft_reference_links import DraftKnowledgeItemLink, DraftSourceLink
from app.models.embedding import Embedding
from app.models.firm_profile import FirmProfile
from app.models.generated_document import GeneratedDocument
from app.models.knowledge_item import KnowledgeItem
from app.models.law import Law
from app.models.law_section import LawSection
from app.models.matter import Matter
from app.models.message import Message
from app.models.outbox_entry import VALID_OUTBOX_STATUSES, OutboxEntry
from app.models.processing_error import (
    VALID_ERROR_CATEGORIES,
    VALID_PROCESSING_ERROR_STATUSES,
    ProcessingError,
)
from app.models.party import Party
from app.models.pilot_feedback import (
    VALID_FEEDBACK_CATEGORIES,
    VALID_REVIEW_STATUSES,
    PilotFeedback,
)
from app.models.policy import Policy
from app.models.prompt_template import PromptTemplate
from app.models.review_finding import ReviewFinding
from app.models.role import Role
from app.models.source import Source
from app.models.task import Task
from app.models.user import User
from app.models.workflow_run import VALID_WORKFLOW_STATUSES, WorkflowRun

__all__ = [
    "Base",
    "Client",
    "VALID_CLIENT_STATUSES",
    "Matter",
    "Party",
    "Policy",
    "ReviewFinding",
    "Message",
    "Document",
    "Task",
    "Deadline",
    "Draft",
    "DraftFeedback",
    "DraftQualityRating",
    "DraftSourceLink",
    "DraftKnowledgeItemLink",
    "AttorneyInstruction",
    "VALID_ATTORNEY_INSTRUCTION_STATUSES",
    "Embedding",
    "Source",
    "KnowledgeItem",
    "WorkflowRun",
    "VALID_WORKFLOW_STATUSES",
    "OutboxEntry",
    "ProcessingError",
    "VALID_ERROR_CATEGORIES",
    "VALID_PROCESSING_ERROR_STATUSES",
    "VALID_OUTBOX_STATUSES",
    "AuditEvent",
    "AuditLogImmutableError",
    "ApiCallLog",
    "User",
    "Role",
    "PilotFeedback",
    "VALID_FEEDBACK_CATEGORIES",
    "VALID_REVIEW_STATUSES",
    "PromptTemplate",
    "FirmProfile",
    "Law",
    "LawSection",
    "DocumentTemplate",
    "GeneratedDocument",
    "ChatConversation",
    "ChatMessage",
    "ChatMessageDocument",
    "VALID_CHAT_MESSAGE_ROLES",
]
