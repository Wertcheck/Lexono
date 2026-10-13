"""Datenminimierung pro KI-Anfrage (13.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md §29). Nur synthetische Daten.

Reproduziert: Bei einer Chatfrage zu einem ausdruecklich angehaengten Dokument ging der Inhalt ALLER Aktendokumente an die
Cloud-KI (Betraege und Daten anderer Vertraege, Mahnungen, Altschreiben). Behoben: nur angehaengte/ausgewaehlte Dokumente
gehen in den Sachverhalt; Fristen bleiben aktenweit; ein sichtbarer Hinweis nennt die Zahl nicht uebermittelter
Dokumente. Ohne Dokumentbezug bleibt alles unveraendert (ganze Akte, kein Hinweis)."""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy.orm import Session

from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.chat.service import ChatService
from app.drafting.review_notes import split_review_notes
from app.models import Client, Deadline, Document, Matter, User
from tests.test_chat_service import _drafting_service, chat_service, db_session, user  # noqa: F401 - Fixtures

INVOICE = "Rechnung Nr. 2026-117: Estricharbeiten 4.250,00 EUR, zahlbar bis 15.08.2026."
CONTRACT = "Werkvertrag vom 01.03.2026 über Estricharbeiten, Pauschalpreis 18.500,00 EUR."
REMINDER = "Mahnung: Bitte begleichen Sie 2.100,00 EUR bis spätestens 30.10.2026."
OLD_LETTER = "Altes Schreiben vom 03.05.2019 über eine Kaution von 350,00 EUR."


def _matter(db: Session) -> tuple[Matter, dict[str, Document]]:
    client = Client(name="Synthetische Bau GmbH")
    matter = Matter(client=client, title="Estrich Werklohn")
    db.add_all([client, matter])
    db.flush()
    docs = {}
    for key, text in (("invoice", INVOICE), ("contract", CONTRACT), ("reminder", REMINDER), ("old", OLD_LETTER)):
        docs[key] = Document(matter_id=matter.id, file_path=f"{key}.txt", original_filename=f"{key}.txt",
                             extracted_text=text, classified_type="Schreiben")
        db.add(docs[key])
    db.flush()
    db.add(Deadline(matter_id=matter.id, document_id=docs["reminder"].id, source_text="begleichen bis spätestens 30.10.2026",
                    due_date=date(2026, 10, 30)))
    db.commit()
    return matter, docs


def _ask(db, chat_service, user, matter, content, document_ids=None, conversation=None):
    conversation = conversation or chat_service.create_conversation(
        db, user=user, matter_id=matter.id, title="Frage", actor=user.email
    )
    message = chat_service.record_user_message(db, conversation=conversation, content=content, document_ids=document_ids)
    drafting, writer = _drafting_service("Antwort.")
    reply = chat_service.send_message(db, conversation=conversation, content=content, drafting_service=drafting,
                                      actor=user.email, current_message_id=message.id)
    payload = json.dumps(writer.received_payloads[0].model_dump(), ensure_ascii=False) if writer.received_payloads else ""
    return conversation, reply, payload


def test_attached_document_only_is_sent_and_deadlines_stay(db_session: Session, user: User, chat_service: ChatService) -> None:
    matter, docs = _matter(db_session)
    _, reply, payload = _ask(db_session, chat_service, user, matter, "Ist diese Rechnung korrekt?", [docs["invoice"].id])
    assert "4.250,00 EUR" in payload and "15.08.2026" in payload  # angehaengtes Dokument vollstaendig
    for other in ("18.500,00", "01.03.2026", "350,00", "03.05.2019"):
        assert other not in payload  # andere Aktendokumente nicht uebermittelt
    assert "30.10.2026" in payload  # Frist der Akte bleibt erhalten
    body, notes = split_review_notes(reply.content)
    assert "von 3 weiteren Dokumenten dieser Akte wurde nicht berücksichtigt" in str(notes)  # Hinweisbereich, nicht Antworttext
    assert "nicht berücksichtigt" not in body


