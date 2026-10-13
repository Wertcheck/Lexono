"""P05 - konsistente Pseudonymisierung grammatikalischer Namensvarianten (13.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md §32).
Nur synthetische Daten.

Reproduziert (Benchmark pseudonymization_v1, Fall P05): "Herr Brenner", "Herrn Brenner" und "Brenners" erhielten im
selben Text drei Platzhalter - die NER-Treffer enthielten die Anrede bzw. das Genitiv-s und verdraengten den bekannten
Namen. Die Cloud-KI sah drei Personen statt einer. Regeln: nur EINDEUTIG verankerte Varianten werden zusammengefuehrt;
gleiche Nachnamen verschiedener Personen und unverankerte Namen nie."""

from __future__ import annotations

import json
import re

import pytest

from app.privacy.gateway import ClaudePrivacyGateway


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _run(gateway, text, known=None, **kwargs):
    result = gateway.prepare_request(purpose="formulate_draft", sachverhalt="Akte: Beispielakte\n" + text,
                                     known_entities=known, **kwargs)
    payload = json.dumps(result.payload.model_dump(), ensure_ascii=False) if result.payload else ""
    return result, payload


def _person_placeholders(text: str) -> list[str]:
    return re.findall(r"\[(?:PERSON|MANDANT|GEGNER|BETEILIGTER|ANWALT)_\d+\]", text)


MANDANT = {"mandant": ["Karl Brenner", "Brenner"]}  # wie RuleBasedLocalAIProvider._build_known_entities


def test_salutation_and_genitive_variants_of_a_known_person_share_one_placeholder(gateway) -> None:
    result, payload = _run(gateway, "Herr Brenner und Herrn Brenner sind dieselbe Person; Brenners Anspruch ist verjährt.",
                           MANDANT)
    sach = result.payload.anonymisierter_sachverhalt
    assert set(_person_placeholders(sach)) == {"[MANDANT_01]"}
    assert "Brenner" not in payload
    # Anrede und Genitiv bleiben lesbar - der Textbereich wird nur verkleinert, nie erweitert
    assert "Herrn [MANDANT_01]" in sach and "[MANDANT_01]s Anspruch" in sach  # Anrede und Genitiv bleiben lesbar


@pytest.mark.parametrize(
    "text",
    [
        # ("Mandant Karl Brenner teilt mit ..." blockiert vorbestehend im Restrisiko-Scan als "ort" - unabhaengig von P05, §32)
        "Karl Brenner schreibt, dass Herr Brenner zahlte. Herrn Brenners Quittung fehlt.",
        # BEWUSST GEAENDERT (§33): vorher "Frau Brenner? Nein: Herrn Karl Brenner gehört das Haus ..." - der Satz sagt, dass
        # Frau Brenner NICHT die Eigentuemerin ist; seit §33 bleibt sie eine eigene Person (eigener Test dort)
        "Herrn Karl Brenner gehört das Haus; Brenners Tochter wohnt dort; Herr Brenner vermietet.",
        "Brenner's Anspruch und Hr. Brenner sowie Hrn. Brenner werden geprüft.",
    ],
)
def test_further_safe_variants_are_consistent(gateway, text) -> None:
    result, payload = _run(gateway, text, MANDANT)
    assert set(_person_placeholders(result.payload.anonymisierter_sachverhalt)) == {"[MANDANT_01]"}
    assert "Brenner" not in payload


def test_variants_without_known_entities_follow_the_unique_full_name(gateway) -> None:
    result, payload = _run(gateway, "Herr Gernot Hahn und Herrn Hahn sowie Hahns Ehefrau erschienen am 03.03.2026.")
    sach = result.payload.anonymisierter_sachverhalt
    assert len(set(_person_placeholders(sach))) == 1
    assert "Hahn" not in payload and "03.03.2026" in sach


def test_two_people_with_the_same_surname_are_not_merged(gateway) -> None:
    result, payload = _run(gateway, "Herr Gernot Hahn und Frau Ilse Hahn sind Eheleute. Herrn Hahns Anteil beträgt 50 Prozent.")
    sach = result.payload.anonymisierter_sachverhalt
    placeholders = _person_placeholders(sach)
    assert "Hahn" not in payload
    # Gernot und Ilse bleiben getrennt; der mehrdeutige "Herrn Hahns" wird NICHT einem der beiden zugeschlagen
    assert len(set(placeholders)) >= 3
    assert "50 Prozent" in sach


