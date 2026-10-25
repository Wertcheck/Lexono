"""Fachbegriffe im Sachkontext (24.10.2026): Tabellenbeschriftungen, Partizipien und allgemeine Bezugnahmen aus
Finanzamt-/Buchhaltungsdokumenten duerfen nicht als Person/Organisation/Ort maskiert werden - echte Namen, Firmen, Orte
und Kennnummern bleiben geschuetzt, auch in denselben Strukturen (Lohnliste, Feldwert, nach Artikel/Anrede).

Befund: die KI schrieb diese Alltagswoerter in ihrer Antwort; die (unveraenderte) Leck-Pruefung blockierte deshalb jede
Antwort. Texte im Format, das der Gateway real verarbeitet (eine Zeile je Dokument, Zellen durch Doppel-Leerzeichen
getrennt). Ausschliesslich synthetische Daten, lokal, kein Cloud-Aufruf."""

from __future__ import annotations

import json

import pytest

from app.privacy import presidio_ner as pn
from app.privacy.gateway import ClaudePrivacyGateway


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _payload(gateway, text: str) -> tuple[str, list[str]]:
    result = gateway.prepare_request(
        purpose="chat_response", sachverhalt="Akte: Beispielakte\n[Steuerbescheid] " + text,
        anwaltliche_anmerkungen="Fasse zusammen.", review_mode="mask",
    )
    assert result.allowed, result.reasons
    return json.dumps(result.payload.model_dump(), ensure_ascii=False), [m.original_value for m in result.mappings]


# --- positiv: Fachbegriffe bleiben lesbar ---------------------------------------------------------------------------

TERMS = [
    ("Wareneinsatz  158.900,00 EUR  Rohergebnis  212.900,00 EUR  Personalaufwand  61.000,00 EUR", "Rohergebnis"),
    ("Gewerbeertrag  71.800,00 EUR  Steuermessbetrag  2.513,00 EUR  Hebesatz  410 %", "Steuermessbetrag"),
    ("Empfohlene Aktenaktion  Zahlungsstatus prüfen – Einspruchsfristen prüfen.", "Zahlungsstatus"),
    ("Gewerbesteuerbescheid 2024  Bezirksfinanzamt / Gewerbesteuerstelle Berlin – Musterfall  Festsetzung",
     "Bezirksfinanzamt"),
    ("Hebesatz  410 %  Festgesetzte Gewerbesteuer  10.303,30 EUR", "Festgesetzte"),
    ("Position  Betrag  Festgesetztes zu versteuerndes Einkommen  72.400,00 EUR", "Festgesetztes"),
    ("Bitte reichen Sie bis zum 15.11.2026 ein.  Angeforderte Unterlagen  Nr.  Unterlage", "Angeforderte"),
    ("Registervermerk  Die Gesellschaft ist seit dem 15.02.2024 eingetragen.", "Die Gesellschaft"),
    ("Der Kaufpreis wird nach Fälligkeitsmitteilung des Notars auf das Anderkonto überwiesen.", "Anderkonto"),
]


@pytest.mark.parametrize("text, term", TERMS)
def test_technical_term_in_context_stays_readable(gateway, text: str, term: str) -> None:
    payload, originals = _payload(gateway, text)
    assert term in payload, f"{term!r} wurde maskiert"
    assert term not in originals


def test_word_alone_is_no_protected_value_even_inside_an_institution_name(gateway) -> None:
    """Fuer die Leck-Pruefung entscheidend: das Fachwort ALLEIN ist kein geschuetzter Originalwert. Ein mehrteiliger
    Behoerden-/Ortstreffer ("Gewerbesteuerstelle Berlin") darf bestehen bleiben - er enthaelt den Ort."""
    _, originals = _payload(gateway, "Gewerbesteuerbescheid 2024  Bezirksfinanzamt / Gewerbesteuerstelle Berlin – Musterfall")
    assert "Gewerbesteuerstelle" not in originals and "Bezirksfinanzamt" not in originals
    assert any("Berlin" in value for value in originals)  # der Ort bleibt geschuetzt


# --- negativ: echte personenbezogene Daten bleiben geschuetzt -------------------------------------------------------

