"""§67: oeffentliche Gerichtsbezeichnungen bleiben als Institution lesbar - Aktenzeichen und personenbezogene Daten
bleiben geschuetzt, auch direkt neben dem Gerichtsnamen; Orte ausserhalb einer Gerichtsbezeichnung bleiben maskierbar;
bei Mehrdeutigkeit (Sitz auch als Name/Anschrift im Text) gewinnt der Schutz. Nur synthetische Texte, lokal."""

from __future__ import annotations

import pytest

from app.privacy.gateway import ClaudePrivacyGateway
from tests.test_privacy_identity_genitive_citations import _known


def _run(gateway, text, known=None):
    """Wie der Helfer der Zitat-Tests, aber ohne Vorspann "Akte: Beispielakte": dieser wird vom Restrisiko-Scan
    unabhaengig von §67 als Ort gemeldet und blockiert manche Saetze fail-closed (mit und ohne Gerichtsregel gemessen)."""
    import json

    result = gateway.prepare_request(purpose="formulate_draft", sachverhalt=text, known_entities=known)
    assert result.payload is not None, result.reasons
    return result, result.payload.anonymisierter_sachverhalt, json.dumps(result.payload.model_dump(), ensure_ascii=False)


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


@pytest.mark.parametrize(
    "text, court",
    [
        ("Klage beim Amtsgericht Kiel, Termin am 15.04.2026.", "Amtsgericht Kiel"),
        ("Das Landgericht Berlin hat die Klage am 03.03.2026 abgewiesen.", "Landgericht Berlin"),
        ("Der Bundesgerichtshof hat die Revision zugelassen.", "Bundesgerichtshof"),
        ("Das Bundesarbeitsgericht hat entschieden.", "Bundesarbeitsgericht"),
        ("Kündigungsschutzklage zum Arbeitsgericht Frankfurt am Main eingereicht.", "Arbeitsgericht Frankfurt am Main"),
        ("Das Landgericht München I hat die Klage abgewiesen.", "Landgericht München I"),
        ("Vermerk zum Verfahren vor dem Landgericht Osnabrück.", "Landgericht Osnabrück"),
        ("Die Akte des Amtsgerichts Kiel liegt vor.", "Amtsgerichts Kiel"),
        ("Klage beim Arbeitsgericht Berlin eingereicht.", "Arbeitsgericht Berlin"),
        ("Das Oberlandesgericht Schleswig hat entschieden.", "Oberlandesgericht Schleswig"),
        ("Das Landesarbeitsgericht Berlin-Brandenburg hat entschieden.", "Landesarbeitsgericht Berlin-Brandenburg"),
        ("Die Staatsanwaltschaft Kiel ermittelt.", "Staatsanwaltschaft Kiel"),
        ("Die Generalstaatsanwaltschaft Schleswig prüft die Beschwerde.", "Generalstaatsanwaltschaft Schleswig"),
    ],
)
def test_public_court_names_stay_readable(gateway, text, court) -> None:
    _, sach, _ = _run(gateway, text)
    assert court in sach, sach


def test_a_seat_also_detected_as_a_person_keeps_the_court_masked(gateway) -> None:
    """Gemessen: in "des Amtsgerichts Köpenick" meldet ein zweiter Erkennungsdurchgang "Köpenick" als Person -
    Mehrdeutigkeit, der Schutz geht vor (Uebermaskierung, kein Leck)."""
    _, _, payload = _run(gateway, "Die Akte des Amtsgerichts Köpenick liegt vor.")
    assert "Köpenick" not in payload


@pytest.mark.parametrize(
    "text, place",
    [
        ("Herr Weber wohnt in Kiel, die Staatsanwaltschaft Kiel ermittelt.", "Kiel"),
        ("Die Stadtwerke Kiel und das Finanzamt Kiel schreiben an Herrn Weber.", "Kiel"),
    ],
)
def test_only_judicial_institutions_are_exempted_and_residence_wins(gateway, text, place) -> None:
    """Kein pauschaler Institutionsbegriff: Stadtwerke/Finanzamt bleiben wie bisher; ein Wohnort im selben Text
    maskiert den Sitz auch in der Behoerdenbezeichnung."""
    _, _, payload = _run(gateway, text)
    assert place not in payload and "Weber" not in payload


@pytest.mark.parametrize(
    "text, place",
    [
        ("Herr Weber wohnt in Kiel.", "Kiel"),
        ("Die Mandantin ist nach Osnabrück umgezogen.", "Osnabrück"),
        ("Treffpunkt ist Köpenick, direkt am Bahnhof.", "Köpenick"),
    ],
)
def test_the_same_places_outside_a_court_name_stay_maskable(gateway, text, place) -> None:
    _, _, payload = _run(gateway, text)
    assert place not in payload


def test_a_place_also_used_as_residence_masks_the_court_seat_too(gateway) -> None:
    _, sach, payload = _run(gateway, "Herr Weber wohnt in Kiel. Zuständig ist das Amtsgericht Kiel.")
    assert "Kiel" not in payload and "Amtsgericht [ORT_" in sach


