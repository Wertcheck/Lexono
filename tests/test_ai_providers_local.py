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


def test_document_excerpt_beyond_max_chars_is_capped_and_visibly_marked(db_session: Session) -> None:
    """Die Obergrenze bleibt (kein unbegrenzter Prompt), die Kuerzung ist aber nicht mehr still: Marker im Text
    (fuer Claude) und Hinweis in `notices` (fuer die Anwaltschaft)."""
    from app.ai_providers.local_ai_provider import _MAX_DOCUMENT_EXCERPT_CHARS

    matter = _matter(db_session, title="Testakte")
    long_text = "A" * (_MAX_DOCUMENT_EXCERPT_CHARS + 100)
    db_session.add(
        Document(matter=matter, file_path="/tmp/x.pdf", extracted_text=long_text, classified_type="Sonstiges")
    )
    db_session.commit()

    result = RuleBasedLocalAIProvider().prepare_draft_context(matter.id, db_session)

    assert "A" * (_MAX_DOCUMENT_EXCERPT_CHARS + 1) not in result.sachverhalt
    assert len(result.sachverhalt) < _MAX_DOCUMENT_EXCERPT_CHARS + 200
    assert "Auszug:" in result.sachverhalt
    assert len(result.notices) == 1 and "auszugsweise" in result.notices[0]


# --- Dokumentvollstaendigkeit (Qualitaetslauf 10.10.): Anfang / Mitte / Ende langer Vertraege ----------------

_START = ["Kaufpreis von 118.750,00 EUR", "Vertragsbeginn am 09.01.2026", "Käufer die Brandt Anlagenbau GmbH"]
_MIDDLE_SIGNAL = [
    "Vertragsstrafe von 0,3 Prozent je Werktag",
    "Sicherheitseinbehalt von 45.300,00 EUR",
    "Skonto von 2 Prozent bei Zahlung bis zum 14.04.2026",
]
_MIDDLE_DESCRIPTIVE = ["Die Anlage wird in Halle 3 aufgestellt", "Ansprechpartnerin ist Frau Lindqvist"]
_END = [
    "Kündigungsfrist von 6 Wochen zum Quartalsende",
    "Nachfrist bis zum 30.11.2026",
    "AUFGABE: Bitte Mängelrüge erstellen",
]
_FILLER = (
    "Die Parteien sind sich darüber einig, dass die Leistung nach den anerkannten Regeln der Technik zu erbringen "
    "ist. Änderungen und Ergänzungen bedürfen der Schriftform. Der Auftragnehmer unterrichtet den Auftraggeber "
    "unverzüglich über erkennbare Hindernisse. "
)