PROTECTED = [
    ("Geschäftsführer  Thomas Berger, Berlin  Unternehmensgegenstand  Handel", "Thomas Berger"),
    ("Geschäftsführer  Thomas Berger, Berlin  Unternehmensgegenstand  Handel", "Berlin"),
    # Nachnamen in derselben Struktur wie "Rohergebnis" (Beschriftung vor Betrag): Lohnliste
    ("Lohnliste September  Hochleiter  2.300,00 EUR  Brandhuber  1.900,00 EUR", "Hochleiter"),
    # frueher (auch ohne Kontextregel) im Klartext: Name als Tabellenbeschriftung - jetzt ueber die Namensspalte
    ("Lohnliste September  Hochleiter  2.300,00 EUR  Brandhuber  1.900,00 EUR", "Brandhuber"),
    # Kopfnomen der Klasse, aber Vorderteil zu kurz: realer Nachname
    ("Mitarbeiterliste  Herr Siewert  1.200,00 EUR", "Siewert"),
    # Partizip-Form, aber Nachname
    ("Die Zeugin Gerste wurde geladen und sagte aus.", "Gerste"),
    # Name nach Artikel bzw. Anrede
    ("Die Bergmann hat den Vertrag am 03.02.2026 unterschrieben.", "Bergmann"),
    ("Rückfrage an Herrn Rohergebnis zur Abrechnung.", "Rohergebnis"),  # synthetischer Name: Anrede schuetzt immer
    ("Gewerbetreibender  Muster & Beispiel Handelsservice GmbH  Rechtsform  GmbH", "Muster & Beispiel Handelsservice GmbH"),
    ("Finanzamt Berlin Mitte · Steuernummer 12/345/67890  Steuerpflichtiger  Muster & Beispiel Handelsservice GmbH", "12/345/67890"),
]


@pytest.mark.parametrize("text, value", PROTECTED)
def test_real_personal_data_in_the_same_structures_stays_protected(gateway, text: str, value: str) -> None:
    payload, _ = _payload(gateway, text)
    assert value not in payload, f"{value!r} ging im Klartext hinaus"


# --- Kontext entscheidet, nicht das Wort allein ----------------------------------------------------------------------


def test_same_word_without_technical_context_is_not_released() -> None:
    """Dasselbe Wort in Namensrolle (Subjekt eines Handlungsverbs, kein Zahlenfeld): KEIN Fachbegriff-Freigabegrund."""
    text = "Rohergebnis hat gestern angerufen und um Rückruf gebeten."
    assert not pn.is_contextual_technical_term(text, 0, len("Rohergebnis"))


def test_field_value_after_a_colon_is_never_released() -> None:
    text = "Ansprechpartner: Zahlungsstatus  Telefon  030 1234567"
    start = text.index("Zahlungsstatus")
    assert not pn.is_contextual_technical_term(text, start, start + len("Zahlungsstatus"))


def test_compound_rule_requires_a_head_from_the_closed_class() -> None:
    assert pn.is_context_term_compound("Rohergebnis") and pn.is_context_term_compound("Gewerbesteuerstelle")
    for name in ("Hochleiter", "Brandhuber", "Siewert", "Waldbauer", "Sonnleiter", "Bergmann"):
        assert not pn.is_context_term_compound(name), name


# --- Tabellen: Spalten teilen ihren Typ ------------------------------------------------------------------------------

NAME_COLUMNS = [
    # Personen-Ueberschrift
    ("Name  Betrag  Brandhuber  1.900,00 EUR  Kostka  2.100,00 EUR  Wiebe  1.750,00 EUR", "Brandhuber"),
    ("Mitarbeiter | Brutto\nBrandhuber | 1.900,00 EUR\nSonnleiter | 2.050,00 EUR", "Brandhuber"),
    ("Gesellschafter  Anteil  Brandhuber  50 %  Sonnleiter  50 %", "Brandhuber"),
    ("Name  Betrag  Anna Brandhuber  1.900,00 EUR  Paul Kostka  2.100,00 EUR", "Brandhuber"),
    # keine Ueberschrift, aber erkannte Personen in den Nachbarzeilen
    ("Auszahlungen  Kostka  2.100,00 EUR  Brandhuber  1.900,00 EUR  Wiebe  1.750,00 EUR", "Brandhuber"),
    # erfundenes Fach-Kompositum in einer Namensspalte: wird NICHT als Fachbegriff freigegeben
    ("Auszahlungen  Kostka  2.100,00 EUR  Waldbetrag  1.900,00 EUR", "Waldbetrag"),
    ("Mitarbeiter  Betrag  Waldbetrag  1.900,00 EUR  Steinbetrag  1.200,00 EUR", "Waldbetrag"),
    # einzelne Beschriftung ohne Fachspalten-Nachweis: kein Freigabegrund
    ("Waldbetrag  500,00 EUR", "Waldbetrag"),
]


@pytest.mark.parametrize("text, value", NAME_COLUMNS)
def test_labels_in_a_name_column_stay_protected(gateway, text: str, value: str) -> None:
    payload, _ = _payload(gateway, text)
    assert value not in payload, f"{value!r} ging im Klartext hinaus"


