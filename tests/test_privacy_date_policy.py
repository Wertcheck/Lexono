"""Datumspolicy (Owner-Entscheidung 13.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md §23).

Sachverhalts-, Vertrags-, Frist- und Ereignisdaten bleiben lesbar (Chronologie, Abstaende, Fristberechnung). Geschuetzt
wird das Geburtsdatum (starkes Quasi-Merkmal, auch bei pseudonymisiertem Namen) - erkannt am Geburtskontext; sein
juristischer Gehalt (Alter) wird lokal berechnet, ohne Tagesabstaende zu lesbaren Daten. Nur synthetische Daten.
Gemessen vorher: 27 von 27 Sachverhaltsdaten im Messkorpus verdeckt."""

from __future__ import annotations

import json
import re
from datetime import date

import pytest

from app.privacy.date_chronology import build_age_note
from app.privacy.detectors import detect_geburtsdatum
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.pseudonymizer import PseudonymMapping


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _prepare(gateway, text: str, mode: str = "mask"):
    sv = "Akte: Beispielakte\n[Schreiben] " + text
    return sv, gateway.prepare_request(
        purpose="chat_response", sachverhalt=sv, anwaltliche_anmerkungen="Fasse zusammen.", review_mode=mode
    )


BIRTH = [
    ("Herr Karl Brenner, geb. 12.03.1980, wohnhaft in Musterstadt, beauftragt uns.", "12.03.1980"),
    ("Frau Petra Lindner (geboren am 5. Mai 1975) ist Alleinerbin.", "5. Mai 1975"),
    ("Geburtsdatum: 01.01.1990, Staatsangehörigkeit deutsch.", "01.01.1990"),
    ("Der Kläger, * 24.12.1968, beantragt Prozesskostenhilfe.", "24.12.1968"),
    ("Geb.-Datum 03.07.2001; Eintritt am 01.08.2020.", "03.07.2001"),
    ("Herr Brenner (12.03.1980) und Frau Lindner (05.05.1975) sind Gesellschafter.", "05.05.1975"),
    ("Die Mandantin wurde am 17. August 1983 in Köln geboren.", "17. August 1983"),
    ("Das Kind (geb. am 02.02.2015) lebt bei der Mutter.", "02.02.2015"),
    ("Der am 12.03.1980 geborene Kläger widerspricht.", "12.03.1980"),
    # Review 13.10.2026 (vorher im Klartext)
    ("Geburtsdatum des Kindes: 01.02.2015", "01.02.2015"),
    ("Sein Geburtstag ist der 12.03.1980.", "12.03.1980"),
    ("Herr Brenner, geb. am Mittwoch, 12.03.1980, klagt.", "12.03.1980"),
    ("Herr Brenner, geboren am 1980-03-12, klagt.", "1980-03-12"),
    ("DOB: 12.03.1980", "12.03.1980"),
    ("Herr Max Brenner, 12.03.1980, wohnhaft in Musterstadt.", "12.03.1980"),
    ("Er ist am 12. März 1980 in Köln zur Welt gekommen.", "12. März 1980"),
    ("Erblasser Max Brenner (12.03.1940 - 01.01.2026) hinterließ ein Testament.", "12.03.1940"),
    ("Name | Geburtsdatum\nBrenner | 12.03.1980", "12.03.1980"),
    ("Geburtsdatum 12/03/1980 laut Ausweis.", "12/03/1980"),
    ("Er feierte am 12.03.2025 seinen 45. Geburtstag.", "12.03.2025"),  # Jahrestag + Alter verraten das Datum
]


@pytest.mark.parametrize("text, birthdate", BIRTH)
def test_birthdates_are_masked_consistently(gateway, text, birthdate) -> None:
    sv, result = _prepare(gateway, text)
    assert result.allowed, result.reasons
    everything = json.dumps(result.payload.model_dump(), ensure_ascii=False)
    assert birthdate not in everything
    # Jahrestag ("feierte am ... Geburtstag") als eigene Kategorie ohne Altersangabe
    assert "[GEBURTSDATUM_" in result.payload.anonymisierter_sachverhalt or "[GEBURTSTAG_" in result.payload.anonymisierter_sachverhalt
    assert gateway.reconstruct_response(result.payload.anonymisierter_sachverhalt, result.mappings) == sv


