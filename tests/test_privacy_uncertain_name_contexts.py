"""Unsichere Namen in Rollen-, Satz- und Tabellenkontext (24.10.2026): Namen, die zugleich Gattungswoerter sind
("Bergmann", "Fischer", "Fuerst", "Licht") oder vom Modell kaum als Person gelesen werden ("Zweigelt", "Kuehne"), gingen
im Klartext hinaus. Sie werden nicht SICHER erkannt, sondern als unsicher behandelt: Rueckfrage (ask) bzw. vorsichtige
Maskierung (mask). Fachbegriffe, Gesetzeszitate, Steuerfachwoerter und Firmen-Kurzformen bleiben unveraendert.

Ausschliesslich synthetische Daten, lokal, kein Cloud-Aufruf."""

from __future__ import annotations

import json

import pytest

from app.privacy import review as rv
from app.privacy.gateway import ClaudePrivacyGateway


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _run(gateway, text: str, mode: str):
    return gateway.prepare_request(purpose="formulate_draft", sachverhalt=text, known_entities={}, review_mode=mode)


def _outgoing(result) -> str:
    return json.dumps(result.payload.model_dump(), ensure_ascii=False) if result.payload is not None else ""


PROTECTED = [
    ("Der Bergmann ist bislang nicht erschienen.", "Bergmann"),  # G1
    ("Der Richter Zweigelt hat den Termin verlegt.", "Zweigelt"),  # G2
    ("Akte: Beispielakte\nDazu erklärt Fuerst, die Anlage sei mangelhaft.", "Fuerst"),  # G3
    ("Akte: Beispielakte\nZeuge  |  Stuckenschneider  |  Hamburg  |  geladen", "Stuckenschneider"),  # G4
    ("Der Fischer hat am 12.03.2026 angerufen.", "Fischer"),  # G7
    ("Der Grotelueschen ist bislang nicht erschienen.", "Grotelueschen"),  # dem Modell unbekanntes Wort nach "der"
    ("Nach Rücksprache hat die Mandantin Licht unterschrieben.", "Licht"),  # G8
    ("Akte: Beispielakte\nBrandhuber  1.900,00 EUR", "Brandhuber"),  # Einzelzeile mit Betrag
    ("Lohnliste September  Brandhuber  1.900,00 EUR", "Brandhuber"),
    ("Der Bescheid ging an Kuester und Partner.", "Kuester"),
    ("Der Bescheid ging an Faerber und Partner.", "Faerber"),
    ("Der Bescheid ging an Kuerschner und Partner.", "Kuerschner"),
    ("Der Bescheid ging an Buerger und Partner.", "Buerger"),
    ("Der Bescheid ging an Kuehne und Partner.", "Kuehne"),
    ("Der Bescheid ging an Gruener und Partner.", "Gruener"),
    ("Zeuge: Kuester.", "Kuester"),
    ("Zeuge: Fuerst.", "Fuerst"),
    ("Zeuge: Schuetz.", "Schuetz"),
    ("Die Akte wurde an Kuester übergeben.", "Kuester"),
    ("Die Akte wurde an Gruener übergeben.", "Gruener"),
    ("Das Schreiben unterzeichnete Fuerst.", "Fuerst"),
    ("Gemäß § 823 BGB haftet Kuehne.", "Kuehne"),
    ("| Beteiligter | Kuehne |", "Kuehne"),
    ("Laut Kuehne war die Ampel rot.", "Kuehne"),
]


@pytest.mark.parametrize("text, name", PROTECTED)
def test_uncertain_name_is_masked_in_mask_mode(gateway, text: str, name: str) -> None:
    result = _run(gateway, text, "mask")
    assert result.allowed, result.reasons
    assert name not in _outgoing(result)


@pytest.mark.parametrize("text, name", PROTECTED)
def test_uncertain_name_is_asked_or_masked_in_ask_mode(gateway, text: str, name: str) -> None:
    """Rueckfrage - oder bereits sichere Maskierung durch eine andere Stufe (dann ist keine Frage noetig)."""
    result = _run(gateway, text, "ask")
    if result.review_required:
        assert result.payload is None and name in {c.term for c in result.review_candidates}
    else:
        assert result.allowed, result.reasons
        assert name not in _outgoing(result)


# --- negativ: keine Rueckfrage zu Fach- und Sachwoertern ----------------------------------------------------------------

