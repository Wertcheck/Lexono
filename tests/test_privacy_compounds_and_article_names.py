"""Ableitungs-Komposita und Nachnamen nach Artikel (13.10.2026). Echter Presidio-/spaCy-Stack, nur synthetische Daten.

Ausgangsfall (installierter Build): "Die Nachbesserungsfrist endet am 30.11.2026." wurde im Dokumentkontext zu
"Die [PERSON_01] endet ..." (NER-Fehlalarm). Leck-Gegenprobe (scripts/bench_article_noun_persons.py): Nachnamen direkt nach
einem Artikel ("die Pfannenstiel hat ...") gingen in 39 von 96 Faellen im Klartext in den Payload."""

from __future__ import annotations

import pytest

from app.ai_providers.local_ai_provider import _normalize_document_text
from app.privacy import presidio_ner as pn
from app.privacy.gateway import ClaudePrivacyGateway

DOCUMENT_PARAGRAPH = (
    "Abnahmeprotokoll zum Werkvertrag. Festgestellt wurden Risse im Estrich und eine undichte Dachrinne. {sentence}"
)


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _payload(gateway, sentence: str, mode: str = "mask"):
    sachverhalt = "Akte: Beispielakte\n[Vertrag] " + _normalize_document_text(DOCUMENT_PARAGRAPH.format(sentence=sentence))
    return gateway.prepare_request(
        purpose="chat_response", sachverhalt=sachverhalt, anwaltliche_anmerkungen="Fasse das Dokument zusammen.",
        review_mode=mode,
    )


# --- Fachwoerter bleiben lesbar -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence, term",
    [
        ("Die Nachbesserungsfrist endet am 30.11.2026.", "Nachbesserungsfrist"),  # Ausgangsfall (PER)
        ("Die Räumungsfrist ist bislang nicht abgelaufen.", "Räumungsfrist"),  # PROPN-Zweitsignal
        ("Die Zustellungsurkunde ist bislang nicht eingegangen.", "Zustellungsurkunde"),  # ORGANISATION
        ("Im Termin wurde der Kostenfestsetzungsbeschluss erörtert.", "Kostenfestsetzungsbeschluss"),
    ],
)
@pytest.mark.parametrize("mode", ["mask", "ask"])
def test_derivational_compounds_stay_readable(gateway, sentence, term, mode) -> None:
    result = _payload(gateway, sentence, mode)
    assert result.allowed, result.reasons  # auch der Restrisiko-Scan blockiert nicht
    assert term in result.payload.anonymisierter_sachverhalt


def test_compound_rule_recognises_only_derivational_compounds() -> None:
    assert pn.is_derivational_compound("Nachbesserungsfrist")
    assert pn.is_derivational_compound("Feuchtigkeitsschaden")
    for word in ("Wessolowski", "Stuckenschneider", "Pfannenstiel", "Hoffmann", "Brinkmoeller", "Thiessen", "Bergmann"):
        assert not pn.is_derivational_compound(word), word
    assert not pn.is_derivational_compound("Ungsfrist")  # Praefix zu kurz


# --- Echte Namen bleiben geschuetzt ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence, name",
    [
        ("Herr Wessolowski hat die Nachbesserungsfrist bestätigt.", "Wessolowski"),
        ("Die Nachbesserungsfrist hat Pfannenstiel gesetzt.", "Pfannenstiel"),
        ("Zeuge  |  Marquardt  |  Hamburg  |  geladen", "Marquardt"),
        ("Die Mail von Thiessen ist eingegangen.", "Thiessen"),
    ],
)
def test_names_next_to_compounds_stay_protected(gateway, sentence, name) -> None:
    result = _payload(gateway, sentence)
    assert result.allowed and name not in result.payload.anonymisierter_sachverhalt


