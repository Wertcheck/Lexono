"""Tests fuer app/search/service.py (Prompt 11).

Nutzt FakeEmbeddingProvider (siehe tests/fake_embedding_provider.py) statt
eines echten Modells - kein Netzwerkzugriff noetig. Der Schwerpunkt liegt
auf der Isolationsgarantie: Dokumentensuche darf NIE ueber die Grenze
einer Akte hinweg Ergebnisse liefern."""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Client, Document, KnowledgeItem, Matter, Source
from app.models.base import Base
from app.search.service import DocumentSearchService
from tests.fake_embedding_provider import FakeEmbeddingProvider


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _matter(db: Session, title: str = "Testakte") -> Matter:
    client = Client(name="Testmandant")
    matter = Matter(client=client, title=title)
    db.add_all([client, matter])
    db.commit()
    return matter


def _service() -> DocumentSearchService:
    return DocumentSearchService(FakeEmbeddingProvider())


def test_fulltext_match_within_matter_is_found(db_session: Session) -> None:
    matter = _matter(db_session)
    document = Document(
        file_path="/tmp/x.pdf",
        matter_id=matter.id,
        extracted_text="Der Mietvertrag wurde am 01.01.2027 gekündigt.",
    )
    db_session.add(document)
    db_session.commit()

    results = _service().search_within_matter(matter.id, "Mietvertrag", db_session)

    assert len(results) == 1
    assert results[0].entity_id == document.id
    assert results[0].match_type == "fulltext"


def test_document_from_other_matter_is_never_returned(db_session: Session) -> None:
    """Kernanforderung aus Prompt 11: strikte Aktenisolation."""
    matter_a = _matter(db_session, title="Akte A")
    matter_b = _matter(db_session, title="Akte B")
    document_a = Document(
        file_path="/tmp/a.pdf", matter_id=matter_a.id, extracted_text="Kündigung Mietvertrag"
    )
    document_b = Document(
        file_path="/tmp/b.pdf", matter_id=matter_b.id, extracted_text="Kündigung Mietvertrag"
    )
    db_session.add_all([document_a, document_b])
    db_session.commit()

    results = _service().search_within_matter(matter_a.id, "Kündigung", db_session)

    assert len(results) == 1
    assert results[0].entity_id == document_a.id
    assert results[0].matter_id == matter_a.id


def test_document_without_matter_is_not_found_via_matter_search(
    db_session: Session,
) -> None:
    """Ein noch nicht zugeordnetes Dokument (matter_id=None) darf bei
    KEINER Aktensuche auftauchen - auch nicht versehentlich."""
    matter = _matter(db_session)
    unassigned_document = Document(
        file_path="/tmp/unassigned.pdf",
        matter_id=None,
        extracted_text="Kündigung Mietvertrag",
    )
    db_session.add(unassigned_document)
    db_session.commit()

    results = _service().search_within_matter(matter.id, "Kündigung", db_session)

    assert results == []


def test_metadata_filter_by_document_type(db_session: Session) -> None:
    matter = _matter(db_session)
    invoice = Document(
        file_path="/tmp/r.pdf",
        matter_id=matter.id,
        extracted_text="Testinhalt Rechnung",
        classified_type="Rechnung",
    )
    contract = Document(
        file_path="/tmp/v.pdf",
        matter_id=matter.id,
        extracted_text="Testinhalt Vertrag",
        classified_type="Vertrag",
    )
    db_session.add_all([invoice, contract])
    db_session.commit()

    results = _service().search_within_matter(
        matter.id, "Testinhalt", db_session, document_type="Rechnung"
    )

    assert len(results) == 1
    assert results[0].entity_id == invoice.id


def test_semantic_search_finds_similar_wording(db_session: Session) -> None:
    """FakeEmbeddingProvider basiert auf gemeinsamen Wörtern - ein Dokument
    mit vielen gemeinsamen Wörtern zur Anfrage sollte semantisch gefunden
    werden, auch ohne exakten Substring-Treffer."""
    matter = _matter(db_session)
    document = Document(
        file_path="/tmp/x.pdf",
        matter_id=matter.id,
        extracted_text="mietvertrag kündigung frist wohnung berlin",
    )
    db_session.add(document)
    db_session.commit()
    _service().index_document(document, db_session)

    results = _service().search_within_matter(
        matter.id, "mietvertrag kündigung frist", db_session
    )

    assert len(results) == 1
    assert results[0].match_type in {"semantic", "hybrid"}


