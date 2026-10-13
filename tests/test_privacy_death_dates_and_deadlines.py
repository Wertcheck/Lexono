"""Todesdaten (§24) und Fristerkennung ohne Geburts-/Todesdaten (13.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md §24).

Todesdaten werden wie Geburtsdaten am Kontext erkannt und als [TODESDATUM_nn] maskiert; lokal geht nur die Zahl der
vollen Jahre seit dem Tod hinaus. Woerter, die "Tod" nur enthalten, machen ein Datum nicht zum Todesdatum. Die lokale
Fristerkennung schlaegt Geburts- und Todesdaten nicht mehr als Frist vor (vorher: "Mögliche Frist: [GEBURTSDATUM_01]",
installierter Build 953dc8d). Nur synthetische Daten."""

from __future__ import annotations

import json
import re
from datetime import date

import pytest

from app.deadlines.extractor import PlaceholderDeadlineExtractor, is_personal_date
from app.privacy.date_chronology import build_death_note
from app.privacy.detectors import detect_todesdatum
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


DEATH = [
    ("Der Erblasser ist am 01.02.2024 in Köln verstorben.", "01.02.2024"),
    ("Herr Brenner, verstorben am 01.02.2024, hinterließ zwei Kinder.", "01.02.2024"),
    ("Die Mutter ist gestorben am 3. März 2025.", "3. März 2025"),
    ("Sein Todestag ist der 01.02.2024.", "01.02.2024"),
    ("Sterbedatum des Erblassers: 01.02.2024", "01.02.2024"),
    ("Max Brenner, gest. 01.02.2024, war Eigentümer.", "01.02.2024"),
    ("Max Brenner († 01.02.2024) war Eigentümer.", "01.02.2024"),
    ("Erblasser Max Brenner (12.03.1940 - 01.01.2026) hinterließ ein Testament.", "01.01.2026"),
    ("Der Tod des Vaters trat am 01.02.2024 ein.", "01.02.2024"),
    ("Erbfall am 01.02.2024; Ausschlagung geprüft.", "01.02.2024"),
    ("Er verstarb am Montag, 01.02.2024, im Krankenhaus.", "01.02.2024"),
    ("Name | Sterbedatum\nBrenner | 01.02.2024", "01.02.2024"),
    ("Name;Vorname;Todesdatum\nLindner;Petra;05.05.2023", "05.05.2023"),
    # Review §24 (vorher im Klartext)
    ("Herr Brenner, verst. am 01.02.2024, war Eigentümer.", "01.02.2024"),
    ("Herr Brenner, gest. am 01.02.2024, war Eigentümer.", "01.02.2024"),
    ("Der Vater starb am 01.02.2024.", "01.02.2024"),
    ("Todeszeitpunkt: 01.02.2024", "01.02.2024"),
    ("Sterbefall vom 01.02.2024 gemeldet.", "01.02.2024"),
    ("Ableben am 01.02.2024 festgestellt.", "01.02.2024"),
    ("Die am 01.02.2024 Verstorbene hinterließ ein Testament.", "01.02.2024"),
    ("Der Tod ist am 01.02.2024 eingetreten.", "01.02.2024"),
    ("Er ist am 01.02.2024 nach langer Krankheit verstorben.", "01.02.2024"),
    ("Todesdatum:\n01.02.2024", "01.02.2024"),
    ("Sterbedatum des Erblassers Maximilian Mustermann: 01.02.2024", "01.02.2024"),
    ("Die verstorbene Mutter (01.02.2024) hinterließ ein Haus.", "01.02.2024"),
    ("Erblasser Max Brenner (1940-03-12 - 2024-02-01) hinterließ ein Testament.", "2024-02-01"),
    ("Erblasser Max Brenner (12. März 1940 - 1. Februar 2024) hinterließ ein Testament.", "1. Februar 2024"),
    ("In stiller Trauer: Max Brenner * 12.03.1940 † 01.02.2024", "01.02.2024"),
    ("Die Großmutter ist verschieden am 01.02.2024.", "01.02.2024"),
    ("Die am 01.02.2024 Verstorbene hinterließ ein Haus.", "01.02.2024"),
]


