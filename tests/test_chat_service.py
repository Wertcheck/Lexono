"""Tests für app/chat/service.py (UI-Überarbeitung: Chat als zentrale
Arbeitsoberfläche).

Nutzt dieselbe echte, ungemockte Privacy-Pipeline wie tests/test_privacy_canary.py
- nur der Cloud-Aufruf selbst ist ein Fake (FakeClaudeWritingProvider aus
tests/test_drafting_service.py, kein neues Test-Double)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.chat.service import ChatService
from app.drafting.service import DraftingService
from app.models import ChatConversation, ChatMessage, Client, Matter, Role, User
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.research.service import LegalResearchService
from app.search.service import DocumentSearchService
from tests.fake_embedding_provider import FakeEmbeddingProvider
from tests.test_drafting_service import FakeClaudeWritingProvider


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def user(db_session: Session) -> User:
    role = Role(name="anwalt")
    db_session.add(role)
    db_session.commit()
    u = User(email="anwalt@kanzlei.test", password_hash="x", role_id=role.id)
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture()
def chat_service(tmp_path: Path) -> ChatService:
    return ChatService(tmp_path / "chat_uploads")


def _drafting_service(response_text: str = "Formulierte Antwort.") -> tuple[DraftingService, FakeClaudeWritingProvider]:
    search_service = DocumentSearchService(FakeEmbeddingProvider())
    research_service = LegalResearchService(search_service, min_score_for_sufficient=0.0)
    writer = FakeClaudeWritingProvider(response_text=response_text)
    service = DraftingService(
        RuleBasedLocalAIProvider(search_service),
        research_service,
        search_service,
        ClaudePrivacyGateway(),
        writer,
        model_name="claude-sonnet-5",
    )
    return service, writer


# --- CHAT FIRST / DRAFTING ONLY WHEN REQUESTED (echter Abnahme-Test-Fund,
# 13.09.): der Chat rief DraftingService.create_draft bisher IMMER mit
# purpose="formulate_draft" auf - "Was steht in § 558 BGB?" erzeugte
# dadurch einen formellen Briefentwurf statt einer normalen Antwort. Diese
# Tests pruefen die tatsaechliche Entscheidungsebene (_looks_like_drafting_
# request), nicht nur oberflaechliches String-Matching im Testcode - jeder
# Fall entspricht einem der in der Nutzervorgabe genannten Beispiele. ---

from app.chat.service import _looks_like_drafting_request, _normalize_law_code  # noqa: E402


@pytest.mark.parametrize(
    "raw_code,expected",
    [
        ("BGB", "BGB"),
        ("bgb", "BGB"),
        ("SGB I", "SGBI"),
        ("SGB II", "SGBII"),
        ("sgb ii", "SGBII"),
        ("SGB 1", "SGBI"),
        ("SGB 12", "SGBXII"),
        ("SGBXII", "SGBXII"),
    ],
)
def test_normalize_law_code(raw_code: str, expected: str) -> None:
    """ECHTER FUND (14.09., SGB-Import): die uebliche Zitierweise "SGB I"/
    "SGB 1" hat ein eingebettetes Leerzeichen - muss auf denselben
    gespeicherten Code wie beim Import normalisiert werden (siehe
    scripts/import_gesetze_im_internet.py::_CODE_OVERRIDES fuer die
    Gegenseite)."""
    assert _normalize_law_code(raw_code) == expected


@pytest.mark.parametrize(
    "content",
    [
        "Was steht in § 558 BGB?",
        "Erkläre mir § 558 BGB.",
        "Welche Voraussetzungen gelten nach § 558 BGB?",
        "Was bedeutet diese Klausel?",
        "Fasse das Dokument zusammen.",
        "Prüfe dieses Schreiben auf rechtliche Probleme.",
        "Welche Fristen ergeben sich aus diesem Schreiben?",
        "Formuliere diesen Absatz verständlicher.",
        "Was könnte man gegen diese Kündigung machen?",
        "Und wie unterscheidet sich das von § 559?",
        "Und welche Argumente sprechen für den Mieter?",
        "hallo",
        "Bitte analysiere das angehängte Dokument und fasse die wichtigsten Punkte zusammen.",
        "Bitte fasse das angehängte Dokument in wenigen Sätzen zusammen.",
        "Was steht in meinem bisherigen Einspruch?",
        "Wie stehen die Erfolgsaussichten für einen Widerspruch?",
    ],
)
def test_looks_like_drafting_request_is_false_for_general_chat_and_analysis(content: str) -> None:
    assert _looks_like_drafting_request(content) is False


@pytest.mark.parametrize(
    "content",
    [
        "Schreibe eine Antwort an den Vermieter.",
        "Erstelle einen Schriftsatz gegen die Kündigung.",
        "Formuliere aus der Analyse eine Klage.",
        "Schreib mir daraus eine Antwort an den Vermieter.",
        "Bitte erstelle einen Schriftsatz-Entwurf zu folgendem Sachverhalt: ",
        "Erstelle daraus einen Einspruch.",
        "Verfasse einen Widerspruch gegen den Bescheid.",
        "Schreibe eine Beschwerde an das Gericht.",
    ],
)
def test_looks_like_drafting_request_is_true_for_explicit_drafting_requests(content: str) -> None:
    assert _looks_like_drafting_request(content) is True


def test_send_message_uses_chat_purpose_for_a_normal_question(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Ende-zu-Ende ueber send_message (nicht nur die reine Funktion): eine
    normale Rechtsfrage muss mit purpose="chat_response" bei der
    (gefakten) Cloud-KI ankommen, NICHT "formulate_draft"."""
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Frage", actor=user.email
    )
    drafting_service, writer = _drafting_service("§ 558 BGB regelt die Mieterhöhung.")

    chat_service.send_message(
        db_session,
        conversation=conversation,
        content="Was steht in § 558 BGB?",
        drafting_service=drafting_service,
        actor=user.email,
    )

    assert len(writer.received_payloads) == 1
    assert writer.received_payloads[0].schreibauftrag == "chat_response"


def test_send_message_traces_routing_and_downstream_steps_under_one_trace_id(
    db_session: Session, user: User, chat_service: ChatService, caplog: pytest.LogCaptureFixture
) -> None:
    """P0 Performance-Root-Cause-Run (13.09.): die Chat-Routing-Entscheidung
    (`_looks_like_drafting_request`) und die anschliessende
    `DraftingService.create_draft`-Pipeline muessen unter DERSELBEN
    PerfTrace-Correlation-ID nachvollziehbar sein - keine Klartextdaten,
    nur Schrittname + Dauer + Trace-ID (siehe app/observability/
    perf_trace.py)."""
    import logging

    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Frage", actor=user.email
    )
    drafting_service, _ = _drafting_service("§ 558 BGB regelt die Mieterhöhung.")

    with caplog.at_level(logging.INFO, logger="lexono.perf"):
        chat_service.send_message(
            db_session,
            conversation=conversation,
            content="Was steht in § 558 BGB?",
            drafting_service=drafting_service,
            actor=user.email,
        )

    perf_records = [r for r in caplog.records if r.name == "lexono.perf"]
    messages = [r.getMessage() for r in perf_records]
    steps = [m.split("step=")[1].split(" ")[0] for m in messages]
    assert "routing" in steps
    assert "claude" in steps
    trace_ids = {m.split("trace=")[1].split(" ")[0] for m in messages}
    assert len(trace_ids) == 1, "routing und Pipeline-Schritte muessen dieselbe Trace-ID teilen"
    for message in messages:
        assert "§" not in message
        assert "558" not in message