def test_search_result_always_references_concrete_document(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    document = Document(
        file_path="/tmp/x.pdf", matter_id=matter.id, extracted_text="Testinhalt Klage"
    )
    db_session.add(document)
    db_session.commit()

    results = _service().search_within_matter(matter.id, "Klage", db_session)

    assert results[0].entity_id == document.id
    assert results[0].entity_type == "Document"


def test_knowledge_base_search_only_returns_approved_items(
    db_session: Session,
) -> None:
    approved = KnowledgeItem(
        title="Freigegebener Baustein",
        content="Textbaustein zur Kündigung eines Mietvertrags",
        approval_status="approved",
    )
    pending = KnowledgeItem(
        title="Ungeprüfter Baustein",
        content="Textbaustein zur Kündigung eines Mietvertrags",
        approval_status="pending",
    )
    db_session.add_all([approved, pending])
    db_session.commit()

    service = _service()
    service.index_knowledge_item(approved, db_session)
    service.index_knowledge_item(pending, db_session)  # sollte No-Op sein

    results = service.search_knowledge_base("Kündigung Mietvertrag", db_session)

    assert len(results) == 1
    assert results[0].entity_id == approved.id


def test_knowledge_base_search_never_returns_documents(db_session: Session) -> None:
    """Wissensbasis-Suche darf niemals Mandantendokumente liefern."""
    matter = _matter(db_session)
    document = Document(
        file_path="/tmp/x.pdf", matter_id=matter.id, extracted_text="Kündigung Mietvertrag"
    )
    db_session.add(document)
    db_session.commit()

    results = _service().search_knowledge_base("Kündigung Mietvertrag", db_session)

    assert all(r.entity_type != "Document" for r in results)


def test_knowledge_base_search_excludes_expired_items(db_session: Session) -> None:
    """Prompt 12: ein abgelaufener Textbaustein darf trotz Freigabe nicht
    zurückgegeben werden."""
    from datetime import date, timedelta

    expired = KnowledgeItem(
        title="Abgelaufener Baustein",
        content="Textbaustein zur Kündigung eines Mietvertrags",
        approval_status="approved",
        valid_until=date.today() - timedelta(days=1),
    )
    current = KnowledgeItem(
        title="Aktueller Baustein",
        content="Textbaustein zur Kündigung eines Mietvertrags",
        approval_status="approved",
    )
    db_session.add_all([expired, current])
    db_session.commit()

    service = _service()
    service.index_knowledge_item(expired, db_session)
    service.index_knowledge_item(current, db_session)

    results = service.search_knowledge_base("Kündigung Mietvertrag", db_session)
    result_ids = {r.entity_id for r in results}

    assert current.id in result_ids
    assert expired.id not in result_ids


def test_knowledge_base_search_excludes_not_yet_valid_items(
    db_session: Session,
) -> None:
    from datetime import date, timedelta

    future = KnowledgeItem(
        title="Zukünftiger Baustein",
        content="Textbaustein zur Kündigung eines Mietvertrags",
        approval_status="approved",
        valid_from=date.today() + timedelta(days=30),
    )
    db_session.add(future)
    db_session.commit()

    service = _service()
    service.index_knowledge_item(future, db_session)

    results = service.search_knowledge_base("Kündigung Mietvertrag", db_session)

    assert all(r.entity_id != future.id for r in results)


def test_no_matter_scoped_search_method_exists_without_matter_id() -> None:
    """Architektonischer Schutz: es darf keine Methode geben, die
    Dokumente ohne matter_id-Argument durchsucht (Ausnahme: die separate
    Wissensbasis-Suche, die bewusst nie Dokumente liefert)."""
    import inspect

    signature = inspect.signature(DocumentSearchService.search_within_matter)
    assert "matter_id" in signature.parameters

    public_methods = [
        name
        for name in dir(DocumentSearchService)
        if not name.startswith("_") and callable(getattr(DocumentSearchService, name))
    ]
    # Alle "search_"-Methoden ausser der Wissensbasis- und Rechtsquellen-
    # Suche muessen matter_id verlangen (Kanzleiwissen und Rechtsquellen
    # sind bewusst global, nicht aktenbezogen - siehe Prompt 12/14/15).
    exempt_methods = {"search_knowledge_base", "search_sources"}
    for method_name in public_methods:
        if method_name.startswith("search_") and method_name not in exempt_methods:
            sig = inspect.signature(getattr(DocumentSearchService, method_name))
            assert "matter_id" in sig.parameters, (
                f"{method_name} erlaubt Dokumentensuche ohne matter_id!"
            )


def test_source_search_only_returns_approved_sources(db_session: Session) -> None:
    approved = Source(
        title="§ 42 AO", source_type="Gesetz", approval_level="freigegeben"
    )
    draft = Source(title="§ 43 AO", source_type="Gesetz", approval_level="entwurf")
    db_session.add_all([approved, draft])
    db_session.commit()

    service = _service()
    service.index_source(approved, db_session)
    service.index_source(draft, db_session)  # sollte No-Op sein (nicht freigegeben)

    results = service.search_sources("AO", db_session)
    result_ids = {r.entity_id for r in results}

    assert approved.id in result_ids
    assert draft.id not in result_ids


def test_source_search_never_returns_documents_or_knowledge_items(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    document = Document(
        file_path="/tmp/x.pdf", matter_id=matter.id, extracted_text="Steuerbescheid"
    )
    knowledge_item = KnowledgeItem(
        title="Baustein", content="Steuerbescheid Textbaustein", approval_status="approved"
    )
    db_session.add_all([document, knowledge_item])
    db_session.commit()

    service = _service()
    service.index_knowledge_item(knowledge_item, db_session)

    results = service.search_sources("Steuerbescheid", db_session)

    assert all(r.entity_type == "Source" for r in results)


def test_source_search_excludes_expired_sources(db_session: Session) -> None:
    from datetime import date, timedelta

    expired = Source(
        title="Alte Verwaltungsanweisung",
        source_type="Verwaltungsanweisung",
        approval_level="freigegeben",
        valid_until=date.today() - timedelta(days=1),
    )
    db_session.add(expired)
    db_session.commit()

    service = _service()
    service.index_source(expired, db_session)

    results = service.search_sources("Verwaltungsanweisung", db_session)

    assert all(r.entity_id != expired.id for r in results)


def test_source_search_filters_by_source_type(db_session: Session) -> None:
    law = Source(
        title="Einkommensteuergesetz Regelung",
        source_type="Gesetz",
        approval_level="freigegeben",
    )
    admin_guidance = Source(
        title="BMF-Schreiben Regelung",
        source_type="Verwaltungsanweisung",
        approval_level="freigegeben",
    )
    db_session.add_all([law, admin_guidance])
    db_session.commit()

    service = _service()
    service.index_source(law, db_session)
    service.index_source(admin_guidance, db_session)

    results = service.search_sources("Regelung", db_session, source_type="Gesetz")
    result_ids = {r.entity_id for r in results}

    assert law.id in result_ids
    assert admin_guidance.id not in result_ids


class _SpyEmbeddingProvider(FakeEmbeddingProvider):
    """Zaehlt echte `embed()`-Aufrufe - P1-Speicherdruck-Befund
    (OPEN_ISSUES.md, 12.09.): das reale FastEmbed-Modell (~1,7 GB) darf
    nicht geladen werden, wenn es ohnehin nichts zu durchsuchen gibt."""

    def __init__(self) -> None:
        self.call_count = 0

    def embed(self, text: str) -> list[float]:
        self.call_count += 1
        return super().embed(text)


def test_search_within_matter_does_not_embed_when_no_candidate_documents(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    provider = _SpyEmbeddingProvider()

    DocumentSearchService(provider).search_within_matter(matter.id, "Mietvertrag", db_session)

    assert provider.call_count == 0


def test_search_within_matter_still_embeds_when_candidate_documents_exist(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    db_session.add(
        Document(file_path="/tmp/x.pdf", matter_id=matter.id, extracted_text="Mietvertrag Text")
    )
    db_session.commit()
    provider = _SpyEmbeddingProvider()

    DocumentSearchService(provider).search_within_matter(matter.id, "Mietvertrag", db_session)

    assert provider.call_count == 1


def test_search_knowledge_base_does_not_embed_when_no_approved_items(
    db_session: Session,
) -> None:
    provider = _SpyEmbeddingProvider()

    DocumentSearchService(provider).search_knowledge_base("Kuendigungsfrist", db_session)

    assert provider.call_count == 0


def test_search_sources_does_not_embed_when_no_approved_sources(
    db_session: Session,
) -> None:
    provider = _SpyEmbeddingProvider()

    DocumentSearchService(provider).search_sources("Regelung", db_session)

    assert provider.call_count == 0
