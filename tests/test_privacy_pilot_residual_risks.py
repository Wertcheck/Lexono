"""Restrisiko-Analyse vor dem begleiteten Pilot (13.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md §25). Nur synthetische Daten.

Reproduziert und behoben: zusammengesetzte Nummernbezeichnungen (Matrikel-, Schueler-, Haeftlings-, Mitarbeiter-,
Bewohnernummer), weitere ausdrueckliche Todesformulierungen, Geburtstag mit Komma bzw. als Jahrestag, Fristvorschlag fuer
ein Geburtsdatum neben einem Fristwort, Altvorschlaege aus E-Mails. Bewusst NICHT geaendert (dokumentiertes Restrisiko):
"tödlicher Unfall am" - das Unfalldatum ist das juristisch zentrale Ereignisdatum."""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.deadlines.extractor import PlaceholderDeadlineExtractor
from app.privacy.gateway import ClaudePrivacyGateway


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _payload(gateway, text: str, mode: str = "mask"):
    result = gateway.prepare_request(
        purpose="chat_response", sachverhalt="Akte: Beispielakte\n[Schreiben] " + text,
        anwaltliche_anmerkungen="Fasse zusammen.", review_mode=mode,
    )
    return result, (json.dumps(result.payload.model_dump(), ensure_ascii=False) if result.payload else "")


@pytest.mark.parametrize(
    "text, value",
    [
        ("Matrikelnummer 123456 der Studentin.", "123456"),
        ("Schülernummer 22311 laut Schulamt.", "22311"),
        ("Häftlingsnummer 12345 in der JVA.", "12345"),
        ("Mitarbeiter-Nr. 4713 im Betrieb.", "4713"),
        ("Bewohner-Nr. 4714 im Pflegeheim.", "4714"),
    ],
)
def test_compound_number_labels_are_masked(gateway, text, value) -> None:
    result, everything = _payload(gateway, text)
    assert result.allowed and value not in everything


@pytest.mark.parametrize(
    "text, value",
    [
        ("Nach dem Tod seines Vaters am 01.02.2024 erbte er.", "01.02.2024"),
        ("Der Tod von Frau Lindner am 01.02.2024 war unerwartet.", "01.02.2024"),
        ("Er ist am 01.02.2024 im Alter von 82 Jahren verstorben.", "01.02.2024"),
        ("Sie ist am 01.02.2024 im Hospiz Sonnenhof verstorben.", "01.02.2024"),
        ("Er verstarb am Sonntag, dem 01.02.2024, zu Hause.", "01.02.2024"),
        ("Die Folgen des am 01.02.2024 eingetretenen Tod sind zu klären.", "01.02.2024"),
        ("The testator died 01.02.2024 in Musterstadt.", "01.02.2024"),
        ("Unser Vater ist am 01.02.2024 sanft entschlafen.", "01.02.2024"),
        ("Sie hat uns am 01.02.2024 für immer verlassen.", "01.02.2024"),
    ],
)
def test_more_explicit_death_phrasings_are_masked(gateway, text, value) -> None:
    result, everything = _payload(gateway, text)
    assert result.allowed and value not in everything
    assert value not in [d.raw_date_text for d in PlaceholderDeadlineExtractor().extract(text)]


@pytest.mark.parametrize(
    "text, value",
    [
        # Restrisiko bewusst: Unfalldatum ist das zentrale Ereignisdatum (Haftung, § 844 BGB, Verjaehrung)
        ("Der tödliche Unfall am 12.03.2025 auf der B 51 ist Gegenstand der Klage.", "12.03.2025"),
        ("Er wurde am 12.03.2025 tot aufgefunden.", "12.03.2025"),  # Auffindedatum, nicht Todesdatum
        ("Unfall mit Todesfolge am 12.03.2025.", "12.03.2025"),
        ("Am 05.03.2024 erfuhr die Tochter dass der Vater verstorben ist.", "05.03.2024"),
    ],
)
def test_accident_finding_and_knowledge_dates_stay_readable(gateway, text, value) -> None:
    result, _ = _payload(gateway, text)
    assert result.allowed and value in result.payload.anonymisierter_sachverhalt


def test_birthday_anniversary_with_comma_is_masked_without_age_note(gateway) -> None:
    for text in ("An seinem 45. Geburtstag, dem 12.03.2025, verunglückte er.",
                 "An seinem 45. Geburtstag am 12.03.2025 verunglückte er."):
        result, everything = _payload(gateway, text)
        assert "12.03.2025" not in everything and "[GEBURTSTAG_" in result.payload.anonymisierter_sachverhalt
        assert not [a for a in result.payload.anonymisierte_argumentationspunkte if a.startswith("Alter laut")]


def test_born_comma_event_date_stays_readable_without_age_note(gateway) -> None:
    """Review §25: "geboren, am 05.03.2024 kuendigte er" wurde zwischenzeitlich als Geburtsdatum gelesen."""
    result, _ = _payload(gateway, "Er ist in Polen geboren, am 05.03.2024 kündigte er den Mietvertrag.")
    assert "05.03.2024" in result.payload.anonymisierter_sachverhalt
    assert not [a for a in result.payload.anonymisierte_argumentationspunkte if a.startswith("Alter laut")]


def test_two_digit_labelled_number_causes_no_block(gateway) -> None:
    """Review §25: "Mitarbeiter-Nr. 12 ... seit 12 Jahren, Seite 12" blockierte (Leck-Pruefung fand die "12" anderswo)."""
    result, _ = _payload(gateway, "Mitarbeiter-Nr. 12 ist seit 12 Jahren im Betrieb, siehe Seite 12.")
    assert result.allowed, result.reasons


def test_birthdate_next_to_a_deadline_word_is_no_deadline_suggestion() -> None:
    found = [d.raw_date_text for d in PlaceholderDeadlineExtractor().extract(
        "Herr Brenner, geb. 12.03.1980, Frist zur Stellungnahme bis 30.11.2026."
    )]
    assert "12.03.1980" not in found and "30.11.2026" in found
    found = [d.raw_date_text for d in PlaceholderDeadlineExtractor().extract(
        "Er verstarb am 01.02.2024; die Ausschlagungsfrist endet am 15.03.2024."
    )]
    assert "01.02.2024" not in found and "15.03.2024" in found


def test_stored_message_suggestion_is_filtered_with_the_message_text() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
    from app.models import Client, Deadline, Matter, Message
    from app.models.base import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    client = Client(name="Synthetische Mandantin")
    matter = Matter(client=client, title="Akte")
    db.add_all([client, matter])
    db.flush()
    message = Message(direction="inbound", body_text="Name | Vorname | Geburtsdatum | Ort\nBrenner | Karl | 12.03.1961 | Musterstadt")
    db.add(message)
    db.flush()
    db.add(Deadline(matter_id=matter.id, message_id=message.id, source_text="12.03.1961 :: Brenner | Karl | 12.03.1961 | Musterstadt",
                    due_date=date(1961, 3, 12)))
    db.commit()
    assert RuleBasedLocalAIProvider()._build_argumentationspunkte(matter.id, db) == []
