"""P2.1 (§40): Dokumentklassifikation - Schriftsatz, Urteil, Vertrag, Rechnung, Bescheid, Gutachten, Protokoll, Anlage.

Vorher (Legal-E2E-Korpus): Kerntypen dev 4/7, test 0/5; Klageschrift als "Mahnung" (Teilwort von "Abmahnung"),
Anwaltsschreiben als "Vollmacht" ("Namens und in Vollmacht"), Schreiben als "Rechnung" ("Schlussrechnung" im Text),
Gutachten/Protokoll/Bescheid ohne eigenen Typ. Synthetische Entwicklungsfaelle, getrennt vom Korpus."""

import pytest

from app.classification.classifier import PlaceholderDocumentClassifier

classifier = PlaceholderDocumentClassifier()

_FILLER = " Weitere Ausführungen zum Sachverhalt folgen in diesem Absatz ohne besondere Begriffe." * 10


@pytest.mark.parametrize("text, expected", [
    ("Amtsgericht Musterstadt\nIm Namen des Volkes\nUrteil\nIn dem Rechtsstreit Beispiel ./. Muster hat das Gericht "
     "für Recht erkannt: Die Klage wird abgewiesen.", "Urteil/Beschluss"),
    ("Landgericht Beispielstadt\nBeschluss\nIn dem Verfahren wird der Antrag zurückgewiesen.", "Urteil/Beschluss"),
    ("An das Landgericht Beispielstadt\nKlageerwiderung\nIn dem Rechtsstreit wird beantragt, die Klage abzuweisen. "
     "Vgl. BGH, Urteil vom 01.01.2020.", "Klage/Schriftsatz"),
    ("An das Arbeitsgericht Beispielhafen\nKlage des Herrn Beispiel gegen die Muster GmbH wegen Kündigungsschutz\n"
     "Die Abmahnung vom 12.02.2026 ist unwirksam.", "Klage/Schriftsatz"),
    ("Landkreis Musterkreis - Bußgeldstelle\nBußgeldbescheid\nIhnen wird vorgeworfen, die zulässige Geschwindigkeit "
     "überschritten zu haben. Rechtsbehelfsbelehrung: Einspruch innerhalb von zwei Wochen.", "Bescheid"),
    ("Stadt Musterstadt\nWiderspruchsbescheid\nDer Widerspruch wird zurückgewiesen.", "Bescheid"),
    ("Wertgutachten zum Grundstück Kastanienweg 3\nWertermittlungsstichtag: 01.03.2026", "Gutachten"),
    ("Privatgutachten Nr. PG-1\nSachverständiger: Max Beispiel\nOrtstermin im Dachgeschoss.", "Gutachten"),
    ("Protokoll der mündlichen Verhandlung vom 12.05.2026\nAnwesend: ...", "Protokoll"),
    ("Polizeiliches Unfallprotokoll\nUnfallzeit: 07.05.2026", "Protokoll"),
    ("Niederschrift über die Eigentümerversammlung", "Protokoll"),
    ("Mietvertrag über Wohnraum (Auszug)\nZwischen A und B wird folgender Vertrag geschlossen.", "Vertrag"),
    ("Malerbetrieb Beispiel GmbH\nRechnung Nr. R-2026-0311\nGesamtbetrag 1.200,00 EUR", "Rechnung"),
    ("Mahnung\nSehr geehrte Damen und Herren, unsere Zahlungserinnerung blieb unbeantwortet.", "Mahnung"),
])
def test_document_types_are_recognised_from_the_heading(text: str, expected: str) -> None:
    result = classifier.classify(text)
    assert result.document_type == expected, result.reasoning
    assert result.confidence <= 0.4  # Platzhalter-Deckel bleibt (keine automatische Aktenzuordnung)