@pytest.mark.parametrize("text, value", DEATH)
def test_death_dates_are_masked_consistently(gateway, text, value) -> None:
    sv, result = _prepare(gateway, text)
    assert result.payload is None or value not in json.dumps(result.payload.model_dump(), ensure_ascii=False)
    if result.payload is not None:
        assert "[TODESDATUM_" in result.payload.anonymisierter_sachverhalt
        assert gateway.reconstruct_response(result.payload.anonymisierter_sachverhalt, result.mappings) == sv


EVENT_DATES = [
    ("Unfall mit Todesfolge am 12.03.2025 auf der Landstraße.", ["12.03.2025"]),
    ("Die Todesanzeige erschien am 05.02.2024 in der Zeitung.", ["05.02.2024"]),
    ("Herr Brenner ist verstorben, Beerdigung am 10.02.2024.", ["10.02.2024"]),
    ("Testament vom 15.06.2019, Ausschlagung am 10.03.2024.", ["15.06.2019", "10.03.2024"]),
    ("Der Erbfall ist eingetreten; Antrag vom 05.03.2024.", ["05.03.2024"]),
    ("Todesfallleistung der Versicherung am 01.04.2024 ausgezahlt.", ["01.04.2024"]),
    # Review §24: "Verstorbenen"/"Erbfall" mit Frist bzw. anderem Ereignis in der Luecke - keine Todesdaten
    ("Die Erben des Verstorbenen haben Frist bis 15.04.2024 für die Annahme.", ["15.04.2024"]),
    ("Nach dem Erbfall wurde Frist bis zum 15.04.2024 gesetzt.", ["15.04.2024"]),
    ("Er ist verstorben und wurde am 10.02.2024 beigesetzt.", ["10.02.2024"]),
    ("Das Konto des Verstorbenen wurde am 03.03.2024 aufgelöst.", ["03.03.2024"]),
    ("Am 01.03.2024 wurde der Vertrag geschlossen, der Vater ist verstorben.", ["01.03.2024"]),
    # 2. Review-Runde §24: Kenntnis-, Bescheid-, Urkunden- und Schreibensdaten bzw. Termin nach Ueberschrift
    ("Am 05.03.2024 erfuhr die Tochter dass der Vater verstorben ist.", ["05.03.2024"]),
    ("Mit Bescheid vom 05.03.2024 wurde festgestellt dass er verstorben ist.", ["05.03.2024"]),
    ("Laut Sterbeurkunde vom 05.02.2024 ist der Erblasser verstorben.", ["05.02.2024"]),
    ("Schreiben vom 05.03.2024 an die Erben des verstorbenen Vaters.", ["05.03.2024"]),
    ("Todesfall gemeldet\n05.03.2024: Termin beim Nachlassgericht", ["05.03.2024"]),
    ("Erbfall\n05.03.2024 Eingang Antrag", ["05.03.2024"]),
]


def test_knowledge_date_stays_a_deadline_suggestion() -> None:
    """Die Ausschlagungsfrist beginnt mit der Kenntnis - dieses Datum darf nicht als Todesdatum verschwinden."""
    found = [d.raw_date_text for d in PlaceholderDeadlineExtractor().extract(
        "Am 05.03.2024 erfuhr die Tochter dass der Vater verstorben ist.\nTodesfall gemeldet\n06.03.2024: Termin beim Nachlassgericht"
    )]
    assert "05.03.2024" in found and "06.03.2024" in found


def test_obituary_masks_birth_and_death_date(gateway) -> None:
    _, result = _prepare(gateway, "In stiller Trauer: Max Brenner * 12.03.1940 † 01.02.2024")
    payload = result.payload.anonymisierter_sachverhalt
    assert "12.03.1940" not in payload and "01.02.2024" not in payload


