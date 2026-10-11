"""Zweitsignal der Namenserkennung (12.10.2026): PROPN ohne NER-Etikett, Feldanfang-Kontext, weiche Zeilenumbrueche.

Ausschliesslich synthetische Namen/Texte, echter Presidio-/spaCy-Stack und echtes Gateway (wie
tests/test_privacy_umlaut_restoration.py). Die Positivfaelle sind genau die, die VOR der Aenderung im Klartext im
Cloud-Payload standen (scripts/bench_name_detection.py: 26,9 % -> 7,5 % Klartext-Lecks bei 1100 Faellen)."""

from __future__ import annotations

import pytest

import app.privacy.presidio_ner as pn
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.presidio_ner import detect_presidio_entities, detect_unlabelled_proper_nouns


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _prepare(gateway: ClaudePrivacyGateway, sachverhalt: str, anmerkungen: str = "Erstelle ein Schreiben."):
    return gateway.prepare_request(
        purpose="formulate_draft", sachverhalt=sachverhalt, argumentationspunkte=[], quellenverweise=[], stil=None, vorlage=None,
        anwaltliche_anmerkungen=anmerkungen, known_entities=None, gespraechsverlauf=[], skip_general_knowledge_pseudonymization=False,
    )


def _outgoing(result) -> str:
    assert result.allowed, result.reasons  # Name erkannt -> Anfrage laeuft pseudonymisiert durch
    return result.payload.anonymisierter_sachverhalt + "\n" + (result.payload.anonymisierte_anwaltliche_anmerkungen or "")


# Namen, die zugleich Gattungswoerter sind und die das NER-Modell ohne Anrede/Titel nicht etikettiert.
AMBIGUOUS_SURNAMES = ["Fischer", "Weber", "Koch", "Wolf", "Lange", "Braun"]

SACHVERHALT_CONTEXTS = [
    "Dazu erklärt {N}, die Anlage sei mangelhaft.",  # nach Verb, Satzmitte
    "Die Anlage ist defekt. {N} hat das bestätigt.",  # Satzanfang
    "Die Unterlagen wurden von {N} geprüft und freigegeben.",  # Agens im Passiv
    "Zeugen sind {N} und Olaf Thiessen.",  # Aufzaehlung
]


@pytest.mark.parametrize("name", AMBIGUOUS_SURNAMES)
@pytest.mark.parametrize("template", SACHVERHALT_CONTEXTS)
def test_ambiguous_surnames_never_reach_the_cloud_payload_in_plaintext(gateway, name, template) -> None:
    result = _prepare(gateway, "Akte: Beispielakte\n[Vertrag] " + template.format(N=name))
    assert name not in _outgoing(result)


@pytest.mark.parametrize("name", AMBIGUOUS_SURNAMES + ["Marquardt", "Wiebe"])
def test_bare_name_at_start_of_the_instruction_field_is_masked(gateway, name) -> None:
    """Feldanfang: der Marker wurde zu Leerzeichen neutralisiert, das Modell verlor den Satzkontext."""
    result = _prepare(gateway, "Akte: Beispielakte", anmerkungen=f"{name} hat angerufen und bittet um Rückruf.")
    assert name not in _outgoing(result)


# Restrisiko (NICHT VERIFIZIERT geloest): hinter einem Umbruch nach "durch" tagged das Modell manche Namen (z. B.
# "Weber") weiterhin als Substantiv ohne Eigennamen-Tag; pdf_umbruch-Lecks im Benchmark: 12,7 %.
@pytest.mark.parametrize("name", ["Fischer", "Koch"])
def test_name_after_soft_line_break_of_a_pdf_is_masked(gateway, name) -> None:
    result = _prepare(gateway, f"Akte: Beispielakte\nDie Unterlagen wurden am 12.03.2026 durch\n{name} geprüft und übergeben.")
    assert name not in _outgoing(result)


def test_table_cell_name_is_masked(gateway) -> None:
    result = _prepare(gateway, "Akte: Beispielakte\nName: Fischer  Funktion: Zeuge  Datum: 12.03.2026")
    assert "Fischer" not in _outgoing(result)