@pytest.mark.parametrize("text, wrong", [
    ("Holzbau Beispiel GmbH\nBeispielheide, 17.12.2025\nDie Abnahme erfolgte bereits am 31.10.2025." + _FILLER
     + " Aus der Schlussrechnung ist noch ein Betrag offen.", "Rechnung"),
    ("Rechtsanwalt Beispiel\nMusterstadt, den 01.04.2026\nNamens und in Vollmacht unserer Mandantin fordern wir Sie auf, "
     "die Mängel zu beseitigen.", "Vollmacht"),
    ("Muster GmbH - Personalabteilung\nAbmahnung\nHerr Beispiel, am 09.02.2026 haben Sie den Arbeitsplatz verlassen.",
     "Mahnung"),
    ("Gesprächsnotiz vom 24.03.2026\nDie Abmahnung vom 12.02.2026 habe er nie erhalten.", "Mahnung"),
    ("Von: mandantin@beispielpost.example\nBetreff: Schimmel\nSehr geehrter Herr Beispiel," + _FILLER
     + " Laut Mietvertrag ist die Wohnung mangelfrei zu übergeben.", "Vertrag"),
    ("Sehr geehrte Frau Beispiel,\nwir verweisen auf das Urteil des BGH vom 01.01.2020 und bitten um Rückruf.",
     "Urteil/Beschluss"),
])
def test_mere_mentions_do_not_force_a_wrong_type(text: str, wrong: str) -> None:
    """Begruendete Unsicherheit statt erzwungener Fehlklassifikation: eine Erwaehnung macht kein Dokument dieses Typs."""
    assert classifier.classify(text).document_type != wrong


@pytest.mark.parametrize("text, filename", [
    ("Anlage K1\nErbschein (Auszug) - Amtsgericht Beispielheim, Nachlassgericht", None),
    ("Erbschein (Auszug) - erteilt am 20.02.2026", "Anlage_K3_Erbschein.pdf"),
])
def test_exhibits_are_recognised_only_by_an_explicit_marker(text: str, filename: str | None) -> None:
    assert classifier.classify(text, filename=filename).document_type == "Anlage"


def test_exhibit_marker_does_not_hide_a_recognised_content_type() -> None:
    result = classifier.classify("Anlage K2\nMietvertrag über Wohnraum\nZwischen A und B wird vereinbart:")
    assert result.document_type == "Vertrag"
    assert "Anlage" in result.reasoning


@pytest.mark.parametrize("text, expected", [
    ("Malerbetrieb Beispiel GmbH\nRechnunq Nr. R-2026-0311\nGesamtbetrag 1.200,00 EUR", "Rechnung"),
    ("Privatgutachlen Nr. PG-1\nOrtstermin im Dachgeschoss", "Gutachten"),
    ("Landkreis Musterkreis\nBußgeldbescheld\nTatvorwurf: Geschwindigkeitsüberschreitung", "Bescheid"),
])
def test_ocr_misspelled_heading_words_are_still_recognised(text: str, expected: str) -> None:
    result = classifier.classify(text)
    assert result.document_type == expected
    assert "OCR" in result.reasoning


def test_unrelated_text_stays_unknown() -> None:
    assert classifier.classify("Kurze Frage zu meiner Sache, bitte um Rückruf.").document_type == "Unbekannt"


@pytest.mark.parametrize("text, expected", [
    ("Rechtsanwalt Beispiel\nHiermit legen wir Einspruch gegen den Bußgeldbescheid vom 03.08.2026 ein.", "Einspruch"),
    ("Von: mandant@beispielpost.example\nBetreff: Bußgeldbescheid\nDer Bescheid wurde mir zugestellt.", "Unbekannt"),
    ("Sehr geehrte Damen und Herren,\nwegen des Kostenbescheids bitten wir um Rückruf.", "Unbekannt"),
])
def test_reference_to_a_notice_is_not_a_notice(text: str, expected: str) -> None:
    """Regression der Erstfassung (im Legal-E2E-Testsplit beobachtet, hier mit eigenen synthetischen Faellen): ein
    Einspruch GEGEN einen Bescheid und eine E-Mail mit Betreff "Bußgeldbescheid" wurden zum "Bescheid"."""
    assert classifier.classify(text).document_type == expected


def test_notice_with_appeal_instructions_stays_a_notice() -> None:
    text = ("Landkreis Musterkreis\nBußgeldbescheid\nGegen diesen Bescheid kann innerhalb von zwei Wochen nach "
            "Zustellung Einspruch eingelegt werden.")
    assert classifier.classify(text).document_type == "Bescheid"
