"""Nach P05: Identitaet bei gleichem Nachnamen, Genitiv gewoehnlicher Woerter, juristische Fundstellen (13.10.2026,
PRIVACY_REVIEW_ARCHITECTURE.md §33). Nur synthetische Daten.

Reproduziert:
A) Mandant "Karl Brenner": "Frau Brenner" (ausdruecklich eine andere Person) wurde [MANDANT_01] - die Nachnamen-Ergaenzung
   der bekannten Namen ordnete jede Anrede+Nachname dem Mandanten zu.
B) Mandant "Peter Lange": "Langes Zoegern" wurde "[MANDANT_01]s Zoegern" (NER-Fehltreffer + P05-Verankerung); und
   "Peter Langes Anspruch" wurde [PERSON_01] statt [MANDANT_01] (P05: der Treffer zaehlte sich selbst als Namenstraeger).
C) "BGH, Urt. v. ... - VIII ZR 1/21" wurde "[PERSON_01]. v. ... - [PERSON_02] ZR 1/21"; "BVerfG" als Ort maskiert."""

from __future__ import annotations

import json
import re

import pytest

from app.privacy.gateway import ClaudePrivacyGateway


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _known(category: str, *names: str) -> dict:
    values = []
    for name in names:
        values.append(name)
        if len(name.split()) > 1:
            values.append(name.split()[-1])
    return {category: values}


def _run(gateway, text, known=None):
    result = gateway.prepare_request(purpose="formulate_draft", sachverhalt="Akte: Beispielakte\n" + text, known_entities=known)
    assert result.payload is not None, result.reasons
    return result, result.payload.anonymisierter_sachverhalt.split("\n", 1)[1], json.dumps(result.payload.model_dump(), ensure_ascii=False)


def _placeholder_after(text: str, prefix: str) -> str:
    match = re.search(re.escape(prefix) + r"\s*(\[[A-Z]+_\d+\])", text)
    assert match, (prefix, text)
    return match.group(1)


# --- A: andere Person mit gleichem Nachnamen ------------------------------------------------------------------------

@pytest.mark.parametrize(
    "mandant, text, other_prefix",
    [
        ("Karl Brenner", "Karl Brenner klagt. Seine Nachbarin Frau Brenner ist nicht verwandt und Zeugin.", "Nachbarin Frau"),
        ("Karl Brenner", "Karl Brenner und Frau Brenner (seine Ehefrau) erschienen am 02.02.2026.", "und Frau"),
        ("Anna Brenner", "Anna Brenner ist Mieterin; ihr Vermieter Herr Brenner ist ein Namensvetter.", "Vermieter Herr"),
        ("Kim Brenner", "Herr Kim Brenner klagt. Die Zeugin Frau Brenner ist seine Schwester.", "Zeugin Frau"),
    ],
)
def test_other_person_with_the_clients_surname_is_not_the_client(gateway, mandant, text, other_prefix) -> None:
    result, sach, payload = _run(gateway, text, _known("mandant", mandant))
    assert "Brenner" not in payload  # weiterhin maskiert
    assert _placeholder_after(sach, other_prefix) != "[MANDANT_01]"
    assert "[MANDANT_01]" in sach


@pytest.mark.parametrize(
    "mandant, text, prefix",
    [
        ("Karl Brenner", "Karl Brenner widerspricht; Herr Brenner zahlt nicht.", "; Herr"),
        ("Anna Brenner", "Anna Brenner widerspricht; Frau Brenner zahlt nicht.", "; Frau"),
        ("Karl Brenner", "Karl Brenner widerspricht; Herrn Brenners Schreiben folgt.", "; Herrn"),
    ],
)
def test_matching_salutation_still_refers_to_the_client(gateway, mandant, text, prefix) -> None:
    _, sach, payload = _run(gateway, text, _known("mandant", mandant))
    assert "Brenner" not in payload and _placeholder_after(sach, prefix) == "[MANDANT_01]"


def test_opponent_with_same_surname_logic_applies_to_every_known_category(gateway) -> None:
    _, sach, payload = _run(gateway, "Gegner ist Uwe Haber. Frau Haber, seine Tochter, bezeugt den Vorfall.", _known("gegner", "Uwe Haber"))
    assert "Haber" not in payload and _placeholder_after(sach, ". Frau") != "[GEGNER_01]"


