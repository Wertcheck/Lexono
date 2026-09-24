from __future__ import annotations
import sys, traceback
sys.path.insert(0, r"C:\Users\Bonit\Lexono"); sys.path.insert(0, r"C:\Users\Bonit\Lexono\tests")
import app.drafting.service as dsvc
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.ai_providers.claude_writing_provider import ClaudeWritingResult
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.drafting.service import DraftingService
from app.models import Client, Matter
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.research.service import LegalResearchService
from app.search.service import DocumentSearchService
from fake_embedding_provider import FakeEmbeddingProvider

# api_logger.log_error patchen, um den ECHTEN Traceback zu sehen
orig = None
class Spy:
    def write(self, payload):
        return ClaudeWritingResult(draft_text="Guten Tag, wie kann ich helfen?", input_tokens=1, output_tokens=1)

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(engine); db = sessionmaker(bind=engine)()
c=Client(name="Muster, Anna"); m=Matter(client=c, title="Einspruch Steuerbescheid 2025")
db.add_all([c,m]); db.commit()
search=DocumentSearchService(FakeEmbeddingProvider())
svc=DraftingService(RuleBasedLocalAIProvider(search), LegalResearchService(search, min_score_for_sufficient=0.0), search, ClaudePrivacyGateway(), Spy(), model_name="claude-sonnet-5")

real_log_error = svc.api_logger.log_error
def loud_log_error(*a, **k):
    print("### log_error aufgerufen, aktueller Traceback:")
    traceback.print_exc()
    return real_log_error(*a, **k)
svc.api_logger.log_error = loud_log_error

res = svc.create_draft(m.id, "chat_response", db, attorney_anmerkungen="Hallo", actor="a@b.test")
print("success:", res.success, "reasons:", res.blocked_reasons)
