"""Tests für app/search/global_search_service.py (Universal Command Bar,
20.08.).

Kernanforderungen aus der Aufgabenstellung:
- Smart-Routing-Trennung: Mandanten/Akten/Dokumente sind IMMER "Lokal",
  Rechtsquellen sind IMMER "Extern" - und die lokale Kategorie macht
  nachweislich KEINEN Netzwerkaufruf (siehe
  test_local_search_never_makes_network_calls).
- Performance: eine moderat große Datenmenge muss in vertretbarer Zeit
  durchsucht werden (siehe test_search_performance_with_many_records)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.laws.service import import_law_fixture_data
from app.models import Client, Document, LawSection, Matter, Message, Source
from app.models.base import Base
from app.search.global_search_service import MIN_QUERY_LENGTH, GlobalSearchService
from app.search.service import DocumentSearchService
from tests.fake_embedding_provider import FakeEmbeddingProvider


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def service() -> GlobalSearchService:
    document_search_service = DocumentSearchService(FakeEmbeddingProvider())
    return GlobalSearchService(document_search_service)


def _client_and_matter(db: Session, *, name: str, matter_title: str) -> tuple[Client, Matter]:
    client = Client(name=name, client_number=f"{name[:3].upper()}-1", status="active")
    db.add(client)
    db.flush()
    matter = Matter(client_id=client.id, title=matter_title, status="open")
    db.add(matter)
    db.commit()
    db.refresh(client)
    db.refresh(matter)
    return client, matter


# --- Mindestlaenge ---


def test_search_below_min_length_returns_no_results(
    service: GlobalSearchService, db_session: Session
) -> None:
    assert MIN_QUERY_LENGTH == 2
    assert service.search("m", db_session) == []
    assert service.search("", db_session) == []
    assert service.search("   ", db_session) == []


# --- "Lokal": Mandanten/Akten/Dokumente ---


def test_search_finds_client_by_name_and_marks_it_local(
    service: GlobalSearchService, db_session: Session
) -> None:
    _client_and_matter(db_session, name="Sonnenschein GmbH", matter_title="Irrelevant")
    results = service.search("Sonnenschein", db_session)
    client_results = [r for r in results if r.entity_type == "Client"]
    assert len(client_results) == 1
    assert client_results[0].badge_label == "Lokal"
    assert client_results[0].url.startswith("/dashboard/clients/")


def test_search_finds_client_by_client_number(
    service: GlobalSearchService, db_session: Session
) -> None:
    client = Client(name="Unbekannter Name", client_number="XZ-42", status="active")
    db_session.add(client)
    db_session.commit()
    results = service.search("XZ-42", db_session)
    assert any(r.entity_type == "Client" and r.title == "Unbekannter Name" for r in results)


def test_search_finds_matter_and_links_to_the_real_matter_detail_page(
    service: GlobalSearchService, db_session: Session
) -> None:
    """ECHTER FUND (17.09.): seit der UI/UX-Ueberarbeitung (13.09.) existiert
    eine echte Aktendetailseite (`matters_router.py`) - der Suchtreffer
    zeigte trotzdem noch auf die Mandanten-Detailseite (veralteter
    Kommentar/Code aus der Zeit VOR dieser Seite). Jetzt korrekt direkt
    verlinkt."""
    client, matter = _client_and_matter(
        db_session, name="Mieterbund", matter_title="Kündigungsschutzklage Mueller"
    )
    results = service.search("Kündigungsschutzklage", db_session)
    matter_results = [r for r in results if r.entity_type == "Matter"]
    assert len(matter_results) == 1
    assert matter_results[0].badge_label == "Lokal"
    assert matter_results[0].url == f"/dashboard/matters/{matter.id}"


def test_search_finds_document_by_filename_only_not_by_content(
    service: GlobalSearchService, db_session: Session
) -> None:
    """Kernanforderung (Aktenisolation, siehe Moduldocstring): die globale
    Dokumentensuche durchsucht NUR den Dateinamen, NIEMALS
    `extracted_text` - eine aktenübergreifende Volltextsuche wäre ein
    Verstoß gegen die bestehende Isolationsregel in
    app/search/service.py."""
    client, matter = _client_and_matter(
        db_session, name="Aktenbund", matter_title="Akte mit Dokument"
    )
    document = Document(
        matter_id=matter.id,
        original_filename="Kaufvertrag_Mueller.pdf",
        file_path="/tmp/irrelevant.pdf",
        extracted_text="Geheimer Inhalt, der garantiert nicht im Dateinamen steht: Zauberwort123",
    )
    db_session.add(document)
    db_session.commit()

    by_filename = service.search("Kaufvertrag", db_session)
    doc_results = [r for r in by_filename if r.entity_type == "Document"]
    assert len(doc_results) == 1
    assert doc_results[0].badge_label == "Lokal"
    # ECHTER FUND (17.09., Owner-Direktive §5 "ein Link fuehrt zu keinem
    # echten Ziel"): zeigte bisher auf die Mandanten-Uebersicht statt auf
    # das Dokument selbst - jetzt direkt auf die echte Aktendokument-Seite.
    assert doc_results[0].url == f"/dashboard/matters/{matter.id}/document/{document.id}"

    by_content = service.search("Zauberwort123", db_session)
    assert [r for r in by_content if r.entity_type == "Document"] == []


def test_local_categories_never_mix_across_clients(
    service: GlobalSearchService, db_session: Session
) -> None:
    _client_and_matter(db_session, name="Erster Mandant", matter_title="Akte A")
    _client_and_matter(db_session, name="Zweiter Mandant", matter_title="Akte B")
    results = service.search("Erster", db_session)
    assert all("Zweiter" not in r.title for r in results)


# --- "Lokal": E-Mails/Posteingang-Nachrichten (25.09., Owner-Direktive
# "POSTEINGANG / STRICT REFERENCE IMPLEMENTATION" §6 - die Referenz zeigt
# "In E-Mails, Mandanten, Akten oder Inhalten suchen", die Command Bar
# durchsuchte E-Mails bislang jedoch gar nicht) ---


def test_search_finds_message_by_sender_and_marks_it_local(
    service: GlobalSearchService, db_session: Session
) -> None:
    message = Message(
        direction="inbound",
        sender="Architekturbüro Neumann & Schulz GbR",
        subject="Kündigung erhalten - Widerspruch prüfen",
        body_text="Testinhalt, keine echten Mandantendaten.",
    )
    db_session.add(message)
    db_session.commit()

    results = service.search("Neumann & Schulz", db_session)
    message_results = [r for r in results if r.entity_type == "Message"]
    assert len(message_results) == 1
    assert message_results[0].badge_label == "Lokal"
    assert message_results[0].url == f"/dashboard/inbox/{message.id}"


def test_search_finds_message_by_subject(
    service: GlobalSearchService, db_session: Session
) -> None:
    message = Message(
        direction="inbound",
        sender="finanzamt@example-testdomain.invalid",
        subject="Steuerbescheid 2025 - Rueckfrage",
        body_text="Testinhalt.",
    )
    db_session.add(message)
    db_session.commit()

    results = service.search("Steuerbescheid 2025", db_session)
    assert any(r.entity_type == "Message" and r.title == message.subject for r in results)


def test_search_does_not_search_message_body_text(
    service: GlobalSearchService, db_session: Session
) -> None:
    """Dieselbe Metadaten-statt-Volltext-Grenze wie bei Dokumenten (siehe
    test_search_finds_document_by_filename_only_not_by_content) - nur
    Absender/Betreff sind durchsuchbar, nicht der Nachrichtentext."""
    message = Message(
        direction="inbound",
        sender="mandant@example-testdomain.invalid",
        subject="Kurze Rückfrage",
        body_text="Geheimer Inhalt, der garantiert nicht im Betreff steht: Zauberwort456",
    )
    db_session.add(message)
    db_session.commit()

    results = service.search("Zauberwort456", db_session)
    assert [r for r in results if r.entity_type == "Message"] == []


# --- "Extern/Gesetz": Gesetzesbibliothek ---


def test_search_finds_law_section_by_section_number_and_marks_it_extern_gesetz(
    service: GlobalSearchService, db_session: Session
) -> None:
    import_law_fixture_data(
        db_session,
        {
            "code": "BGB",
            "title": "Bürgerliches Gesetzbuch",
            "sections": [
                {
                    "section_number": "§ 433",
                    "title": "Vertragstypische Pflichten beim Kaufvertrag",
                    "text_content": "Durch den Kaufvertrag wird der Verkäufer einer Sache verpflichtet...",
                    "last_updated": "2026-08-20",
                }
            ],
        },
    )
    section = db_session.query(LawSection).one()

    results = service.search("§ 433", db_session)
    law_results = [r for r in results if r.entity_type == "LawSection"]
    assert len(law_results) == 1
    assert law_results[0].badge_label == "Extern/Gesetz"
    assert law_results[0].url == f"/dashboard/laws/BGB/{section.id}"


def test_search_excludes_law_sections_of_a_deactivated_law(
    service: GlobalSearchService, db_session: Session
) -> None:
    """26.09., Owner-Direktive "KANZLEIWISSEN FINAL PRODUCT IMPLEMENTATION"
    §19/§20: dieselbe Deaktivierungssperre wie im Chat-Fast-Path
    (app/chat/service.py::_find_law_section) - ein in Kanzleiwissen
    deaktiviertes Gesetz gilt als "für die lokale Nutzung nicht
    aktiviert" und darf auch in der globalen Suche nicht mehr auftauchen."""
    from app.laws.service import toggle_law_active

    import_law_fixture_data(
        db_session,
        {
            "code": "BGB",
            "title": "Bürgerliches Gesetzbuch",
            "sections": [
                {
                    "section_number": "§ 433",
                    "title": "Vertragstypische Pflichten beim Kaufvertrag",
                    "text_content": "Durch den Kaufvertrag wird der Verkäufer einer Sache verpflichtet...",
                    "last_updated": "2026-08-20",
                }
            ],
        },
    )
    toggle_law_active(db_session, "BGB", active=False)

    results = service.search("§ 433", db_session)

    assert [r for r in results if r.entity_type == "LawSection"] == []


