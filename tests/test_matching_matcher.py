"""Tests fuer app/matching/matcher.py (Prompt 09).

Nutzt ausschliesslich synthetische Mandanten/Akten - keine echten
Mandantendaten."""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.matching.matcher import MatterMatchingService
from app.models import Client, Matter, Message, Party
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


def _matcher(auto=0.85, review=0.4) -> MatterMatchingService:
    return MatterMatchingService(auto_assign_threshold=auto, review_threshold=review)


def test_exact_aktenzeichen_match_leads_to_auto_assignment(db_session: Session) -> None:
    client = Client(name="Testmandant")
    matter = Matter(client=client, title="Testakte", reference_number="A-1001")
    db_session.add_all([client, matter])
    db_session.commit()

    message = Message(
        direction="inbound",
        sender="unbekannt@example.test",
        subject="Rückfrage",
        body_text="Bezug: Az.: A-1001, bitte um Rückmeldung.",
    )
    db_session.add(message)
    db_session.commit()

    result = _matcher().match_message(message, db_session)

    assert result.decision == "auto_assigned"
    assert result.matter_id == matter.id
    assert "aktenzeichen_match" in result.candidates[0].matched_signals


def test_email_match_alone_is_below_default_review_threshold(db_session: Session) -> None:
    """E-Mail-Treffer allein (Gewicht 0.3) liegt unter der Standard-
    Review-Schwelle (0.4) - mit den Standardwerten also "no_match"."""
    client = Client(name="Testmandant")
    matter = Matter(client=client, title="Immobilienrecht Akte")
    party = Party(matter=matter, name="Max Mustermann", email="max@example.test")
    db_session.add_all([client, matter, party])
    db_session.commit()

    message = Message(direction="inbound", sender="max@example.test", subject="Testbetreff")
    db_session.add(message)
    db_session.commit()

    result = _matcher().match_message(message, db_session)

    assert result.decision == "no_match"
    assert result.matter_id is None


def test_email_match_alone_triggers_review_with_lower_threshold(
    db_session: Session,
) -> None:
    """Mit einer (konfigurierbar) niedrigeren Review-Schwelle reicht ein
    E-Mail-Treffer allein für "needs_review" - aber nie für Auto-Zuordnung."""
    client = Client(name="Testmandant")
    matter = Matter(client=client, title="Immobilienrecht Akte")
    party = Party(matter=matter, name="Max Mustermann", email="max@example.test")
    db_session.add_all([client, matter, party])
    db_session.commit()

    message = Message(direction="inbound", sender="max@example.test", subject="Testbetreff")
    db_session.add(message)
    db_session.commit()

    result = _matcher(review=0.2).match_message(message, db_session)

    assert result.decision == "needs_review"
    assert result.matter_id is None


def test_no_signals_leads_to_no_match(db_session: Session) -> None:
    client = Client(name="Testmandant")
    matter = Matter(client=client, title="Völlig unrelated Akte XYZ")
    db_session.add_all([client, matter])
    db_session.commit()

    message = Message(
        direction="inbound",
        sender="fremd@example.test",
        subject="Allgemeine Anfrage",
        body_text="Ein Text ohne jeden Bezug.",
    )
    db_session.add(message)
    db_session.commit()

    result = _matcher().match_message(message, db_session)

    assert result.decision == "no_match"
    assert result.matter_id is None


def test_ambiguous_top_candidates_force_review_even_above_threshold(
    db_session: Session,
) -> None:
    """Zwei unterschiedliche Akten können (z. B. durch Zufall) denselben
    Beteiligten-Kontakt teilen. Auch wenn der Score dabei über einer
    (hier bewusst niedrig konfigurierten) Auto-Schwelle liegt, darf bei
    Ambiguität NIE automatisch zugeordnet werden."""
    client = Client(name="Testmandant")
    matter_a = Matter(client=client, title="Akte A")
    matter_b = Matter(client=client, title="Akte B")
    party_a = Party(matter=matter_a, name="Geteilter Kontakt", email="shared@example.test")
    party_b = Party(matter=matter_b, name="Geteilter Kontakt", email="shared@example.test")
    db_session.add_all([client, matter_a, matter_b, party_a, party_b])
    db_session.commit()

    message = Message(direction="inbound", sender="shared@example.test")
    db_session.add(message)
    db_session.commit()

    # Auto-Schwelle bewusst auf 0.25 gesenkt: der Email-Match-Score (0.3)
    # läge damit OHNE Ambiguitätsprüfung über der Schwelle.
    result = _matcher(auto=0.25, review=0.1).match_message(message, db_session)

    assert result.decision == "needs_review"
    assert result.matter_id is None
    assert len(result.candidates) == 2
    assert result.candidates[0].score == result.candidates[1].score


def test_low_classification_confidence_prevents_auto_assignment(
    db_session: Session,
) -> None:
    client = Client(name="Testmandant")
    matter = Matter(client=client, title="Testakte", reference_number="A-2002")
    db_session.add_all([client, matter])
    db_session.commit()

    message = Message(direction="inbound", body_text="Az.: A-2002")
    db_session.add(message)
    db_session.commit()

    result = _matcher().match_message(message, db_session, classification_ok=False)

    assert result.decision == "needs_review"
    assert result.matter_id is None
    assert "klassifikation" in result.reasoning.lower()