def test_birthdate_in_csv_column_never_leaves_in_plain_text(gateway) -> None:
    """CSV-Zeile mit Kopf "Geburtsdatum": das Datum wird ueber die Spalte erkannt. Die Zeile selbst blockiert der
    Restrisiko-Scan vorbestehend (NER auf CSV, auf 64be4f1 gleich) - fail-closed; geprueft wird: nie Klartext."""
    from app.privacy.detectors import detect_geburtsdatum

    text = "Name;Vorname;Geburtsdatum;Ort\nBrenner;Max;12.03.1980;Köln"
    assert [s.value for s in detect_geburtsdatum(text)] == ["12.03.1980"]
    _, result = _prepare(gateway, text)
    assert result.payload is None or "12.03.1980" not in json.dumps(result.payload.model_dump(), ensure_ascii=False)


FACT_DATES = [
    ("Der Mietvertrag vom 01.04.2019 wurde am 30.09.2026 gekündigt.", ["01.04.2019", "30.09.2026"]),
    ("Die Berufungsfrist endet am 14.11.2026.", ["14.11.2026"]),
    ("Unfall am 12.03.2025 gegen 14:30 Uhr; Anzeige am 13.03.2025.", ["12.03.2025", "13.03.2025"]),
    ("Schreiben vom 2. Oktober 2026, zugegangen am 05.10.2026.", ["2. Oktober 2026", "05.10.2026"]),
    # Klammer nach Person, aber kein plausibles Geburtsjahr bzw. andere Bedeutung
    ("Herr Brenner (Schreiben vom 12.03.2026) widersprach.", ["12.03.2026"]),
    ("Herr Brenner (12.03.2026) widersprach.", ["12.03.2026"]),
    ("Der Arbeitsvertrag (01.09.1998) sieht drei Monate Kündigungsfrist vor.", ["01.09.1998"]),
    ("Herr Brenner (seit 01.09.1998 beschäftigt) erhielt die Kündigung.", ["01.09.1998"]),
    # BEWUSST GEAENDERT 13.10.2026 (§24): das Todesdatum ist jetzt geschuetzt, das Testamentsdatum bleibt lesbar
    ("Der Erblasser verstarb am 01.02.2024; das Testament datiert vom 15.06.2019.", ["15.06.2019"]),
    ("Zahlungen am 1.2.26 und 15.2.26 blieben aus.", ["1.2.26", "15.2.26"]),
    ("Gebäude geb. Baujahr? Nein: Errichtet am 01.01.1985, saniert am 01.06.2010.", ["01.06.2010"]),
    # Review 13.10.2026: Mietbeginn in Klammern, Fussnoten-Stern, Datum mit Schraegstrich, Satzende vor "Geburtstag"
    ("Herr Max Brenner (01.04.1995) ist seit diesem Tag Mieter.", ["01.04.1995"]),
    ("Siehe *12.03.2024 Fußnote.", ["12.03.2024"]),
    ("Vertrag vom 12/03/2026 gekündigt.", ["12/03/2026"]),
    ("Frist am 01.02.2026. Sein 40. Geburtstag war schön.", ["01.02.2026"]),
    ("Herr Brenner, 01.09.1998 eingestellt, klagt.", ["01.09.1998"]),
    # 2. Review-Runde: Geburtsname nach "geb.", Wortteil "Geburtstagsfeier", Tabellentrenner, Mietzeitraum
    ("Herr Max Brenner, geb. Sommer, kündigte am 12.03.2026.", ["12.03.2026"]),
    ("Vertrag vom 12.03.2026, Geburtstagsfeier geplant.", ["12.03.2026"]),
    ("Geburtsdatum: siehe Anlage | Termin 12.03.2026", ["12.03.2026"]),
    ("Mieterin Sommer (01.04.1995 – 31.03.2005) zahlte pünktlich.", ["01.04.1995", "31.03.2005"]),
]


@pytest.mark.parametrize(
    "text, ip",
    [("IP-Adr. 10.0.12.77 laut Logdatei.", "10.0.12.77"), ("Zugriff von 10.0.12.77 um 10:00 Uhr.", "10.0.12.77"),
     ("Login von 192.168.178.23 bestätigt.", "192.168.178.23")],
)
def test_ip_addresses_stay_masked_after_the_date_policy(gateway, text, ip) -> None:
    """Messkette h11: die pauschale Datumsmaskierung verdeckte "10.0.12" vorher nur zufaellig - nach der Datumspolicy ging
    die IP-Adresse im Klartext hinaus. Jetzt eigene IPv4-Erkennung (detectors.detect_ip_adresse)."""
    _, result = _prepare(gateway, text)
    assert result.allowed and ip not in result.payload.anonymisierter_sachverhalt


