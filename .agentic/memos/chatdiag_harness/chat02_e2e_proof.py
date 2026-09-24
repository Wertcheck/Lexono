"""CHAT-02 Ende-zu-Ende-Beweis: exakt derselbe 4-Turn-Dialog wie in der
urspruenglichen Chat-Intelligence-Forensik (payload_spy.py), diesmal MIT
CHAT-02 - zeigt, dass Turn 4 jetzt tatsaechlich Inhalte aus Turn 2/3
erhaelt (vorher: strukturell unmoeglich, siehe .agentic/memos/
chatdiag_harness/payload_capture.json)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\Bonit\Lexono")
sys.path.insert(0, r"C:\Users\Bonit\Lexono\tests")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai_providers.claude_writing_provider import ClaudeWritingResult
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.chat.service import ChatService
from app.drafting.service import DraftingService
from app.models import Client, Matter, User
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.research.service import LegalResearchService
from app.search.service import DocumentSearchService
from fake_embedding_provider import FakeEmbeddingProvider

CAPTURED: list[dict] = []


class SpyWritingProvider:
    def __init__(self):
        self._answers = iter([
            "Guten Tag, wie kann ich Ihnen helfen?",
            "Gerne, worum geht es steuerrechtlich?",
            "Verstanden, es geht um einen Steuerbescheid.",
            "Die Einspruchsfrist betraegt einen Monat nach Bekanntgabe.",
        ])

    def write(self, payload):
        CAPTURED.append(payload.model_dump())
        return ClaudeWritingResult(text=next(self._answers), input_tokens=1, output_tokens=1)


engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(engine)
db = sessionmaker(bind=engine)()

search = DocumentSearchService(FakeEmbeddingProvider())
drafting = DraftingService(
    RuleBasedLocalAIProvider(search),
    LegalResearchService(search, min_score_for_sufficient=0.0),
    search,
    ClaudePrivacyGateway(),
    SpyWritingProvider(),
    model_name="claude-sonnet-5",
)

client = Client(name="Muster, Anna")
matter = Matter(client=client, title="Einspruch Steuerbescheid 2025")
user = User(email="anwalt@kanzlei.test", password_hash="x", role_id=None)
db.add_all([client, matter, user])
db.commit()

chat = ChatService(storage_dir=Path(r"C:\Users\Bonit\AppData\Local\Temp\claude_chat02_e2e_store"))
conversation = chat.create_conversation(
    db, user=user, matter_id=matter.id, title="Diagnose", actor="anwalt@kanzlei.test"
)

turns = [
    "Hallo",
    "Ich habe eine Frage zum Steuerrecht.",
    "Es geht um einen Steuerbescheid.",
    "Welche Frist gilt?",
]

report = []
for index, text in enumerate(turns, start=1):
    before = len(CAPTURED)
    user_message = chat.record_user_message(db, conversation=conversation, content=text)
    answer = chat.send_message(
        db,
        conversation=conversation,
        content=text,
        drafting_service=drafting,
        actor="anwalt@kanzlei.test",
        current_message_id=user_message.id,
    )
    captured = CAPTURED[before:] if len(CAPTURED) > before else []
    report.append({
        "turn": index,
        "user_text": text,
        "payload_sent_to_cloud": captured[0] if captured else None,
        "assistant_answer": answer.content,
        "blocked": answer.blocked,
    })

print("=" * 78)
print("CHAT-02 ENDE-ZU-ENDE-BEWEIS")
print("=" * 78)
for entry in report:
    print(f"\n--- TURN {entry['turn']}: {entry['user_text']!r}")
    payload = entry["payload_sent_to_cloud"]
    if payload is None:
        print("    PAYLOAD: KEINER (Fast Path)")
    else:
        print(f"    gespraechsverlauf ({len(payload['anonymisierter_gespraechsverlauf'])} Eintraege):")
        for line in payload["anonymisierter_gespraechsverlauf"]:
            print(f"        {line!r}")
        print(f"    anwaltl. Anmerkungen (aktuelle Nachricht): {payload['anonymisierte_anwaltliche_anmerkungen']!r}")
    print(f"    Antwort: {entry['assistant_answer']!r}  (blocked={entry['blocked']})")

print("\n" + "=" * 78)
print("KERNFRAGE: Enthaelt der Payload von TURN 4 die Inhalte aus TURN 2/3?")
print("=" * 78)
last_payload = report[-1]["payload_sent_to_cloud"]
if last_payload is None:
    print("Kein Payload in Turn 4.")
else:
    blob = json.dumps(last_payload, ensure_ascii=False)
    for probe in ["Steuerrecht", "Steuerbescheid", "Hallo"]:
        print(f"  enthaelt {probe!r}: {probe in blob}")
    # Die aktuelle Nachricht ("Welche Frist gilt?") darf NICHT im
    # Gespraechsverlauf selbst nochmal auftauchen (keine Duplizierung).
    verlauf_blob = json.dumps(last_payload["anonymisierter_gespraechsverlauf"], ensure_ascii=False)
    print(f"  aktuelle Nachricht im Verlauf dupliziert: {'Welche Frist gilt' in verlauf_blob}")
