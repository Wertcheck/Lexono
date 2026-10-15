"""P2.2 (§41): reproduzierbare Fehler aus dem Entwicklungs-Split `benchmarks/classification_deadlines_v1/cases/dev.json`
(und aus dem frueheren Legal-E2E-Testsplit, dort zuerst als Entwicklungsfaelle nachgebaut). Synthetische Kurzfaelle."""

from datetime import date

import pytest

from app.classification.classifier import PlaceholderDocumentClassifier
from app.deadlines.extractor import PlaceholderDeadlineExtractor

classifier = PlaceholderDocumentClassifier()
extractor = PlaceholderDeadlineExtractor()


def _kinds(text: str) -> dict[str, set]:
    out: dict[str, set] = {"Frist": set(), "Termin": set(), "Fristbeginn": set(), "Fristdauer": set()}
    for r in extractor.extract(text):
        kind = next((k for k in ("Termin", "Fristbeginn", "Fristdauer") if f"Art: {k}" in r.reasoning), "Frist")
        out[kind].add(r.due_date or r.raw_date_text)
    return out


# --- Fristdauern: Standard-Rechtsbehelfsbelehrung (Regression aus P2.1) -----------------------------------------------

@pytest.mark.parametrize("text", [
    "Die Berufung ist binnen eines Monats nach Zustellung einzulegen.",
    "Gegen diesen Bescheid kann innerhalb eines Monats nach Bekanntgabe Widerspruch erhoben werden.",
    "Zahlbar innerhalb von 14 Tagen ohne Abzug.",
    "Wir setzen Ihnen hiermit eine Frist von zwei Wochen ab Zugang dieses Schreibens.",
])
def test_standard_durations_are_recognised_without_computing_an_end(text: str) -> None:
    results = extractor.extract(text)
    assert len(results) == 1, [r.raw_date_text for r in results]
    assert results[0].due_date is None and "Art: Fristdauer" in results[0].reasoning


# --- Fristen: ungewoehnliche Formulierungen, Gueltigkeitsdaten -------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Bitte senden Sie uns die Unterlagen bis Freitag, 05.06.2026.", date(2026, 6, 5)),
    ("Die Einspruchsfrist läuft am 20.07.2026 ab.", date(2026, 7, 20)),
    ("Die Frist zur Stellungnahme endet am 12.06.2026.", date(2026, 6, 12)),
])
def test_unusual_explicit_deadline_wordings(text: str, expected: date) -> None:
    assert _kinds(text)["Frist"] == {expected}


@pytest.mark.parametrize("text", [
    "Messgerät: Typ Beispielmess 3000, Eichschein gültig bis 31.12.2026.",
    "Hauptuntersuchung (TÜV) gültig bis 30.09.2027.",
    "Personalausweis gueltig bis 14.03.2031.",
    "Die Frist begann am 02.05.2026 mit Zugang des Schreibens.",
])
def test_validity_dates_and_deadline_starts_are_not_deadline_ends(text: str) -> None:
    assert _kinds(text)["Frist"] == set()


def test_deadline_table_uses_the_column_header() -> None:
    text = "Fristenübersicht\nVorgang | Frist\nStellungnahme Gutachten | 12.06.2026\nBerufungsbegründung | 26.06.2026"
    assert _kinds(text)["Frist"] == {date(2026, 6, 12), date(2026, 6, 26)}


def test_table_without_deadline_header_creates_no_deadline() -> None:
    text = "Positionen\nDatum | Leistung | Betrag\n02.06.2026 | Wände streichen | 1.120,00 EUR"
    assert _kinds(text)["Frist"] == set()


# --- Termine: Verweis auf vergangene Sitzung ist kein Termin; lange Terminformel --------------------------------------

@pytest.mark.parametrize("text", [
    "hat das Amtsgericht auf die mündliche Verhandlung vom 03.03.2026 für Recht erkannt:",
    "Protokoll der öffentlichen Sitzung vom 07.05.2026",
])
def test_reference_to_a_past_hearing_is_not_an_appointment(text: str) -> None:
    assert _kinds(text)["Termin"] == set()


def test_long_appointment_formula_is_an_appointment() -> None:
    text = "Termin zur Verkündung einer Entscheidung wird anberaumt auf den 18.06.2026."
    assert _kinds(text)["Termin"] == {date(2026, 6, 18)}