def test_two_known_people_with_the_same_surname_are_not_merged(gateway) -> None:
    known = {"mandant": ["Karl Brenner", "Brenner"], "gegner": ["Anna Brenner", "Brenner"]}
    result, payload = _run(gateway, "Karl Brenner verklagt Anna Brenner. Herrn Brenners Klage ist zulässig.", known)
    sach = result.payload.anonymisierter_sachverhalt
    assert "Brenner" not in payload
    assert "[MANDANT_01]" in sach and "[GEGNER_01]" in sach
    first, second = sach.split("verklagt")
    assert "[MANDANT_01]" in first and "[GEGNER_01]" in second.split(".")[0]


@pytest.mark.parametrize("name", ["Klaus", "Andreas", "Jens", "Lucas", "Hans"])
def test_first_names_ending_in_s_are_not_cut(gateway, name) -> None:
    """Genitiv-s nur bei Verankerung: "Klaus" wird nie zu "Klau"."""
    result, payload = _run(gateway, f"Herr {name} Weber und Herr {name} Ritter erschienen.")
    assert name not in payload
    assert f"{name[:-1]}]" not in payload and not re.search(rf"\]{name[-1]}\b", result.payload.anonymisierter_sachverhalt)


def test_unanchored_name_with_s_is_left_alone(gateway) -> None:
    """"Herr Jens" (kein Vollname, kein bekannter Name): keine spekulative Kuerzung, dennoch geschuetzt."""
    result, payload = _run(gateway, "Herr Jens hat angerufen; Termin am 04.04.2026.")
    assert "Jens" not in payload and "04.04.2026" in payload


def test_repeated_occurrences_and_order_are_stable(gateway) -> None:
    text = "Brenners Antrag, dann Herrn Brenner, dann Herr Brenner, dann wieder Brenners Brief. Karl Brenner zahlt 1.200,00 EUR."
    result, payload = _run(gateway, text, MANDANT)
    assert set(_person_placeholders(result.payload.anonymisierter_sachverhalt)) == {"[MANDANT_01]"}
    assert "1.200,00 EUR" in payload and "Brenner" not in payload


def test_literal_placeholders_in_the_text_are_not_merged(gateway) -> None:
    result, payload = _run(gateway, "Im Muster steht [PERSON_01]; gemeint ist Herrn Brenners Erklärung.", MANDANT)
    assert "Brenner" not in payload and result.allowed


def test_mapping_scope_is_per_request(gateway) -> None:
    """Zwei unabhaengige Anfragen (verschiedene Dokumente/Akten): keine Zuordnung wandert in die naechste Anfrage."""
    first, _ = _run(gateway, "Herrn Brenners Brief.", MANDANT)
    second, payload = _run(gateway, "Herrn Hahns Brief und Gernot Hahn.")
    assert all(m.original_value != "Karl Brenner" for m in second.mappings)
    assert "Hahn" not in payload


@pytest.mark.parametrize(
    "text, facts",
    [
        ("Herrn Brenners Mietschuld von 2.340,00 EUR ist bis 31.08.2026 nach § 543 BGB fällig; Az. 3 O 123/26.",
         ["2.340,00 EUR", "31.08.2026", "§ 543 BGB"]),
        ("Brenners Arbeitgeberin, die Hansa Bau GmbH, kündigte am 01.02.2026.", ["01.02.2026"]),
    ],
)
def test_legal_facts_are_preserved(gateway, text, facts) -> None:
    result, payload = _run(gateway, text, MANDANT)
    for fact in facts:
        assert fact in result.payload.anonymisierter_sachverhalt
    assert "Brenner" not in payload


def test_leak_check_still_blocks_a_reconstructed_name_in_the_answer(gateway) -> None:
    from app.privacy.security_check import check_response_placeholder_integrity

    result, _ = _run(gateway, "Herrn Brenners Brief.", MANDANT)
    assert check_response_placeholder_integrity("Antwort an Herrn Brenner.", result.mappings, require_full_coverage=False)