def test_unknown_gender_keeps_previous_behaviour_and_stays_masked(gateway) -> None:
    """Ohne Geschlechtsbeleg (Vorname nicht eindeutig, keine Anrede am Vollnamen): kein Wechsel - weiterhin maskiert."""
    _, _, payload = _run(gateway, "Kim Brenner klagt. Frau Brenner ist Zeugin.", _known("mandant", "Kim Brenner"))
    assert "Brenner" not in payload


# --- B: Genitiv gewoehnlicher Woerter ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "mandant, text, word",
    [
        ("Peter Lange", "Herr Peter Lange rügt die Mängel. Langes Zögern des Vermieters schadet.", "Zögern"),
        ("Peter Lange", "Herr Peter Lange klagt. Langes Warten auf die Reparatur ist unzumutbar.", "Warten"),
        ("Ute Groß", "Frau Ute Groß klagt. Großes Interesse an einer Einigung besteht.", "Interesse"),
        ("Max Kurz", "Herr Max Kurz klagt. Kurzes Zögern genügt nicht.", "Zögern"),
    ],
)
def test_ordinary_adjective_is_not_attributed_to_the_client(gateway, mandant, text, word) -> None:
    _, sach, _ = _run(gateway, text, _known("mandant", mandant))
    before_word = sach.split(word)[0].rsplit(".", 1)[-1]
    assert "[MANDANT_01]" not in before_word, sach


@pytest.mark.parametrize(
    "text, prefix",
    [
        ("Peter Langes Anspruch auf Minderung ist begründet; Herr Peter Lange klagt.", ""),
        ("Herr Peter Lange klagt. Herrn Langes Anspruch ist begründet.", ". Herrn"),
    ],
)
def test_real_genitive_of_the_client_still_maps_to_the_client(gateway, text, prefix) -> None:
    _, sach, payload = _run(gateway, text, _known("mandant", "Peter Lange"))
    assert "Lange" not in payload
    first = re.search(r"\[[A-Z]+_\d+\]", sach.split(prefix, 1)[1] if prefix else sach).group(0)
    assert first == "[MANDANT_01]", sach


# --- C: juristische Fundstellen ------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "citation",
    [
        "BGH, Urt. v. 12.10.2022 - VIII ZR 1/21",
        "BGH, Beschl. v. 03.05.2021 - IX ZB 3/21",
        "BGH, Urt. v. 01.02.2020 - XI ZR 5/19",
        "BVerfG, Beschl. v. 01.01.2022 - 1 BvR 2017/21",
        "BAG, Urt. v. 05.05.2021 - 2 AZR 123/20",
        "BGH VI ZR 123/19, NJW 2020, 2345 Rn. 12",
    ],
)
def test_federal_court_citations_stay_readable(gateway, citation) -> None:
    _, sach, _ = _run(gateway, f"Vgl. {citation}; die Kündigung ist unwirksam.")
    assert citation in sach, sach


@pytest.mark.parametrize(
    "text, name",
    [
        ("Der Kläger Max Mustermann beruft sich auf BGH VIII ZR 1/21.", "Max Mustermann"),
        ("Vgl. BGH, Urt. v. 12.10.2022 - VIII ZR 1/21; Zeugin ist Frau Henrike Wallbaum.", "Henrike Wallbaum"),
        ("Herr Gernot Hahn (vgl. BVerfG, 1 BvR 2017/21) widerspricht.", "Gernot Hahn"),
    ],
)
def test_names_next_to_citations_are_still_masked(gateway, text, name) -> None:
    _, _, payload = _run(gateway, text)
    assert name not in payload and name.split()[-1] not in payload


@pytest.mark.parametrize(
    "text, value",
    [
        ("Unser Zeichen: Az. 3 O 123/26 - bitte stets angeben.", "3 O 123/26"),
        ("Klage beim LG München I, 3 O 123/26, eingereicht.", "3 O 123/26"),
        ("OLG Köln, 7 U 123/20 - unser Berufungsverfahren.", "7 U 123/20"),
    ],
)
def test_own_and_lower_court_file_numbers_stay_masked(gateway, text, value) -> None:
    _, _, payload = _run(gateway, text)
    assert value not in payload


def test_court_abbreviation_does_not_unmask_a_company_or_person(gateway) -> None:
    _, _, payload = _run(gateway, "Die BGH Bau GmbH und Herr Bernd Vogel streiten; vgl. BGH VIII ZR 1/21.")
    assert "Bernd Vogel" not in payload and "Vogel" not in payload


