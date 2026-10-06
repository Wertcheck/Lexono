"""Tests für app/export/html_content.py (05.10., Owner-Direktive
"Vollständiger UX- und Workflow-Audit") - isolierte Tests des HTML->
Zwischendarstellung-Parsers, unabhängig von PDF-/DOCX-Rendering (siehe
tests/test_draft_pdf_export.py/test_draft_docx_export.py für die
Export-Ebene)."""

from app.export.html_content import parse_html_content


def test_plain_paragraph_becomes_one_block_with_one_run() -> None:
    blocks = parse_html_content("<p>Hallo Welt.</p>")

    assert len(blocks) == 1
    assert blocks[0].kind == "p"
    assert blocks[0].runs[0].text == "Hallo Welt."


def test_bold_italic_underline_are_tracked_per_run() -> None:
    blocks = parse_html_content(
        "<p>normal <b>fett</b> <i>kursiv</i> <u>unterstrichen</u></p>"
    )

    runs = {r.text.strip(): r for r in blocks[0].runs}
    assert runs["normal"].bold is False
    assert runs["fett"].bold is True
    assert runs["kursiv"].italic is True
    assert runs["unterstrichen"].underline is True


def test_strong_and_em_are_treated_like_b_and_i() -> None:
    blocks = parse_html_content("<p><strong>fett</strong> <em>kursiv</em></p>")

    runs = {r.text.strip(): r for r in blocks[0].runs}
    assert runs["fett"].bold is True
    assert runs["kursiv"].italic is True


def test_nested_formatting_combines() -> None:
    blocks = parse_html_content("<p><b><i>fett und kursiv</i></b></p>")

    run = blocks[0].runs[0]
    assert run.bold is True
    assert run.italic is True


def test_bullet_list_produces_li_bullet_blocks() -> None:
    blocks = parse_html_content("<ul><li>Eins</li><li>Zwei</li></ul>")

    assert [b.kind for b in blocks] == ["li_bullet", "li_bullet"]
    assert blocks[0].runs[0].text == "Eins"
    assert blocks[1].runs[0].text == "Zwei"


def test_numbered_list_increments_and_resets_per_list() -> None:
    blocks = parse_html_content(
        "<ol><li>Erstens</li><li>Zweitens</li></ol><ol><li>Neu</li></ol>"
    )

    assert [b.kind for b in blocks] == ["li_number", "li_number", "li_number"]
    assert [b.number for b in blocks] == [1, 2, 1]


def test_link_href_is_captured_on_the_inline_run() -> None:
    blocks = parse_html_content('<p>Siehe <a href="https://example.com">hier</a>.</p>')

    link_run = next(r for r in blocks[0].runs if r.text == "hier")
    assert link_run.href == "https://example.com"
    assert blocks[0].runs[0].href is None  # "Siehe " ist kein Link


def test_br_produces_a_literal_newline_run_within_the_same_block() -> None:
    blocks = parse_html_content("<p>Zeile 1<br>Zeile 2</p>")

    assert len(blocks) == 1  # EIN Block, kein neuer Absatz
    texts = [r.text for r in blocks[0].runs]
    assert "\n" in texts


def test_div_is_treated_like_a_paragraph_boundary() -> None:
    blocks = parse_html_content("<div>Erster</div><div>Zweiter</div>")

    assert len(blocks) == 2
    assert blocks[0].runs[0].text == "Erster"
    assert blocks[1].runs[0].text == "Zweiter"


def test_span_does_not_create_a_new_block() -> None:
    blocks = parse_html_content("<p>A <span>B</span> C</p>")

    assert len(blocks) == 1


def test_empty_paragraph_is_dropped() -> None:
    blocks = parse_html_content("<p>Echter Inhalt</p><p></p><p>   </p>")

    assert len(blocks) == 1
    assert blocks[0].runs[0].text == "Echter Inhalt"


def test_multiple_paragraphs_produce_separate_blocks() -> None:
    blocks = parse_html_content("<p>Erster Absatz.</p><p>Zweiter Absatz.</p>")

    assert len(blocks) == 2
    assert blocks[0].runs[0].text == "Erster Absatz."
    assert blocks[1].runs[0].text == "Zweiter Absatz."


def test_h2_becomes_bold_paragraph_block() -> None:
    # 05.10., Owner-Direktive "LONG-RUN PRODUCT QUALITY PASS" Phase D -
    # siehe html_sanitizer.py-Kommentar: der Renderer kennt keine eigene
    # Schriftgroesse pro Block, daher wird "h2" als fetter Absatz exportiert
    # statt die Layout-Engine um eine neue Groessenstufe zu erweitern.
    blocks = parse_html_content("<h2>Zusammenfassung</h2><p>Text danach.</p>")

    assert len(blocks) == 2
    assert blocks[0].kind == "p"
    assert blocks[0].runs[0].text == "Zusammenfassung"
    assert blocks[0].runs[0].bold is True
    assert blocks[1].runs[0].bold is False