def test_search_finds_law_section_by_title_or_text_content(
    service: GlobalSearchService, db_session: Session
) -> None:
    import_law_fixture_data(
        db_session,
        {
            "code": "StGB",
            "title": "Strafgesetzbuch",
            "sections": [
                {
                    "section_number": "§ 242",
                    "title": "Diebstahl",
                    "text_content": "Wer eine fremde bewegliche Sache wegnimmt...",
                    "last_updated": "2026-08-20",
                }
            ],
        },
    )
    by_title = service.search("Diebstahl", db_session)
    assert any(r.entity_type == "LawSection" for r in by_title)

    by_content = service.search("bewegliche Sache", db_session)
    assert any(r.entity_type == "LawSection" for r in by_content)


def test_law_section_search_never_makes_network_calls(
    service: GlobalSearchService, db_session: Session
) -> None:
    import_law_fixture_data(
        db_session,
        {
            "code": "BGB",
            "title": "Bürgerliches Gesetzbuch",
            "sections": [
                {
                    "section_number": "§ 1",
                    "title": "Beginn der Rechtsfähigkeit",
                    "text_content": "Die Rechtsfähigkeit des Menschen beginnt mit der Vollendung der Geburt.",
                    "last_updated": "2026-08-20",
                }
            ],
        },
    )
    with patch("httpx.Client.request") as mock_request:
        results = service.search("Rechtsfähigkeit", db_session)
        mock_request.assert_not_called()
    assert any(r.entity_type == "LawSection" for r in results)


