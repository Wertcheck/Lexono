"""Tests fuer app/ai_providers/local_ai_provider.py."""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.models import Client, Deadline, Document, Matter, Party
from app.models.base import Base


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


def _matter(db: Session, client_name: str = "Max Mustermann", title: str = "Testakte", **kwargs) -> Matter:
    client = Client(name=client_name)
    matter = Matter(client=client, title=title, **kwargs)
    db.add_all([client, matter])
    db.commit()
    return matter


def test_requires_matter_id() -> None:
    provider = RuleBasedLocalAIProvider()
    with pytest.raises(ValueError):
        provider.prepare_draft_context("", db=None)  # type: ignore[arg-type]


def test_raises_for_unknown_matter(db_session: Session) -> None:
    provider = RuleBasedLocalAIProvider()
    with pytest.raises(ValueError):
        provider.prepare_draft_context("nicht-vorhanden", db_session)


def test_sachverhalt_includes_document_excerpts(db_session: Session) -> None:
    matter = _matter(db_session, title="Testakte")
    document = Document(
        matter=matter,
        file_path="/tmp/x.pdf",
        extracted_text="Wichtiger Inhalt des Dokuments.",
        classified_type="Steuerbescheid",
    )
    db_session.add(document)
    db_session.commit()

    provider = RuleBasedLocalAIProvider()
    result = provider.prepare_draft_context(matter.id, db_session)

    assert "Testakte" in result.sachverhalt
    assert "Wichtiger Inhalt des Dokuments." in result.sachverhalt
    assert "Steuerbescheid" in result.sachverhalt
    assert result.has_document_context is True


def test_sachverhalt_includes_document_content_beyond_160_characters(
    db_session: Session,
) -> None:
    """ECHTER FUND, SYNTHETISCH REPRODUZIERT (05.10., Owner-Direktive
    "Vollstaendiger UX- und Workflow-Audit"): `_build_sachverhalt` rief
    bisher `build_snippet(text[:500], "")` auf - mit leerer Suchanfrage
    griff `build_snippet`s SUCHTREFFER-Vorschau-Fallback
    (`_SNIPPET_FALLBACK_LENGTH = 160`), nicht die hier beabsichtigten 500
    Zeichen. Jedes Dokument > 160 Zeichen wurde dadurch faktisch nach dem
    Briefkopf/der Anrede abgeschnitten, BEVOR der eigentliche inhaltliche
    Absatz (hier: Betrag/Begruendung) ueberhaupt erreicht wurde - live an
    einem synthetischen Einspruchsschreiben reproduziert (Betrag "4.500,00
    EUR" fehlte im generierten Entwurf vollstaendig, ein Bescheiddatum
    wurde mitten im Jahr abgeschnitten). Der bisherige Test
    `test_sachverhalt_includes_document_excerpts` (oben) nutzte nur einen
    31 Zeichen langen Dokumenttext und konnte diesen Fehler strukturell
    nie aufdecken - dieser Test nutzt bewusst einen laengeren, realistisch
    strukturierten Text (> 160, < 500 Zeichen)."""
    matter = _matter(db_session, title="Testakte")
    long_text = (
        "EINSPRUCH GEGEN STEUERBESCHEID\n"
        "Finanzamt Musterstadt\n"
        "Az.: 123/456/7890\n"
        "Sehr geehrte Damen und Herren,\n"
        "hiermit lege ich gegen den Steuerbescheid vom 01.09.2026 fuer das\n"
        "Veranlagungsjahr 2025 form- und fristgerecht Einspruch ein.\n"
        "Begruendung: Die Betriebsausgaben in Hoehe von 4.500,00 EUR fuer\n"
        "Fortbildungsmassnahmen wurden nicht beruecksichtigt."
    )
    assert len(long_text) > 160  # Testvoraussetzung: muss den alten Fehler ueberhaupt treffen koennen
    document = Document(
        matter=matter, file_path="/tmp/x.pdf", extracted_text=long_text, classified_type="Steuerbescheid",
    )
    db_session.add(document)
    db_session.commit()

    provider = RuleBasedLocalAIProvider()
    result = provider.prepare_draft_context(matter.id, db_session)

    # Das vollstaendige Datum - nicht mitten im Jahr abgeschnitten.
    assert "01.09.2026" in result.sachverhalt
    # Inhalt NACH Zeichen 160 (Betrag/Begruendung) muss ankommen.
    assert "4.500,00 EUR" in result.sachverhalt
    assert "Fortbildungsmassnahmen" in result.sachverhalt