def test_fail_closed_on_ner_failure_is_unchanged() -> None:
    from app.privacy.pseudonymizer import Pseudonymizer

    def failing(text):  # noqa: ANN001
        raise RuntimeError("NER nicht verfuegbar (simuliert)")

    with pytest.raises(RuntimeError):
        ClaudePrivacyGateway(pseudonymizer=Pseudonymizer(ner_detector=failing)).prepare_request(
            purpose="formulate_draft", sachverhalt="Herrn Brenners Brief.", known_entities=MANDANT)


def test_e2e_chat_payload_uses_one_placeholder_for_all_variants(tmp_path) -> None:
    """Echter Chat-Pfad mit Anhang (Aktenkontext, bekannter Mandant aus der Akte): Payload unmittelbar vor dem Cloud-Aufruf."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.chat.service import ChatService
    from app.models import Client, Document, Matter, Role, User
    from app.models.base import Base
    from tests.test_chat_service import _drafting_service

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    role = Role(name="anwalt")
    db.add(role)
    db.flush()
    user = User(email="a@kanzlei.test", password_hash="x", role_id=role.id)
    client = Client(name="Karl Brenner")
    matter = Matter(client=client, title="Mietsache")
    db.add_all([user, client, matter])
    db.flush()
    doc = Document(matter_id=matter.id, file_path="b.txt", classified_type="Schreiben",
                   extracted_text="Herr Brenner zahlte 800,00 EUR. Herrn Brenners Mieterhöhung vom 01.03.2026 ist strittig; "
                                  "Brenners Widerspruch folgt bis 15.04.2026.")
    db.add(doc)
    db.commit()
    chat = ChatService(tmp_path / "uploads")
    conversation = chat.create_conversation(db, user=user, matter_id=matter.id, title="F", actor=user.email)
    message = chat.record_user_message(db, conversation=conversation, content="Fasse zusammen.", document_ids=[doc.id])
    drafting, writer = _drafting_service("Antwort.")
    chat.send_message(db, conversation=conversation, content="Fasse zusammen.", drafting_service=drafting,
                      actor=user.email, current_message_id=message.id)
    payload = writer.received_payloads[0]
    sent = json.dumps(payload.model_dump(), ensure_ascii=False)
    assert "Brenner" not in sent
    assert set(_person_placeholders(payload.anonymisierter_sachverhalt)) == {"[MANDANT_01]"}
    for fact in ("800,00 EUR", "01.03.2026", "15.04.2026"):
        assert fact in sent


def test_genitive_is_kept_in_the_reconstruction(gateway) -> None:
    """Review §32: "[MANDANT_01] Anspruch" wurde zu "Karl Brenner Anspruch" zurueckgefuehrt."""
    result, _ = _run(gateway, "Herr Brenner widerspricht; Brenners Anspruch ist verjährt.", MANDANT)
    assert "[MANDANT_01]s Anspruch" in result.payload.anonymisierter_sachverhalt
    assert gateway.reconstruct_response("[MANDANT_01]s Anspruch ist verjährt.", result.mappings) == "Karl Brenners Anspruch ist verjährt."


@pytest.mark.parametrize(
    "answer",
    ["Nach Auffassung des Richters ist [MANDANT_01] beweispflichtig.", "Die Haftung des Kochs ist zu prüfen.",
     "Langes Zuwarten schadet; vgl. Fischers Kommentar."],
)
def test_common_genitives_in_answers_are_not_blocked(gateway, answer) -> None:
    """Review §32 (F1): keine allgemeine "Nachname + s"-Regel - normale Genitive blockieren keine Antwort."""
    from app.privacy.security_check import check_response_placeholder_integrity

    for name in ("Anna Richter", "Uwe Koch", "Peter Lange", "Hans Fischer"):
        known = {"mandant": [name, name.split()[-1]]}
        result, _ = _run(gateway, f"Herr {name} hat angerufen.", known)
        assert not check_response_placeholder_integrity(answer, result.mappings, require_full_coverage=False), name


@pytest.mark.parametrize("answer", ["Antwort an Herrn Brenner.", "Brenners Anspruch.", "Sehr geehrter Herr Dr. Brenner,",
                                    "HERRN BRENNER"])
def test_leak_check_catches_variants_that_were_replaced(gateway, answer) -> None:
    from app.privacy.security_check import check_response_placeholder_integrity

    result, _ = _run(gateway, "Herrn Brenners Brief und Herr Karl Brenner.", MANDANT)
    assert check_response_placeholder_integrity(answer, result.mappings, require_full_coverage=False)