@pytest.mark.parametrize(
    "mandant, text, value",
    [
        ("Karl Brenner", "Die Brennersche Erbengemeinschaft klagt; Karl Brenner ist Miterbe.", "Brennersche"),
        ("Karl Brenner", "Das Brennerhaus wurde 2019 verkauft; Karl Brenner bestreitet das.", "Brennerhaus"),
        ("Ute Groß", "Frau Ute Groß und Großes Interesse; Groß' Anspruch ist begründet.", "Groß' Anspruch"),
    ],
)
def test_name_derivations_and_compounds_stay_masked(gateway, mandant, text, value) -> None:
    """Gegenprobe zur Adjektiv-Ausnahme: vom Namen abgeleitete Woerter und Komposita bleiben maskiert."""
    _, _, payload = _run(gateway, text, _known("mandant", mandant))
    assert value not in payload


def test_same_surname_other_person_is_still_masked_in_the_answer_check(gateway) -> None:
    from app.privacy.security_check import check_response_placeholder_integrity

    result, _, _ = _run(gateway, "Karl Brenner klagt. Seine Nachbarin Frau Brenner ist Zeugin.", _known("mandant", "Karl Brenner"))
    assert check_response_placeholder_integrity("Frau Brenner bestätigt das.", result.mappings, require_full_coverage=False)


@pytest.mark.parametrize(
    "text, value",
    [
        # Review §33 (N1/N2): eigene Bundesverfahren und Aktenzeichen ausserhalb der kanonischen Zitierform bleiben maskiert
        ("Unsere Verfassungsbeschwerde beim BVerfG, 1 BvR 77/25, ist anhängig.", "77/25"),
        ("Revision des Mandanten zum BAG unter 2 AZR 55/25 eingelegt.", "55/25"),
        ("Beim BGH ist unser Verfahren 3 StR 12/25 anhängig.", "12/25"),
        ("Revision beim BGH, Az. VIII ZR 99/25, eingelegt.", "99/25"),
        ("Unser Verfahren VIII ZR 99/25 läuft.", "99/25"),
        ("Vgl. BGH NJW 2023, 123 (I ZR 23/20).", "23/20"),  # Reporter-Form: konservativ maskiert (Grenze, §33)
        ("Unsere Revision: BGH, Urt. v. 01.02.2026 - VIII ZR 7/25.", "7/25"),
    ],
)
def test_own_or_non_canonical_federal_file_numbers_stay_masked(gateway, text, value) -> None:
    _, _, payload = _run(gateway, text)
    assert value not in payload


def test_ambiguous_first_names_do_not_split_the_client(gateway) -> None:
    """Review §33 (N4): "Andrea" ist je nach Sprache maennlich oder weiblich - kein Geschlechtsbeleg, keine Trennung."""
    _, sach, _ = _run(gateway, "Andrea Brenner widerspricht; Herr Brenner (der Mandant) zahlt nicht.", _known("mandant", "Andrea Brenner"))
    assert _placeholder_after(sach, "; Herr") == "[MANDANT_01]"


def test_contradicting_gender_evidence_counts_as_unknown(gateway) -> None:
    _, sach, _ = _run(gateway, "Die Mandantin Karl Brenner widerspricht; Herr Brenner zahlt nicht.", _known("mandant", "Karl Brenner"))
    assert _placeholder_after(sach, "; Herr") == "[MANDANT_01]"


def test_pos_analysis_runs_at_most_once_per_text(monkeypatch) -> None:
    """Review §33 (N3): Wortarten einmal je Text statt einmal je Treffer (vorher bis zu 100 s bei langen Einzeilern)."""
    import app.privacy.presidio_ner as presidio_ner
    from app.privacy.detectors import detect_all

    calls = []
    original = presidio_ner.get_pos_tags

    def counting(text):  # noqa: ANN001
        calls.append(len(text))
        return original(text)

    monkeypatch.setattr(presidio_ner, "get_pos_tags", counting)
    text = " ".join(f"Herr Karl Brenner und Brenners Antrag sowie Großes Interesse Nr. {i}" for i in range(40))
    detect_all(text, {"mandant": ["Karl Brenner", "Brenner"]}, ner_detector=presidio_ner.detect_presidio_entities)
    assert len(calls) <= 1


