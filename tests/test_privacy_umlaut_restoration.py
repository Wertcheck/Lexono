"""Umlaut-wiederhergestellte Erkennungsfassung (11.10.2026): positionsgenaue Rueckabbildung, nie weniger Schutz.

Ausschliesslich synthetische Namen/Texte. Der echte Presidio-/spaCy-Stack wird genutzt (wie tests/test_privacy_presidio_ner.py)."""

from __future__ import annotations

import pytest

import app.privacy.presidio_ner as pn
from app.privacy.detectors import detect_all
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.presidio_ner import _restore_for_analysis, detect_presidio_entities


def _orig_only(text: str) -> set[tuple[str, int, int]]:
    saved = pn._restore_for_analysis
    pn._restore_for_analysis = lambda _t: None  # type: ignore[assignment]
    try:
        return {(s.category, s.start, s.end) for s in detect_presidio_entities(text)}
    finally:
        pn._restore_for_analysis = saved  # type: ignore[assignment]


def _union(text: str) -> set[tuple[str, int, int]]:
    return {(s.category, s.start, s.end) for s in detect_presidio_entities(text)}


# --- Wiederherstellung und Positionsabbildung ---------------------------------------------------------------


def test_unknown_transliterated_words_are_restored_for_analysis_only_and_mapped_back() -> None:
    text = "Die Maengel der Gewaehrleistung sind angezeigt."
    restored = _restore_for_analysis(text)
    assert restored is not None
    analysis, starts, ends = restored
    assert "Mängel" in analysis and "Gewährleistung" in analysis
    assert text == "Die Maengel der Gewaehrleistung sind angezeigt."  # Originaltext unveraendert
    i = analysis.index("Mängel")
    assert text[starts[i] : ends[i + len("Mängel") - 1]] == "Maengel"  # Rueckabbildung auf den Originalausschnitt
    j = analysis.index("Gewährleistung")
    assert text[starts[j] : ends[j + len("Gewährleistung") - 1]] == "Gewaehrleistung"


@pytest.mark.parametrize("text", ["Die Quelle ist bekannt.", "Michael und Raphael kommen aus Israel.", "Nichts zu restaurieren hier."])
def test_known_words_and_names_are_not_changed(text: str) -> None:
    assert _restore_for_analysis(text) is None


def test_mapping_is_monotonic_and_covers_every_character() -> None:
    text = "Herr Dr. Mueller wohnt in der Hauptstrasse 5, die Maengel der Gewaehrleistung folgen. Maengelruege und Flachdachflaeche."
    restored = _restore_for_analysis(text)
    assert restored is not None
    analysis, starts, ends = restored
    assert len(analysis) == len(starts) == len(ends)
    assert all(a < b for a, b in zip(starts, ends))
    assert all(starts[i] <= starts[i + 1] and ends[i] <= ends[i + 1] for i in range(len(starts) - 1))
    assert starts[0] == 0 and ends[-1] == len(text)


def test_inconsistent_mapping_fails_safe_to_the_original_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    # defekte Abbildung (rueckwaerts laufende Positionen) -> keine zweite Fassung, Originalanalyse bleibt
    monkeypatch.setattr(pn, "_restore_word", lambda w: (("K", 5, 6), ("ü", 1, 3), ("n", 0, 1)) if w == "Gewaehrleistung" else None)
    assert _restore_for_analysis("Die Gewaehrleistung folgt.") is None


# --- Vereinigung: nie weniger Schutz ---------------------------------------------------------------------


_SENTENCES = [
    "Herr Olaf Thiessen und Frau Henrike Marquardt haben unterschrieben.",
    "Herr Mueller wohnt in der Hauptstrasse 5 in Hamburg.",
    "Die Kuestenkontor Verwaltungs GmbH beauftragt die Daemmtechnik Mueller KG.",
    "Frau Schroeder und Herr Voelker erscheinen am 30.11.2026 beim Amtsgericht Kiel.",
    "Das Schreiben von Haeussler liegt vor, Ansprechpartner ist Herr Koenig aus Muenchen.",
    "Lieferung an Muehlenweg 12, 24103 Kiel durch die Baeckerei Schulze OHG.",
    "Die Maengel an der Flachdachflaeche wurden von Herrn Kostka geruegt.",
    "Sachverstaendiger ist Dr. Jonas Wiebe aus Lueneburg.",
]