@pytest.mark.parametrize("text, dates", EVENT_DATES)
def test_ordinary_event_dates_near_death_words_stay_readable(gateway, text, dates) -> None:
    _, result = _prepare(gateway, text)
    assert result.allowed, result.reasons
    for value in dates:
        assert value in result.payload.anonymisierter_sachverhalt


def test_mixed_inheritance_case_keeps_legal_chronology_and_hides_birth_and_death(gateway) -> None:
    text = (
        "Erblasser Max Brenner (12.03.1940 - 01.01.2026) verstarb in Köln. Testament vom 15.06.2019, "
        "Eröffnung am 20.02.2026, Ausschlagung am 10.03.2026, Erbschein beantragt am 01.04.2026."
    )
    sv, result = _prepare(gateway, text)
    payload = result.payload.anonymisierter_sachverhalt
    everything = json.dumps(result.payload.model_dump(), ensure_ascii=False)
    for hidden in ("12.03.1940", "01.01.2026"):
        assert hidden not in everything
    for readable in ("15.06.2019", "20.02.2026", "10.03.2026", "01.04.2026"):
        assert readable in payload
    assert payload.index("20.02.2026") < payload.index("10.03.2026") < payload.index("01.04.2026")
    # keine Tagesabstaende oder Lagebeziehung des Todesdatums zu lesbaren Daten
    assert not re.search(r"TODESDATUM_\d+\][^.;]*(?:\d+ Tage|vor dem|nach dem)", everything)
    assert gateway.reconstruct_response(payload, result.mappings) == sv


def test_death_note_gives_only_full_years() -> None:
    mappings = [
        PseudonymMapping(placeholder="[TODESDATUM_01]", category="todesdatum", original_value="01.02.2024"),
        PseudonymMapping(placeholder="[TODESDATUM_02]", category="todesdatum", original_value="01.10.2026"),
    ]
    note = build_death_note(mappings, today=date(2026, 10, 13))
    assert "[TODESDATUM_01]: vor 2 vollen Jahren" in note
    assert "[TODESDATUM_02]: vor weniger als einem Jahr" in note
    assert "2024" not in note and "Tage" not in note


@pytest.mark.parametrize(
    "text, expected",
    [
        ("verstorben, Beerdigung am 10.02.2024", []),
        ("Unfall mit Todesfolge am 12.03.2025", []),
        ("verstorben am 01.02.2024, Beerdigung am 10.02.2024", ["01.02.2024"]),
        ("Die Frist endet am 14.11.2026.", []),
    ],
)
def test_death_context_rules(text, expected) -> None:
    assert [s.value for s in detect_todesdatum(text, current_year=2026)] == expected


def test_deadline_extractor_ignores_birth_and_death_dates_but_keeps_real_deadlines() -> None:
    text = (
        "Kläger: Karl Brenner, geb. 12.03.1980. Der Erblasser verstarb am 01.02.2024. Mietvertrag vom 01.04.2019, "
        "Kündigung am 30.09.2026; die Räumungsfrist endet am 31.12.2026. Ausschlagung bis zum 15.03.2024."
    )
    found = {d.raw_date_text: d.confidence for d in PlaceholderDeadlineExtractor().extract(text)}
    assert "12.03.1980" not in found and "01.02.2024" not in found
    assert found["31.12.2026"] == 0.5 and found["15.03.2024"] == 0.5  # echte Fristen mit Schluesselwort
    assert "01.04.2019" in found and "30.09.2026" in found  # Vertrags-/Ereignisdaten weiterhin als Vorschlag


def test_real_deadline_is_never_lost_to_a_birth_or_death_misreading() -> None:
    """Review §24: "Die Erben des Verstorbenen haben Frist bis 15.04.2024" lieferte vorher keinen Fristvorschlag."""
    for text in (
        "Die Erben des Verstorbenen haben Frist bis 15.04.2024 für die Annahme.",
        "Nach dem Erbfall wurde Frist bis zum 15.04.2024 gesetzt.",
        "Die Ausschlagungsfrist endet spätestens am 15.04.2024; der Erblasser verstarb am 01.02.2024.",
    ):
        found = [d.raw_date_text for d in PlaceholderDeadlineExtractor().extract(text)]
        assert "15.04.2024" in found, text
    assert not is_personal_date("Termin am 15.04.2024; Erblasser verstorben am 15.04.2024", date(2024, 4, 15))