@pytest.mark.parametrize("article", ["die", "der"])
@pytest.mark.parametrize("name", ["Wessolowski", "Pfannenstiel", "Marquardt", "Brinkmoeller", "Eberhaerter", "Thiessen"])
def test_surname_after_article_is_protected(gateway, article, name) -> None:
    """Vorher Klartext-Leck (u. a. Wessolowski, Pfannenstiel, Brinkmoeller, Eberhaerter): alle Zweitsignale verlangten
    ein bares Wort ohne Artikel."""
    for sentence in (
        f"{article.capitalize()} {name} ist bislang nicht erschienen.",
        f"Nach Rücksprache am 12.03.2026 hat {article} {name} die Unterlagen übergeben.",
    ):
        result = _payload(gateway, sentence)
        assert result.allowed and name not in result.payload.anonymisierter_sachverhalt, sentence


@pytest.mark.parametrize("sentence", ["Die Teilabnahme ist erfolgt.", "Der Werkvertrag ist gekündigt.", "Das Gutachten liegt vor."])
def test_article_detector_does_not_flag_known_nouns(sentence) -> None:
    """Nur Woerter, die das Modell in den Namensrahmen ueberwiegend als Person liest (Schwelle wie bei baren Woertern).
    "Teilabnahme" ist ein bekannter, dokumentierter Grenzfall (Modell-Probe 5/5) und wird vorsichtshalber maskiert."""
    found = [span.value for span in pn.detect_article_names(sentence)]
    if "Teilabnahme" in sentence:
        assert found == ["Teilabnahme"]
    else:
        assert found == []


def test_common_noun_surname_in_feminine_singular_is_protected(gateway) -> None:
    """Folgezyklus 13.10.2026 (presidio_ner.detect_feminine_context_names): "die Bergmann" im femininen Singular kann kein
    Gattungswort sein ("die Bergfrau" gibt es nicht) - bisher als bekannte Grenze markiert, jetzt geschuetzt."""
    result = _payload(gateway, "Die Bergmann ist bislang nicht erschienen.")
    assert "Bergmann" not in result.payload.anonymisierter_sachverhalt


# Bekannte Grenze (dokumentiert, NOT VERIFIED geloest): im MASKULINEN Kontext ("der Bergmann") sind Nachnamen, die
# zugleich Berufsbezeichnungen sind, ohne Einfuehrung nicht vom Gattungswort zu unterscheiden; ebenso schwach erkannte
# unbekannte Namen ("Grotelueschen").
@pytest.mark.xfail(reason="maskuliner Artikel + Nachname = Gattungswort (Bergmann/Zimmermann) nicht unterscheidbar", strict=False)
def test_known_limit_common_noun_surname_after_article(gateway) -> None:
    result = _payload(gateway, "Der Bergmann ist bislang nicht erschienen.")
    assert "Bergmann" not in result.payload.anonymisierter_sachverhalt


# --- Unabhaengiger Review (13.10.2026): namensfoermige Bildungen MIT Fugenelement bleiben geschuetzt --------------------
# Erste Fassung (Kopfwort nur "dem Modell bekannt") gab sie frei; jetzt zaehlt nur eine geschlossene Klasse von
# Sach-/Rechtsnomen als Kopfwort.

_NAME_LIKE_WITH_LINK = ["Siedlungsmann", "Lichtungsberger", "Rodungshausen", "Grafschaftsberg", "Stationsberg", "Kreuzungsbach"]


@pytest.mark.parametrize("word", _NAME_LIKE_WITH_LINK)
def test_name_like_words_with_linking_element_are_not_compounds(word) -> None:
    assert not pn.is_derivational_compound(word)


@pytest.mark.parametrize("name", ["Siedlungsmann", "Lichtungsberger", "Stationsberg"])
@pytest.mark.parametrize(
    "template",
    [
        "{N} hat die Unterlagen am 12.03.2026 übergeben.",
        "Die Mail von {N} ist am 12.03.2026 eingegangen.",
        "Nach Rücksprache hat die {N} die Unterlagen übergeben.",
        "Zeuge  |  {N}  |  Hamburg  |  geladen",
    ],
)
def test_name_like_words_with_linking_element_stay_protected(gateway, name, template) -> None:
    result = _payload(gateway, template.format(N=name))
    assert result.allowed and name not in result.payload.anonymisierter_sachverhalt