@pytest.mark.parametrize("sentence", _SENTENCES)
def test_union_never_drops_a_finding_of_the_original_analysis(sentence: str) -> None:
    assert _orig_only(sentence) <= _union(sentence)


@pytest.mark.parametrize("sentence", _SENTENCES)
def test_every_mapped_span_value_is_the_exact_original_slice(sentence: str) -> None:
    for span in detect_presidio_entities(sentence):
        assert sentence[span.start : span.end] == span.value


def test_restored_pass_adds_findings_for_transliterated_addresses() -> None:
    text = "Die Lieferung erfolgt an Hauptstrasse 5, 20099 Hamburg vor Ort."
    assert _orig_only(text) <= _union(text)
    assert any("Hauptstrasse" in s.value or "Hamburg" in s.value for s in detect_presidio_entities(text))


_COMMON_ASCII = ["Maengel", "Gewaehrleistung", "Kuendigung", "Verguetung", "Uebergabe", "Pruefung", "Aenderung", "Zusaetze", "Buergschaft", "Moeglichkeit"]


@pytest.mark.parametrize("word", _COMMON_ASCII)
def test_restored_view_does_not_add_false_alarms_on_common_legal_words(word: str) -> None:
    text = f"Die {word} ist nicht ordnungsgemäß erfolgt und wird bis zum 30.11.2026 nachgeholt."
    new = _union(text) - _orig_only(text)
    assert not any(word.lower() in text[a:b].lower() for _c, a, b in new)


def test_overlapping_findings_of_both_views_are_resolved_without_overlap_or_loss() -> None:
    text = "Herr Dr.  Mueller und Frau Schroeder wohnen in der Hauptstrasse 5."
    spans = detect_all(text, ner_detector=detect_presidio_entities)
    ordered = sorted(spans, key=lambda s: s.start)
    assert all(a.end <= b.start for a, b in zip(ordered, ordered[1:]))
    covered = "".join(text[s.start : s.end] for s in ordered)
    assert "Mueller" in covered and "Schroeder" in covered


# --- Gateway: tatsaechlicher Cloud-Payload ------------------------------------------------------------------


def _payload(text: str) -> str:
    result = ClaudePrivacyGateway().prepare_request(
        purpose="formulate_draft", sachverhalt=text, argumentationspunkte=[], quellenverweise=[], stil=None,
        vorlage=None, anwaltliche_anmerkungen="Erstelle ein Schreiben.", known_entities=None, gespraechsverlauf=[],
        skip_general_knowledge_pseudonymization=False,
    )
    assert result.allowed, result.reasons
    return result.payload.anonymisierter_sachverhalt


@pytest.mark.parametrize(
    "text, secrets",
    [
        ("Herr Olaf Thiessen, Gewerbering 6, 24105 Beispielstadt, hat die Maengel an der Anlage geruegt.", ["Thiessen", "Gewerbering"]),
        ("Frau Henrike Marquardt (Geschaeftsfuehrerin der Kuestenkontor Verwaltungs GmbH) bestaetigt die Lieferung.", ["Marquardt", "Kuestenkontor"]),
        ("Gemischt: Herr Müller und Herr Mueller sowie Frau Schröder und Frau Schroeder erscheinen.", ["Müller", "Mueller", "Schröder", "Schroeder"]),
        ("Der Gutachter Dr.\nWiebe und Prof.  Dr. Lindqvist pruefen die Daemmung in der Hauptstrasse 5, 20099 Hamburg.", ["Wiebe", "Lindqvist", "Hauptstrasse"]),
    ],
)
def test_no_original_value_reaches_the_cloud_payload_in_transliterated_mixed_and_wrapped_text(text: str, secrets: list[str]) -> None:
    payload = _payload(text + " Der Betrag von 4.500,00 EUR ist offen.")
    for secret in secrets:
        assert secret not in payload, (secret, payload)
    assert "4.500,00 EUR" in payload  # Geldbetraege bleiben im Klartext