def test_send_message_uses_draft_purpose_for_an_explicit_drafting_request(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Gegenprobe: eine explizite Schriftsatz-Anfrage muss weiterhin
    purpose="formulate_draft" verwenden - der bestehende Drafting-Workflow
    bleibt fuer echte Auftraege unveraendert erreichbar."""
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Entwurf", actor=user.email
    )
    drafting_service, writer = _drafting_service("Sehr geehrte Damen und Herren...")

    chat_service.send_message(
        db_session,
        conversation=conversation,
        content="Schreibe eine Antwort an den Vermieter.",
        drafting_service=drafting_service,
        actor=user.email,
    )

    assert len(writer.received_payloads) == 1
    assert writer.received_payloads[0].schreibauftrag == "formulate_draft"


# --- Anbindung "Gesetze im Internet" an den Chat (13.09.): eine REINE
# Normzitat-Frage, deren Paragraph bereits lokal importiert ist, wird
# DIREKT aus der lokalen Gesetzesbibliothek beantwortet - kein Presidio-,
# Local-AI- oder Claude-Aufruf. `_find_law_section` sucht case-insensitive
# ueber die Ziffernfolge, `LawSection.section_number` selbst traegt weiter
# das "§ "-Praefix (siehe app/models/law_section.py). ---

from app.laws.service import import_law_fixture_data  # noqa: E402
from app.models import LawSection  # noqa: E402


def _import_real_bgb_558(db_session: Session) -> None:
    """Ein ECHTER (gekuerzter) Ausschnitt des amtlichen § 558 BGB-Texts -
    identischer Wortlaut-Ausschnitt wie tests/fixtures/
    gesetze_im_internet_bgb_sample.xml, keine erfundenen Testdaten."""
    import_law_fixture_data(
        db_session,
        {
            "code": "BGB",
            "title": "Bürgerliches Gesetzbuch",
            "sections": [
                {
                    "section_number": "§ 558",
                    "title": "Mieterhöhung bis zur ortsüblichen Vergleichsmiete",
                    "text_content": (
                        "(1) Der Vermieter kann die Zustimmung zu einer Erhöhung der Miete "
                        "bis zur ortsüblichen Vergleichsmiete verlangen, wenn die Miete in "
                        "dem Zeitpunkt, zu dem die Erhöhung eintreten soll, seit 15 Monaten "
                        "unverändert ist."
                    ),
                    "last_updated": "2024-01-01",
                }
            ],
        },
    )
    section = db_session.query(LawSection).filter_by(law_code="BGB", section_number="§ 558").first()
    section.source_name = "Gesetze im Internet"
    section.source_url = "https://www.gesetze-im-internet.de/bgb/__558.html"
    section.doknr = "BJNR001950896BJNE056206360"
    db_session.commit()


def _import_real_gg_art1(db_session: Session) -> None:
    """Ein ECHTER Ausschnitt des amtlichen Art 1 GG-Texts - Artikel-
    basiertes Gesetz (KEIN "§"-Zeichen), deckt den Fast-Path fuer
    Artikel-Zitate ab (ECHTER FUND 13.09. beim Import des GG)."""
    import_law_fixture_data(
        db_session,
        {
            "code": "GG",
            "title": "Grundgesetz für die Bundesrepublik Deutschland",
            "sections": [
                {
                    "section_number": "Art 1",
                    "title": "Menschenwürde",
                    "text_content": (
                        "(1) Die Würde des Menschen ist unantastbar. Sie zu achten und zu "
                        "schützen ist Verpflichtung aller staatlichen Gewalt."
                    ),
                    "last_updated": "2024-01-01",
                }
            ],
        },
    )
    section = db_session.query(LawSection).filter_by(law_code="GG", section_number="Art 1").first()
    section.source_name = "Gesetze im Internet"
    section.source_url = "https://www.gesetze-im-internet.de/gg/art_1.html"
    db_session.commit()


@pytest.mark.parametrize(
    "content",
    [
        "Was steht in Art 1 GG?",
        "Art 1 GG",
        "Artikel 1 GG",
        "Erkläre mir Art 1 GG.",
    ],
)
def test_send_message_answers_pure_article_question_directly_from_local_law_db(
    db_session: Session, user: User, chat_service: ChatService, content: str
) -> None:
    """Artikel-basierte Gesetze (GG) muessen denselben Fast Path nutzen
    wie Paragraphen-basierte (BGB) - ECHTER FUND 13.09."""
    _import_real_gg_art1(db_session)
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Frage", actor=user.email
    )

    message = chat_service.send_message(
        db_session,
        conversation=conversation,
        content=content,
        drafting_service=None,
        actor=user.email,
    )

    assert message.blocked is False
    assert message.law_section_id is not None
    assert "unantastbar" in message.content


def _import_real_sgb1_section1(db_session: Session) -> None:
    """Ein ECHTER Ausschnitt des amtlichen § 1 SGB I-Texts - deckt die
    mehrteilige Gesetzeskuerzel-Schreibweise "SGB I"/"SGB 1" (mit
    Leerzeichen) ab, ECHTER FUND 14.09. beim SGB-Import (siehe
    _normalize_law_code)."""
    import_law_fixture_data(
        db_session,
        {
            "code": "SGBI",
            "title": "Sozialgesetzbuch (SGB) Erstes Buch (I) - Allgemeiner Teil",
            "sections": [
                {
                    "section_number": "§ 1",
                    "title": "Aufgaben des Sozialgesetzbuchs",
                    "text_content": (
                        "(1) Das Recht des Sozialgesetzbuchs soll zur Verwirklichung "
                        "sozialer Gerechtigkeit und sozialer Sicherheit Sozialleistungen "
                        "einschließlich sozialer und erzieherischer Hilfen gestalten."
                    ),
                    "last_updated": "2024-01-01",
                }
            ],
        },
    )
    section = db_session.query(LawSection).filter_by(law_code="SGBI", section_number="§ 1").first()
    section.source_name = "Gesetze im Internet"
    section.source_url = "https://www.gesetze-im-internet.de/sgb_1/__1.html"
    db_session.commit()


@pytest.mark.parametrize(
    "content",
    [
        "Was steht in § 1 SGB I?",
        "§ 1 SGB I",
        "§ 1 SGB 1",
        "Was regelt § 1 SGB I?",
    ],
)
def test_send_message_answers_pure_norm_question_for_sgb_book_with_space_in_code(
    db_session: Session, user: User, chat_service: ChatService, content: str
) -> None:
    """ECHTER FUND (14.09., SGB-Import): die uebliche Zitierweise "SGB I"/
    "SGB 1" hat ein eingebettetes Leerzeichen, das der generische
    Gesetzeskuerzel-Zweig NICHT abdeckt (kein Leerzeichen, keine Ziffern
    erlaubt) - `_normalize_law_code` fasst beide Schreibweisen auf
    denselben gespeicherten Code ("SGBI") zusammen."""
    _import_real_sgb1_section1(db_session)
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Frage", actor=user.email
    )

    message = chat_service.send_message(
        db_session,
        conversation=conversation,
        content=content,
        drafting_service=None,
        actor=user.email,
    )

    assert message.blocked is False
    assert message.law_section_id is not None
    assert "sozialer Gerechtigkeit" in message.content


def test_send_message_does_not_confuse_sgb_citation_with_case_context(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Kritische Gegenprobe: eine Nachricht mit echtem Fallbezug im selben
    Satz darf trotz gueltigem SGB-Zitat NICHT den Fast Path ausloesen."""
    _import_real_sgb1_section1(db_session)
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Frage", actor=user.email
    )

    message = chat_service.send_message(
        db_session,
        conversation=conversation,
        content="Was steht in § 1 SGB I, und betrifft das meinen Mandanten Max Mustermann?",
        drafting_service=None,
        actor=user.email,
    )

    assert message.law_section_id is None


