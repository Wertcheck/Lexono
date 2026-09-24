"""Reale Latenzmessung des CHAT-04-Fixes gegen echtes Ollama (qwen2.5:1.5b,
lokal geladen) - kein Fake-Provider. Vergleicht auf DERSELBEN Maschine im
selben Prozess:
  A) Akte mit Mandantennamen im Titel + "Hallo"  -> soll jetzt SKIPPEN
  B) Akte mit Mandantennamen im Titel + Nachricht mit NEUEM Namen -> soll
     weiterhin die volle Vorabanalyse durchlaufen (Kontrollgruppe, zeigt
     dieselbe Maschine/dasselbe Modell OHNE den Skip als Referenz).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, r"C:\Users\Bonit\Lexono")
sys.path.insert(0, r"C:\Users\Bonit\Lexono\tests")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai_providers.claude_writing_provider import ClaudeWritingResult
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.ai_providers.ollama_provider import OllamaLocalLLMProvider
from app.drafting.service import DraftingService
from app.models import Client, Matter
from app.models.base import Base
from app.observability.perf_trace import PerfTrace
from app.privacy.gateway import ClaudePrivacyGateway
from app.research.service import LegalResearchService
from app.search.service import DocumentSearchService
from fake_embedding_provider import FakeEmbeddingProvider


class SpyWritingProvider:
    def write(self, payload):
        return ClaudeWritingResult(text="Guten Tag, wie kann ich helfen?", input_tokens=1, output_tokens=1)


def build_service():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    search = DocumentSearchService(FakeEmbeddingProvider())
    local_llm = OllamaLocalLLMProvider(base_url="http://localhost:11434", model="qwen2.5:1.5b")
    service = DraftingService(
        RuleBasedLocalAIProvider(search),
        LegalResearchService(search, min_score_for_sufficient=0.0),
        search,
        ClaudePrivacyGateway(),
        SpyWritingProvider(),
        model_name="claude-sonnet-5",
        local_llm_provider=local_llm,
    )
    return db, service


def run(label, db, service, matter, message):
    trace = PerfTrace()
    t0 = time.perf_counter()
    result = service.create_draft(
        matter.id, "chat_response", db, attorney_anmerkungen=message, actor="a@b.test", trace=trace
    )
    elapsed = time.perf_counter() - t0
    steps = {name: round(duration, 3) for name, duration in trace.steps}
    print(f"\n--- {label}")
    print(f"  Nachricht     : {message!r}")
    print(f"  success       : {result.success}")
    print(f"  Gesamtzeit    : {elapsed:.2f}s")
    print(f"  Trace-Schritte: {steps}")
    return elapsed


print("=== A) Mandantenname im Aktentitel, einfache Begruessung (der reale gemeldete Fall) ===")
db_a, service_a = build_service()
client_a = Client(name="Erika Mustermann")
matter_a = Matter(client=client_a, title="Erika Mustermann offen 1")
db_a.add_all([client_a, matter_a])
db_a.commit()
t_skip = run("A) sollte SKIPPEN", db_a, service_a, matter_a, "Hallo")

print("\n=== B) Kontrollgruppe: neuer, unbekannter Name in der Nachricht (soll NICHT skippen) ===")
db_b, service_b = build_service()
client_b = Client(name="Erika Mustermann")
matter_b = Matter(client=client_b, title="Erika Mustermann offen 1")
db_b.add_all([client_b, matter_b])
db_b.commit()
t_full = run("B) volle Pipeline (Kontrolle)", db_b, service_b, matter_b, "Bitte notiere, dass auch Herr Klaus Andersen beteiligt ist.")

print(f"\n=== ERGEBNIS ===")
print(f"A (Skip)         : {t_skip:.2f}s")
print(f"B (volle Pipeline): {t_full:.2f}s")
print(f"Beschleunigung   : {t_full/max(t_skip,0.001):.1f}x")