# --- Detektor selbst -------------------------------------------------------------------------------------------------


def test_unlabelled_proper_noun_span_points_at_the_original_text() -> None:
    text = "Dazu erklärt Fischer, die Anlage sei mangelhaft."
    spans = detect_unlabelled_proper_nouns(text)
    assert [(s.category, s.value) for s in spans] == [("person", "Fischer")]
    assert text[spans[0].start : spans[0].end] == "Fischer"


def test_merged_detection_never_returns_overlapping_duplicates() -> None:
    text = "Herr Marquardt und Dr. Wiebe erklären, Fischer habe unterschrieben."
    spans = sorted(detect_presidio_entities(text), key=lambda s: s.start)
    assert all(a.end <= b.start for a, b in zip(spans, spans[1:]))
    assert {"Marquardt", "Wiebe", "Fischer"} <= {w for s in spans for w in s.value.replace("Dr. ", "").split()}


def test_neutralization_keeps_length_and_all_original_offsets() -> None:
    text = "Vorspann.@@GATEWAY_ANMERKUNGEN@@\nFischer hat angerufen, durch\nWeber [PERSON_01] bestätigt."
    neutral = pn._neutralize_internal_tokens(text)
    assert len(neutral) == len(text)
    assert neutral.index("Fischer") == text.index("Fischer") and neutral.index("Weber") == text.index("Weber")
    assert "@@" not in neutral and "[PERSON_01]" not in neutral


# --- Gegenproben: keine neuen Fehlalarme ---------------------------------------------------------------------------


NEGATIVE_TEXTS = [
    "Der Kläger macht Gewährleistungsansprüche nach § 634 BGB geltend. Die VOB/B wurde nicht wirksam einbezogen.",
    "Die Mängelrüge ging per E-Mail ein. Der Auftragnehmer verweigerte die Nacherfüllung. Die Frist lief ab.",
    "Schadensersatz ist zu leisten. Verzug liegt vor. Kündigung ist wirksam. Zahlung erfolgte nicht. Abnahme wurde verweigert.",
    "Der Vermieter beruft sich auf Eigenbedarf nach § 573 BGB. Die Kaution wurde nicht zurückgezahlt.",
]


@pytest.mark.parametrize("text", NEGATIVE_TEXTS)
def test_ordinary_legal_vocabulary_is_not_masked_by_the_second_signal(text) -> None:
    assert detect_unlabelled_proper_nouns(text) == []


@pytest.mark.parametrize(
    "instruction",
    [
        "Erstelle ein Schreiben an die Gegenseite.", "Fasse das Dokument zusammen.", "Prüfe die Fristen und nenne Risiken.",
        "Überarbeite den Entwurf und kürze ihn.", "Formuliere eine Mahnung mit Fristsetzung.", "Berücksichtige § 634 BGB und die VOB/B.",
    ],
)
def test_lawyer_instructions_pass_the_gateway_unchanged(gateway, instruction) -> None:
    result = _prepare(gateway, "Akte: Beispielakte", anmerkungen=instruction)
    assert result.allowed
    assert result.payload.anonymisierte_anwaltliche_anmerkungen == instruction


def test_conversation_role_label_is_not_treated_as_a_name(gateway) -> None:
    result = gateway.prepare_request(
        purpose="formulate_draft", sachverhalt="Frage", argumentationspunkte=[], quellenverweise=[], stil=None, vorlage=None,
        anwaltliche_anmerkungen="Und danach?", known_entities=None,
        gespraechsverlauf=["Anwalt: Wie lange?", "Assistent: Ihr Mandant Klaus Andersen hat noch bis zum 15. April Zeit."],
        skip_general_knowledge_pseudonymization=False,
    )
    assert result.allowed
    history = result.payload.anonymisierter_gespraechsverlauf
    assert history[0].startswith("Anwalt:") and history[1].startswith("Assistent:")
    assert "Andersen" not in "".join(history)