@pytest.mark.parametrize(
    "content",
    [
        "Was steht in § 558 BGB?",
        "§ 558 BGB",
        "§558 BGB",
        "Erkläre mir § 558 BGB.",
    ],
)
def test_send_message_answers_pure_norm_question_directly_from_local_law_db(
    db_session: Session, user: User, chat_service: ChatService, content: str
) -> None:
    """Der zentrale neue Fall: ist der Paragraph lokal bereits importiert,
    antwortet der Chat SOFORT aus der lokalen Gesetzesbibliothek - ganz
    ohne `drafting_service`/Cloud-Aufruf (funktioniert daher auch ohne
    konfigurierten API-Schluessel, siehe drafting_service=None weiter
    unten)."""
    _import_real_bgb_558(db_session)
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Frage", actor=user.email
    )

    message = chat_service.send_message(
        db_session,
        conversation=conversation,
        content=content,
        drafting_service=None,
        actor=user.email,
    )

    assert message.blocked is False
    assert message.draft_id is None
    assert message.law_section_id is not None
    assert "ortsüblichen Vergleichsmiete" in message.content
    assert "Gesetze im Internet" in message.content


def test_send_message_ignores_deactivated_law_and_falls_back(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """26.09., Owner-Direktive "KANZLEIWISSEN FINAL PRODUCT IMPLEMENTATION"
    §20: ein in Kanzleiwissen DEAKTIVIERTES Gesetz (siehe app/laws/
    service.py::toggle_law_active) darf vom Chat-Fast-Path nicht mehr
    gefunden werden, obwohl die Paragraphen technisch noch in der DB
    liegen - identisches Verhalten wie ein nie importiertes Gesetz
    (transparenter Fallback auf die volle Pipeline, hier sichtbar an der
    "nicht konfiguriert"-Meldung bei `drafting_service=None`)."""
    from app.laws.service import toggle_law_active

    _import_real_bgb_558(db_session)
    toggle_law_active(db_session, "BGB", active=False)
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Frage", actor=user.email
    )

    message = chat_service.send_message(
        db_session,
        conversation=conversation,
        content="Was steht in § 558 BGB?",
        drafting_service=None,
        actor=user.email,
    )

    assert message.law_section_id is None
    assert message.blocked is True
    assert "nicht konfiguriert" in message.content


def test_send_message_norm_question_fast_path_needs_no_drafting_service(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Explizite Gegenprobe zur Doku-Aussage 'funktioniert auch ohne
    API-Schluessel': `drafting_service=None` fuehrt HIER NICHT zur
    Provider-nicht-konfiguriert-Meldung, weil der Fast Path bereits vorher
    greift."""
    _import_real_bgb_558(db_session)
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Frage", actor=user.email
    )

    message = chat_service.send_message(
        db_session,
        conversation=conversation,
        content="Was steht in § 558 BGB?",
        drafting_service=None,
        actor=user.email,
    )

    assert message.blocked is False
    assert "nicht konfiguriert" not in message.content


@pytest.mark.parametrize(
    "content",
    [
        "Was steht in § 558 BGB, und wie wirkt sich das auf meinen Mandanten Max Mustermann aus?",
        "Schreibe mir einen Schriftsatz zu § 558 BGB.",
        "§ 558 BGB - und was ist mit § 559?",
        "Was steht in § 559 BGB?",  # nicht importiert -> Fallback auf volle Pipeline
    ],
)
def test_send_message_does_not_use_fast_path_for_non_pure_norm_questions(
    db_session: Session, user: User, chat_service: ChatService, content: str
) -> None:
    """Kritische Gegenprobe (Sicherheitsanforderung): jede Nachricht mit
    echtem Fallbezug/Zusatzinhalt ODER einem lokal nicht vorhandenen
    Paragraphen MUSS weiterhin die volle, unveraenderte Pipeline (inkl.
    Pseudonymisierung + Cloud-KI) durchlaufen - der Fast Path darf hier
    NIEMALS greifen."""
    _import_real_bgb_558(db_session)
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Frage", actor=user.email
    )
    drafting_service, writer = _drafting_service("Antwort der Cloud-KI.")

    message = chat_service.send_message(
        db_session,
        conversation=conversation,
        content=content,
        drafting_service=drafting_service,
        actor=user.email,
    )

    assert message.law_section_id is None
    assert len(writer.received_payloads) == 1


# --- Aktenbestand-Fastpath (16.09., OPEN_ISSUES.md "P1 - Chat kennt den
# AKTENBESTAND nicht", Diagnose vom 14.09. jetzt umgesetzt) -------------

from datetime import datetime, timedelta, timezone  # noqa: E402

from app.chat.service import _find_most_recently_active_matter  # noqa: E402
from app.drafting.quick_matter import PLACEHOLDER_CLIENT_NAME  # noqa: E402
from app.models import Document, Message  # noqa: E402

_RECENT_MATTER_QUESTIONS = [
    "Was ist die aktuellste Akte?",
    "was ist die neueste akte",
    "Welche Akte ist die aktuellste?",
    "Zeig mir die letzte Akte.",
]


def _matter(
    db_session: Session, *, client_name: str, title: str, created_at: datetime | None = None
) -> Matter:
    client = Client(name=client_name)
    matter = Matter(client=client, title=title)
    if created_at is not None:
        matter.created_at = created_at
    db_session.add_all([client, matter])
    db_session.commit()
    return matter


def _touch_with_document(db_session: Session, matter: Matter, *, at: datetime) -> None:
    db_session.add(
        Document(matter_id=matter.id, file_path="irrelevant.pdf", created_at=at)
    )
    db_session.commit()


def _touch_with_message(db_session: Session, matter: Matter, *, at: datetime) -> None:
    db_session.add(
        Message(matter_id=matter.id, direction="inbound", created_at=at)
    )
    db_session.commit()


@pytest.mark.parametrize("content", _RECENT_MATTER_QUESTIONS)
def test_send_message_answers_recent_matter_question_directly_from_db(
    db_session: Session, user: User, chat_service: ChatService, content: str
) -> None:
    """Der zentrale neue Fall: mehrere echte Akten mit bekannten
    Aktivitaets-Zeitstempeln -> die Antwort muss die tatsaechlich juengste
    nennen, kein Cloud-Aufruf (drafting_service=None reicht, genau wie
    beim Norm-Fast-Path)."""
    now = datetime.now(timezone.utc)
    older = _matter(db_session, client_name="Schneider GmbH", title="Alte Akte")
    _touch_with_document(db_session, older, at=now - timedelta(days=10))
    newest = _matter(db_session, client_name="Müller AG", title="Neueste Akte")
    _touch_with_message(db_session, newest, at=now - timedelta(hours=1))
    middle = _matter(db_session, client_name="Becker & Partner", title="Mittlere Akte")
    _touch_with_document(db_session, middle, at=now - timedelta(days=3))

    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Bestandsfrage", actor=user.email
    )

    message = chat_service.send_message(
        db_session,
        conversation=conversation,
        content=content,
        drafting_service=None,
        actor=user.email,
    )

    assert message.blocked is False
    assert "Neueste Akte" in message.content
    assert "Müller AG" in message.content
    # Aktenisolation: keine andere Akte wird in derselben Antwort genannt.
    assert "Alte Akte" not in message.content
    assert "Mittlere Akte" not in message.content


def test_recent_matter_fastpath_excludes_schnellentwurf_placeholder_matters(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """ECHTER FUND (16.09., siehe OPEN_ISSUES.md "225 Junk-Fristen"): ohne
    Ausschluss waere die Antwort auf "aktuellste Akte" fast immer trivial/
    nutzlos - naemlich exakt die Schnellentwurf-Akte, die durch das
    Anlegen DIESER Konversation gerade erst automatisch entstanden ist.
    Eine echte, aeltere Akte muss stattdessen genannt werden."""
    now = datetime.now(timezone.utc)
    real_matter = _matter(db_session, client_name="Weber Steuerberatung", title="Echte Akte")
    _touch_with_document(db_session, real_matter, at=now - timedelta(days=1))

    # Ohne Aktenauswahl -> create_quick_matter legt automatisch eine neue
    # "Schnellentwurf"-Akte unter dem Sammel-Mandanten an, JUENGER als
    # real_matter (genau jetzt).
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Bestandsfrage", actor=user.email
    )
    assert conversation.matter.client.name == PLACEHOLDER_CLIENT_NAME

    message = chat_service.send_message(
        db_session,
        conversation=conversation,
        content="Was ist die aktuellste Akte?",
        drafting_service=None,
        actor=user.email,
    )

    assert "Echte Akte" in message.content
    assert "Weber Steuerberatung" in message.content


def test_recent_matter_fastpath_falls_back_to_full_pipeline_without_real_matters(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Gibt es (nach Ausschluss der Schnellentwuerfe) KEINE einzige echte
    Akte, faellt die Frage transparent auf die volle Pipeline zurueck -
    exakt wie beim Norm-Fast-Path ohne lokal importierten Paragraphen."""
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Bestandsfrage", actor=user.email
    )
    drafting_service, writer = _drafting_service("Antwort der Cloud-KI.")

    message = chat_service.send_message(
        db_session,
        conversation=conversation,
        content="Was ist die aktuellste Akte?",
        drafting_service=drafting_service,
        actor=user.email,
    )

    assert message.content == "Antwort der Cloud-KI."
    assert len(writer.received_payloads) == 1