def test_a_seat_that_appears_in_an_address_keeps_the_court_masked(gateway) -> None:
    _, _, payload = _run(gateway, "Amtsgericht Kiel, Frau Hannelore Brüggemann, Am Weidenhang 17, 24103 Kiel.",
                         _known("mandant", "Hannelore Brüggemann"))
    for value in ("Kiel", "Brüggemann", "Weidenhang", "24103"):
        assert value not in payload, value


def test_a_seat_that_is_also_a_person_name_keeps_the_court_masked(gateway) -> None:
    _, _, payload = _run(gateway, "Beim Amtsgericht Weber ist Herr Weber als Zeuge geladen.")
    assert "Weber" not in payload


@pytest.mark.parametrize(
    "text, court, protected",
    [
        ("Klage beim Amtsgericht Kiel, Az. 14 C 233/26.", "Amtsgericht Kiel", "14 C 233/26"),
        ("Klage beim Landgericht Berlin, Az. 27 O 118/26, Termin am 15.04.2026.", "Landgericht Berlin", "27 O 118/26"),
        ("Die Mandantin klagt vor dem Amtsgericht Köpenick (Az. 14 C 233/26).", "Amtsgericht Köpenick", "14 C 233/26"),
        ("Unser Verfahren beim Bundesarbeitsgericht, 2 AZR 72/24, ist anhängig.", "Bundesarbeitsgericht", "2 AZR 72/24"),
        ("Bundesgerichtshof, unsere Revision VIII ZR 274/23 wurde eingelegt.", "Bundesgerichtshof", "VIII ZR 274/23"),
    ],
)
def test_court_name_stays_readable_but_file_numbers_stay_masked(gateway, text, court, protected) -> None:
    _, sach, payload = _run(gateway, text)
    assert court in sach, sach
    assert protected not in payload


def test_a_seat_labelled_as_person_by_the_ner_stays_masked(gateway) -> None:
    """Die Regel gilt nur fuer Ort-/Organisationstreffer - markiert die Namenserkennung den Sitz als Person
    ("Landgericht [PERSON_01]"), bleibt er maskiert (konservativ, keine Lockerung der Personenerkennung)."""
    _, sach, payload = _run(gateway, "Landgericht Berlin 27 O 118/26 - Termin am 15.04.2026.")
    assert "27 O 118/26" not in payload
    assert "Landgericht Berlin" in sach or "Berlin" not in payload  # lesbar nur als Gericht, sonst maskiert


def test_court_name_next_to_personal_data_never_unmasks_it(gateway) -> None:
    text = ("Amtsgericht Kiel, Ladung an Frau Hannelore Brüggemann, Tel. 0431 556677, hanne.b@mail.example, "
            "IBAN DE74 3704 0044 5320 1304 71, geb. 14.02.1941.")
    _, sach, payload = _run(gateway, text, _known("mandant", "Hannelore Brüggemann"))
    assert "Amtsgericht Kiel" in sach
    for value in ("Brüggemann", "0431 556677", "hanne.b@mail.example", "DE74 3704 0044 5320 1304 71", "14.02.1941"):
        assert value not in payload, value


def test_court_names_in_client_and_proceeding_context_release_nothing_else(gateway) -> None:
    text = ("Für unseren Mandanten Herrn Quirin Vossberg-Lahm haben wir beim Landgericht Berlin Klage erhoben "
            "(Az. 27 O 118/26); Gegner ist die Spreeblick Wohnen GmbH, vertreten durch Herrn Arvid Klostermann.")
    known = {**_known("mandant", "Quirin Vossberg-Lahm"), **_known("gegner", "Spreeblick Wohnen GmbH")}
    _, sach, payload = _run(gateway, text, known)
    assert "Landgericht Berlin" in sach
    for value in ("Vossberg-Lahm", "27 O 118/26", "Spreeblick Wohnen", "Klostermann"):
        assert value not in payload, value


def test_lookalike_institutions_are_not_exempted(gateway) -> None:
    """Nur Gerichte - keine anderen Ortsverbindungen ("Stadtwerke Kiel", "Kieler Hof", Anschrift)."""
    _, _, payload = _run(gateway, "Herr Weber arbeitet im Kieler Hof und wohnt Am Hafen 3, 24103 Kiel.")
    assert "Weber" not in payload and "24103" not in payload


def test_ner_failure_still_blocks_with_a_court_name() -> None:
    """Fail-Closed unveraendert: faellt die Namenserkennung aus, entsteht auch mit Gerichtsnamen kein Payload."""
    from app.privacy.pseudonymizer import Pseudonymizer

    def failing(*args, **kwargs):
        raise RuntimeError("NER nicht verfuegbar (simuliert)")

    gateway = ClaudePrivacyGateway(pseudonymizer=Pseudonymizer(ner_detector=failing))
    try:
        result = gateway.prepare_request(purpose="formulate_draft",
                                         sachverhalt="Akte\nKlage beim Amtsgericht Kiel, Frau Hannelore Brüggemann.")
        created = bool(result.allowed and result.payload is not None)
    except Exception:  # noqa: BLE001 - Ausnahme = nichts gesendet
        created = False
    assert not created