# --- "Extern": Rechtsquellen ---


def test_search_finds_approved_source_and_marks_it_extern(
    service: GlobalSearchService, db_session: Session
) -> None:
    source = Source(
        title="Bürgerliches Gesetzbuch § 573",
        source_type="Gesetz",
        approval_level="freigegeben",
        reference="§ 573 BGB",
    )
    db_session.add(source)
    db_session.commit()
    # Indizieren wie im echten Betrieb (SourceService, hier direkt der
    # zugrunde liegende DocumentSearchService).
    service._document_search_service.index_source(source, db_session)

    results = service.search("Bürgerliches Gesetzbuch", db_session)
    source_results = [r for r in results if r.entity_type == "Source"]
    assert len(source_results) == 1
    assert source_results[0].badge_label == "Extern"
    # ECHTER FUND (17.09.): "/dashboard/sources" ist weiterhin nur die
    # "in Vorbereitung"-Platzhalterseite - die echte, tatsaechlich befuellte
    # Rechtsquellen-Tabelle lebt seit 14.09. unter "/dashboard/knowledge".
    assert source_results[0].url == "/dashboard/knowledge"


def test_search_ignores_unapproved_source_drafts(
    service: GlobalSearchService, db_session: Session
) -> None:
    """Nur freigegebene Quellen duerfen auftauchen (siehe
    DocumentSearchService.search_sources) - ein Entwurf ohne Freigabe
    ist nicht indiziert."""
    source = Source(
        title="Ungeprüfter Gesetzesentwurf",
        source_type="Gesetz",
        approval_level="entwurf",
    )
    db_session.add(source)
    db_session.commit()
    service._document_search_service.index_source(source, db_session)

    results = service.search("Ungeprüfter", db_session)
    assert [r for r in results if r.entity_type == "Source"] == []