def test_document_excerpt_is_truncated_with_ellipsis_beyond_max_chars(
    db_session: Session,
) -> None:
    """Gegenprobe: eine Obergrenze (`_MAX_DOCUMENT_EXCERPT_CHARS`) bleibt
    bestehen - nur die fehlerhafte Zwischenkuerzung wurde entfernt, keine
    Entgrenzung. Grenzwert selbst 05.10. mit echten Produktionsdaten neu
    gemessen und auf 5000 Zeichen angehoben (siehe dortiger Kommentar) -
    dieser Test prueft nur das PRINZIP (Kappung + Ellipse an der
    tatsaechlich konfigurierten Grenze), nicht einen fest einprogrammierten
    Zahlenwert."""
    from app.ai_providers.local_ai_provider import _MAX_DOCUMENT_EXCERPT_CHARS

    matter = _matter(db_session, title="Testakte")
    long_text = "A" * (_MAX_DOCUMENT_EXCERPT_CHARS + 100)
    document = Document(
        matter=matter, file_path="/tmp/x.pdf", extracted_text=long_text, classified_type="Sonstiges",
    )
    db_session.add(document)
    db_session.commit()

    provider = RuleBasedLocalAIProvider()
    result = provider.prepare_draft_context(matter.id, db_session)

    assert "A" * _MAX_DOCUMENT_EXCERPT_CHARS in result.sachverhalt
    assert "A" * (_MAX_DOCUMENT_EXCERPT_CHARS + 1) not in result.sachverhalt
    assert "…" in result.sachverhalt


def test_document_excerpt_captures_the_full_real_world_test_document(
    db_session: Session,
) -> None:
    """ECHTER FUND, mit realen Produktionsdaten gemessen (05.10.): die
    zuvor fest verdrahtete 500-Zeichen-Grenze schnitt bei einem realen
    Testdokument (2638 Zeichen) die eigentliche Aufgabenstellung am Ende
    des Dokuments komplett ab. Reproduziert mit einem realistisch
    strukturierten, laengeren Dokument (Einleitung + Sachverhalt +
    Aufgabenstellung am Ende, > 500 aber < 5000 Zeichen) - die
    Aufgabenstellung am Ende MUSS im Sachverhalt ankommen."""
    matter = _matter(db_session, title="Testakte")
    long_text = (
        "Einleitung. " * 50  # > 500 Zeichen Fuellwortlaut vor der eigentlichen Anweisung
        + "AUFGABENSTELLUNG AM ENDE DES DOKUMENTS: Bitte einen Entwurf erstellen."
    )
    assert len(long_text) > 500
    assert len(long_text) < 5000
    document = Document(
        matter=matter, file_path="/tmp/x.pdf", extracted_text=long_text, classified_type="Sonstiges",
    )
    db_session.add(document)
    db_session.commit()

    provider = RuleBasedLocalAIProvider()
    result = provider.prepare_draft_context(matter.id, db_session)

    assert "AUFGABENSTELLUNG AM ENDE DES DOKUMENTS" in result.sachverhalt


def test_has_document_context_is_false_without_any_attached_document(
    db_session: Session,
) -> None:
    """P0-Performance-Follow-up (13.09.): `has_document_context` ist das
    objektive Signal, das `DraftingService._should_skip_llm_privacy_layers`
    nutzt, um zu entscheiden, ob die LLM-gestuetzten §65-Schritte fuer eine
    einfache Chat-Nachricht ohne Aktendokument uebersprungen werden
    duerfen - siehe DECISIONS.md fuer die volle Herleitung."""
    matter = _matter(db_session, title="Testakte")

    provider = RuleBasedLocalAIProvider()
    result = provider.prepare_draft_context(matter.id, db_session)

    assert result.has_document_context is False
    assert result.sachverhalt == "Akte: Testakte"


def test_argumentationspunkte_include_deadlines(db_session: Session) -> None:
    matter = _matter(db_session)
    deadline = Deadline(matter=matter, source_text="Frist am 15.03.2027", confidence=0.4)
    db_session.add(deadline)
    db_session.commit()

    provider = RuleBasedLocalAIProvider()
    result = provider.prepare_draft_context(matter.id, db_session)

    assert any("Frist am 15.03.2027" in a for a in result.argumentationspunkte)


def test_known_entities_include_client_as_mandant(db_session: Session) -> None:
    matter = _matter(db_session, client_name="Max Mustermann")
    provider = RuleBasedLocalAIProvider()

    result = provider.prepare_draft_context(matter.id, db_session)

    assert "Max Mustermann" in result.known_entities.get("mandant", [])