def test_follow_up_without_attachment_uses_the_whole_matter_again(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Review §29: eine Folgefrage mit "Dokumente" ("Welche Dokumente der Akte erwähnen eine Kaution?") wurde auf den
    frueheren Anhang eingeengt - Faktenverlust. Eingeengt wird nur durch Anhaenge der AKTUELLEN Nachricht."""
    matter, docs = _matter(db_session)
    conversation, _, _ = _ask(db_session, chat_service, user, matter, "Fasse die Rechnung zusammen.", [docs["invoice"].id])
    _, reply, payload = _ask(db_session, chat_service, user, matter, "Welche Dokumente der Akte erwähnen eine Kaution?",
                             conversation=conversation)
    assert "350,00" in payload and "18.500,00" in payload
    assert "nicht berücksichtigt" not in reply.content


def test_duplicate_of_the_attachment_is_not_counted_as_missing(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    matter, docs = _matter(db_session)
    db_session.add(Document(matter_id=matter.id, file_path="dup.txt", extracted_text=INVOICE))
    db_session.commit()
    _, reply, _ = _ask(db_session, chat_service, user, matter, "Ist diese Rechnung korrekt?", [docs["invoice"].id])
    assert "von 3 weiteren Dokumenten" in reply.content


def test_question_without_document_reference_still_uses_the_whole_matter(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    matter, _ = _matter(db_session)
    _, reply, payload = _ask(db_session, chat_service, user, matter, "Welche Beträge stehen im Raum?")
    for fact in ("4.250,00", "18.500,00", "2.100,00", "350,00"):
        assert fact in payload
    assert "nicht berücksichtigt" not in reply.content


def test_scope_note_is_not_repeated_in_the_next_payload_history(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    matter, docs = _matter(db_session)
    conversation, _, _ = _ask(db_session, chat_service, user, matter, "Ist diese Rechnung korrekt?", [docs["invoice"].id])
    _, _, payload = _ask(db_session, chat_service, user, matter, "Danke, und allgemein?", conversation=conversation)
    assert "nicht berücksichtigt" not in payload


def test_prepare_draft_context_with_document_ids_stays_in_the_matter(db_session: Session) -> None:
    matter, docs = _matter(db_session)
    other_client = Client(name="Fremde GmbH")
    other = Matter(client=other_client, title="Fremd")
    db_session.add_all([other_client, other])
    db_session.flush()
    foreign = Document(matter_id=other.id, file_path="f.txt", extracted_text="Fremder Inhalt 999,00 EUR")
    db_session.add(foreign)
    db_session.commit()
    prep = RuleBasedLocalAIProvider().prepare_draft_context(
        matter.id, db_session, document_ids=[docs["invoice"].id, foreign.id]
    )
    assert prep.included_document_ids == [docs["invoice"].id]
    assert "999,00" not in prep.sachverhalt and "4.250,00" in prep.sachverhalt


def test_streaming_path_is_scoped_too(db_session: Session, user: User, chat_service: ChatService) -> None:
    from app.models import ChatMessage

    matter, docs = _matter(db_session)
    conversation = chat_service.create_conversation(db_session, user=user, matter_id=matter.id, title="F", actor=user.email)
    message = chat_service.record_user_message(db_session, conversation=conversation, content="Ist diese Rechnung korrekt?",
                                               document_ids=[docs["invoice"].id])
    drafting, writer = _drafting_service("Antwort.")
    list(chat_service.send_message_stream(db_session, conversation=conversation, content="Ist diese Rechnung korrekt?",
                                          drafting_service=drafting, actor=user.email, current_message_id=message.id))
    payload = json.dumps(writer.received_payloads[0].model_dump(), ensure_ascii=False)
    assert "4.250,00 EUR" in payload and "18.500,00" not in payload and "30.10.2026" in payload
    last = (db_session.query(ChatMessage).filter_by(conversation_id=conversation.id, role="assistant")
            .order_by(ChatMessage.created_at.desc()).first())
    assert "von 3 weiteren Dokumenten dieser Akte wurde nicht berücksichtigt" in last.content