@pytest.mark.parametrize(
    "content",
    [
        "Was ist die aktuellste Akte, und betrifft das meinen Mandanten Max Mustermann?",
        "Erstelle einen Schriftsatz für die aktuellste Akte.",
        "Ich frage mich, was die aktuellste Akte in Steuersachen ist.",
    ],
)
def test_recent_matter_fastpath_does_not_trigger_for_non_pure_questions(
    db_session: Session, user: User, chat_service: ChatService, content: str
) -> None:
    """Kritische Gegenprobe (wie beim Norm-Fast-Path): jede Nachricht mit
    echtem Fallbezug/Zusatzinhalt MUSS weiterhin die volle Pipeline
    durchlaufen - der Fast Path darf hier NIEMALS greifen."""
    _matter(db_session, client_name="Schneider GmbH", title="Irgendeine Akte")
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Frage", actor=user.email
    )
    drafting_service, writer = _drafting_service("Antwort der Cloud-KI.")

    chat_service.send_message(
        db_session,
        conversation=conversation,
        content=content,
        drafting_service=drafting_service,
        actor=user.email,
    )

    assert len(writer.received_payloads) == 1


def test_find_most_recently_active_matter_ignores_updated_at_as_activity_signal(
    db_session: Session
) -> None:
    """ECHTER FUND (16.09.): `Matter.updated_at` wird beim INSERT immer auf
    die tatsaechliche Wanduhrzeit gesetzt, unabhaengig vom (kuenstlich
    zurueckdatierten) Alter ihrer Dokumente/Nachrichten - wuerde es in die
    Aktivitaets-Berechnung einfliessen, wuerde eine ganz frisch angelegte
    Akte OHNE jede echte Aktivitaet eine echte, kuerzlich bearbeitete Akte
    faelschlich verdraengen. Reflektiert dieselbe Erkenntnis wie die
    urspruengliche Diagnose vom 14.09. (siehe OPEN_ISSUES.md)."""
    now = datetime.now(timezone.utc)
    # Kuenstlich zurueckdatiertes `created_at` bei BEIDEN Akten (nicht auf
    # die reale Insert-Reihenfolge im Test verlassen, die sonst zufaellig
    # dasselbe falsche Ergebnis vortaeuschen koennte).
    really_active = _matter(
        db_session, client_name="A GmbH", title="Wirklich aktiv",
        created_at=now - timedelta(days=30),
    )
    _touch_with_document(db_session, really_active, at=now - timedelta(hours=1))

    old_and_empty = _matter(
        db_session, client_name="B GmbH", title="Alt und ohne jede Aktivitaet",
        created_at=now - timedelta(days=10),
    )

    found = _find_most_recently_active_matter(db_session)
    assert found is not None
    best_matter, _ = found
    assert best_matter.id == really_active.id
    assert best_matter.id != old_and_empty.id


def test_find_most_recently_active_matter_falls_back_to_created_at_without_any_activity(
    db_session: Session
) -> None:
    """Eine Akte OHNE jedes Dokument/jede Nachricht braucht trotzdem einen
    sinnvollen Rueckfallwert - ihr eigenes Anlagedatum (`created_at`, NICHT
    `updated_at`, siehe Funktionsdocstring)."""
    now = datetime.now(timezone.utc)
    older = _matter(
        db_session, client_name="A GmbH", title="Aeltere leere Akte",
        created_at=now - timedelta(days=20),
    )
    newer = _matter(
        db_session, client_name="B GmbH", title="Neuere leere Akte",
        created_at=now - timedelta(days=1),
    )

    found = _find_most_recently_active_matter(db_session)
    assert found is not None
    best_matter, activity = found
    assert best_matter.id == newer.id
    assert activity == newer.created_at


def test_send_message_stream_recent_matter_question_yields_one_delta_then_done(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Streaming-Variante: identisches Verhalten wie beim Norm-Fast-Path -
    ein Delta mit der vollstaendigen Antwort, dann done, kein Cloud-Call."""
    now = datetime.now(timezone.utc)
    matter = _matter(db_session, client_name="Fischer & Kollegen", title="Streaming-Akte")
    _touch_with_document(db_session, matter, at=now - timedelta(hours=2))

    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Bestandsfrage", actor=user.email
    )

    events = list(
        chat_service.send_message_stream(
            db_session,
            conversation=conversation,
            content="Was ist die aktuellste Akte?",
            drafting_service=None,
            actor=user.email,
        )
    )

    delta_events = [e for e in events if e.kind == "delta"]
    done_events = [e for e in events if e.kind == "done"]
    assert len(delta_events) == 1
    assert "Streaming-Akte" in delta_events[0].text
    assert len(done_events) == 1
    assert done_events[0].message is not None
    assert done_events[0].message.content == delta_events[0].text


# --- send_message_stream (13.09., Streaming-Architekturentscheidung) --

from tests.test_drafting_service_streaming import FakeStreamingClaudeWritingProvider  # noqa: E402


def _streaming_drafting_service(chunks: list[str]) -> tuple[DraftingService, FakeStreamingClaudeWritingProvider]:
    search_service = DocumentSearchService(FakeEmbeddingProvider())
    research_service = LegalResearchService(search_service, min_score_for_sufficient=0.0)
    writer = FakeStreamingClaudeWritingProvider(chunks=chunks)
    service = DraftingService(
        RuleBasedLocalAIProvider(search_service),
        research_service,
        search_service,
        ClaudePrivacyGateway(),
        writer,
        model_name="claude-sonnet-5",
    )
    return service, writer


def test_send_message_stream_yields_real_deltas_for_eligible_chat_message(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Frage", actor=user.email
    )
    drafting_service, writer = _streaming_drafting_service(["Guten ", "Tag, ", "hier die Antwort."])

    events = list(
        chat_service.send_message_stream(
            db_session,
            conversation=conversation,
            content="Was ist ein Mietvertrag?",
            drafting_service=drafting_service,
            actor=user.email,
        )
    )

    delta_events = [e for e in events if e.kind == "delta"]
    done_events = [e for e in events if e.kind == "done"]
    assert [e.text for e in delta_events] == ["Guten ", "Tag, ", "hier die Antwort."]
    assert len(done_events) == 1
    message = done_events[0].message
    assert message is not None
    assert message.content == "Guten Tag, hier die Antwort."
    assert message.blocked is False
    assert len(writer.stream_received_payloads) == 1


def test_send_message_stream_pure_norm_question_yields_one_delta_then_done(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Der bereits bestehende Norm-Fast-Path (Gesetze im Internet) bleibt
    unveraendert - er liefert (da ohnehin ~instant) ein einzelnes Delta mit
    dem vollstaendigen Text, dann sofort "done", OHNE drafting_service zu
    benoetigen."""
    _import_real_bgb_558(db_session)
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Frage", actor=user.email
    )

    events = list(
        chat_service.send_message_stream(
            db_session,
            conversation=conversation,
            content="Was steht in § 558 BGB?",
            drafting_service=None,
            actor=user.email,
        )
    )

    delta_events = [e for e in events if e.kind == "delta"]
    done_events = [e for e in events if e.kind == "done"]
    assert len(delta_events) == 1
    assert "ortsüblichen Vergleichsmiete" in delta_events[0].text
    assert done_events[0].message.law_section_id is not None