def test_known_entities_also_include_bare_surname(db_session: Session) -> None:
    """ECHTER FUND (14.09., Overnight-Direktive §8, realer Regressionsfall
    "Frau Müller"): bisher wurde nur der VOLLSTAENDIGE Mandantenname als
    bekannte Entitaet indiziert - `detect_known_entities` sucht aber exakt
    danach, ein blosser Nachname-Verweis im Text ("Müller" ohne "Anna")
    wurde dadurch NICHT erkannt. Jetzt wird zusaetzlich der Nachname
    (letztes Wort) separat indiziert."""
    matter = _matter(db_session, client_name="Anna Müller")
    provider = RuleBasedLocalAIProvider()

    result = provider.prepare_draft_context(matter.id, db_session)

    mandant_entities = result.known_entities.get("mandant", [])
    assert "Anna Müller" in mandant_entities
    assert "Müller" in mandant_entities


def test_known_entities_do_not_index_very_short_surnames(db_session: Session) -> None:
    """ECHTER FUND (14.09., beim Haerten der obigen Ergaenzung): ein
    einzelner Buchstabe als vermeintlicher 'Nachname' (z. B. synthetischer
    Testname "Mandant A") wuerde per Substring-Suche JEDES Vorkommen
    dieses Buchstabens irgendwo im Text treffen und die Pseudonymisierung
    unbrauchbar machen (real reproduziert:
    test_context_never_contains_data_from_other_matter in
    test_drafting_service.py). Mindestlaenge 3 verhindert das."""
    matter = _matter(db_session, client_name="Mandant A")
    provider = RuleBasedLocalAIProvider()

    result = provider.prepare_draft_context(matter.id, db_session)

    mandant_entities = result.known_entities.get("mandant", [])
    assert "Mandant A" in mandant_entities
    assert "A" not in mandant_entities


def test_party_with_opponent_role_is_categorized_as_gegner(db_session: Session) -> None:
    matter = _matter(db_session)
    party = Party(matter=matter, name="Erika Musterfrau", role="Gegnerin")
    db_session.add(party)
    db_session.commit()

    provider = RuleBasedLocalAIProvider()
    result = provider.prepare_draft_context(matter.id, db_session)

    assert "Erika Musterfrau" in result.known_entities.get("gegner", [])


def test_party_with_court_role_is_categorized_as_gericht(db_session: Session) -> None:
    matter = _matter(db_session)
    party = Party(matter=matter, name="Finanzamt Musterstadt", role="Behörde")
    db_session.add(party)
    db_session.commit()

    provider = RuleBasedLocalAIProvider()
    result = provider.prepare_draft_context(matter.id, db_session)

    assert "Finanzamt Musterstadt" in result.known_entities.get("gericht", [])


def test_party_without_recognized_role_goes_to_generic_category(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    party = Party(matter=matter, name="Zeuge Unbekannt", role="Zeuge")
    db_session.add(party)
    db_session.commit()

    provider = RuleBasedLocalAIProvider()
    result = provider.prepare_draft_context(matter.id, db_session)

    assert "Zeuge Unbekannt" in result.known_entities.get("beteiligter", [])


def test_context_never_contains_data_from_other_matter(db_session: Session) -> None:
    """Aktenisolation - dasselbe Muster wie bei PromptContextBuilder (Prompt 16)."""
    matter_a = _matter(db_session, client_name="Mandant A", title="Akte A")
    matter_b = _matter(db_session, client_name="Mandant B", title="Akte B")

    doc_a = Document(
        matter=matter_a, file_path="/tmp/a.pdf", extracted_text="Vertraulicher Inhalt A"
    )
    doc_b = Document(
        matter=matter_b, file_path="/tmp/b.pdf", extracted_text="Vertraulicher Inhalt B"
    )
    db_session.add_all([doc_a, doc_b])
    db_session.commit()

    provider = RuleBasedLocalAIProvider()
    result = provider.prepare_draft_context(matter_a.id, db_session)

    assert "Inhalt A" in result.sachverhalt
    assert "Inhalt B" not in result.sachverhalt
    assert "Mandant B" not in result.known_entities.get("mandant", [])


def test_no_search_service_results_in_empty_quellenverweise(db_session: Session) -> None:
    matter = _matter(db_session)
    provider = RuleBasedLocalAIProvider(search_service=None)

    result = provider.prepare_draft_context(matter.id, db_session)

    assert result.quellenverweise == []
