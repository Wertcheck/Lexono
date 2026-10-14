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




_CONTRACT = (
    "Zwischen der Beispiel Logistik GmbH, Hafenstrasse 1, 54321 Beispielhafen, und Herrn Max Beispiel, geb. 14.07.1988, "
    "wird vereinbart:\nBeginn des Arbeitsverhaeltnisses: 15.01.2019. Taetigkeit: Disponent."
)


def _payload_text(gateway, argument: str | None = None, sachverhalt: str = "Abmahnung vom 12.02.2026.") -> str:
    result = gateway.prepare_request(
        purpose="chat_response", sachverhalt="Akte: Beispielakte\n[Schreiben] " + sachverhalt,
        anwaltliche_anmerkungen="Fasse zusammen.", review_mode="mask",
        argumentationspunkte=[argument] if argument else [],
    )
    return json.dumps(result.payload.model_dump(), ensure_ascii=False)


def test_unlabelled_full_birthdate_in_deadline_snippet_is_removed(gateway) -> None:
    """Legal-E2E-Benchmark §34 (LE-D002, Chat mit nur der Abmahnung als Anhang): der Ausschnitt zur Frist "Beginn
    15.01.2019" begann mit dem vollstaendigen Geburtsdatum, "geb." lag ausserhalb - der Arbeitsvertrag stand nicht im
    Sachverhalt, das Datum ging im Klartext in den Payload. Jetzt entfernt, wenn das Quelldokument es als Geburtsdatum
    ausweist."""
    from app.deadlines.extractor import trim_partial_personal_dates

    start = next(d for d in PlaceholderDeadlineExtractor().extract(_CONTRACT) if d.due_date == date(2019, 1, 15))
    stored = "14.07.1988, wird vereinbart: Beginn des Arbeitsverhaeltnisses: 15.01.2019. Taetigkeit: Disponent."
    for snippet in (start.source_text, stored):
        trimmed = trim_partial_personal_dates(snippet, _CONTRACT)
        assert "14.07.1988" not in trimmed and "15.01.2019" in trimmed
        assert "1988" not in _payload_text(gateway, f"Mögliche Frist (unreviewed): {trimmed}")
    # ohne Quelldokument bleibt ein unbezeichnetes Datum unveraendert (keine Annahme ohne Beleg)
    assert "14.07.1988" in trim_partial_personal_dates(stored, None)
    # Review §34: auch ein Altvorschlag mit Fristwort, dessen Datum das Geburtsdatum ist, gibt es nicht preis
    assert "1988" not in trim_partial_personal_dates("14.07.1988, Frist zur Stellungnahme läuft.", _CONTRACT)


def test_personal_date_removal_respects_digit_boundaries_and_edge_fragments() -> None:
    from app.deadlines.extractor import remove_unlabelled_personal_dates

    source = "Erblasser Max Beispiel, geb. 1.2.1988, und Anna Beispiel, geboren am 14. Juli 1990."
    assert remove_unlabelled_personal_dates("Vorgang Nr. 11.2.19880 und 1.2.1988 offen", source) == "Vorgang Nr. 11.2.19880 und (Datum entfernt) offen"
    assert remove_unlabelled_personal_dates("Juli 1990, Beginn am 01.03.2020", source) == "Beginn am 01.03.2020"
    assert remove_unlabelled_personal_dates("Beginn am 01.03.2020, Anna am 14. Juli", source) == "Beginn am 01.03.2020, Anna am"
    # bezeichnete Daten bleiben fuer die Maskierung im Gateway stehen
    labelled = "Max Beispiel, geb. 1.2.1988, Beginn 01.03.2020"
    assert remove_unlabelled_personal_dates(labelled, source) == labelled


def test_long_document_key_passages_never_separate_birth_label_from_date(gateway) -> None:
    """Unabhaengiges Review §34 (CONFIRMED): bei langen Dokumenten trennte die Auswahl der Schluesselstellen "geb." vom
    Datum ("geb. […] 14.07.1988") - Namen maskiert, Geburtsdatum im Klartext."""
    from app.ai_providers.local_ai_provider import _document_excerpt

    filler = " ".join(f"Klausel {i}: Die Parteien vereinbaren die Regelung Nummer {i} zur Durchfuehrung." for i in range(1200))
    text = filler + " Arbeitnehmer ist Herr Max Beispiel, geb.\n14.07.1988, wohnhaft in Beispielhafen; Frist bis 30.06.2026. " + filler
    excerpt = _document_excerpt(text)
    assert excerpt.omitted_chars > 0
    assert "14.07.1988" not in excerpt.text or "geb. 14.07.1988" in " ".join(excerpt.text.split())
    assert "1988" not in _payload_text(gateway, sachverhalt=excerpt.text)


@pytest.mark.parametrize(
    "written",
    ["geb. 14. Juli 1988", "geb. 14. 07. 1988", "geb. 14.07.1988", "geboren am 14.7.88"],
)
def test_long_document_excerpt_never_splits_a_spaced_birthdate(gateway, written: str) -> None:
    """Re-Review §34 (CONFIRMED): die Segmentierung teilte "geb. 14. Juli 1988" an den Punkten ("14. […] Juli 1988"),
    Tag, Monat und Jahr gingen einzeln im Klartext hinaus."""
    from app.ai_providers.local_ai_provider import _document_excerpt

    filler = " ".join(f"Klausel {i}: Die Parteien vereinbaren die Regelung Nummer {i} zur Durchfuehrung." for i in range(1200))
    text = filler + f" Arbeitnehmer ist Herr Max Beispiel, {written}, wohnhaft in Beispielhafen; Kuendigung zum 30.06.2026. " + filler
    excerpt = _document_excerpt(text)
    assert excerpt.omitted_chars > 0 and "\u27e6" not in excerpt.text
    payload = _payload_text(gateway, sachverhalt=excerpt.text)
    assert "1988" not in payload and "Juli" not in payload and "14.7.88" not in payload


