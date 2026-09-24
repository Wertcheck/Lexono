"""Tests fuer app/classification/classifier.py (Prompt 08).

Nutzt ausschliesslich synthetische Testtexte - keine echten
Mandantendaten."""

import pytest

from app.classification.classifier import PlaceholderDocumentClassifier

classifier = PlaceholderDocumentClassifier()


def test_recognizes_rechnung_by_keyword() -> None:
    result = classifier.classify("Sehr geehrte Damen und Herren, anbei unsere Rechnung Nr. 123.")
    assert result.document_type == "Rechnung"


def test_recognizes_vollmacht_by_keyword() -> None:
    result = classifier.classify("Hiermit erteile ich Ihnen Vollmacht in dieser Angelegenheit.")
    assert result.document_type == "Vollmacht"


def test_recognizes_kuendigung_by_keyword() -> None:
    result = classifier.classify("Hiermit kündige ich den Vertrag fristgerecht.")
    assert result.document_type == "Kündigungsschreiben"


def test_unrecognized_text_is_unbekannt() -> None:
    result = classifier.classify("Ein völlig neutraler Testsatz ohne besondere Begriffe.")
    assert result.document_type == "Unbekannt"


def test_confidence_is_always_low_for_placeholder() -> None:
    """Der Platzhalter darf NIE hochsicher wirken - unabhaengig vom Text."""
    texts = [
        "Rechnung Vollmacht Kündigung Mahnung Klage Gericht Vertrag",
        "Ein völlig neutraler Testsatz.",
        "",
    ]
    for text in texts:
        result = classifier.classify(text)
        assert result.confidence <= 0.4


def test_detects_action_required_keyword() -> None:
    result = classifier.classify("Bitte antworten Sie dringend bis zum 15.03.")
    assert result.action_required is True


def test_no_action_required_without_keyword() -> None:
    result = classifier.classify("Ein ganz normales Schreiben ohne Eile.")
    assert result.action_required is False


def test_detects_possible_matter_reference() -> None:
    result = classifier.classify("Bezug: Az.: 123/24, Ihr Schreiben vom 01.02.")
    assert result.possible_matter_reference == "123/24"


def test_no_matter_reference_when_absent() -> None:
    result = classifier.classify("Ein Schreiben ganz ohne Aktenzeichen.")
    assert result.possible_matter_reference is None


def test_reasoning_mentions_placeholder_nature() -> None:
    result = classifier.classify("Beliebiger Text.")
    assert "Platzhalter" in result.reasoning
    assert "kein LLM" in result.reasoning


def test_possible_parties_and_topic_are_empty_placeholder_stubs() -> None:
    """Namens-/Themenerkennung ist bewusst nicht Teil des Platzhalters."""
    result = classifier.classify("Text mit Namen wie Max Mustermann GmbH.")
    assert result.possible_parties == []
    assert result.topic is None


# --- Steuerrechtliche Dokumenttypen (14.09.) -------------------------------
# ECHTER FUND beim Durchspielen der synthetischen Kanzlei-Datenbasis gegen
# den PRODUKTIVEN DocumentProcessingService: ein Dokument mit dem Wort
# "Steuerbescheid" wurde als "Unbekannt" (Konfidenz 0.1) eingestuft, weil
# die Typliste keinen einzigen steuerrechtlichen Typ enthielt - fuer die
# Pilotkanzlei (Steuerfachanwaltskanzlei) der haeufigste Dokumenttyp
# ueberhaupt.


@pytest.mark.parametrize(
    ("text", "expected_type"),
    [
        ("Einkommensteuerbescheid für 2024 vom Finanzamt.", "Steuerbescheid"),
        ("Gewerbesteuerbescheid 2023, Festsetzung der Steuer.", "Steuerbescheid"),
        # Schreibweise eines ECHTEN deutschen Bescheids (kein Kompositum):
        ("Bescheid für 2025 über Einkommensteuer und Solidaritätszuschlag.", "Steuerbescheid"),
        ("Wir legen Einspruch gegen den Bescheid ein.", "Einspruch"),
        ("Prüfungsanordnung nach § 196 AO.", "Prüfungsanordnung"),
        ("Die Betriebsprüfung beginnt am 01.03.", "Prüfungsanordnung"),
        ("Umsatzsteuer-Nachschau angekündigt.", "Prüfungsanordnung"),
        ("Anbei die Umsatzsteuer-Voranmeldung für Q1.", "Steuererklärung"),
    ],
)
def test_detects_tax_document_types(text: str, expected_type: str) -> None:
    result = classifier.classify(text)
    assert result.document_type == expected_type
    assert result.confidence > 0.1, "Ein erkannter Typ darf nicht auf der Null-Treffer-Konfidenz bleiben"


