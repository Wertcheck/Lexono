"""CHAT-INT-DIAG Schritt 2+3: Payload- und Conversation-Memory-Forensik.

Faengt den TATSAECHLICH an den Writing-Provider uebergebenen Payload ab
(Spy auf Provider-Ebene, kein Code-Lesen) und fuehrt einen echten
4-Turn-Dialog durch dieselbe ChatService-Pipeline wie die Web-UI.

Schreibt NICHTS ins Repo und nichts in die installierte Instanz -
eigene In-Memory-SQLite.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\Bonit\Lexono")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai_providers.claude_writing_provider import ClaudeWritingResult
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.chat.service import ChatService
from app.drafting.service import DraftingService
from app.models import ChatMessage, Client, Matter, User
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.research.service import LegalResearchService
from app.search.service import DocumentSearchService

sys.path.insert(0, r"C:\Users\Bonit\Lexono\tests")
from fake_embedding_provider import FakeEmbeddingProvider  # noqa: E402

CAPTURED: list[dict] = []


class SpyWritingProvider:
    """Faengt den finalen Payload ab, statt Claude wirklich aufzurufen."""

    def write(self, payload):
        CAPTURED.append(
            {
                "schreibauftrag": payload.schreibauftrag,
                "gewuenschter_stil": payload.gewuenschter_stil,
                "anonymisierter_sachverhalt": payload.anonymisierter_sachverhalt,
                "anonymisierte_argumentationspunkte": list(
                    payload.anonymisierte_argumentationspunkte
                ),
                "anonymisierte_quellenverweise": list(payload.anonymisierte_quellenverweise),
                "schreibvorlage": payload.schreibvorlage,
                "anonymisierte_anwaltliche_anmerkungen": (
                    payload.anonymisierte_anwaltliche_anmerkungen
                ),
                "_payload_fields": sorted(payload.model_dump().keys()),
            }
        )
        return ClaudeWritingResult(
            text="Guten Tag, wie kann ich Ihnen helfen?", input_tokens=1, output_tokens=1
        )


def build_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def main() -> None:
    db = build_session()
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

    chat = ChatService(storage_dir=Path(r"C:\Users\Bonit\AppData\Local\Temp\claude_chatdiag_store"))
    conversation = chat.create_conversation(
        db,
        user=user,
        matter_id=matter.id,
        title="Diagnose",
        actor="anwalt@kanzlei.test",
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
        chat.record_user_message(db, conversation=conversation, content=text)
        answer = chat.send_message(
            db,
            conversation=conversation,
            content=text,
            drafting_service=drafting,
            actor="anwalt@kanzlei.test",
        )
        persisted = (
            db.query(ChatMessage)
            .filter(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.created_at)
            .all()
        )
        captured = CAPTURED[before:] if len(CAPTURED) > before else []
        report.append(
            {
                "turn": index,
                "user_text": text,
                "conversation_id": conversation.id,
                "persisted_message_count": len(persisted),
                "persisted_roles": [m.role for m in persisted],
                "payload_sent_to_cloud": captured[0] if captured else None,
                "assistant_answer": (answer.content or "")[:160],
                "blocked": answer.blocked,
            }
        )

    out = Path(__file__).parent / "payload_capture.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 78)
    print("CONVERSATION-MEMORY / PAYLOAD-FORENSIK")
    print("=" * 78)
    for entry in report:
        print(f"\n--- TURN {entry['turn']}: {entry['user_text']!r}")
        print(f"    conversation_id      : {entry['conversation_id']}")
        print(f"    persistierte Messages: {entry['persisted_message_count']} "
              f"{entry['persisted_roles']}")
        payload = entry["payload_sent_to_cloud"]
        if payload is None:
            print("    PAYLOAD              : KEINER (Fast Path / kein Cloud-Aufruf)")
        else:
            print(f"    Payload-Felder       : {payload['_payload_fields']}")
            print(f"    schreibauftrag       : {payload['schreibauftrag']}")
            print(f"    sachverhalt          : {payload['anonymisierter_sachverhalt']!r}")
            print(f"    argumentationspunkte : {payload['anonymisierte_argumentationspunkte']}")
            print(f"    anwaltl. Anmerkungen : "
                  f"{payload['anonymisierte_anwaltliche_anmerkungen']!r}")
        print(f"    Antwort              : {entry['assistant_answer']!r}")

    print("\n" + "=" * 78)
    print("KERNFRAGE: Enthaelt der Payload von TURN 4 die Inhalte aus TURN 2/3?")
    print("=" * 78)
    last = report[-1]["payload_sent_to_cloud"]
    if last is None:
        print("Kein Payload in Turn 4 - Fast Path.")
    else:
        blob = json.dumps(last, ensure_ascii=False)
        for probe in ["Steuerrecht", "Steuerbescheid", "Hallo"]:
            print(f"  enthaelt {probe!r}: {probe in blob}")
    print(f"\nRohdaten: {out}")


if __name__ == "__main__":
    main()