def _contract(size: int, *, descriptive: bool = True) -> str:
    n = max(6, size // 700)
    sections = []
    for k in range(n):
        extra = ""
        if k == 0:
            extra = ". ".join(_START) + "."
        elif k == n // 2:
            extra = ". ".join(_MIDDLE_SIGNAL) + "."
        elif k == n // 2 + 1 and descriptive:
            extra = ". ".join(_MIDDLE_DESCRIPTIVE) + "."
        elif k == n - 1:
            extra = ". ".join(_END) + "."
        sections.append(f"§ {k + 1} Abschnitt {k + 1}\n{_FILLER * 4}{extra}")
    return "\n".join(sections)


def _sachverhalt_for(db_session: Session, *texts: str):
    matter = _matter(db_session, title="Testakte")
    for idx, text in enumerate(texts):
        db_session.add(
            Document(matter=matter, file_path=f"/tmp/v{idx}.pdf", extracted_text=text, classified_type="Vertrag")
        )
    db_session.commit()
    return RuleBasedLocalAIProvider().prepare_draft_context(matter.id, db_session)


def test_contract_over_5000_chars_arrives_complete_start_middle_end(db_session: Session) -> None:
    """VORHER (Grenze 5000, gemessen): Mitte und Ende solcher Vertraege 0 % im Sachverhalt."""
    text = _contract(12_000)
    assert len(text) > 10_000
    result = _sachverhalt_for(db_session, text)

    for fact in _START + _MIDDLE_SIGNAL + _MIDDLE_DESCRIPTIVE + _END:
        assert fact in result.sachverhalt, fact
    assert "Auszug:" not in result.sachverhalt  # vollstaendig enthalten -> keine Kuerzung, kein Hinweis
    assert result.notices == []


def test_very_long_contract_keeps_start_end_and_key_passages_and_flags_the_cut(db_session: Session) -> None:
    from app.ai_providers.local_ai_provider import _MAX_DOCUMENT_EXCERPT_CHARS

    text = _contract(120_000)
    assert len(text) > 4 * _MAX_DOCUMENT_EXCERPT_CHARS
    result = _sachverhalt_for(db_session, text)

    for fact in _START + _END:  # Anfang und Ende (inkl. Schlussanweisung)
        assert fact in result.sachverhalt, fact
    for fact in _MIDDLE_SIGNAL:  # Betraege/Daten/Fristen/Rechtsfolgen aus dem ausgelassenen Mittelteil
        assert fact in result.sachverhalt, fact
    assert len(result.sachverhalt) < _MAX_DOCUMENT_EXCERPT_CHARS + 400  # weiterhin begrenzt
    assert "Auszug:" in result.sachverhalt
    assert len(result.notices) == 1


def test_excerpt_cuts_at_word_boundaries_never_inside_a_date() -> None:
    from app.ai_providers.local_ai_provider import _document_excerpt

    text = ("Wort " * 4000) + " Stichtag 01.09.2026 " + ("Wort " * 4000)
    excerpt = _document_excerpt(text, 2000)
    assert excerpt.omitted_chars > 0
    for token in excerpt.text.replace("[…]", " ").split():
        assert token in {"Wort", "Stichtag", "01.09.2026"} or token.startswith("[") or token.endswith("]") or token in {
            "Auszug:", "von", "Zeichen", "dieses", "Dokuments", "ausgelassen;", "Anfang,", "Ende", "und", "Stellen",
            "mit", "Beträgen,", "Daten", "Fristen", "sind", "enthalten", "…]", "…",
        } or token.isdigit()


def test_total_budget_across_many_long_documents_is_bounded_and_every_document_keeps_its_start(
    db_session: Session,
) -> None:
    from app.ai_providers.local_ai_provider import (
        _MAX_DOCUMENT_CHARS_TOTAL,
        _MAX_DOCUMENT_EXCERPT_CHARS,
        _MIN_DOCUMENT_ALLOWANCE,
    )

    texts = [f"DOKUMENT-{k}-ANFANG. " + ("Fülltext. " * 6000) for k in range(8)]
    result = _sachverhalt_for(db_session, *texts)

    for k in range(8):
        assert f"DOKUMENT-{k}-ANFANG" in result.sachverhalt
    # Gesamtbudget (+ je Dokument Mindestzuteilung als Untergrenze) bleibt die Obergrenze des Prompts
    assert len(result.sachverhalt) < _MAX_DOCUMENT_CHARS_TOTAL + 8 * (_MIN_DOCUMENT_ALLOWANCE + 400)
    assert len(result.sachverhalt) < 8 * _MAX_DOCUMENT_EXCERPT_CHARS


def test_names_in_the_previously_cut_off_tail_are_pseudonymized_before_the_cloud() -> None:
    """Privacy-Regression: Inhalte, die jetzt NEU im Sachverhalt ankommen (Mitte/Ende), muessen genauso geschuetzt
    werden - echte Namen/Adressen duerfen nicht im Cloud-Payload stehen."""
    from app.privacy.gateway import ClaudePrivacyGateway

    text = _contract(40_000) + " Unterzeichnet: Herr Olaf Thiessen, wohnhaft Gewerbering 6, 24105 Beispielstadt."
    from app.ai_providers.local_ai_provider import _document_excerpt

    excerpt = _document_excerpt(text).text
    assert "Olaf Thiessen" in excerpt  # Ende ist jetzt enthalten ...
    result = ClaudePrivacyGateway().prepare_request(
        purpose="formulate_draft", sachverhalt=excerpt, argumentationspunkte=[], quellenverweise=[], stil=None,
        vorlage=None, anwaltliche_anmerkungen=None, known_entities=None, gespraechsverlauf=None,
        skip_general_knowledge_pseudonymization=False,
    )
    assert result.allowed
    payload_text = result.payload.anonymisierter_sachverhalt
    assert "Thiessen" not in payload_text  # ... aber pseudonymisiert
    assert "Gewerbering" not in payload_text and "24105" not in payload_text
    assert "118.750,00 EUR" in payload_text  # Geldbetraege bleiben erhalten


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


def test_sachverhalt_for_a_real_matter_still_includes_the_literal_title(
    db_session: Session,
) -> None:
    """Gegenprobe zu `test_placeholder_matter_sachverhalt_omits_the_auto_
    generated_title` unten: eine ECHTE Akte (realer Mandant) muss ihren
    Titel weiterhin unveraendert in den Sachverhalt uebernehmen - der
    Titel kann echte Mandantendaten enthalten und muss wie bisher durch
    Presidio geschuetzt werden."""
    matter = _matter(db_session, client_name="Erika Musterfrau", title="Mietsache Musterfrau")

    provider = RuleBasedLocalAIProvider()
    result = provider.prepare_draft_context(matter.id, db_session)

    assert result.sachverhalt == "Akte: Mietsache Musterfrau"


def test_placeholder_matter_sachverhalt_omits_the_auto_generated_title(
    db_session: Session,
) -> None:
    """ECHTER FUND (07.10., Owner-Direktive "INSTALLER + GIT + CLOUD-E2E-
    CHAT-QUALITY", per echtem Cloud-E2E-Test reproduziert): fuer die
    automatisch angelegte "Schnellentwurf"-Akte (matterlose allgemeine
    Chat-Frage, gemeinsamer Sammel-Mandant `PLACEHOLDER_CLIENT_NAME`)
    enthielt der Sachverhalt bisher woertlich den generierten Titel
    ("Akte: Schnellentwurf 2026-10-07") - reiner Systemtext, den Presidios
    deutsches NER-Modell teils faelschlich als Entitaet (z. B. Datum)
    erkannte und pseudonymisierte. Eine spaetere, voellig unverdaechtige
    Chat-Antwort, die denselben Text erneut im Klartext enthielt, wurde
    dadurch faelschlich als "nicht ausreichend anonymisiert" blockiert.
    Der Sachverhalt fuer diese Platzhalter-Akte muss jetzt ein fester,
    niemals durch Presidio fehlinterpretierbarer Text sein."""
    from app.drafting.quick_matter import create_quick_matter

    matter = create_quick_matter(db_session, title=None, client_name=None, actor="test@kanzlei.test")
    db_session.commit()

    provider = RuleBasedLocalAIProvider()
    result = provider.prepare_draft_context(matter.id, db_session)

    assert "Schnellentwurf" not in result.sachverhalt
    assert result.sachverhalt == "Akte: (kein spezifischer Fall zugeordnet)"


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