# --- Klassifikation --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Amtsgericht Beispielstadt\nIm Namen des Volkes\nEndurteil\nDie Beklagte wird verurteilt.\nRechtsbehelfsbelehrung: "
     "Gegen dieses Urteil ist die Berufung statthaft.", "Urteil/Beschluss"),
    ("An das Landgericht Musterstadt\nKlage\nwegen Werklohn. Die Beklagte hat die Schlussrechnung vom 12.12.2025 nicht "
     "beglichen.", "Klage/Schriftsatz"),
    ("Beispiel Energie GmbH\nMahnung\nZu unserer Rechnung vom 03.04.2026 fehlt noch der Zahlungseingang.", "Mahnung"),
    ("Unfallmitteilung (Polizeiinspektion Beispielstadt)\nUnfallzeit: 14.04.2026\nHergang: Vorfahrt missachtet.",
     "Protokoll"),
    ("Rechtsanwalt Max Beispiel\nAn die Bußgeldstelle\nGegen den Bußgeldbescheid vom 30.06.2026 legen wir Einspruch\nein.",
     "Einspruch"),
])
def test_dev_documents_get_their_type(text: str, expected: str) -> None:
    assert classifier.classify(text).document_type == expected


@pytest.mark.parametrize("text, wrong", [
    ("Holzbau Muster GmbH\nBeispielstadt, 05.05.2026\nAus unserer Schlussrechnung ist noch ein Betrag offen.", "Rechnung"),
    ("Rechtsanwältin Beispiel\nIn der Mietsache fordern wir den Rückstand.\nSollte der Rückstand nicht ausgeglichen "
     "werden, kommt eine fristlose Kündigung in Betracht; die Kündigung behält sich die Mandantin vor.",
     "Kündigungsschreiben"),
    ("Beispiel Versicherung AG\nAbweichend davon bitten wir um Eingang der Rechnungskopien spätestens am 18.05.2026.",
     "Rechnung"),
    ("Fristenübersicht Akte Muster\nVorgang | Frist\nStellungnahme Gutachten | 12.06.2026", "Gutachten"),
])
def test_mentions_and_threats_do_not_force_a_type(text: str, wrong: str) -> None:
    assert classifier.classify(text).document_type != wrong


def test_real_invoice_and_real_termination_are_still_recognised() -> None:
    assert classifier.classify("Malerbetrieb Muster\nRechnung Nr. R-2026-0612\nGesamtbetrag 1.600,00 EUR").document_type == "Rechnung"
    assert classifier.classify("Sehr geehrte Damen und Herren, anbei unsere Rechnung Nr. 123.").document_type == "Rechnung"
    assert classifier.classify("Kündigung des Mietvertrags\nHiermit kündige ich ordentlich zum 31.07.2026.").document_type \
        == "Kündigungsschreiben"


def test_threat_with_paragraph_abbreviations_is_not_a_termination() -> None:
    """Legal-E2E dev LE-D001/D5: die Androhung mit "§ 543 Abs. 2 Satz 1 Nr. 3 BGB" - die Abkuerzungspunkte trennten den
    Satz, "fristlose Kündigung" blieb ausserhalb der Androhung und machte das Schreiben zum Kuendigungsschreiben."""
    text = ("Rechtsanwalt Beispiel\nNamens unserer Mandantin fordern wir Sie auf, den Rückstand zu zahlen.\n"
            "Bei weiterem Rückstand kommt eine fristlose Kündigung nach § 543 Abs. 2 Satz 1 Nr. 3 BGB in Betracht.\n"
            "Wir erwarten Ihre Stellungnahme bis zum 15.04.2026.")
    assert classifier.classify(text).document_type != "Kündigungsschreiben"


def test_real_termination_stays_a_termination_next_to_a_reservation_sentence() -> None:
    """Gegenprobe: ein Vorbehalt in einem ANDEREN Satz darf die echte Kuendigung nicht verschlucken."""
    text = ("Muster Logistik GmbH\nKündigung des Arbeitsverhältnisses\nHiermit kündigen wir das Arbeitsverhältnis "
            "ordentlich zum 30.06.2026. Weitere Schritte behalten wir uns vor.")
    assert classifier.classify(text).document_type == "Kündigungsschreiben"


@pytest.mark.parametrize("text", [
    "Messprotokoll Geschwindigkeitsmessung\nHinweis: Gegen den Bescheid kann binnen eines Monats nach Zustellung "
    "Einspruch eingelegt werden.",
    "Messprotokoll\nSie können innerhalb von zwei Wochen Einspruch einlegen.",
    "Protokoll der Sitzung\nDie Frist, Einspruch einzulegen, beträgt einen Monat.",
])
def test_appeal_instructions_are_not_an_objection(text: str) -> None:
    """P2.2 (§41, beim Vorbereiten der Pruefung im installierten Build gefunden): die Rechtsbehelfsbelehrung beschreibt die
    MOEGLICHKEIT eines Einspruchs - "kann ... Einspruch eingelegt werden" machte ein Protokoll zum "Einspruch"."""
    assert classifier.classify(text).document_type == "Protokoll"


@pytest.mark.parametrize("text", [
    "Hiermit wird Einspruch gegen den Bescheid vom 01.03. eingelegt.",
    "Namens unseres Mandanten legen wir Einspruch ein.",
    "Der Einspruch wurde fristgerecht eingelegt. Das Einspruchsverfahren ruht.",
])
def test_active_objection_is_still_an_objection(text: str) -> None:
    assert classifier.classify(text).document_type == "Einspruch"