def test_party_name_fuzzy_match_contributes_score(db_session: Session) -> None:
    client = Client(name="Testmandant")
    matter = Matter(client=client, title="Testakte")
    party = Party(matter=matter, name="Erika Musterfrau")
    db_session.add_all([client, matter, party])
    db_session.commit()

    message = Message(
        direction="inbound", sender="Erika Musterfrau <erika@example.test>"
    )
    db_session.add(message)
    db_session.commit()

    result = _matcher().match_message(message, db_session)

    assert len(result.candidates) == 1
    assert "party_name_match" in result.candidates[0].matched_signals


# --- Mandantenname + Anzeigename (14.09., echte Funde beim Durchspielen
# der Posteingangs-Zuordnung auf der synthetischen Kanzlei-Datenbasis) ---


def test_client_name_is_matched_not_only_parties(db_session: Session) -> None:
    """ECHTER FUND: der Namensabgleich betrachtete ausschliesslich
    `matter.parties`. Der MANDANT selbst - der wichtigste Beteiligte einer
    Akte und im Kanzleialltag der haeufigste Absender ueberhaupt - wurde
    nie verglichen. Eine Mail des eigenen Mandanten erhielt dadurch kein
    einziges Namenssignal."""
    client = Client(name="Sabine Schmidt")
    matter = Matter(client=client, title="Einspruch Steuerbescheid 2025")
    db_session.add_all([client, matter])
    db_session.commit()

    message = Message(
        direction="inbound",
        sender="Sabine Schmidt <sabine.schmidt@example-testdomain.invalid>",
    )
    db_session.add(message)
    db_session.commit()

    result = _matcher().match_message(message, db_session)

    assert len(result.candidates) == 1
    assert "client_name_match" in result.candidates[0].matched_signals


def test_client_name_match_does_not_double_count_with_party_match(
    db_session: Session,
) -> None:
    """Ist derselbe Mensch zusaetzlich als Party gefuehrt, darf der Name
    nicht doppelt zaehlen (deshalb bewusst im else-Zweig)."""
    client = Client(name="Sabine Schmidt")
    matter = Matter(client=client, title="Akte")
    party = Party(matter=matter, name="Sabine Schmidt")
    db_session.add_all([client, matter, party])
    db_session.commit()

    message = Message(
        direction="inbound", sender="Sabine Schmidt <s.schmidt@example.test>"
    )
    db_session.add(message)
    db_session.commit()

    signals = _matcher().match_message(message, db_session).candidates[0].matched_signals
    assert "party_name_match" in signals
    assert "client_name_match" not in signals


def test_bare_address_is_not_treated_as_a_display_name(db_session: Session) -> None:
    """ECHTER FUND: ohne Anzeigenamen lieferte `_extract_display_name` die
    vollstaendige E-MAIL-ADRESSE als vermeintlichen Namen zurueck - der
    Namensabgleich verglich dann eine Adresse mit einem Personennamen.
    Ohne echten Anzeigenamen gibt es keinen Namen zu vergleichen."""
    matcher = _matcher()
    assert matcher._extract_display_name("sabine.schmidt@example.test") is None
    assert matcher._extract_display_name("Sabine Schmidt <s@example.test>") == "Sabine Schmidt"
    assert matcher._extract_display_name("") is None


def test_sender_of_another_matter_citing_a_reference_is_not_auto_assigned(db_session: Session) -> None:
    """Legal-E2E-Benchmark §34.10: Mandantin A schreibt von ihrer bekannten Adresse und nennt das Aktenzeichen der
    Akte B (Tippfehler/Weiterleitung). Vorher: automatisch Akte B (Nachricht von A in fremder Akte), 6 von 6 Proben.
    Jetzt: Pruefung durch die Kanzlei."""
    client_a = Client(name="Mandantin A", contact_email="a@beispielpost.example")
    client_b = Client(name="Mandant B", contact_email="b@beispielpost.example")
    matter_a = Matter(client=client_a, title="Akte A", reference_number="A-2001")
    matter_b = Matter(client=client_b, title="Akte B", reference_number="B-3002")
    db_session.add_all([client_a, client_b, matter_a, matter_b])
    db_session.commit()

    message = Message(direction="inbound", sender="a@beispielpost.example", subject="Rückfrage Az. B-3002", body_text="Frage.")
    result = _matcher().match_message(message, db_session)

    assert result.decision == "needs_review" and result.matter_id is None
    assert "anderen Akte" in result.reasoning
    assert result.candidates[0].matter_id == matter_b.id  # Vorschlag bleibt sichtbar


def test_reference_with_matching_sender_is_still_auto_assigned(db_session: Session) -> None:
    client_a = Client(name="Mandantin A", contact_email="a@beispielpost.example")
    client_b = Client(name="Mandant B", contact_email="b@beispielpost.example")
    matter_a = Matter(client=client_a, title="Akte A", reference_number="A-2001")
    matter_b = Matter(client=client_b, title="Akte B", reference_number="B-3002")
    db_session.add_all([client_a, client_b, matter_a, matter_b])
    db_session.commit()

    own = Message(direction="inbound", sender="a@beispielpost.example", subject="Rückfrage Az. A-2001", body_text="Frage.")
    unknown = Message(direction="inbound", sender="extern@beispielpost.example", subject="Az. B-3002", body_text="Frage.")
    assert _matcher().match_message(own, db_session).matter_id == matter_a.id
    assert _matcher().match_message(unknown, db_session).matter_id == matter_b.id