NOT_A_NAME = [
    "Der Vermieter hat die Kaution einbehalten.",
    "Der Bescheid ist rechtswidrig.",
    "Der Vertrag wurde am 12.03.2026 geschlossen.",
    "Die Akte wurde an das Gericht übergeben.",  # "die" + Gattungswort: keine Artikel-Lesart
    "Die Ware ging an Kunden und Partner.",
    "Laut Vertrag ist die Miete monatlich fällig.",
    "Laut Gutachten war die Wand feucht.",
    "Laut Bescheid ist der Betrag zu zahlen.",
    "Gemäß § 823 BGB haftet der Schädiger.",
    "Der Richter hat den Termin verlegt.",  # Amtswort selbst ist kein Kandidat
    "Frau Kollegin, der Streitwert beträgt 12.400,00 EUR.",  # Sachnomen nach "der" (Fehlalarm der ersten Fassung)
    "Inzwischen hat der Gutachter Risse festgestellt.",  # flektierte Form nach Rollenwort
    "Der Kaufmann im Sinne des HGB haftet für die Verbindlichkeiten.",  # Begriffsbestimmung: Rechtsbegriff
    "Offen: Der Zustellnachweis liegt der Kanzlei nicht vor und ist anzufordern.",  # Benchmark LE-D005 (Sach-Kopfnomen)
    "Der Sachverständigenbeweis ist bislang nicht erhoben.",
    "Wer haftet bei Mängeln der Dämmung?",  # "bei" + Plural (Fehlalarm der ersten Fassung)
    "Herr Bescheid hat unterschrieben.",  # nach Anrede bleibt der staerkste Namensrahmen Pflicht
    "Anwalt: Fasse das Dokument zusammen.",  # Praefix des Gespraechsverlaufs ist kein Rollenwort
    "Die Mandantin hat Einspruch eingelegt.",
    "Zeuge: unbekannt.",
    "Miete  900,00 EUR",
    "Kaution  1.800,00 EUR",
    "Wareneinsatz  158.900,00 EUR",
    "Der Mieter rügte Mängel.",
    "Die Behörde erließ Bescheide.",
    "Akte: Mietsache\nDer Vermieter hat gekündigt.",
]


@pytest.mark.parametrize("text", NOT_A_NAME)
def test_technical_and_role_words_are_not_asked(text: str) -> None:
    assert rv.find_review_candidates(text, context_source=text, max_candidates=None) == []


def test_matter_title_itself_is_still_not_asked() -> None:
    """Die Systemzeile "Akte: <Titel>" bleibt ausgenommen - nur ihre Verlaengerung in die naechste Zeile entfaellt."""
    text = "Akte: Brandmeier gegen Beispiel\nDer Vertrag wurde geschlossen."
    assert "Brandmeier" not in {c.term for c in rv.find_review_candidates(text, context_source=text)}


def test_system_label_line_ends_at_the_line_break() -> None:
    assert rv._SYSTEM_LABEL_LINE.search("x\nAkte: Beispielakte")
    assert not rv._SYSTEM_LABEL_LINE.search("x\nAkte: Beispielakte\n")


# --- echte Daten bleiben geschuetzt, Fachinhalte lesbar ----------------------------------------------------------------

CONTROL = (
    "Die Nordbauholding GmbH (NBH) legt Einspruch ein; Steuernummer 12/345/67890.\n"
    "Geschaeftsfuehrer: Thomas Berger, Beispielstraße 17, 10115 Berlin. Nach § 1 StVO gilt Ruecksicht.\n"
    "Zeuge: Schuetz. Der Bescheid ging an Kuester und Partner.\n"
    "Wareneinsatz  158.900,00 EUR  Rohergebnis  212.900,00 EUR"
)


def test_control_document_protects_identifiers_and_keeps_legal_content(gateway) -> None:
    result = _run(gateway, CONTROL, "mask")
    assert result.allowed, result.reasons
    payload = _outgoing(result)
    for value in ("Thomas Berger", "Nordbauholding", "NBH", "12/345/67890", "Beispielstraße 17", "Schuetz", "Kuester"):
        assert value not in payload, value
    for value in ("§ 1 StVO", "gilt Ruecksicht", "Wareneinsatz", "Rohergebnis", "Einspruch"):
        assert value in payload, value


# --- dokumentierte Grenzen (bewusst offen, siehe Bericht 24.10.2026) ----------------------------------------------------


# Im Einzelsatz-Test (detect_all + Rueckfrage ohne Gateway) offen, im Gateway-Pfad aber geschuetzt (gemessen 24.10.2026)
@pytest.mark.parametrize(
    "text, name",
    [("Das Schreiben unterzeichnete Buerger.", "Buerger"), ("Die Fischer hat am 12.03.2026 angerufen.", "Fischer")],
)
def test_gateway_protects_further_ambiguous_forms(gateway, text: str, name: str) -> None:
    result = _run(gateway, text, "mask")
    assert result.allowed and name not in _outgoing(result)


# Frueher offene Entscheidung, umgesetzt als Option C (24.10.2026): blockieren statt Initialen preisgeben.
def test_company_short_form_colliding_with_a_statute_is_blocked(gateway) -> None:
    result = _run(gateway, "Die Bremer Grundbesitz GmbH (BGB) zahlt nicht. Die BGB verweigert die Zahlung. "
                           "Nach § 823 BGB haftet sie.", "mask")
    assert not result.allowed and result.payload is None
