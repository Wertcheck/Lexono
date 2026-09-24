from __future__ import annotations
import sys
sys.path.insert(0, r"C:\Users\Bonit\Lexono"); sys.path.insert(0, r"C:\Users\Bonit\Lexono\tests")
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

CAP=[]
class Spy:
    def generate_draft(self, payload, *, model_name=None, **kw):
        CAP.append(payload)
        return ClaudeWritingResult(draft_text="Hallo! Wie kann ich Ihnen helfen?", input_tokens=1, output_tokens=1)

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(engine); db = sessionmaker(bind=engine)()
c=Client(name="Muster, Anna"); m=Matter(client=c, title="Einspruch Steuerbescheid 2025")
db.add_all([c,m]); db.commit()
search=DocumentSearchService(FakeEmbeddingProvider())
svc=DraftingService(RuleBasedLocalAIProvider(search), LegalResearchService(search, min_score_for_sufficient=0.0), search, ClaudePrivacyGateway(), Spy(), model_name="claude-sonnet-5")
res = svc.create_draft(m.id, "chat_response", db, attorney_anmerkungen="Hallo", actor="a@b.test")
print("success        :", res.success)
print("blocked_reasons:", res.blocked_reasons)
print("draft_text     :", repr(res.draft_text))
print("open_review    :", res.open_review_points)
print("PAYLOAD gesendet:", len(CAP))
if CAP:
    p=CAP[0]
    print("  sachverhalt:", repr(p.anonymisierter_sachverhalt))
    print("  anmerkungen:", repr(p.anonymisierte_anwaltliche_anmerkungen))