def test_local_search_never_makes_network_calls(
    service: GlobalSearchService, db_session: Session
) -> None:
    """Kern der Datenschutz-Weiche: selbst die "Extern" gelabelte Kategorie
    macht technisch KEINEN echten Netzwerkaufruf (siehe Moduldocstring -
    Rechtsquellen-Suche laeuft ueber dasselbe lokale fastembed-Modell wie
    alles andere). Patcht httpx.Client.request (die gemeinsame
    Basisfunktion, die JEDE httpx-Anfrage letztlich durchläuft) und
    stellt sicher, dass eine vollstaendige Command-Bar-Suche sie nie
    aufruft."""
    _client_and_matter(db_session, name="Netzwerktest GmbH", matter_title="Akte Netzwerktest")
    source = Source(
        title="Netzwerktest-Gesetz",
        source_type="Gesetz",
        approval_level="freigegeben",
    )
    db_session.add(source)
    db_session.commit()
    service._document_search_service.index_source(source, db_session)

    with patch("httpx.Client.request") as mock_request:
        results = service.search("Netzwerktest", db_session)
        mock_request.assert_not_called()
    assert len(results) >= 2  # Client + Source mindestens gefunden


# --- Robustheit: Embedding-Ausfall darf lokale Treffer nicht verhindern ---


def test_source_search_failure_does_not_break_local_results(
    service: GlobalSearchService, db_session: Session
) -> None:
    """Regressionsschutz (gefunden bei der manuellen Verifikation im
    Browser, 20.08.): auf der Testmaschine schlug das echte
    Embedding-Modell (onnxruntime) mit einem DLL-Ladefehler fehl - das
    liess die GESAMTE Suche inkl. der reinen SQL-Treffer (Mandant/Akte)
    mit einem 500er abstuerzen, obwohl die nichts mit dem Embedding-
    Modell zu tun haben. `search_sources` muss deshalb in einem eigenen
    try/except laufen."""
    _client_and_matter(db_session, name="Robust GmbH", matter_title="Robuste Akte")

    with patch.object(
        service._document_search_service,
        "search_sources",
        side_effect=RuntimeError("Embedding-Modell nicht verfügbar (simuliert)"),
    ):
        results = service.search("Robust", db_session)

    assert any(r.entity_type == "Client" and r.title == "Robust GmbH" for r in results)
    assert [r for r in results if r.entity_type == "Source"] == []


# --- Performance ---


def test_search_performance_with_many_records(
    service: GlobalSearchService, db_session: Session
) -> None:
    """Kein N+1/quadratisches Verhalten: 50 Mandanten je mit einer Akte
    und einem Dokument muessen deutlich unter einer Sekunde durchsucht
    werden (grosszuegige Schwelle, um in CI nicht flaky zu sein)."""
    for i in range(50):
        client = Client(name=f"Performance Mandant {i}", client_number=f"PERF-{i}", status="active")
        db_session.add(client)
        db_session.flush()
        matter = Matter(client_id=client.id, title=f"Performance Akte {i}", status="open")
        db_session.add(matter)
        db_session.flush()
        db_session.add(
            Document(
                matter_id=matter.id,
                original_filename=f"Performance_Dokument_{i}.pdf",
                file_path="/tmp/irrelevant.pdf",
            )
        )
    db_session.commit()

    start = time.perf_counter()
    results = service.search("Performance", db_session)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"Suche dauerte {elapsed:.3f}s - zu langsam fuer eine Live-Suche"
    assert len(results) > 0
