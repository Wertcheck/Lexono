"""Warum wird selbst "Hallo" blockiert? Gateway direkt befragen."""
from __future__ import annotations
import sys
sys.path.insert(0, r"C:\Users\Bonit\Lexono")
sys.path.insert(0, r"C:\Users\Bonit\Lexono\tests")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.models import Client, Matter
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.search.service import DocumentSearchService
from fake_embedding_provider import FakeEmbeddingProvider

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(engine)
db = sessionmaker(bind=engine)()

client = Client(name="Muster, Anna")
matter = Matter(client=client, title="Einspruch Steuerbescheid 2025")
db.add_all([client, matter]); db.commit()

local = RuleBasedLocalAIProvider(DocumentSearchService(FakeEmbeddingProvider()))
prep = local.prepare_draft_context(matter.id, db)
print("=== prepare_draft_context Ergebnis ===")
print("sachverhalt          :", repr(prep.sachverhalt))
print("argumentationspunkte :", prep.argumentationspunkte)
print("known_entities       :", prep.known_entities)
print("has_document_context :", prep.has_document_context)

gw = ClaudePrivacyGateway()
for msg in ["Hallo", "Ich habe eine Frage zum Steuerrecht.", "Welche Frist gilt?"]:
    res = gw.prepare_request(
        purpose="chat_response",
        sachverhalt=prep.sachverhalt,
        argumentationspunkte=prep.argumentationspunkte,
        quellenverweise=[],
        stil=None, vorlage=None,
        anwaltliche_anmerkungen=msg,
        known_entities=prep.known_entities,
    )
    print(f"\n=== Nachricht {msg!r}")
    print("  allowed :", res.allowed)
    print("  reasons :", res.reasons)
    if res.payload:
        print("  sachverhalt im Payload:", repr(res.payload.anonymisierter_sachverhalt))
        print("  anmerkungen im Payload:", repr(res.payload.anonymisierte_anwaltliche_anmerkungen))