def test_steuerbescheid_wins_over_generic_keywords() -> None:
    """Ein Steuerbescheid enthaelt regelmaessig auch generische Woerter
    ("Rechnung", "Frist"). Die steuerrechtlichen Typen stehen deshalb
    bewusst VOR den generischen in der Keyword-Tabelle - dieser Test haelt
    genau diese Reihenfolgeabhaengigkeit fest, damit sie nicht
    versehentlich umsortiert wird."""
    result = classifier.classify(
        "Steuerbescheid 2024. Die Rechnung des Steuerberaters liegt bei. "
        "Zahlung bis zum 01.04."
    )
    assert result.document_type == "Steuerbescheid"


def test_kuendigungsfrist_clause_does_not_turn_a_contract_into_a_termination() -> None:
    """ECHTER FUND (14.09., am Demo-Dokument reproduziert, gleiche
    Fehlerklasse wie die Rechtsbehelfsbelehrung): die Klausel
    "Kündigungsfrist 3 Monate" steht in praktisch JEDEM Vertrag. Das
    blosse Fragment "kündig" stufte einen "Vertragsentwurf zwischen den
    Parteien" dadurch als Kündigungsschreiben ein."""
    vertrag_text = (
        "Vertragsentwurf zwischen den Parteien. "
        "§ 8 Haftung: Die Haftung wird auf Vorsatz und grobe Fahrlässigkeit beschränkt. "
        "Laufzeit: 24 Monate, Kündigungsfrist 3 Monate zum Laufzeitende."
    )
    assert classifier.classify(vertrag_text).document_type == "Vertrag"


def test_real_termination_letter_is_still_detected() -> None:
    """Gegenprobe: echte Kuendigungsschreiben muessen weiterhin erkannt
    werden - auch wenn sie (wie ueblich) den gekuendigten Vertrag nennen."""
    for text in (
        "Hiermit kündige ich den Vertrag fristgerecht.",
        "Fristlose Kündigung des Arbeitsverhältnisses zum 01.03.",
        "Mir wurde fristlos gekündigt, obwohl keine Pflichtverletzung vorlag.",
    ):
        assert classifier.classify(text).document_type == "Kündigungsschreiben", text


def test_rechtsbehelfsbelehrung_does_not_turn_a_bescheid_into_an_einspruch() -> None:
    """ECHTER FUND (14.09., beim Gegenpruefen am realen Dokumenttext der
    synthetischen Datenbasis): die Rechtsbehelfsbelehrung "Einspruch
    innerhalb eines Monats nach Bekanntgabe" steht auf praktisch JEDEM
    deutschen Steuerbescheid. Eine Keyword-Regel auf dem blossen Wort
    "einspruch" stufte diesen Bescheid dadurch faelschlich als "Einspruch"
    ein - real reproduziert am erzeugten Demo-Dokument."""
    bescheid_text = (
        "Bescheid für 2025 über Einkommensteuer und Solidaritätszuschlag. "
        "Festgesetzte Einkommensteuer: 19.261 EUR. "
        "Rechtsbehelfsbelehrung: Einspruch innerhalb eines Monats nach Bekanntgabe."
    )
    assert classifier.classify(bescheid_text).document_type == "Steuerbescheid"


def test_actively_lodged_einspruch_is_still_detected() -> None:
    """Gegenprobe zum Test darueber: ein tatsaechliches
    Einspruchsschreiben muss weiterhin als "Einspruch" erkannt werden -
    die Verschaerfung darf die echte Erkennung nicht kaputtmachen."""
    for text in (
        "Namens und im Auftrag unseres Mandanten legen wir Einspruch ein.",
        "Hiermit wird Einspruch gegen den Bescheid vom 01.03. eingelegt.",
        "Das Einspruchsverfahren ruht bis zur Entscheidung des BFH.",
    ):
        assert classifier.classify(text).document_type == "Einspruch", text


def test_confidence_has_no_floating_point_artefacts() -> None:
    """ECHTER FUND (14.09.): ohne Rundung lieferte die Konfidenz Werte wie
    0.30000000000000004. Dieser Wert wird PERSISTIERT
    (Document.classification_confidence) und kann in Oberflaeche/Export
    auftauchen - real beim Durchspielen der synthetischen Datenbasis gegen
    den produktiven Verarbeitungspfad beobachtet."""
    for text in (
        "Prüfungsanordnung nach § 196 AO, Betriebsprüfung angekündigt.",
        "Rechnung mit Rechnungsnummer 123.",
        "Ein Text ganz ohne Schlüsselwörter.",
    ):
        confidence = classifier.classify(text).confidence
        assert confidence == round(confidence, 2), f"{confidence!r} bei: {text}"


def test_tax_types_still_respect_the_placeholder_confidence_ceiling() -> None:
    """Die bewusste Sicherheitsgrenze des Platzhalter-Klassifikators
    (max. 0.4, zu wenig fuer eine automatische Aktenzuordnung) bleibt
    UNVERAENDERT - die neuen Typen erweitern nur die Erkennung, nicht die
    Konfidenz."""
    result = classifier.classify(
        "Steuerbescheid, Einkommensteuerbescheid, Festsetzung, "
        "Körperschaftsteuerbescheid, Gewerbesteuerbescheid"
    )
    assert result.confidence <= 0.4