@pytest.mark.parametrize(
    "text, value",
    [
        ("Termin in unserem Verfahren: BGH VIII ZR 99/25 am 03.03.2026.", "99/25"),
        ("Der Mandant hat Revision eingelegt, BGH, VIII ZR 98/25.", "98/25"),
    ],
)
def test_direct_court_form_with_own_proceeding_hint_stays_masked(gateway, text, value) -> None:
    _, _, payload = _run(gateway, text)
    assert value not in payload


def test_e2e_chat_payload_identity_genitive_and_citation(tmp_path) -> None:
    """Echter Chat-Pfad mit Anhang (bekannter Mandant aus der Akte): Payload unmittelbar vor dem Cloud-Aufruf.
    ("Herr Peter Lange rügt Feuchtigkeit ..." blockiert vorbestehend im Restrisiko-Scan als "person" - auch auf dem
    Ausgangsstand 6dac5c5, unabhaengig von §33; daher hier eine andere Formulierung.)"""
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
    client = Client(name="Peter Lange")
    matter = Matter(client=client, title="Mietsache")
    db.add_all([user, client, matter])
    db.flush()
    doc = Document(matter_id=matter.id, file_path="b.txt", classified_type="Schreiben",
                   extracted_text="Herr Peter Lange klagt seit 01.03.2026. Langes Zögern des Vermieters schadet. "
                                  "Seine Nachbarin Frau Lange ist Zeugin. Vgl. BGH, Urt. v. 12.10.2022 - VIII ZR 1/21; "
                                  "Minderung 20 Prozent.")
    db.add(doc)
    db.commit()
    chat = ChatService(tmp_path / "uploads")
    conversation = chat.create_conversation(db, user=user, matter_id=matter.id, title="F", actor=user.email)
    message = chat.record_user_message(db, conversation=conversation, content="Fasse zusammen.", document_ids=[doc.id])
    drafting, writer = _drafting_service("Antwort.")
    chat.send_message(db, conversation=conversation, content="Fasse zusammen.", drafting_service=drafting,
                      actor=user.email, current_message_id=message.id)
    payload = writer.received_payloads[0]
    sach = payload.anonymisierter_sachverhalt
    assert "Peter Lange" not in json.dumps(payload.model_dump(), ensure_ascii=False)
    assert "Frau Lange" not in sach and _placeholder_after(sach, "Nachbarin Frau") != "[MANDANT_01]"
    assert "[MANDANT_01]s Zögern" not in sach
    for fact in ("VIII ZR 1/21", "01.03.2026", "20 Prozent"):
        assert fact in sach


def test_date_swallowed_by_a_name_hit_stays_readable_and_is_not_merged(gateway) -> None:
    """NER-Treffer "01.03.2026. Langes": Frist blieb unlesbar (vorbestehend) und - seit P05 - wurde "Frau Lange" damit
    zusammengefuehrt (neu, §33 behoben)."""
    _, sach, payload = _run(gateway, "Herr Peter Lange klagt seit 01.03.2026. Langes Zögern schadet. Seine Nachbarin Frau Lange ist Zeugin.",
                            _known("mandant", "Peter Lange"))
    assert "01.03.2026" in sach and "Lange" not in payload
    assert _placeholder_after(sach, "Nachbarin Frau") not in ("[MANDANT_01]",)
    zoegern = _placeholder_after(sach, "01.03.2026.") if re.search(r"01\.03\.2026\.\s*\[", sach) else None
    assert zoegern != _placeholder_after(sach, "Nachbarin Frau")


@pytest.mark.parametrize(
    "text, value",
    [
        # Review 2 §33: arabische Kurzform war auf dem Ausgangsstand maskiert und bleibt es
        ("Hahn, BAG 2 AZR 55/25.", "55/25"),
        # Hinweisfenster wird nicht mehr an Abkuerzungen, Zeilenumbruechen oder Semikola abgeschnitten
        ("Unser Verfahren, vgl. BGH VIII ZR 99/25, ist offen.", "99/25"),
        ("Unsere Revision z. B. BGH VIII ZR 99/25.", "99/25"),
        ("Unsere Revision (Az. BGH VIII ZR 99/25).", "99/25"),
        ("Bitte unter folgendem Zeichen führen:\nBGH VIII ZR 99/25", "99/25"),
        ("Der Termin ist BGH VIII ZR 99/25 am 03.11.2026; unsere Revision läuft.", "99/25"),
    ],
)
def test_review2_own_proceeding_variants_stay_masked(gateway, text, value) -> None:
    _, _, payload = _run(gateway, text)
    assert value not in payload