def test_send_message_stream_drafting_purpose_falls_back_to_single_chunk(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Ein expliziter Schreibauftrag (purpose=formulate_draft) ist per
    Definition nie streaming-faehig (siehe _should_skip_llm_privacy_layers)
    - liefert ein einzelnes Delta mit dem vollstaendigen Entwurfstext."""
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Entwurf", actor=user.email
    )
    drafting_service, writer = _streaming_drafting_service(["Sehr geehrte Damen und Herren..."])

    events = list(
        chat_service.send_message_stream(
            db_session,
            conversation=conversation,
            content="Schreibe eine Antwort an den Vermieter.",
            drafting_service=drafting_service,
            actor=user.email,
        )
    )

    delta_events = [e for e in events if e.kind == "delta"]
    assert len(delta_events) == 1
    assert delta_events[0].text == "Sehr geehrte Damen und Herren..."
    assert writer.stream_received_payloads == []
    assert len(writer.received_payloads) == 1


def test_send_message_stream_without_provider_yields_blocked_message(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Frage", actor=user.email
    )

    events = list(
        chat_service.send_message_stream(
            db_session,
            conversation=conversation,
            content="Was ist ein Mietvertrag?",
            drafting_service=None,
            actor=user.email,
        )
    )

    done_events = [e for e in events if e.kind == "done"]
    assert done_events[0].message.blocked is True
    assert "nicht konfiguriert" in done_events[0].message.content


# --- Konversationen ---------------------------------------------------


def test_create_conversation_without_matter_auto_creates_one(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Erste Frage", actor=user.email
    )
    assert conversation.matter_id is not None
    matter = db_session.query(Matter).filter_by(id=conversation.matter_id).first()
    assert matter is not None


def test_create_conversation_reuses_existing_matter(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    client = Client(name="Testmandant GmbH")
    matter = Matter(client=client, title="Bestehende Akte")
    db_session.add_all([client, matter])
    db_session.commit()

    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=matter.id, title="Frage zur Akte", actor=user.email
    )
    assert conversation.matter_id == matter.id


def test_long_title_gets_truncated_for_display(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    long_title = "A" * 120
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title=long_title, actor=user.email
    )
    assert len(conversation.title) <= 60
    assert conversation.title.endswith("…")


def test_list_conversations_only_returns_own(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    role = db_session.query(Role).first()
    other_user = User(email="andere@kanzlei.test", password_hash="x", role_id=role.id)
    db_session.add(other_user)
    db_session.commit()

    chat_service.create_conversation(db_session, user=user, matter_id=None, title="Meine", actor=user.email)
    chat_service.create_conversation(
        db_session, user=other_user, matter_id=None, title="Fremde", actor=other_user.email
    )

    own = chat_service.list_conversations(db_session, user=user)
    assert len(own) == 1
    assert own[0].title == "Meine"


# --- Nachrichten: Canary-Beweis (kein Klartext-Leck) --------------------


def test_send_message_pseudonymizes_before_cloud_call(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Beweis: der Chat sendet AUSSCHLIESSLICH pseudonymisierten Inhalt an
    die (gefakte) Cloud-KI - identische Garantie wie beim Schriftsatz-
    Generator, hier ueber den Chat-Pfad reproduziert."""
    client = Client(name="Erika Mustermann")
    matter = Matter(client=client, title="Einspruch Steuerbescheid")
    db_session.add_all([client, matter])
    db_session.commit()

    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=matter.id, title="Zusammenfassen", actor=user.email
    )
    drafting_service, writer = _drafting_service("Hier die Zusammenfassung.")

    # CHAT-02 (15.09.): Rueckgabewert jetzt erfasst und als
    # `current_message_id` durchgereicht - exakt das reale Aufrufmuster des
    # Routers (siehe app/web/chat_router.py). Ohne das wuerde die aktuelle
    # Nachricht zusaetzlich als Gespraechsverlauf-Eintrag erscheinen (die
    # History-Abfrage schliesst sie nur aus, wenn ihre ID bekannt ist) -
    # ein Zustand, der in der echten Anwendung nie auftritt.
    user_message = chat_service.record_user_message(
        db_session, conversation=conversation, content="Fasse die Akte für Erika Mustermann zusammen"
    )
    reply = chat_service.send_message(
        db_session,
        conversation=conversation,
        content="Fasse die Akte für Erika Mustermann zusammen",
        drafting_service=drafting_service,
        actor=user.email,
        current_message_id=user_message.id,
    )

    assert reply.role == "assistant"
    assert reply.blocked is False
    assert reply.content == "Hier die Zusammenfassung."
    assert len(writer.received_payloads) == 1
    payload_json = writer.received_payloads[0].model_dump_json()
    assert "Erika Mustermann" not in payload_json


def test_send_message_without_provider_returns_clear_blocked_message(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=None, title="Frage", actor=user.email
    )
    reply = chat_service.send_message(
        db_session, conversation=conversation, content="Hallo", drafting_service=None, actor=user.email
    )
    assert reply.blocked is True
    assert "nicht konfiguriert" in reply.content
    assert "API" not in reply.content or "Schlüssel" in reply.content  # keine rohe Fehlermeldung/Traceback


class _AlwaysBlockSecurityCheck:
    """Deterministischer Test-Stub statt eines organischen Text-Triggers
    (frueher: ALL-CAPS-Text, der zufaellig die Grossschreibungs-Heuristik
    ausloeste - seit der POS-Tag-Verfeinerung dieser Heuristik, 13.09.,
    nicht mehr zuverlaessig/robust genug fuer einen Test, der NUR das
    downstream-Verhalten bei EINEM BELIEBIGEN Block pruefen will, nicht
    einen bestimmten Heuristik-Mechanismus)."""

    def check(self, pseudonymized_text, mappings, *, purpose):
        from app.privacy.security_check_schema import SecurityCheckResult

        return SecurityCheckResult(
            passed=False,
            reasons=["Möglicherweise nicht erkannte Namen/Entitäten gefunden: ['Test Person']"],
        )


def test_send_message_gateway_block_uses_friendly_message_not_raw_reason(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Ein blockierter Gateway-Aufruf (z. B. unzulaessiger Zweck oder
    Restrisiko) darf NIE die rohen Blockierungsgruende im Chat anzeigen -
    dieselbe Regel wie ueberall sonst im Projekt (friendly_block_message)."""
    client = Client(name="Mandant GmbH")
    matter = Matter(client=client, title="Testakte")
    db_session.add_all([client, matter])
    db_session.commit()
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=matter.id, title="Testfrage", actor=user.email
    )
    drafting_service, writer = _drafting_service()
    drafting_service.gateway = ClaudePrivacyGateway(security_check=_AlwaysBlockSecurityCheck())

    blocking_text = "Eine ganz normale Nachricht."
    chat_service.record_user_message(db_session, conversation=conversation, content=blocking_text)
    reply = chat_service.send_message(
        db_session,
        conversation=conversation,
        content=blocking_text,
        drafting_service=drafting_service,
        actor=user.email,
    )

    assert reply.blocked is True
    assert len(writer.received_payloads) == 0
    # Die rohen Gruende (koennten erkannte Namen enthalten) duerfen nicht
    # im gespeicherten Chat-Text stehen.
    assert "Test Person" not in reply.content
    assert reply.content != ""


def test_send_message_unexpected_exception_is_caught_fail_closed(
    db_session: Session, user: User, chat_service: ChatService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wirft `DraftingService.create_draft` unerwartet eine Ausnahme (z. B.
    ein Presidio/spaCy-Absturz), darf der Chat weder abstuerzen noch die
    Ausnahme-Details anzeigen - fail-closed, klarer Chat-Fehlerzustand."""
    client = Client(name="Mandant GmbH")
    matter = Matter(client=client, title="Testakte")
    db_session.add_all([client, matter])
    db_session.commit()
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=matter.id, title="Testfrage", actor=user.email
    )
    drafting_service, _ = _drafting_service()

    def _boom(*args, **kwargs):
        raise RuntimeError("Simulierter Presidio-Absturz mit sensiblem Text: Erika Mustermann")

    monkeypatch.setattr(drafting_service, "create_draft", _boom)

    reply = chat_service.send_message(
        db_session, conversation=conversation, content="Hallo", drafting_service=drafting_service, actor=user.email
    )

    assert reply.blocked is True
    assert "Erika Mustermann" not in reply.content
    assert "RuntimeError" not in reply.content


