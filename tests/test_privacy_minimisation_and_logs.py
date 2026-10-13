"""Datenminimierung, Aktenisolation der Wissensbasis und Protokolle (13.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md §27).
Nur synthetische Daten.

Reproduziert und behoben: (1) doppelt hochgeladene Dokumente gingen zweimal an die Cloud; (2) Fristen aus geloeschten
Dokumenten gingen weiter an die Cloud; (3) ein freigegebener Wissenseintrag mit dem Namen des Mandanten einer ANDEREN Akte
ging im Klartext in die Cloud-Anfrage dieser Akte (der Gateway kennt nur die Namen der eigenen Akte); (4) SQL-Parameter
(gespeicherte Werte) in Datenbank-Fehlermeldungen; (5) Pfad im Audit-Eintrag eines wiederhergestellten Intake-Fehlers;
(6) Suchbegriffe im uvicorn-Zugriffsprotokoll."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.models import AuditEvent, Client, Deadline, Document, Draft, DraftFeedback, Matter
from app.models.base import Base
from app.search.service import DocumentSearchService
from tests.fake_embedding_provider import FakeEmbeddingProvider


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _matter(db: Session, client_name: str = "Theda Ommerborn", area: str = "Arbeitsrecht") -> Matter:
    client = Client(name=client_name)
    matter = Matter(client=client, title=f"{client_name.split()[-1]} Kuendigung", practice_area=area)
    db.add_all([client, matter])
    db.commit()
    return matter


def _doc(db: Session, matter: Matter, text: str, **kwargs) -> Document:
    document = Document(matter_id=matter.id, file_path="synthetisch.txt", extracted_text=text, **kwargs)
    db.add(document)
    db.commit()
    return document


def test_duplicate_upload_is_sent_once_but_both_count_as_included(db: Session) -> None:
    matter = _matter(db)
    first = _doc(db, matter, "Abnahmeprotokoll: Risse im Estrich, Nachbesserung bis 30.11.2026.")
    second = _doc(db, matter, "Abnahmeprotokoll: Risse im Estrich, Nachbesserung bis 30.11.2026.")
    other = _doc(db, matter, "Rechnung über 4.200 EUR für Estricharbeiten.")
    prep = RuleBasedLocalAIProvider().prepare_draft_context(matter.id, db)
    assert prep.sachverhalt.count("Abnahmeprotokoll") == 1
    assert "4.200 EUR" in prep.sachverhalt  # kein Sachverhaltsverlust
    assert set(prep.included_document_ids) == {first.id, second.id, other.id}


def test_deadline_from_a_deleted_document_is_not_sent(db: Session) -> None:
    matter = _matter(db)
    kept = _doc(db, matter, "Die Frist zur Stellungnahme endet am 30.11.2026.")
    gone = _doc(db, matter, "Zahlung binnen 14 Tagen bis 15.12.2026.", deleted_at=datetime.now(timezone.utc))
    db.add_all([
        Deadline(matter_id=matter.id, document_id=kept.id, source_text="Stellungnahme endet am 30.11.2026",
                 due_date=date(2026, 11, 30)),
        Deadline(matter_id=matter.id, document_id=gone.id, source_text="Zahlung bis 15.12.2026",
                 due_date=date(2026, 12, 15)),
        Deadline(matter_id=matter.id, source_text="Termin am 20.12.2026", due_date=date(2026, 12, 20)),
    ])
    db.commit()
    punkte = RuleBasedLocalAIProvider()._build_argumentationspunkte(matter.id, db)
    assert any("30.11.2026" in p for p in punkte) and any("20.12.2026" in p for p in punkte)
    assert not any("15.12.2026" in p for p in punkte)


def _knowledge(db: Session, search: DocumentSearchService, matter: Matter, text: str):
    from app.feedback.service import DraftFeedbackService
    from app.knowledge.service import KnowledgeItemService

    draft = Draft(matter_id=matter.id, content="x")
    db.add(draft)
    db.commit()
    feedback = DraftFeedback(draft_id=draft.id, original_content=text, approval_status="approved", actor="a@x.test")
    db.add(feedback)
    db.commit()
    service = KnowledgeItemService(search_service=search)
    item = DraftFeedbackService(service).promote_to_knowledge(feedback, db, title="Arbeitsrecht Abfindung", actor="a@x.test")
    service.approve(item, db, actor="a@x.test")
    return item


def test_knowledge_item_naming_another_matters_client_is_not_sent(db: Session) -> None:
    from app.drafting.service import DraftingService
    from app.privacy.gateway import ClaudePrivacyGateway
    from app.research.service import LegalResearchService
    from tests.test_drafting_service import FakeClaudeWritingProvider

    search = DocumentSearchService(FakeEmbeddingProvider())
    matter_a = _matter(db, "Quirin Zabelhuber")
    matter_b = _matter(db, "Theda Ommerborn")
    _knowledge(db, search, matter_a, "Arbeitsrecht Muster: Im Fall Zabelhuber erzielten wir 18.500 EUR Abfindung.")
    neutral = _knowledge(db, search, matter_a, "Arbeitsrecht Muster: Abfindung regelmäßig ein halbes Monatsgehalt je Jahr.")

    prep = RuleBasedLocalAIProvider(search).prepare_draft_context(matter_b.id, db)
    assert not any("Zabelhuber" in q for q in prep.quellenverweise)
    assert any("Monatsgehalt" in q for q in prep.quellenverweise)  # neutraler Eintrag bleibt

    drafting = DraftingService(RuleBasedLocalAIProvider(search), LegalResearchService(search, min_score_for_sufficient=0.0), search,
                               ClaudePrivacyGateway(), FakeClaudeWritingProvider(response_text="x"), model_name="m")
    used, texts = drafting._gather_knowledge_items(matter_b, db)
    assert not any("Zabelhuber" in t for t in texts)
    assert [u.knowledge_item_id for u in used] == [neutral.id]

    # in der eigenen Akte ist der eigene Mandantenname kein Ausschlussgrund (der Gateway kennt ihn und maskiert ihn)
    own = RuleBasedLocalAIProvider(search).prepare_draft_context(matter_a.id, db)
    assert any("Zabelhuber" in q for q in own.quellenverweise)


def test_organisation_names_do_not_exclude_by_legal_form() -> None:
    from app.ai_providers.local_ai_provider import _name_variants, mentions_any_name

    assert _name_variants("Brezlmayr Logistik GmbH") == {"Brezlmayr Logistik GmbH"}
    assert not mentions_any_name("Eine GmbH haftet beschränkt.", _name_variants("Brezlmayr Logistik GmbH"))
    assert mentions_any_name("Herr Zabelhuber klagte.", _name_variants("Quirin Zabelhuber"))
    assert not mentions_any_name("Zabelhuberstraße 4", _name_variants("Quirin Zabelhuber"))


def test_database_errors_do_not_log_stored_values() -> None:
    from app.db.session import engine

    assert engine.hide_parameters is True


def test_recovered_intake_error_audit_has_no_path(db: Session) -> None:
    from app.errors.service import RetryService

    service = RetryService()
    path = r"C:\Scan\Mandant Erika Beispiel - Kuendigung.pdf"
    service.record_failure(db, entity_type="IntakeFile", entity_id=path, operation="intake",
                           error_category="transient", error_message="x")
    service.record_success(db, entity_type="IntakeFile", entity_id=path, operation="intake")
    details = [e.details for e in db.query(AuditEvent).all()]
    assert details and not any("Erika" in (d or "") for d in details)


def test_access_log_line_has_no_query_string() -> None:
    from app.observability.logging_config import StripQueryStringFilter

    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1:5000", "GET", "/dashboard/search/results?q=Erika%20Beispiel", "1.1", 200),
                               None)
    StripQueryStringFilter().filter(record)
    line = record.getMessage()
    assert "Erika" not in line and "/dashboard/search/results" in line and "200" in line


@pytest.mark.parametrize("text", ["Zabelhubers Anspruch", "Im Fall ZABELHUBER", "Herrn Zabelhuber's Klage"])
def test_name_filter_catches_genitive_and_uppercase(text: str) -> None:
    """Review §27: "Zabelhubers" (Genitiv) und Grossschreibung umgingen den Filter."""
    from app.ai_providers.local_ai_provider import _name_variants, mentions_any_name

    assert mentions_any_name(text, _name_variants("Erika Zabelhuber"))


@pytest.mark.parametrize(
    "name, harmless",
    [("Amtsgericht München", "Klage beim Gericht in München"), ("Stadt Köln", "Termin in Köln"),
     ("Bundesagentur für Arbeit", "Recht auf Arbeit"), ("Deutsche Rentenversicherung Bund", "Bund und Länder")],
)
def test_organisation_names_count_only_in_full(name: str, harmless: str) -> None:
    """Review §27: das letzte Wort von Gerichten/Behoerden schloss jeden Eintrag mit "München", "Arbeit" usw. aus."""
    from app.ai_providers.local_ai_provider import _name_variants, mentions_any_name

    assert _name_variants(name) == {name}
    assert not mentions_any_name(harmless, _name_variants(name))
    assert mentions_any_name(f"Bescheid der {name} vom Mai", _name_variants(name))


def test_surname_first_notation_uses_the_surname() -> None:
    from app.ai_providers.local_ai_provider import _name_variants

    assert "Mustermann" in _name_variants("Mustermann, Max") and "Max" not in _name_variants("Mustermann, Max")


def test_court_and_lawyer_parties_of_other_matters_count_only_in_full(db: Session) -> None:
    from app.ai_providers.local_ai_provider import foreign_matter_names
    from app.models import Party

    other = _matter(db, "Quirin Zabelhuber")
    own = _matter(db, "Theda Ommerborn")
    db.add_all([Party(matter_id=other.id, name="Dr. Hans Recht", role="Rechtsanwalt"),
                Party(matter_id=other.id, name="Paula Kraft", role="Gegner")])
    db.commit()
    names = foreign_matter_names(own, db)
    assert "Recht" not in names and "Dr. Hans Recht" in names  # Anwalt: nur voller Name
    assert "Kraft" in names and "Zabelhuber" in names and "Ommerborn" not in names
