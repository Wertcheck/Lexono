"""P2.3 (§42): Fehlerklassen aus dem P2.2-Testsplit, als NEUE Entwicklungsfaelle nachgebaut (`cases/dev_v2.json`).
Der neue blinde Testsplit `test_v2.json` wurde fuer diese Faelle nicht eingesehen. Synthetische Kurzfaelle."""

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


# --- PDF-Zeilenumbruch: Hinweiswort am Zeilenende, Datum in der naechsten Zeile ------------------------------------

def test_deadline_cue_at_the_end_of_a_wrapped_pdf_line() -> None:
    text = ("Wir bitten Sie, den offenen Betrag zuzüglich einer Mahngebühr von 5,00 EUR letztmalig bis zum\n"
            "31.07.2026 zu überweisen.\nSollte die Zahlung bereits erfolgt sein, ist dieses Schreiben gegenstandslos.")
    assert _kinds(text)["Frist"] == {date(2026, 7, 31)}


def test_appointment_formula_wrapped_before_weekday_and_date() -> None:
    text = ("Termin zur Güteverhandlung und sich\nunmittelbar anschließenden mündlichen Verhandlung wird bestimmt auf\n"
            "Mittwoch, 18.11.2026, 10:30 Uhr, Sitzungssaal 2.")
    assert _kinds(text)["Termin"] == {date(2026, 11, 18)}


def test_real_paragraph_breaks_still_separate_sentences() -> None:
    """Gegenprobe: nach Satzende bzw. in Tabellen bleibt der Zeilenumbruch eine Grenze - kein Fristwort aus der
    Vorzeile."""
    assert _kinds("Die Frist endet bald.\n02.05.2026 Eingang der Akte")["Frist"] == set()
    assert _kinds("Vorgang | Datum\nAkteneingang bis | 03.05.2026")["Frist"] == set()


@pytest.mark.parametrize("text", [
    "Den Parteien wird Gelegenheit gegeben, bis einschlieBlich 15.09.2026 Stellung zu nehmen.",
    "Den Parteien wird Gelegenheit gegeben, bis einschliesslich 15.09.2026 Stellung zu nehmen.",
])
def test_bis_einschliesslich_with_ocr_variants(text: str) -> None:
    assert _kinds(text)["Frist"] == {date(2026, 9, 15)}


# --- Klassifikation ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Oberlandesgericht Beispielstadt\n– 4. Zivilsenat –\nAz. 4 U 19/26\nIn der Berufungssache Muster ./. Beispiel "
     "GmbH beantragen wir namens der Berufungsklägerin, die Frist zur Begründung zu verlängern.", "Klage/Schriftsatz"),
    ("Amtsgericht Musterstadt\nLadung\nSehr geehrter Herr Beispiel, die Klageschrift wurde Ihnen am 18.08.2026 "
     "zugestellt. Termin wird bestimmt auf den 22.10.2026.", "Gerichtliches Schreiben"),
    ("Beispiel Reinigung GmbH\n2. Mahnung – Kundennummer 7788\nunsere Rechnung Nr. 2026-0420 ist fällig.", "Mahnung"),
    ("Kanzlei Beispiel – Steuerrecht\nFinanzamt Musterstadt\nEinspruch gegen den Bescheid für 2025 über "
     "Einkommensteuer vom 06.05.2026\nnamens unserer Mandantin legen wir Einspruch ein.", "Einspruch"),
])
def test_dev_v2_documents_get_their_type(text: str, expected: str) -> None:
    assert classifier.classify(text).document_type == expected


@pytest.mark.parametrize("text, wrong", [
    ("Kanzlei Beispiel & Partner\nHerrn Max Muster\nSehr geehrter Herr Muster,\ndas Gericht hat Termin zur mündlichen "
     "Verhandlung bestimmt. Die Gegenseite hat auf die Klage erwidert.", "Gerichtliches Schreiben"),
    ("Kanzlei Beispiel & Partner\nHerrn Max Muster\nSehr geehrter Herr Muster,\ndas Gericht hat Termin zur mündlichen "
     "Verhandlung bestimmt. Die Gegenseite hat auf die Klage erwidert.", "Klage/Schriftsatz"),
    ("Von: mandant@beispielpost.example\nBetreff: Brief vom Anwalt\nder Anwalt verlangt den Rückschnitt, andernfalls "
     "werde Klage erhoben.", "Klage/Schriftsatz"),
    ("Beispiel Hausverwaltung GmbH\nMietverhältnis Ahornweg 3 – Mietvertrag vom 01.02.2024\nWir fordern Sie auf, den "
     "Rückstand zu überweisen.", "Vertrag"),
])
def test_mentions_of_court_claim_and_contract_do_not_force_a_type(text: str, wrong: str) -> None:
    assert classifier.classify(text).document_type != wrong


@pytest.mark.parametrize("text, expected", [
    ("An das Landgericht Musterstadt\nKlage\nder Beispielbau GmbH gegen Frau Muster wegen Werklohn.", "Klage/Schriftsatz"),
    ("Mietvertrag über Wohnraum\nZwischen A und B wird folgender Vertrag geschlossen.", "Vertrag"),
    ("Amtsgericht Beispielstadt\nVerfügung\nDer Termin wird aufgehoben.", "Gerichtliches Schreiben"),
    ("Malerbetrieb Muster\nRechnung Nr. R-2026-0612\nGesamtbetrag 1.600,00 EUR", "Rechnung"),
])
def test_genuine_documents_keep_their_type(text: str, expected: str) -> None:
    assert classifier.classify(text).document_type == expected
