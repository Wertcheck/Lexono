"""Tests für die semantische Chat-Titel-Ableitung (07.10., Owner-Direktive
"CHAT-HISTORY-MANAGEMENT ERWEITERN" §1).

ECHTER FUND, den diese Direktive beheben sollte (siehe
app/chat/title_generation.py-Moduldocstring): die bisherige
`_derive_title` (app/chat/service.py) war reines Whitespace-Normalisieren
+ 60-Zeichen-Abschneiden - die ersten Woerter der Nachricht wurden also
woertlich zum Titel, unabhaengig vom eigentlichen Thema."""

from __future__ import annotations

from app.chat.title_generation import generate_conversation_title


def test_paragraph_citation_with_symbol() -> None:
    assert generate_conversation_title("Was regelt § 823 BGB?") == "§ 823 BGB"


def test_paragraph_citation_as_written_out_word() -> None:
    """Direktive-Beispiel 1 - UND der explizite Test, dass dies ueber ein
    allgemeines Muster erkannt wird, nicht ueber eine Sonderbehandlung
    speziell fuer § 558 BGB."""
    assert (
        generate_conversation_title("Bitte nenne mir den Paragraphen 558 BGB.")
        == "§ 558 BGB"
    )


def test_paragraph_citation_generalizes_to_other_laws_and_numbers() -> None:
    """Gegenprobe zu obigem Test: keine Hardcodierung fuer § 558 BGB -
    ein voellig anderer Paragraph/ein anderes Gesetz muss genauso
    funktionieren."""
    assert generate_conversation_title("Was steht in § 1 StGB?") == "§ 1 StGB"
    assert (
        generate_conversation_title("Ich habe eine Frage zu § 312 HGB.")
        == "§ 312 HGB"
    )


def test_paragraph_citation_with_absatz() -> None:
    assert (
        generate_conversation_title("Kannst du mir § 558 Abs. 2 BGB erklären?")
        == "§ 558 Abs. 2 BGB"
    )


def test_article_citation() -> None:
    assert generate_conversation_title("Was sagt Art. 14 GG zum Eigentum?") == "Art. 14 GG"


def test_comparison_pattern() -> None:
    assert (
        generate_conversation_title(
            "Erkläre mir den Unterschied zwischen Besitz und Eigentum."
        )
        == "Besitz vs. Eigentum"
    )


def test_summarize_intent_verb_form() -> None:
    assert (
        generate_conversation_title("Bitte fasse den Einkommensteuerbescheid zusammen.")
        == "Einkommensteuerbescheid – Zusammenfassung"
    )


def test_summarize_intent_noun_form() -> None:
    assert (
        generate_conversation_title(
            "Kannst du eine Zusammenfassung des Mietvertrags erstellen?"
        )
        == "Mietvertrags – Zusammenfassung"
    )


def test_drafting_intent_email() -> None:
    assert (
        generate_conversation_title(
            "Schreibe eine E-Mail an den Mandanten wegen der Fristverlängerung."
        )
        == "E-Mail – Fristverlängerung"
    )


def test_drafting_intent_generalizes_to_other_document_types() -> None:
    """Gegenprobe: die Schreibabsicht-Erkennung ist nicht auf E-Mail
    beschraenkt."""
    assert (
        generate_conversation_title(
            "Schreib mir bitte einen Schriftsatz wegen der Betriebskostenabrechnung."
        )
        == "Schriftsatz – Betriebskostenabrechnung"
    )


def test_general_question_without_special_pattern_falls_back_cleanly() -> None:
    title = generate_conversation_title("Wie ist das Wetter heute?")
    assert title == "Wie ist das Wetter heute?"


def test_complex_question_falls_back_to_cleaned_truncated_text() -> None:
    long_message = (
        "Ich habe eine Frage zur Verjährung von Werklohnansprüchen im "
        "Baurecht nach einer umfangreichen Sanierung."
    )
    title = generate_conversation_title(long_message)
    assert len(title) <= 60
    assert title.endswith("…")
    # Die formelhafte Einleitung "Ich habe eine Frage..." darf nicht
    # woertlich im Titel stehen.
    assert not title.lower().startswith("ich habe eine frage")


def test_fallback_strips_common_filler_prefixes() -> None:
    assert generate_conversation_title(
        "Kannst du mir erklären, was eine Kündigungsschutzklage ist?"
    ).startswith("Erklären")


def test_fallback_never_exceeds_sixty_characters() -> None:
    long_message = "Dies ist eine sehr lange Nachricht " * 5
    title = generate_conversation_title(long_message)
    assert len(title) <= 60


def test_empty_message_gets_a_safe_default_title() -> None:
    assert generate_conversation_title("") == "Neue Unterhaltung"
    assert generate_conversation_title("   ") == "Neue Unterhaltung"


def test_statute_citation_takes_precedence_over_fallback_truncation() -> None:
    """Selbst innerhalb einer laengeren Nachricht muss der Gesetzesverweis
    gefunden werden, nicht nur am Nachrichtenanfang (reines String-Slicing
    haette ihn hier verpasst)."""
    message = (
        "Ich bin mir nicht sicher, ob in meinem Fall § 626 BGB einschlägig "
        "ist oder ob eine ordentliche Kündigung ausreicht."
    )
    assert generate_conversation_title(message) == "§ 626 BGB"