# --- CHAT-02 (15.09.): Gespraechsverlauf/History ------------------------
# Owner-Entscheidungen A-D final (siehe .agentic/DECISIONS.md fuer die
# vollstaendige Herleitung): A max. 10 Messages, B max. 3.000/12.000
# Zeichen, C user+assistant, D nur aktuelle Conversation.


def _history_conversation(
    db_session: Session, user: User, chat_service: ChatService, *, title: str = "Verlauf-Test"
) -> ChatConversation:
    client = Client(name="Mandant GmbH")
    matter = Matter(client=client, title="Testakte")
    db_session.add_all([client, matter])
    db_session.commit()
    return chat_service.create_conversation(
        db_session, user=user, matter_id=matter.id, title=title, actor=user.email
    )


def _add_message(
    db_session: Session, conversation: ChatConversation, *, role: str, content: str, blocked: bool = False
) -> ChatMessage:
    message = ChatMessage(
        conversation_id=conversation.id, role=role, content=content, blocked=blocked
    )
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)
    return message


def test_build_history_includes_user_messages(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    conversation = _history_conversation(db_session, user, chat_service)
    _add_message(db_session, conversation, role="user", content="Erste Frage")

    history = chat_service._build_history(db_session, conversation=conversation, exclude_message_id=None)

    assert history == ["Anwalt: Erste Frage"]


def test_build_history_includes_assistant_messages(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    conversation = _history_conversation(db_session, user, chat_service)
    _add_message(db_session, conversation, role="assistant", content="Erste Antwort")

    history = chat_service._build_history(db_session, conversation=conversation, exclude_message_id=None)

    assert history == ["Assistent: Erste Antwort"]


def test_build_history_preserves_both_roles_and_chronological_order(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Owner-Beispiel woertlich nachgebaut: User-Frage -> Assistant-Antwort
    -> User-Anschlussfrage muss in GENAU dieser Reihenfolge ankommen."""
    conversation = _history_conversation(db_session, user, chat_service)
    _add_message(db_session, conversation, role="user", content="Es geht um den Steuerbescheid von Max Mustermann.")
    _add_message(
        db_session,
        conversation,
        role="assistant",
        content="Der Bescheid kann grundsätzlich innerhalb eines Monats angefochten werden.",
    )

    history = chat_service._build_history(db_session, conversation=conversation, exclude_message_id=None)

    assert history == [
        "Anwalt: Es geht um den Steuerbescheid von Max Mustermann.",
        "Assistent: Der Bescheid kann grundsätzlich innerhalb eines Monats angefochten werden.",
    ]


def test_build_history_respects_the_ten_message_limit(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Owner-Entscheidung A: maximal 10 History-Messages, ausgewaehlt nach
    Aktualitaet (die 12 juengsten von insgesamt 14 erzeugten sollten auf
    die 10 juengsten gekappt werden)."""
    conversation = _history_conversation(db_session, user, chat_service)
    for i in range(14):
        _add_message(db_session, conversation, role="user", content=f"Nachricht {i}")

    history = chat_service._build_history(db_session, conversation=conversation, exclude_message_id=None)

    assert len(history) == 10
    # Die 10 JUENGSTEN (4..13), nicht die 10 aeltesten.
    assert history[0] == "Anwalt: Nachricht 4"
    assert history[-1] == "Anwalt: Nachricht 13"


def test_build_history_truncates_a_single_long_message_to_3000_chars(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Owner-Entscheidung B, woertlich: 'Eine einzelne sehr lange Message
    darf daher nicht das gesamte History-Budget verbrauchen.'"""
    conversation = _history_conversation(db_session, user, chat_service)
    _add_message(db_session, conversation, role="user", content="X" * 5_000)

    history = chat_service._build_history(db_session, conversation=conversation, exclude_message_id=None)

    assert len(history) == 1
    # "Anwalt: " (8 Zeichen) + 3.000 Zeichen Inhalt.
    assert len(history[0]) == len("Anwalt: ") + 3_000


def test_build_history_respects_the_12000_char_total_budget(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Owner-Entscheidung B: 5 Nachrichten a 3.000 Zeichen Inhalt (bereits
    einzeln am Limit) - MIT dem "Anwalt: "-Praefix (8 Zeichen) sind das
    3.008 Zeichen je uebertragener Zeile, macht 15.040 Zeichen gesamt.
    Das Gesamtbudget von 12.000 erlaubt nur 3 davon (3 * 3.008 = 9.024,
    ein 4. wuerde auf 12.032 ueberschreiten). Prioritaet 2 vor 3
    (Owner-Vorgabe woertlich): die JUENGSTEN werden bevorzugt behalten,
    nicht die aeltesten."""
    conversation = _history_conversation(db_session, user, chat_service)
    for i in range(5):
        _add_message(db_session, conversation, role="user", content=f"{i}" * 3_000)

    history = chat_service._build_history(db_session, conversation=conversation, exclude_message_id=None)

    assert len(history) == 3
    total_chars = sum(len(entry) for entry in history)
    assert total_chars <= 12_000
    # Die BEIDEN AELTESTEN Nachrichten (Index 0, 1) wurden verworfen, nicht
    # juengere - Aktualitaet hat Vorrang vor Vollstaendigkeit.
    assert not any(entry.startswith("Anwalt: 0000") for entry in history)
    assert not any(entry.startswith("Anwalt: 1111") for entry in history)
    assert history[-1].startswith("Anwalt: 4444")
    assert history[0].startswith("Anwalt: 2222")


def test_build_history_selection_is_deterministic(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Owner-Vorgabe: die Kuerzungs-/Auswahllogik muss deterministisch
    sein - zweimaliger Aufruf mit identischem Zustand liefert dasselbe
    Ergebnis."""
    conversation = _history_conversation(db_session, user, chat_service)
    for i in range(12):
        _add_message(db_session, conversation, role="user", content=f"Nachricht {i}" * 100)

    first = chat_service._build_history(db_session, conversation=conversation, exclude_message_id=None)
    second = chat_service._build_history(db_session, conversation=conversation, exclude_message_id=None)

    assert first == second


def test_build_history_excludes_the_current_message(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """CHAT-02 Kernanforderung ('CURRENT MESSAGE', Owner-Vorgabe): die
    gerade gestellte aktuelle Frage darf nicht zusaetzlich als History
    erscheinen."""
    conversation = _history_conversation(db_session, user, chat_service)
    _add_message(db_session, conversation, role="user", content="Alte Nachricht")
    current = _add_message(db_session, conversation, role="user", content="Aktuelle Nachricht")

    history = chat_service._build_history(
        db_session, conversation=conversation, exclude_message_id=current.id
    )

    assert history == ["Anwalt: Alte Nachricht"]
    assert not any("Aktuelle Nachricht" in entry for entry in history)


def test_build_history_uses_only_the_current_conversation(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Owner-Entscheidung D: ausschliesslich die aktuelle ChatConversation -
    eine ANDERE Konversation (auch derselben Akte) darf nicht einfliessen."""
    conversation_a = _history_conversation(db_session, user, chat_service, title="Konversation A")
    conversation_b = chat_service.create_conversation(
        db_session, user=user, matter_id=conversation_a.matter_id, title="Konversation B", actor=user.email
    )
    _add_message(db_session, conversation_a, role="user", content="Nachricht in A")
    _add_message(db_session, conversation_b, role="user", content="Nachricht in B")

    history_a = chat_service._build_history(db_session, conversation=conversation_a, exclude_message_id=None)

    assert history_a == ["Anwalt: Nachricht in A"]
    assert not any("Nachricht in B" in entry for entry in history_a)


def test_build_history_excludes_a_conversation_of_a_different_matter(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Owner-Entscheidung D, Gegenprobe: eine Konversation einer VOLLSTAENDIG
    ANDEREN Akte darf erst recht nicht einfliessen (Aktenisolation)."""
    conversation_a = _history_conversation(db_session, user, chat_service, title="Akte A")
    conversation_other_matter = _history_conversation(db_session, user, chat_service, title="Akte B")
    _add_message(db_session, conversation_a, role="user", content="Nachricht in Akte A")
    _add_message(db_session, conversation_other_matter, role="user", content="Nachricht in Akte B")

    history_a = chat_service._build_history(db_session, conversation=conversation_a, exclude_message_id=None)

    assert history_a == ["Anwalt: Nachricht in Akte A"]
    assert not any("Akte B" in entry for entry in history_a)


def test_build_history_excludes_blocked_messages(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Eine blockierte Nachricht traegt als `content` eine inhaltsfreie
    Fehlermeldung (`friendly_block_message`), kein echter
    Gespraechsbeitrag - darf nicht als History-Kontext erscheinen."""
    conversation = _history_conversation(db_session, user, chat_service)
    _add_message(db_session, conversation, role="user", content="Echte Frage")
    _add_message(
        db_session, conversation, role="assistant", content="Blockiert aus Datenschutzgründen.", blocked=True
    )

    history = chat_service._build_history(db_session, conversation=conversation, exclude_message_id=None)

    assert history == ["Anwalt: Echte Frage"]


# --- CHAT-02: Integration - Pseudonymisierung/Fail-Closed ----------------


def test_send_message_pseudonymizes_history_before_cloud_call(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Beweis (Owner-Vorgabe: 'History muss denselben Privacy-Schutz
    erhalten wie die uebrigen Claude-relevanten Inhalte'): ein Mandantenname
    aus einer FRUEHEREN Nachricht darf im an Claude gesendeten Payload
    NIRGENDS im Klartext erscheinen - auch nicht im neuen achten Feld."""
    conversation = _history_conversation(db_session, user, chat_service)
    _add_message(
        db_session, conversation, role="user", content="Es geht um meinen Mandanten Klaus Andersen."
    )
    drafting_service, writer = _drafting_service("Verstanden.")

    current = chat_service.record_user_message(db_session, conversation=conversation, content="Welche Frist gilt?")
    reply = chat_service.send_message(
        db_session,
        conversation=conversation,
        content="Welche Frist gilt?",
        drafting_service=drafting_service,
        actor=user.email,
        current_message_id=current.id,
    )

    assert reply.blocked is False
    assert len(writer.received_payloads) == 1
    payload = writer.received_payloads[0]
    assert payload.anonymisierter_gespraechsverlauf, "Gespraechsverlauf haette befuellt sein muessen"
    assert "Klaus Andersen" not in payload.model_dump_json()
    # Der Verlauf muss aber trotzdem INHALTLICH ankommen (pseudonymisiert).
    assert any("Anwalt:" in entry for entry in payload.anonymisierter_gespraechsverlauf)


def test_send_message_repseudonymizes_reconstructed_assistant_history(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Owner-Vorgabe woertlich: 'Das gilt ausdruecklich auch fuer
    gespeicherten Assistant-Content.' `ChatMessage.content` einer
    Assistant-Zeile ist bereits lokal rekonstruierter Klartext (siehe
    app/models/chat_message.py) - er muss beim NAECHSTEN Aufruf erneut
    durch die Pseudonymisierung laufen, bevor er (als History) wieder
    Richtung Claude geht."""
    conversation = _history_conversation(db_session, user, chat_service)
    # Simuliert eine bereits lokal REKONSTRUIERTE fruehere Antwort - genau
    # der Zustand, in dem ChatMessage.content fuer role="assistant"
    # tatsaechlich gespeichert ist.
    _add_message(
        db_session,
        conversation,
        role="assistant",
        content="Ihr Mandant Klaus Andersen hat noch bis zum 15. April Zeit.",
    )
    drafting_service, writer = _drafting_service("Verstanden.")

    current = chat_service.record_user_message(db_session, conversation=conversation, content="Und danach?")
    reply = chat_service.send_message(
        db_session,
        conversation=conversation,
        content="Und danach?",
        drafting_service=drafting_service,
        actor=user.email,
        current_message_id=current.id,
    )

    assert reply.blocked is False
    payload = writer.received_payloads[0]
    assert "Klaus Andersen" not in payload.model_dump_json()
    assert any("Assistent:" in entry for entry in payload.anonymisierter_gespraechsverlauf)


def test_send_message_keeps_placeholder_consistent_between_sachverhalt_and_history(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Placeholder-Konsistenz innerhalb EINES Requests: derselbe Mandant
    (Client.name, fliesst ueber known_entities/Aktentitel in den
    Sachverhalt ein) UND derselbe Name in der History muessen DENSELBEN
    Platzhalter erhalten - das ist der Grund, warum CHAT-02 bewusst KEINE
    zweite, unabhaengige Pseudonymisierung fuer die History einfuehrt,
    sondern GENAU DENSELBEN gemeinsamen Durchlauf nutzt."""
    client = Client(name="Erika Mustermann")
    matter = Matter(client=client, title="Erika Mustermann offen 1")
    db_session.add_all([client, matter])
    db_session.commit()
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=matter.id, title="Konsistenz-Test", actor=user.email
    )
    _add_message(
        db_session, conversation, role="user", content="Meine Mandantin ist Erika Mustermann."
    )
    drafting_service, writer = _drafting_service("Verstanden.")

    current = chat_service.record_user_message(db_session, conversation=conversation, content="Welche Frist gilt für sie?")
    reply = chat_service.send_message(
        db_session,
        conversation=conversation,
        content="Welche Frist gilt für sie?",
        drafting_service=drafting_service,
        actor=user.email,
        current_message_id=current.id,
    )

    assert reply.blocked is False
    payload = writer.received_payloads[0]
    # Derselbe Platzhalter muss sowohl im (aus dem Aktentitel abgeleiteten)
    # Sachverhalt als auch im Gespraechsverlauf auftauchen - EIN Mapping,
    # nicht zwei verschiedene Platzhalter fuer dieselbe Person. Praeziser
    # Regex statt naivem Whitespace-Split (der z. B. "[MANDANT_01]." mit
    # angehaengtem Satzzeichen als eigenen "Platzhalter" gezaehlt haette).
    placeholder_pattern = re.compile(r"\[[A-Z_]+_\d+\]")
    verlauf_text = " ".join(payload.anonymisierter_gespraechsverlauf)
    sachverhalt_platzhalter = set(placeholder_pattern.findall(payload.anonymisierter_sachverhalt))
    verlauf_platzhalter = set(placeholder_pattern.findall(verlauf_text))
    assert sachverhalt_platzhalter, "Sachverhalt sollte einen Platzhalter enthalten (Mandantenname im Aktentitel)"
    assert verlauf_platzhalter, "Verlauf sollte einen Platzhalter enthalten (derselbe Mandantenname)"
    assert sachverhalt_platzhalter & verlauf_platzhalter, (
        "Sachverhalt und Verlauf muessten denselben Platzhalter fuer dieselbe Person teilen"
    )


def test_send_message_fails_closed_when_pseudonymization_of_history_raises(
    db_session: Session, user: User, chat_service: ChatService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owner-Vorgabe woertlich: 'Bei einem Fehler der Pseudonymisierung:
    Request abbrechen, bestehendes Fail-Closed-Verhalten verwenden, kein
    Klartext-Fallback.' Getestet MIT nicht-leerer History, um zu belegen,
    dass ihr Vorhandensein keinen zweiten, weniger sicheren Pfad eroeffnet -
    History durchlaeuft GENAU DENSELBEN Pseudonymizer-Aufruf wie alles
    andere, ein Fehler dort blockiert daher die GESAMTE Anfrage."""
    conversation = _history_conversation(db_session, user, chat_service)
    _add_message(db_session, conversation, role="user", content="Es geht um Klaus Andersen.")
    drafting_service, writer = _drafting_service("Sollte nie ankommen.")

    def _boom(*args, **kwargs):
        raise RuntimeError("Simulierter Presidio-Absturz")

    monkeypatch.setattr(drafting_service.gateway.pseudonymizer, "pseudonymize", _boom)

    current = chat_service.record_user_message(db_session, conversation=conversation, content="Welche Frist gilt?")
    reply = chat_service.send_message(
        db_session,
        conversation=conversation,
        content="Welche Frist gilt?",
        drafting_service=drafting_service,
        actor=user.email,
        current_message_id=current.id,
    )

    assert reply.blocked is True
    assert len(writer.received_payloads) == 0
    assert "Klaus Andersen" not in reply.content
    assert "RuntimeError" not in reply.content


def test_send_message_stream_includes_history_identically_to_send_message(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Owner-Vorgabe §5: send_message() und send_message_stream() muessen
    hinsichtlich der History dasselbe Verhalten besitzen."""
    conversation = _history_conversation(db_session, user, chat_service)
    _add_message(db_session, conversation, role="user", content="Es geht um Klaus Andersen.")
    drafting_service, writer = _drafting_service("Verstanden.")

    current = chat_service.record_user_message(db_session, conversation=conversation, content="Welche Frist gilt?")
    events = list(
        chat_service.send_message_stream(
            db_session,
            conversation=conversation,
            content="Welche Frist gilt?",
            drafting_service=drafting_service,
            actor=user.email,
            current_message_id=current.id,
        )
    )

    done_events = [e for e in events if e.kind == "done"]
    assert len(done_events) == 1
    assert done_events[0].message.blocked is False
    assert len(writer.received_payloads) == 1
    payload = writer.received_payloads[0]
    assert payload.anonymisierter_gespraechsverlauf
    assert "Klaus Andersen" not in payload.model_dump_json()


def test_send_message_stream_excludes_current_message_from_history(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Dieselbe 'CURRENT MESSAGE'-Anforderung wie bei send_message, hier
    fuer den Streaming-Pfad explizit nachgewiesen."""
    conversation = _history_conversation(db_session, user, chat_service)
    drafting_service, writer = _drafting_service("Antwort.")

    current = chat_service.record_user_message(
        db_session, conversation=conversation, content="Einmalige aktuelle Nachricht"
    )
    list(
        chat_service.send_message_stream(
            db_session,
            conversation=conversation,
            content="Einmalige aktuelle Nachricht",
            drafting_service=drafting_service,
            actor=user.email,
            current_message_id=current.id,
        )
    )

    payload = writer.received_payloads[0]
    assert payload.anonymisierter_gespraechsverlauf == []


# --- Dokumente ---------------------------------------------------------


def test_attach_document_path_traversal_filename_stays_contained(
    db_session: Session, user: User, chat_service: ChatService, tmp_path: Path
) -> None:
    """Sicherheitsregression (siehe Pilot Readiness Review + gleicher Fund
    im Schriftsatz-Generator): ein Dateiname mit '../'-Segmenten darf nicht
    dazu fuehren, dass die Datei ausserhalb des konfigurierten Upload-
    Ordners landet."""
    from io import BytesIO

    from starlette.datastructures import UploadFile as StarletteUploadFile
    from fastapi import UploadFile

    client = Client(name="Mandant GmbH")
    matter = Matter(client=client, title="Testakte")
    db_session.add_all([client, matter])
    db_session.commit()
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=matter.id, title="Upload-Test", actor=user.email
    )

    marker = b"%PDF-1.4 canary"
    upload = UploadFile(filename="../../../evil.pdf", file=BytesIO(marker))

    document = chat_service.attach_document(
        db_session,
        conversation=conversation,
        upload=upload,
        ocr_enabled=False,
        ocr_languages="deu+eng",
        tesseract_cmd=None,
        actor=user.email,
    )

    assert document is not None
    stored_path = Path(document.file_path).resolve()
    upload_dir = (tmp_path / "chat_uploads").resolve()
    assert upload_dir in stored_path.parents


# --- Dokument-Workspace (Masterprompt V2, Task #62) --------------------


def test_get_attached_document_returns_document_attached_to_this_conversation(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    client = Client(name="Mandant GmbH")
    matter = Matter(client=client, title="Testakte")
    db_session.add_all([client, matter])
    db_session.commit()
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=matter.id, title="Dokument-Test", actor=user.email
    )

    from io import BytesIO

    from fastapi import UploadFile

    upload = UploadFile(filename="finanzamt.pdf", file=BytesIO(b"%PDF-1.4 canary"))
    document = chat_service.attach_document(
        db_session,
        conversation=conversation,
        upload=upload,
        ocr_enabled=False,
        ocr_languages="deu+eng",
        tesseract_cmd=None,
        actor=user.email,
    )
    assert document is not None
    chat_service.record_user_message(
        db_session, conversation=conversation, content="Bitte prüfen.", document_ids=[document.id]
    )

    found = chat_service.get_attached_document(
        db_session, conversation=conversation, document_id=document.id
    )
    assert found is not None
    assert found.id == document.id


def test_get_attached_document_returns_none_for_document_from_other_conversation(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    """Aktenisolation: eine erratene/manipulierte Dokument-ID aus einer
    FREMDEN Konversation darf ueber den Dokument-Workspace nicht abrufbar
    sein (siehe ChatService.get_attached_document-Docstring)."""
    client = Client(name="Mandant GmbH")
    matter_a = Matter(client=client, title="Akte A")
    matter_b = Matter(client=client, title="Akte B")
    db_session.add_all([client, matter_a, matter_b])
    db_session.commit()
    conversation_a = chat_service.create_conversation(
        db_session, user=user, matter_id=matter_a.id, title="Konversation A", actor=user.email
    )
    conversation_b = chat_service.create_conversation(
        db_session, user=user, matter_id=matter_b.id, title="Konversation B", actor=user.email
    )

    from io import BytesIO

    from fastapi import UploadFile

    upload = UploadFile(filename="geheim.pdf", file=BytesIO(b"%PDF-1.4 canary"))
    document = chat_service.attach_document(
        db_session,
        conversation=conversation_a,
        upload=upload,
        ocr_enabled=False,
        ocr_languages="deu+eng",
        tesseract_cmd=None,
        actor=user.email,
    )
    assert document is not None
    chat_service.record_user_message(
        db_session, conversation=conversation_a, content="Bitte prüfen.", document_ids=[document.id]
    )

    found = chat_service.get_attached_document(
        db_session, conversation=conversation_b, document_id=document.id
    )
    assert found is None


def test_get_attached_document_returns_none_for_unknown_document_id(
    db_session: Session, user: User, chat_service: ChatService
) -> None:
    client = Client(name="Mandant GmbH")
    matter = Matter(client=client, title="Testakte")
    db_session.add_all([client, matter])
    db_session.commit()
    conversation = chat_service.create_conversation(
        db_session, user=user, matter_id=matter.id, title="Leere Konversation", actor=user.email
    )

    found = chat_service.get_attached_document(
        db_session, conversation=conversation, document_id="does-not-exist"
    )
    assert found is None