def test_technical_rows_of_a_name_table_stay_readable(gateway) -> None:
    """Summenzeile in einer Lohnliste: bekannte Fachwoerter ohne Namenslesart werden NICHT zusaetzlich maskiert."""
    payload, _ = _payload(gateway, "Name  Betrag  Brandhuber  1.900,00 EUR  Kostka  2.100,00 EUR  Summe  4.000,00 EUR")
    assert "Summe" in payload and "Brandhuber" not in payload and "Kostka" not in payload


def test_name_column_labels_are_only_added_never_released() -> None:
    """Die Namensspalten-Erkennung fuegt nur Treffer hinzu: vorhandene Treffer bleiben unveraendert."""
    text = "[Lohnliste] Name  Betrag  Brandhuber  1.900,00 EUR  Kostka  2.100,00 EUR"
    existing = [s for s in pn.detect_presidio_entities(text) if s.value == "Kostka"]
    added = pn.detect_name_column_labels(text, existing)
    assert {s.value for s in added} == {"Brandhuber"}
    assert all(s.category == "person" for s in added)


def test_masked_name_in_a_sentence_before_a_technical_table_does_not_block(gateway) -> None:
    """ECHTER FUND (installierter Build 24.10.2026): ein Satz mit erkanntem Namen direkt vor einer Fachtabelle wurde im
    Restrisiko-Scan zu "... von [PERSON_02] vom ..." - das Platzhalterwort "PERSON" machte die Tabelle zur Namensspalte,
    die Fachbegriffe galten als Namen und die GANZE Anfrage wurde blockiert (fail-closed, aber unnoetig)."""
    text = ("Wir bestaetigen den Eingang des Schreibens von Thomas Berger vom 16.03.2024.  Umsatzerloese  371.800,00 EUR  "
            "Wareneinsatz  158.900,00 EUR  Rohergebnis  212.900,00 EUR  Steuermessbetrag  2.513,00 EUR")
    payload, _ = _payload(gateway, text)  # _payload verlangt result.allowed (inkl. Restrisiko-Scan)
    assert "Rohergebnis" in payload and "Steuermessbetrag" in payload
    assert "Thomas Berger" not in payload


def test_placeholders_and_sentences_are_never_column_headers() -> None:
    text = "Es schrieb [PERSON_01] am 03.02.2026.  Rohergebnis  212.900,00 EUR  Wareneinsatz  158.900,00 EUR"
    assert [person_header for _, person_header in pn._label_value_tables(text)] == [False]
    assert [h for _, h in pn._label_value_tables("Name  Betrag  Brandhuber  1.900,00 EUR  Kostka  2.100,00 EUR")] == [True]


def test_document_type_marker_is_not_part_of_a_label() -> None:
    text = "[Lohnliste] Brandhuber  1.900,00 EUR  Kostka  2.100,00 EUR"
    labels = [text[a:b] for table, _ in pn._label_value_tables(text) for a, b in table]
    assert labels == ["Brandhuber", "Kostka"]


# --- Abkuerzungen und Kopfzeilen -------------------------------------------------------------------------------------


def test_abbreviation_defined_in_the_same_document_stays_readable(gateway) -> None:
    payload, originals = _payload(
        gateway, "Einnahmenüberschussrechnung 2025  Betriebseinnahmen  143.400,00 EUR  Hinweis  Die EÜR ist vollständig."
    )
    assert "EÜR" in payload and "EÜR" not in originals


def test_abbreviation_without_long_form_stays_protected(gateway) -> None:
    payload, _ = _payload(gateway, "Hinweis  Die EÜR ist vollständig synthetisch.")
    assert "EÜR" not in payload


def test_company_long_form_defines_no_releasable_abbreviation() -> None:
    assert pn._compound_initialisms("Nordbauholding") == frozenset()
    assert "EÜR" in pn._compound_initialisms("Einnahmenüberschussrechnung")


def test_uppercase_header_words_stay_masked_as_a_documented_limit(gateway) -> None:
    """Keine belastbare Regel (gemessen): "ECHT" hat Namensprobe 1 wie "Fischer"/"Lange", "Bauer"/"Leiter" sogar 0 - eine
    Freigabe "Versalwort = bekanntes Wort" gaebe "RA BAUER" frei. Bleibt maskiert (fail-closed)."""
    payload, _ = _payload(gateway, "LEXONO TESTFALL · SYNTHETISCHES MUSTERDOKUMENT  Seite 1  NICHT ECHT  Mahnung")
    assert "LEXONO" not in payload


def test_generic_article_noun_requires_a_non_name_noun() -> None:
    text = "Die Gesellschaft ist eingetragen. Die Bergmann ist eingetragen."
    assert pn.is_generic_article_noun(text, 0, len("Die Gesellschaft"))
    start = text.index("Die Bergmann")
    assert not pn.is_generic_article_noun(text, start, start + len("Die Bergmann"))
