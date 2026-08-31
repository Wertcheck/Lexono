"""Tests für app/chat/document_preview.py (Masterprompt V2, Task #62 -
Dokument-Workspace mit Pseudonymisierungs-Highlighting).

Nutzt nur synthetische Testdaten (CLAUDE.md, nicht verhandelbar)."""

from __future__ import annotations

from app.chat.document_preview import build_document_preview


def test_empty_text_returns_empty_preview() -> None:
    preview = build_document_preview("")
    assert preview.highlighted_html == ""
    assert preview.entities == []


def test_none_text_returns_empty_preview() -> None:
    preview = build_document_preview(None)
    assert preview.highlighted_html == ""
    assert preview.entities == []


def test_detects_and_highlights_email_address() -> None:
    text = "Bitte antworten Sie an max.mustermann@beispielkanzlei.de bis Freitag."
    preview = build_document_preview(text)
    assert "max.mustermann@beispielkanzlei.de" in preview.highlighted_html
    assert 'class="pii-highlight pii-highlight--email"' in preview.highlighted_html
    categories = {entity.category for entity in preview.entities}
    assert "email" in categories


def test_detects_iban_and_labels_it() -> None:
    text = "Die Rueckerstattung erfolgt auf IBAN DE89370400440532013000."
    preview = build_document_preview(text)
    iban_entities = [e for e in preview.entities if e.category == "iban"]
    assert len(iban_entities) == 1
    assert iban_entities[0].label == "IBAN"


def test_repeated_value_is_counted_not_duplicated_in_entity_list() -> None:
    text = "Kontakt: info@beispielkanzlei.de. Erneut: info@beispielkanzlei.de."
    preview = build_document_preview(text)
    email_entities = [e for e in preview.entities if e.category == "email"]
    assert len(email_entities) == 1
    assert email_entities[0].count == 2


def test_highlighted_html_escapes_plain_text_content() -> None:
    text = "Vertrag <wichtig> zwischen den Parteien, Kontakt a@b.de"
    preview = build_document_preview(text)
    assert "<wichtig>" not in preview.highlighted_html
    assert "&lt;wichtig&gt;" in preview.highlighted_html


def test_text_without_any_pii_has_no_entities_but_keeps_text() -> None:
    text = "Allgemeine Erlaeuterung ohne erkennbare personenbezogene Daten."
    preview = build_document_preview(text)
    assert preview.entities == []
    assert "Allgemeine Erlaeuterung" in preview.highlighted_html
