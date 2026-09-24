"""CHAT-INT-DIAG Schritt 8 + 5: Performance- und Local-AI-Forensik.

Misst die tatsaechlichen Stufenzeiten ueber PerfTrace, mit ECHTEM Ollama
(qwen3:8b, wie in der installierten Instanz) und einem Spy statt Claude
(kein Geld, keine Netzlatenz der Cloud - die Cloud-Zeit ist separat
auszuweisen und wird hier bewusst NICHT simuliert).

ZWEI Szenarien, die den entscheidenden Unterschied zeigen:
  (1) Aktentitel OHNE Personenbezug  -> Presidio findet nichts ->
      _should_skip_llm_privacy_layers = True -> Local AI wird UEBERSPRUNGEN
  (2) Aktentitel MIT Personenbezug ("Muster, Anna offen 1" - genau die
      Form, die in der ECHTEN Datenbank vorkommt) -> mappings != []
      -> Local AI ist PFLICHT, auch fuer "Hallo"
"""

from __future__ import annotations

import json
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

MODEL = "qwen3:8b"


class SpyClaude:
    def write(self, payload):
        # Simuliert KEINE Cloud-Latenz - die ist separat zu betrachten.
        return ClaudeWritingResult(text="Guten Tag, wie kann ich helfen?", input_tokens=1, output_tokens=1)


def build(matter_title: str, client_name: str, with_local_ai: bool):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    c = Client(name=client_name)
    m = Matter(client=c, title=matter_title)
    db.add_all([c, m])
    db.commit()
    search = DocumentSearchService(FakeEmbeddingProvider())
    local_llm = OllamaLocalLLMProvider(base_url="http://localhost:11434", model=MODEL) if with_local_ai else None
    svc = DraftingService(
        RuleBasedLocalAIProvider(search),
        LegalResearchService(search, min_score_for_sufficient=0.0),
        search,
        ClaudePrivacyGateway(),
        SpyClaude(),
        model_name="claude-sonnet-5",
        local_llm_provider=local_llm,
    )
    return db, m, svc


def run(label: str, matter_title: str, client_name: str, message: str, with_local_ai: bool):
    db, matter, svc = build(matter_title, client_name, with_local_ai)
    trace = PerfTrace()
    start = time.perf_counter()
    res = svc.create_draft(
        matter.id, "chat_response", db, attorney_anmerkungen=message, actor="a@b.test", trace=trace
    )
    total = time.perf_counter() - start
    steps = {name: round(dur, 3) for name, dur in trace.steps}
    return {
        "label": label,
        "message": message,
        "matter_title": matter_title,
        "local_ai_injected": with_local_ai,
        "success": res.success,
        "blocked_reasons": res.blocked_reasons,
        "total_s": round(total, 3),
        "steps": steps,
    }


def main() -> None:
    results = []
    scenarios = [
        ("A1 neutraler Aktentitel, Local AI aktiv, 'Hallo' (COLD)",
         "Steuersache 2025", "Beispiel GmbH", "Hallo", True),
        ("A2 neutraler Aktentitel, Local AI aktiv, 'Hallo' (WARM)",
         "Steuersache 2025", "Beispiel GmbH", "Hallo", True),
        ("B1 PERSONENBEZOGENER Aktentitel (wie in der echten DB), 'Hallo' (COLD)",
         "Muster, Anna offen 1", "Muster, Anna", "Hallo", True),
        ("B2 PERSONENBEZOGENER Aktentitel, 'Hallo' (WARM)",
         "Muster, Anna offen 1", "Muster, Anna", "Hallo", True),
        ("C  personenbezogen, Anschlussfrage 'Welche Frist gilt?'",
         "Muster, Anna offen 1", "Muster, Anna", "Welche Frist gilt?", True),
        ("D  ohne Local AI (Vergleichsbasis), 'Hallo'",
         "Muster, Anna offen 1", "Muster, Anna", "Hallo", False),
    ]
    for label, title, client, msg, local in scenarios:
        print(f"\n### {label}")
        r = run(label, title, client, msg, local)
        results.append(r)
        print(f"    total={r['total_s']}s success={r['success']} reasons={r['blocked_reasons']}")
        for k, v in r["steps"].items():
            print(f"      {k:32s} {v}")

    out = Path(__file__).parent / "perf_results.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nRohdaten: {out}")


if __name__ == "__main__":
    main()