def test_stored_table_row_suggestion_is_filtered_with_the_document_text() -> None:
    """Review §24: gespeicherter Ausschnitt ohne Tabellenkopf - erkannt ueber den Volltext des Dokuments."""
    snippet = "01.02.2024 :: Max Muster  |  12.03.1940  |  01.02.2024"
    document = "Name | Geburtsdatum | Sterbedatum\nMax Muster | 12.03.1940 | 01.02.2024"
    assert not is_personal_date(snippet, date(2024, 2, 1))
    assert is_personal_date(snippet, date(2024, 2, 1), document)
    assert is_personal_date(snippet, date(1940, 3, 12), document)


def test_deadline_snippet_never_cuts_a_date_in_half() -> None:
    """Installierter Build 5cafe0f, Fall 19: der Ausschnitt begann bei "1940," - das Geburtsjahr ging als Bruchstueck
    hinaus, das keine Datumserkennung mehr traf. Jetzt endet der Ausschnitt nur an Leerraum."""
    doc = (
        "Erblasser: Max Brenner, geb. 12.03.1940, verstorben am 01.02.2026 in Musterstadt.\n"
        "Testament vom 15.06.2019; Eröffnung durch das Nachlassgericht am 20.02.2026."
    )
    for deadline in PlaceholderDeadlineExtractor().extract(doc):
        first, last = deadline.source_text.split()[0], deadline.source_text.split()[-1]
        assert first in doc.split() or first.rstrip(",.;") in doc, first
        assert not re.fullmatch(r"\d{2,4}[,.;]?", first) or first in ("12.03.1940,",), first
        assert last in doc.replace("\n", " ").split() or last.rstrip(",.;") in doc, last


def test_stored_snippet_fragments_of_birth_or_death_dates_are_trimmed(gateway) -> None:
    from app.deadlines.extractor import trim_partial_personal_dates

    old = "1940, verstorben am 01.02.2026 in Musterstadt. Testament vom 15.06.2019"
    trimmed = trim_partial_personal_dates(old, None)
    assert not trimmed.startswith("1940") and "15.06.2019" in trimmed
    assert trim_partial_personal_dates("Die Frist endet am 15.03.2026 laut Schreiben vom 12", None).endswith("vom")
    # vollstaendiges Geburtsdatum im Argument wird ueber den Wert konsistent maskiert
    sv = "Akte: Beispielakte\n[Schreiben] Erblasser: Max Brenner, geb. 12.03.1940, verstorben am 01.02.2026.\nTestament vom 15.06.2019."
    result = gateway.prepare_request(
        purpose="chat_response", sachverhalt=sv, anwaltliche_anmerkungen="Fasse zusammen.", review_mode="mask",
        argumentationspunkte=["Mögliche Frist (unreviewed): 15.06.2019 :: 12.03.1940, verstorben am 01.02.2026.  Testament vom 15.06.2019"],
    )
    everything = json.dumps(result.payload.model_dump(), ensure_ascii=False)
    assert "1940" not in everything and "01.02.2026" not in everything and "15.06.2019" in everything


def test_stored_birthdate_suggestions_are_recognised_for_filtering() -> None:
    assert is_personal_date("Kläger: Karl Brenner, geb. 12.03.1980, wohnhaft in Musterstadt.", date(1980, 3, 12))
    assert is_personal_date("Der Erblasser verstarb am 01.02.2024 in Köln.", date(2024, 2, 1))
    assert not is_personal_date("Die Räumungsfrist endet am 31.12.2026.", date(2026, 12, 31))
    assert not is_personal_date(None, date(2026, 12, 31))