# --- Filter-Korrektur (Folgelauf 12.10.2026): vom NER erkannte Namen duerfen nicht als "Imperativ" verworfen werden -----
# Ursache der Restlecks: `drop_sentence_initial_imperatives` verwarf jeden EINWORT-Personentreffer am Satz-/Zeilen-/Label-Anfang,
# der nicht als PROPN getaggt war - auch echte, vom NER als PER erkannte Namen ("Wessolowski hat angerufen.", "Name: X  Funktion:",
# "durch" + weicher Umbruch + Name). Neu: Subjekt eines nicht-infiniten Verbs, reiner Zellenwert und weicher Umbruch sind kein
# Imperativ-Satzanfang.
RARE_NOUN_TAGGED = ["Wessolowski", "Brinkmoeller", "Stuckenschneider", "Eberhaerter"]


@pytest.mark.parametrize("name", RARE_NOUN_TAGGED)
def test_ner_person_as_subject_at_field_start_is_not_dropped_as_imperative(gateway, name) -> None:
    result = _prepare(gateway, "Akte: Beispielakte", anmerkungen=f"{name} hat angerufen und bittet um Rückruf.")
    assert name not in _outgoing(result)


# Restrisiko: "Stuckenschneider" wird nach weichem Umbruch/in Zellen vom Tagger als ADJ/ADV/NOUN ohne PER-Etikett gelesen.
@pytest.mark.parametrize("name", ["Wessolowski", "Brinkmoeller", "Eberhaerter"])
def test_ner_person_in_form_cell_and_after_soft_wrap_is_masked(gateway, name) -> None:
    cell = _prepare(gateway, f"Akte: Beispielakte\nName: {name}  Funktion: Zeuge  Datum: 12.03.2026")
    wrap = _prepare(gateway, f"Akte: Beispielakte\nDie Unterlagen wurden am 12.03.2026 durch\n{name} geprüft und übergeben.")
    assert name not in _outgoing(cell)
    assert name not in _outgoing(wrap)


@pytest.mark.parametrize(
    "instruction",
    [
        "Überarbeite das Schreiben.", "Fasse das Dokument zusammen.", "Kürze den Entwurf.", "Ergänze den Absatz zur Verjährung.",
        "Streiche den dritten Satz.", "Verfasse eine Klageerwiderung.", "Gewährleistung und Verjährung: bitte erläutern.",
        "Kündigung wegen Eigenbedarf: Voraussetzungen nennen.",
    ],
)
def test_imperatives_and_topic_headings_still_pass_unchanged_after_the_filter_fix(gateway, instruction) -> None:
    result = _prepare(gateway, "Akte: Beispielakte", anmerkungen=instruction)
    assert result.allowed
    assert result.payload.anonymisierte_anwaltliche_anmerkungen == instruction


def test_filter_unit_keeps_subject_but_drops_imperative() -> None:
    from app.privacy.detectors import DetectedSpan
    from app.privacy.presidio_ner import drop_sentence_initial_imperatives

    subject = "Wessolowski hat angerufen."
    imperative = "Überarbeite das Schreiben."
    kept = drop_sentence_initial_imperatives(subject, [DetectedSpan(category="person", start=0, end=11, value="Wessolowski")])
    dropped = drop_sentence_initial_imperatives(imperative, [DetectedSpan(category="person", start=0, end=11, value="Überarbeite")])
    assert [s.value for s in kept] == ["Wessolowski"]
    assert dropped == []


# Dokumentierte Grenze (NICHT geloest): Namen, die als bares Gattungswort getaggt werden und vom NER nicht als PER erkannt werden.
# Gemessen: eine Regel "bares Substantiv als Subjekt" erfasst 8/55 Namen, aber 141/153 Rechtsbegriffe -> verworfen (zu viele Fehlalarme).
@pytest.mark.xfail(reason="Richter/Fuerst: Gattungswort ohne unabhaengiges Signal, Regel waere nicht fehlalarmarm", strict=False)
@pytest.mark.parametrize("name", ["Richter", "Fuerst"])
def test_known_limit_name_that_is_also_a_common_noun(gateway, name) -> None:
    result = _prepare(gateway, f"Akte: Beispielakte\nDazu erklärt {name}, die Anlage sei mangelhaft.")
    assert name not in _outgoing(result)
