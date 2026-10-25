"""§50: Gerichtspersonen als Aktenbeteiligte (pseudonymization_v1 P10) und zwei Uebermaskierungen der NER-Stufe
("Mobil" vor einer Rufnummer, "bei Rot" im Fahrsatz) samt dem dabei gefundenen Teil-Leck der Rufnummer.

Echter Gateway (Presidio), Maskier-Modus, echte `_build_known_entities`/`foreign_matter_names` gegen eine In-Memory-DB.
Nur synthetische Namen und Nummern."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider, foreign_matter_names, mentions_any_name
from app.models import Client, Matter, Party
from app.models.base import Base
from app.privacy.detectors import DetectedSpan, _resolve_overlaps
from app.privacy.gateway import ClaudePrivacyGateway


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _matter(db, parties: list[tuple[str, str]], client_name: str = "Corinna Haselhorst") -> Matter:
    matter = Matter(client=Client(name=client_name), title="Haselhorst ./. Wendeburg - Mietsache")
    db.add(matter)
    db.flush()
    for name, role in parties:
        db.add(Party(matter_id=matter.id, name=name, role=role))
    db.commit()
    return matter


def _known(db, parties: list[tuple[str, str]]) -> dict[str, list[str]]:
    matter = _matter(db, parties)
    return RuleBasedLocalAIProvider(None)._build_known_entities(matter.id, matter, db)


def _payload(gateway, text: str, known=None) -> str:
    result = gateway.prepare_request(purpose="formulate_draft", sachverhalt=text, known_entities=known, review_mode="mask")
    assert result.allowed, result.reasons
    return result.payload.anonymisierter_sachverhalt


# --- Gerichtspersonen als Beteiligte ------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", [
    "Richter", "Richterin am Amtsgericht", "Vorsitzende Richterin am Landgericht", "Gerichtsvollzieher",
    "Gerichtssachverständiger", "Sachverständige", "Rechtspflegerin", "Staatsanwältin", "Oberstaatsanwalt",
    "Urkundsbeamtin der Geschäftsstelle des Landgerichts", "Schöffe",
])
def test_court_person_roles_are_persons_with_surname(db, role) -> None:
    known = _known(db, [("Dr. Hans Zweigelt", role)])
    assert "Zweigelt" in known.get("beteiligter", []), known
    assert "gericht" not in known and "anwalt" not in known


@pytest.mark.parametrize(("name", "role", "category"), [
    ("Amtsgericht Musterstadt", "Gericht", "gericht"),
    ("Staatsanwaltschaft Musterstadt", "Staatsanwaltschaft", "anwalt"),
    ("Finanzamt Musterstadt", "Finanzamt", "gericht"),
    ("Beispiel & Partner Rechtsanwälte", "gegnerischer Anwalt", "gegner"),
])
def test_institutions_keep_their_previous_category(db, name, role, category) -> None:
    known = _known(db, [(name, role)])
    assert name in known.get(category, []), known
    assert name not in known.get("beteiligter", [])


def test_surname_before_comma_is_the_known_surname(db) -> None:
    known = _known(db, [("Zweigelt, Hans", "Richter")])
    assert "Zweigelt" in known["beteiligter"]
    assert "Hans" not in known["beteiligter"]  # vorher wurde der Vorname zur bekannten Entitaet


@pytest.mark.parametrize("text,keep", [
    ("Der Richter Zweigelt hat angerufen und den Termin auf den 22.07.2026 verlegt.",
     ["Der Richter", "den Termin auf den 22.07.2026 verlegt"]),
    ("Richterin Zweigelt wies auf § 139 ZPO hin.", ["§ 139 ZPO"]),
    ("Nach Zweigelts Hinweis ist die Klage unschlüssig.", ["ist die Klage unschlüssig"]),
    ("VERFÜGUNG: ZWEIGELT, Richter am Amtsgericht", ["Richter am Amtsgericht"]),
])
def test_recorded_judge_is_masked_in_various_spellings(gateway, db, text, keep) -> None:
    known = _known(db, [("Hans Zweigelt", "Richter am Amtsgericht")])
    payload = _payload(gateway, text, known)
    assert "Zweigelt" not in payload and "ZWEIGELT" not in payload
    for fragment in keep:
        assert fragment in payload


def test_recorded_judge_does_not_mask_legal_words(gateway, db) -> None:
    known = _known(db, [("Hans Zweigelt", "Richter am Amtsgericht"), ("Erika Wiebe", "Sachverständige")])
    text = ("Der Richter am Amtsgericht entschied nach § 286 ZPO. Die Sachverständige stellte Feuchtigkeit fest; "
            "der Termin zur Beweisaufnahme bleibt bestehen.")
    assert _payload(gateway, text, known) == text


def test_court_person_of_another_matter_blocks_knowledge_entry_by_surname(db) -> None:
    """Aktenisolation (§27): vorher zaehlte bei "Richter am Amtsgericht" nur der volle Name."""
    _matter(db, [("Hans Zweigelt", "Richter am Amtsgericht")], client_name="Andere Mandantin")
    own = _matter(db, [], client_name="Corinna Haselhorst")
    foreign = foreign_matter_names(own, db)
    assert "Zweigelt" in foreign
    assert mentions_any_name("Im Verfahren vor Richter Zweigelt wurde verglichen.", foreign)


def test_own_judge_is_not_foreign(db) -> None:
    own = _matter(db, [("Hans Zweigelt", "Richter am Amtsgericht")])
    assert "Zweigelt" not in foreign_matter_names(own, db)


# Frueher Grenze P10 (G2, behoben 24.10.2026): das Amtswort davor ist jetzt Kontextsignal fuer die Rueckfrage
# (review._roles "nach_rollenwort"); spaCy liest "Zweigelt" weiterhin wie "Termin".
def test_unrecorded_judge_without_matter_is_masked(gateway) -> None:
    assert "Zweigelt" not in _payload(gateway, "Der Richter Zweigelt hat den Termin verlegt.")


# --- Kontaktbezeichnung vor Rufnummer / Teil-Leck -------------------------------------------------------------------


@pytest.mark.parametrize("text,label,number_parts", [
    ("Bitte melden Sie sich bei Herrn Gustav Wendelin, Mobil 0170 1234567.", "Mobil", ["0170", "1234567"]),
    ("Telefon 0301 2345678, Mobil 0171 7654321.", "Mobil", ["0171", "7654321", "2345678"]),
    ("Erreichbar unter Mobil: 0172 5550123.", "Mobil:", ["0172", "5550123"]),
    ("Herr Gustav Wendelin, Handy 0173 9876543, bittet um Rückruf.", "Handy", ["0173", "9876543"]),
    # als Kennnummer laenger erfasst als von der Telefon-Regex (K04-Form) - darf nicht blockieren
    ("Herr Gustav Wendelin, Mobil 0174-321 65 87.", "Mobil", ["0174", "321 65", "87"]),
])
def test_contact_label_stays_and_number_is_fully_masked(gateway, text, label, number_parts) -> None:
    payload = _payload(gateway, text)
    assert label in payload and ("[TELEFON_" in payload or "[KENNUNG_" in payload)
    for part in number_parts:
        assert part not in payload
    assert "Wendelin" not in payload


@pytest.mark.parametrize("text,name", [
    ("Rückruf an Lehmann 0170 1234567 erbeten.", "Lehmann"),
    ("Kontakt: Gustav Wendelin, 0170 1234567.", "Wendelin"),
    ("Mobil erreichbar ist Frau Erika Wiebe unter 0170 1234567.", "Wiebe"),
])
def test_names_next_to_numbers_stay_masked(gateway, text, name) -> None:
    payload = _payload(gateway, text)
    assert name not in payload and "1234567" not in payload


def test_overlap_of_different_categories_loses_no_character() -> None:
    """NER-Treffer schneidet eine Rufnummer an: die Nummer bleibt vollstaendig ein Treffer, der Name endet davor."""
    text = "Lehmann 0170 1234567"
    person = DetectedSpan(category="person", start=0, end=12, value=text[:12])
    phone = DetectedSpan(category="telefon", start=8, end=20, value=text[8:])
    resolved = _resolve_overlaps([person, phone])
    assert [(s.category, s.value) for s in resolved] == [("person", "Lehmann"), ("telefon", "0170 1234567")]


def test_overlap_without_structured_precedence_keeps_the_remainder() -> None:
    text = "Musterstadt 4711 Ende"
    place = DetectedSpan(category="ort", start=0, end=16, value=text[:16])
    ident = DetectedSpan(category="kennung", start=12, end=21, value=text[12:21])
    resolved = _resolve_overlaps([place, ident])
    covered = set()
    for span in resolved:
        covered |= set(range(span.start, span.end))
    assert set(range(0, 21)) - covered <= {16}  # nur das Leerzeichen
    assert resolved[0] == place


# --- Ampelfarbe -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Der Fahrer fuhr bei Rot in die Kreuzung.",
    "Er ist bei Rot über die Kreuzung gefahren.",
    "Der Fahrer fuhr bei Grün über die Kreuzung.",
])
def test_traffic_light_colour_in_unambiguous_manoeuvre_stays(gateway, text) -> None:
    assert _payload(gateway, text) == text


@pytest.mark.parametrize("text,word", [
    ("Die Zeugin übernachtete bei Rot, direkt an der Kreuzung.", "Rot"),
    ("Herr Rot war Zeuge des Unfalls.", "Rot"),
    ("Sie wohnt in Rot am See.", "Rot"),
    ("Herr Grün fuhr in die Kreuzung.", "Grün"),
])
def test_colour_word_as_name_or_place_stays_masked(gateway, text, word) -> None:
    assert word not in _payload(gateway, text)