def test_other_spellings_of_a_personal_date_are_removed_but_labelled_ones_stay() -> None:
    from app.deadlines.extractor import remove_unlabelled_personal_dates

    source = "Max Beispiel, geb. 14.07.1988, Disponent."
    for other in ("14.7.1988", "14.07.88", "14. 07. 1988", "14. Juli 1988", "14. Jul. 1988"):
        assert remove_unlabelled_personal_dates(f"Am {other} unterschrieben.", source) == "Am (Datum entfernt) unterschrieben.", other
    assert remove_unlabelled_personal_dates("Herr Beispiel, geb. 14. Juli 1988, ist Disponent.", source) == (
        "Herr Beispiel, geb. 14. Juli 1988, ist Disponent."
    )


def test_edge_fragment_stripping_keeps_ordinary_words_and_strips_once() -> None:
    from app.deadlines.extractor import remove_unlabelled_personal_dates

    source = "Anna Beispiel, geboren am 14. Juli 1990, Ziffer 12.03. gilt."
    assert remove_unlabelled_personal_dates("1990er Jahrgang", source) == "1990er Jahrgang"
    assert remove_unlabelled_personal_dates("Juli 1990, 1990 Mitarbeiter", source) == "1990 Mitarbeiter"


def test_personal_date_detection_of_a_source_text_is_cached() -> None:
    import time

    from app.deadlines.extractor import trim_partial_personal_dates

    text = ("Herr Max Beispiel, geb. 14.07.1988, Disponent. " + "Die Parteien vereinbaren die Regelung zur Durchfuehrung. " * 3000)
    trim_partial_personal_dates("Beginn 15.01.2019", text)
    started = time.perf_counter()
    for _ in range(30):
        trim_partial_personal_dates("Beginn 15.01.2019, Disponent", text)
    assert time.perf_counter() - started < 1.0


def test_labelled_birthdate_in_long_document_middle_is_masked_not_dropped(gateway) -> None:
    """Review §34: die Segmentierung trennte "geb." vom Datum; das Datum wurde dann sicher entfernt, aber auch nicht
    mehr maskiert uebergeben. Jetzt bleibt die Bezeichnung beim Datum und der Gateway maskiert es."""
    from app.ai_providers.local_ai_provider import _document_excerpt

    filler = " ".join(f"Klausel {i}: Die Parteien vereinbaren die Regelung Nummer {i} zur Durchfuehrung." for i in range(1200))
    text = filler + " Die Klaegerin Anna Beispiel, geb. 14.07.1988, verlangt Zahlung von 4.250,00 EUR bis 30.06.2026. " + filler
    excerpt = _document_excerpt(text).text
    assert "geb. 14.07.1988" in excerpt
    payload = _payload_text(gateway, sachverhalt=excerpt)
    assert "1988" not in payload and "GEBURTSDATUM" in payload


def test_birthdates_from_a_table_column_never_reach_the_payload(gateway) -> None:
    """Legal-E2E-Benchmark §34.14 (LE-D004): Erbentabelle "Erbe | Geburtsdatum | Erbquote" - die Normalisierung des
    Dokumentauszugs (Zeilen -> Doppel-Leerzeichen) machte die Tabellenspalte unkenntlich; alle drei Geburtsdaten gingen
    im Klartext hinaus. Massgeblich ist jetzt der rohe Text."""
    from app.ai_providers.local_ai_provider import _document_excerpt

    raw = ("Sie wird beerbt von ihren Kindern zu je einem Drittel:\nErbe | Geburtsdatum | Erbquote\n"
           "Anna Beispiel | 03.04.1962 | 1/3\nBernd Beispiel | 17.09.1965 | 1/3\nCarla Beispiel | 28.11.1970 | 1/3")
    for text in (raw, raw + " " + "Weitere Regelung zur Durchfuehrung des Nachlasses. " * 900):
        excerpt = _document_excerpt(text).text
        payload = _payload_text(gateway, sachverhalt=excerpt)
        assert not any(year in payload for year in ("1962", "1965", "1970")), text[:40]
        assert "1/3" in payload


def test_label_and_date_on_separate_lines_in_long_document(gateway) -> None:
    """Review §34.14: der rohe Text erkennt "geboren am" / "14.07.1988" auf getrennten Zeilen nicht, der normalisierte
    schon - beide Quellen zaehlen. Entfernte Daten sind sichtbar markiert."""
    from app.ai_providers.local_ai_provider import _document_excerpt

    filler = " ".join(f"Klausel {i}: Die Parteien vereinbaren die Regelung Nummer {i} zur Durchfuehrung." for i in range(1200))
    text = filler + " Arbeitnehmer Max Beispiel, geboren am\n14.07.1988, wohnhaft in Beispielhafen. Frist bis 30.06.2026. " + filler
    excerpt = _document_excerpt(text).text
    assert "1988" not in _payload_text(gateway, sachverhalt=excerpt)