def test_version_and_chapter_numbers_are_no_ip_address(gateway) -> None:
    _, result = _prepare(gateway, "Version 2.4.1.0 der Software; Kapitel 10.2.3.4 des Gutachtens.")
    assert "2.4.1.0" in result.payload.anonymisierter_sachverhalt
    assert "10.2.3.4" in result.payload.anonymisierter_sachverhalt


def test_birthday_anniversary_is_masked_without_an_age_note(gateway) -> None:
    _, result = _prepare(gateway, "Am 12.03.2026 war sein Geburtstag; er wurde 45.")
    assert "12.03.2026" not in result.payload.anonymisierter_sachverhalt
    assert "[GEBURTSTAG_" in result.payload.anonymisierter_sachverhalt
    assert not [a for a in result.payload.anonymisierte_argumentationspunkte if a.startswith("Alter laut Geburtsdatum")]


@pytest.mark.parametrize("text, dates", FACT_DATES)
def test_fact_contract_deadline_and_event_dates_stay_readable(gateway, text, dates) -> None:
    _, result = _prepare(gateway, text)
    assert result.allowed, result.reasons
    for value in dates:
        assert value in result.payload.anonymisierter_sachverhalt


def test_mixed_chronology_keeps_order_gaps_and_deadlines_and_hides_only_the_birthdate(gateway) -> None:
    text = (
        "Kläger: Karl Brenner, geb. 12.03.1980. Beklagte: Petra Lindner, geb. 05.05.1975. "
        "Vertrag vom 01.04.2019; Mängelanzeige am 01.03.2026, Fristsetzung bis 29.03.2026, Rücktritt am 02.04.2026."
    )
    sv, result = _prepare(gateway, text)
    payload = result.payload.anonymisierter_sachverhalt
    for value in ("01.04.2019", "01.03.2026", "29.03.2026", "02.04.2026"):
        assert value in payload
    assert payload.index("01.03.2026") < payload.index("29.03.2026") < payload.index("02.04.2026")
    everything = json.dumps(result.payload.model_dump(), ensure_ascii=False)
    for value in ("12.03.1980", "05.05.1975"):
        assert value not in everything
    # zwei Personen -> zwei verschiedene Geburtsdatum-Platzhalter
    assert len(set(re.findall(r"\[GEBURTSDATUM_\d+\]", payload))) == 2
    # keine Tagesabstaende zwischen Geburtsdatum und lesbaren Daten (sonst exakt zurueckrechenbar)
    assert not re.search(r"GEBURTSDATUM_\d+\][^.;]*\d+ Tage", everything)
    assert gateway.reconstruct_response(payload, result.mappings) == sv


def test_age_note_gives_completed_years_and_majority_only() -> None:
    mappings = [
        PseudonymMapping(placeholder="[GEBURTSDATUM_01]", category="geburtsdatum", original_value="12.03.1980"),
        PseudonymMapping(placeholder="[GEBURTSDATUM_02]", category="geburtsdatum", original_value="13.10.2010"),
        PseudonymMapping(placeholder="[GEBURTSDATUM_03]", category="geburtsdatum", original_value="14.10.2008"),
    ]
    note = build_age_note(mappings, today=date(2026, 10, 13))
    assert "[GEBURTSDATUM_01]: 46 vollendete Jahre (volljährig)" in note
    assert "[GEBURTSDATUM_02]: 16 vollendete Jahre (minderjährig)" in note
    assert "[GEBURTSDATUM_03]: 17 vollendete Jahre (minderjährig)" in note  # 18. Geburtstag erst morgen
    assert "1980" not in note and "Tage" not in note


def test_gateway_passes_the_age_note(gateway) -> None:
    _, result = _prepare(gateway, "Herr Karl Brenner, geb. 12.03.1980, beantragt Rente.")
    notes = [a for a in result.payload.anonymisierte_argumentationspunkte if a.startswith("Alter laut Geburtsdatum")]
    assert len(notes) == 1 and "vollendete Jahre" in notes[0]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Herr Brenner (12.03.1980)", ["12.03.1980"]),
        ("Herr Brenner (12.03.2020)", []),  # juenger als 14 Jahre - kein plausibles Geburtsdatum in Klammern
        ("Mieter Karl Brenner (01.01.1950)", ["01.01.1950"]),
        ("Kaufvertrag (01.01.1950)", []),  # keine Personenbezeichnung davor
        ("geb.  12.03.1980", ["12.03.1980"]),
        ("Gebühr vom 12.03.1980", []),  # "Geb" nur als Wortteil
    ],
)
def test_birth_context_rules(text, expected) -> None:
    assert [s.value for s in detect_geburtsdatum(text, current_year=2026)] == expected
