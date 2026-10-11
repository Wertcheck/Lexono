"""Datenschutz-Rueckfrage im ECHTEN Dokumentpfad (12.10.2026, Befund im installierten Build).

`local_ai_provider._normalize_document_text` ersetzt Zeilenumbrueche durch zwei Leerzeichen. Die frueheren Tests mit
Zeilenumbruechen bildeten diesen Pfad nicht ab: "Zeugenaussage Richter" und "durch  Lange geprueft" gingen im Payload im
Klartext an die Cloud. Ausschliesslich synthetische Daten, echter Presidio-/spaCy-Stack."""

from __future__ import annotations

import pytest

from app.ai_providers.local_ai_provider import _normalize_document_text
from app.privacy import presidio_ner as pn
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.review import _is_bare


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _doc_prepare(gateway, document_text: str, mode: str):
    return gateway.prepare_request(
        purpose="formulate_draft",
        sachverhalt="Akte: Beispielakte\n[Dokument] " + _normalize_document_text(document_text),
        anwaltliche_anmerkungen="Erstelle ein Schreiben.",
        review_mode=mode,
    )


DOCUMENT_CASES = [
    ("Richter", "Zeugenaussage Richter\nDer Zeuge schildert den Ablauf der Abnahme am 12.03.2026."),
    ("Lange", "Die Unterlagen wurden am 12.03.2026 durch\nLange geprueft und dem Auftraggeber uebergeben."),
    ("Richter", "Zeuge  |  Richter  |  Hamburg  |  geladen"),
]


@pytest.mark.parametrize(("name", "document_text"), DOCUMENT_CASES)
def test_names_in_normalized_documents_are_protected(gateway, name, document_text) -> None:
    masked = _doc_prepare(gateway, document_text, "mask")
    assert masked.allowed and name not in masked.payload.anonymisierter_sachverhalt
    asked = _doc_prepare(gateway, document_text, "ask")
    assert asked.review_required and name in [c.term for c in asked.review_candidates]


@pytest.mark.parametrize(
    "document_text",
    [
        "Die Feuchtigkeit wurde festgestellt.\nDie Abdichtung ist mangelhaft.",
        "Der Streitwert betraegt 9.150,00 EUR.\nDie Zugluft im Flachdach ist ein Baumangel.",
        "Position  |  Menge  |  Preis\nEstrich  |  3  |  1.200,00 EUR",
    ],
)
def test_legal_text_in_normalized_documents_is_not_asked(gateway, document_text) -> None:
    result = _doc_prepare(gateway, document_text, "ask")
    assert not result.review_required, [c.term for c in result.review_candidates]


# Bekannte, vorbestehende Grenze (NOT VERIFIED geloest): transliterierte Fachwoerter in Agens-Rolle ("durch Ueberweisung")
# liest das Modell auch im Rahmen "Herr X" als Person; die Umlaut-Rueckschreibung trennt nicht verlaesslich. Im Chat
# entsteht eine Rueckfrage, nicht-interaktiv eine vorsichtshalber-Maskierung (fail-closed) - kein Leck.
@pytest.mark.xfail(reason="vorbestehender Fachwort-Fehlalarm bei transliteriertem Agens", strict=False)
def test_known_transliterated_agent_false_alarm(gateway) -> None:
    result = _doc_prepare(gateway, "Die Zahlung erfolgte durch\nUeberweisung am 12.03.2026.", "ask")
    assert not result.review_required


def test_table_pipe_is_not_treated_as_an_adjective() -> None:
    """Der Tagger etikettiert "|" als ADJ; dadurch galt ein Name in einer Tabellenzelle faelschlich als nicht "bar"."""
    doc = pn._analysis_doc("Zeuge  |  Richter  |  Hamburg")
    token = next(t for t in doc if t.text == "Richter")
    assert _is_bare(doc, token)


# --- Realistische Dokumente (12.10.2026): Tabellengrenzen, Listen, Satzrollen ueber Zeilengrenzen -----------------------


def test_invoice_table_after_a_party_table_is_not_asked(gateway) -> None:
    """Nach der Dokumentaufbereitung verschmolzen Beteiligten- und Rechnungstabelle zu EINER Zeile; "Estrich" galt als Zelle
    neben einem Namen. Zeilengrenze ist auch ein Doppel-Leerzeichen, das nicht an einem "|" steht."""
    text = "Zeuge | Lange | Hafenstraße 9, 24105 Kiel\n\nPosition | Menge | Betrag\nEstrich | 3 | 1.200,00 EUR"
    result = _doc_prepare(gateway, text, "ask")
    terms = [c.term for c in result.review_candidates]
    assert "Estrich" not in terms and "Menge" not in terms
    masked = _doc_prepare(gateway, text, "mask")
    assert "Estrich" in masked.payload.anonymisierter_sachverhalt and "1.200,00 EUR" in masked.payload.anonymisierter_sachverhalt


def test_flattened_pdf_table_cells_are_not_subjects_of_a_later_sentence(gateway) -> None:
    text = "Position\nMenge\nBetrag\nEstrich\n3\n1.200,00 EUR\nDer Streitwert beträgt 12.400,00 EUR."
    result = _doc_prepare(gateway, text, "ask")
    assert not result.review_required, [c.term for c in result.review_candidates]


@pytest.mark.parametrize(
    "text",
    [
        "Als Zeugen werden benannt:\n- Herr Marquardt\n- Fuerst\n- Dr. Henrike Stuckenschneider",  # mit Aufzaehlungszeichen
        "Als Zeugen werden benannt:\nHerr Marquardt\nFuerst\nDr. Henrike Stuckenschneider",  # DOCX-Liste ohne Zeichen
    ],
)
def test_list_item_next_to_a_replaced_person_is_protected(gateway, text) -> None:
    masked = _doc_prepare(gateway, text, "mask")
    assert "Fuerst" not in masked.payload.anonymisierter_sachverhalt
