"""Bekannte Firmennamen auch ohne Rechtsform schuetzen (Legal-E2E-Benchmark §34, Fall LE-D003).

Die Partei hiess "Holzbau Wendeburg GmbH"; im Aktentitel stand "Haselhorst ./. Holzbau Wendeburg". Als bekannte Entitaet
war nur der volle Name indiziert - maskiert wurde allein "Wendeburg" (zufaellig Nachname eines Beteiligten), "Holzbau"
ging im Klartext hinaus. Nur synthetische Daten."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.models import Client, Matter, Party
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _known(db, parties: list[tuple[str, str]]) -> dict[str, list[str]]:
    client = Client(name="Corinna Haselhorst")
    matter = Matter(client=client, title="Haselhorst ./. Holzbau Wendeburg - Baumängel")
    db.add_all([client, matter])
    db.flush()
    for name, role in parties:
        db.add(Party(matter_id=matter.id, name=name, role=role))
    db.commit()
    return RuleBasedLocalAIProvider(None)._build_known_entities(matter.id, matter, db)


def test_company_name_without_legal_form_is_a_known_entity(db) -> None:
    known = _known(db, [("Holzbau Wendeburg GmbH", "Gegner"), ("Torsten Wendeburg", "Beteiligter")])
    assert "Holzbau Wendeburg GmbH" in known["gegner"] and "Holzbau Wendeburg" in known["gegner"]
    result = ClaudePrivacyGateway().prepare_request(
        purpose="chat_response", sachverhalt="Akte: Haselhorst ./. Holzbau Wendeburg - Baumängel\n[Schreiben] Die Holzbau "
        "Wendeburg GmbH fordert 23.600,00 EUR.", anwaltliche_anmerkungen="Fasse zusammen.", review_mode="mask",
        known_entities=known,
    )
    payload = json.dumps(result.payload.model_dump(), ensure_ascii=False)
    assert "Holzbau" not in payload and "Wendeburg" not in payload and "23.600,00 EUR" in payload


@pytest.mark.parametrize(
    ("party", "expected", "absent"),
    [
        ("Ostwind Versicherung AG", "Ostwind Versicherung", None),
        ("Beispiel Bau UG (haftungsbeschränkt)", "Beispiel Bau", None),
        ("Muster Holz GmbH & Co KG", "Muster Holz", None),
        ("Muster Holz SE & Co. KGaA", "Muster Holz", None),
        ("Rechtsanwälte Müller & Partner PartG mbB", "Rechtsanwälte Müller & Partner", None),
        # Einzelwort-Rest wird nicht indiziert (traefe als Teilstring "Sparkassenstiftung", "zum Beispiel", Review §34)
        ("Sparkasse AG", None, "Sparkasse"),
        ("Bau AG", None, "Bau"),
        # die Rechtsform ist kein Nachname (vorher: jedes "GmbH" wurde zur Partei)
        ("Holzbau Wendeburg GmbH", "Holzbau Wendeburg", "GmbH"),
        ("Rechtsanwälte Müller & Partner PartG mbB", None, "mbB"),
    ],
)
def test_legal_form_variants(db, party: str, expected: str | None, absent: str | None) -> None:
    known = _known(db, [(party, "Gegner")])
    if expected:
        assert expected in known["gegner"]
    if absent:
        assert absent not in known["gegner"]


def test_both_forms_share_one_placeholder_and_response_check_passes(db) -> None:
    """Review §34: "Holzbau Wendeburg" und "Holzbau Wendeburg GmbH" bekamen zwei Platzhalter; ein Entwurf mit nur einem
    davon wurde mangels voller Abdeckung abgelehnt."""
    from app.privacy.security_check import check_response_placeholder_integrity

    known = _known(db, [("Holzbau Wendeburg GmbH", "Gegner")])
    result = ClaudePrivacyGateway().prepare_request(
        purpose="formulate_draft", sachverhalt="Akte: Haselhorst ./. Holzbau Wendeburg\n[Schreiben] Die Holzbau Wendeburg "
        "GmbH fordert 23.600,00 EUR.", anwaltliche_anmerkungen="Fristsetzung.", review_mode="mask", known_entities=known,
    )
    gegner = sorted({m.placeholder for m in result.mappings if m.placeholder.startswith("[GEGNER")})
    assert len(gegner) == 1, gegner
    others = " ".join(sorted({m.placeholder for m in result.mappings} - set(gegner)))
    answer = f"Sehr geehrte Damen und Herren, die {gegner[0]} fordert 23.600,00 EUR zu Unrecht. {others}"
    assert check_response_placeholder_integrity(answer, result.mappings, require_full_coverage=True) == []


def test_generic_gmbh_mentions_are_not_attributed_to_the_party(db) -> None:
    known = _known(db, [("Holzbau Wendeburg GmbH", "Gegner")])
    result = ClaudePrivacyGateway().prepare_request(
        purpose="chat_response", sachverhalt="[Notiz] Die Beklagte ist eine GmbH; Haftung nach § 13 GmbHG.",
        anwaltliche_anmerkungen="Fasse zusammen.", review_mode="mask", known_entities=known,
    )
    sach = result.payload.anonymisierter_sachverhalt
    assert "eine GmbH" in sach and "§ 13 GmbHG" in sach and "[GEGNER" not in sach
