"""Tests fuer app/privacy/security_check.py.

Kernanforderung (Architekturvorgabe, wörtlich): "Bei einem nicht
eindeutigen Ergebnis: KEIN API-AUFRUF." - jeder Test, der einen Grund zum
Blockieren simuliert, muss `passed=False` liefern."""

import pytest

from app.privacy.detectors import DetectedSpan
from app.privacy.gateway_schema import ClaudeRequestPayload
from app.privacy.pseudonymizer import PseudonymMapping, Pseudonymizer
from app.privacy.security_check import (
    ALLOWED_PURPOSES,
    SecurityCheckService,
    check_payload_placeholder_integrity,
    check_response_placeholder_integrity,
)


def _clean_pseudonymized_text() -> tuple[str, list[PseudonymMapping]]:
    p = Pseudonymizer()
    text = "Sehr geehrter Herr Max Mustermann, Az.: 123/24, Frist 15.03.2027."
    return p.pseudonymize(text, known_entities={"mandant": ["Max Mustermann"]})


def test_clean_pseudonymized_text_with_allowed_purpose_passes() -> None:
    text, mappings = _clean_pseudonymized_text()
    checker = SecurityCheckService()

    result = checker.check(text, mappings, purpose="formulate_draft")

    assert result.passed is True
    assert result.reasons == []


def test_disallowed_purpose_blocks_the_call() -> None:
    """Punkt 7: Ist der API-Aufruf für diese Aufgabe zulässig?"""
    text, mappings = _clean_pseudonymized_text()
    checker = SecurityCheckService()

    result = checker.check(text, mappings, purpose="analyze_full_file")

    assert result.passed is False
    assert any("Zweck" in r for r in result.reasons)


def test_all_allowed_purposes_are_text_production_only() -> None:
    """Stichprobenartige Absicherung: die Allowlist darf keine
    Analyse-/Zuordnungs-/Rechercheaufgaben enthalten (Vorgabe Punkt 2)."""
    forbidden_keywords = ["assign", "match", "research", "decide", "send", "analyze"]
    for purpose in ALLOWED_PURPOSES:
        assert not any(keyword in purpose for keyword in forbidden_keywords)


def test_residual_pii_in_supposedly_pseudonymized_text_blocks_the_call() -> None:
    """Punkt 2/3/4: Enthält der Text noch personenbezogene/vertrauliche
    Daten? Simuliert eine unvollständige Pseudonymisierung (E-Mail
    vergessen)."""
    text, mappings = _clean_pseudonymized_text()
    leaky_text = text + " Kontakt: max@example.test"
    checker = SecurityCheckService()

    result = checker.check(leaky_text, mappings, purpose="formulate_draft")

    assert result.passed is False
    assert any("email" in r for r in result.reasons)


def test_missing_placeholder_in_text_blocks_the_call() -> None:
    """Punkt 5: Wurden alle bekannten Platzhalter korrekt gesetzt?
    Simuliert eine Mapping-Text-Inkonsistenz."""
    _, mappings = _clean_pseudonymized_text()
    checker = SecurityCheckService()

    # Text enthaelt die im Mapping erwarteten Platzhalter nicht.
    result = checker.check("Ein völlig anderer Text.", mappings, purpose="formulate_draft")

    assert result.passed is False
    assert any("Platzhalter" in r for r in result.reasons)


def test_possible_unrecognized_name_blocks_the_call() -> None:
    """Punkt 6: Gibt es möglicherweise nicht erkannte personenbezogene
    Daten? Ein echter, nicht als bekannte Entität übergebener Name muss
    zum Blockieren führen."""
    checker = SecurityCheckService()

    result = checker.check(
        "Bitte informieren Sie auch Herrn Peter Müller.", [], purpose="formulate_draft"
    )

    assert result.passed is False
    assert any("Peter Müller" in r for r in result.reasons)


def test_legal_heading_does_not_trigger_false_positive_when_pos_tagger_is_wired() -> None:
    """ECHTER FUND (realer Abnahme-Test, 13.09.): "Salvatorische Klausel"
    ist eine ganz gewoehnliche Rechtsdokument-Ueberschrift (Adjektiv +
    Substantiv), keine zwei Namensbestandteile - blockierte real JEDE
    Chat-Nachricht in einer Unterhaltung mit einem angehaengten,
    voellig gewoehnlichen Vertragsdokument. Mit `pos_tagger` (echte
    Produktivkonfiguration, siehe ClaudePrivacyGateway) korrekt NICHT
    mehr blockiert - ohne (alte Stopwortliste allein) waere dieser
    KONKRETE Fall nicht abgedeckt (bewusst NICHT in der Stopwortliste,
    um genau diesen Unterschied zu beweisen)."""
    from app.privacy.presidio_ner import get_pos_tags

    checker = SecurityCheckService(pos_tagger=get_pos_tags)

    result = checker.check(
        "Salvatorische Klausel. Sollte eine Bestimmung unwirksam sein, "
        "bleibt der Rest des Vertrags davon unberührt.",
        [],
        purpose="chat_response",
    )

    assert result.passed is True


def test_real_name_still_blocks_even_with_pos_tagger_wired() -> None:
    """Gegenprobe: der POS-Tag-Filter darf echte Namen nicht durchlassen -
    "Peter Müller" bleibt PROPN+PROPN und damit weiterhin ein Fund."""
    from app.privacy.presidio_ner import get_pos_tags

    checker = SecurityCheckService(pos_tagger=get_pos_tags)

    result = checker.check(
        "Bitte informieren Sie auch Herrn Peter Müller.", [], purpose="chat_response"
    )

    assert result.passed is False
    assert any("Peter Müller" in r for r in result.reasons)


def test_synthetic_test_document_title_does_not_trigger_false_positive() -> None:
    """ECHTER FUND (realer Abnahme-Test, 13.09.): "Synthetisches
    Testdokument" ist ein Dokumenttitel (Adjektiv + Substantiv), keine
    zwei Namensbestandteile - blockierte real jede Chat-Nachricht in
    Unterhaltungen, die dieses Testdokument angehängt hatten."""
    checker = SecurityCheckService()

    result = checker.check(
        "Synthetisches Testdokument für Lexono. 1. Sachverhalt.",
        [],
        purpose="formulate_draft",
    )

    assert result.passed is True


def test_common_german_formal_letter_does_not_trigger_false_positive() -> None:
    """Regressionstest fuer den gefundenen Bug: normale deutsche
    Kanzleibrief-Formulierungen (Grossschreibung von Substantiven/
    Hoeflichkeitsform) duerfen NICHT faelschlich als unbekannter Name
    gewertet werden."""
    sample = (
        "Sehr geehrte Damen und Herren,\n"
        "vielen Dank für Ihr Schreiben. Wir haben die Unterlagen geprüft "
        "und teilen Ihnen mit, dass der Einspruch gegen den Steuerbescheid "
        "form- und fristgerecht eingelegt wurde. Die Finanzbehörde hat "
        "eine Frist gesetzt. Mit freundlichen Grüßen"
    )
    checker = SecurityCheckService()

    result = checker.check(sample, [], purpose="formulate_draft")

    assert result.passed is True
    assert result.reasons == []


def test_multiple_problems_all_reported() -> None:
    """Mehrere gleichzeitige Probleme muessen alle in reasons auftauchen,
    nicht nur der erste gefundene."""
    checker = SecurityCheckService()

    result = checker.check(
        "Kontakt: max@example.test, mit Peter Müller besprochen.",
        [],
        purpose="analyze_full_file",
    )

    assert result.passed is False
    assert len(result.reasons) >= 3  # Zweck + Email-Leak + unbekannter Name


def test_real_pipeline_output_passes_when_correctly_pseudonymized() -> None:
    """Integrationstest: der reale Pseudonymizer-Output (nicht
    handgebaut) muss den Security-Check normalerweise bestehen - sonst
    waere die Pipeline in der Praxis dauerhaft blockiert."""
    p = Pseudonymizer()
    text = (
        "Sehr geehrter Herr Max Mustermann, bezugnehmend auf Ihr Schreiben "
        "vom 01.02.2027 (Az.: 55/27) teilen wir mit, dass die Frist am "
        "15.03.2027 endet. Mit freundlichen Grüßen"
    )
    pseudo_text, mappings = p.pseudonymize(
        text, known_entities={"mandant": ["Max Mustermann"]}
    )
    checker = SecurityCheckService()

    result = checker.check(pseudo_text, mappings, purpose="formulate_draft")

    assert result.passed is True, result.reasons


# --- NER-Injektion beim Restrisiko-Scan (Fake statt echtem Presidio - siehe
# test_privacy_presidio_ner.py fuer den echten Stack) ---


def _fake_ner_detector(text: str) -> list[DetectedSpan]:
    idx = text.find("Julia Neumann")
    if idx == -1:
        return []
    return [DetectedSpan(category="person", start=idx, end=idx + len("Julia Neumann"), value="Julia Neumann")]


def test_ner_detector_catches_residual_pii_the_regex_heuristic_missed() -> None:
    """Ein Name, den die Grossschreibungs-Heuristik zufaellig NICHT als
    Kandidat listet (hier simuliert durch einen Fake, der gezielt einen
    Namen findet), muss ueber den injizierten ner_detector trotzdem zum
    Blockieren fuehren."""
    checker = SecurityCheckService(ner_detector=_fake_ner_detector)

    result = checker.check("Kontakt bitte an Julia Neumann.", [], purpose="formulate_draft")

    assert result.passed is False


# --- check_response_placeholder_integrity: deterministische Pruefung der
# EINGEHENDEN Claude-Antwort vor der Rekonstruktion (Increment "lokale KI
# als Datenschutz-/Qualitaetsschicht") - "Ein LLM darf niemals eine
# deterministische Privacy-Regel ueberschreiben." ---


def test_response_missing_placeholder_fails() -> None:
    """Fall 1: die Claude-Antwort vergisst einen erwarteten Platzhalter."""
    mappings = [
        PseudonymMapping(placeholder="[MANDANT_01]", category="person", original_value="Erika Mustermann")
    ]

    reasons = check_response_placeholder_integrity(
        "Vielen Dank fuer Ihre Nachricht.", mappings
    )

    assert reasons != []
    assert any("MANDANT_01" in r for r in reasons)


def test_response_with_altered_placeholder_token_fails() -> None:
    """Fall 2: ein erfundener/veraenderter Platzhalter-Token (Struktur-/
    ID-Manipulation), der so nicht im Mapping steht."""
    mappings = [
        PseudonymMapping(placeholder="[MANDANT_01]", category="person", original_value="Erika Mustermann")
    ]
    text = "Sehr geehrte Frau [MANDANT_99], vielen Dank fuer Ihre Nachricht."

    reasons = check_response_placeholder_integrity(text, mappings)

    assert reasons != []
    assert any("MANDANT_99" in r for r in reasons)


def test_response_containing_original_value_fails() -> None:
    """Fall 3: der pseudonymisierte Originalwert taucht zusaetzlich zum
    korrekten Platzhalter im Klartext auf - deterministisch erkennbarer
    Datenschutzverstoss."""
    mappings = [
        PseudonymMapping(placeholder="[MANDANT_01]", category="person", original_value="Erika Mustermann")
    ]
    text = (
        "Sehr geehrte Frau [MANDANT_01], wir haben mit Erika Mustermann "
        "bereits telefoniert."
    )

    reasons = check_response_placeholder_integrity(text, mappings)

    assert reasons != []
    assert any("Datenschutzverstoss" in r for r in reasons)


def test_response_with_correct_placeholders_passes() -> None:
    """Fall 4: unauffaellige Antwort mit ausschliesslich korrekten,
    unveraenderten Platzhaltern -> keine Beanstandung."""
    mappings = [
        PseudonymMapping(placeholder="[MANDANT_01]", category="person", original_value="Erika Mustermann")
    ]
    text = "Sehr geehrte Frau [MANDANT_01], vielen Dank fuer Ihre Nachricht."

    reasons = check_response_placeholder_integrity(text, mappings)

    assert reasons == []


# --- require_full_coverage (15.09., CHAT-01): Vollstaendigkeitsforderung
# nur fuer Brief-/Entwurfstext, nicht fuer freie Chatantworten. Die beiden
# ANDEREN Stufe-1-Pruefungen (Manipulation, Original-Leck) bleiben in
# BEIDEN Modi Pflicht. ---


def test_missing_placeholder_is_ignored_when_full_coverage_not_required() -> None:
    """Der reproduzierte Kernfall: eine natuerliche Chat-Begruessung, die
    keinen einzigen Platzhalter erwaehnt, darf nicht mehr blockiert
    werden, wenn `require_full_coverage=False`."""
    mappings = [
        PseudonymMapping(placeholder="[MANDANT_01]", category="person", original_value="Erika Mustermann")
    ]

    reasons = check_response_placeholder_integrity(
        "Guten Tag, wie kann ich Ihnen helfen?", mappings, require_full_coverage=False
    )

    assert reasons == []


def test_missing_placeholder_still_fails_by_default() -> None:
    """Default bleibt `True` - unveraendertes Verhalten fuer jeden
    bestehenden Aufrufer, der den neuen Parameter nicht setzt."""
    mappings = [
        PseudonymMapping(placeholder="[MANDANT_01]", category="person", original_value="Erika Mustermann")
    ]

    reasons = check_response_placeholder_integrity(
        "Vielen Dank fuer Ihre Nachricht.", mappings
    )

    assert reasons != []


def test_altered_placeholder_token_still_fails_even_without_full_coverage() -> None:
    """Die Manipulations-Pruefung ist KEINE Vollstaendigkeitsforderung und
    muss deshalb unabhaengig vom neuen Parameter immer greifen."""
    mappings = [
        PseudonymMapping(placeholder="[MANDANT_01]", category="person", original_value="Erika Mustermann")
    ]
    text = "Sehr geehrte Frau [MANDANT_99], vielen Dank fuer Ihre Nachricht."

    reasons = check_response_placeholder_integrity(
        text, mappings, require_full_coverage=False
    )

    assert reasons != []
    assert any("MANDANT_99" in r for r in reasons)


def test_original_value_leak_still_fails_even_without_full_coverage() -> None:
    """Dieselbe Garantie fuer die zweite tatsaechlich schuetzende Pruefung:
    ein geleakter Originalwert darf durch `require_full_coverage=False`
    NICHT unentdeckt bleiben."""
    mappings = [
        PseudonymMapping(placeholder="[MANDANT_01]", category="person", original_value="Erika Mustermann")
    ]
    text = "Guten Tag, wir haben bereits mit Erika Mustermann telefoniert."

    reasons = check_response_placeholder_integrity(
        text, mappings, require_full_coverage=False
    )

    assert reasons != []
    assert any("Datenschutzverstoss" in r for r in reasons)


# --- ECHTER FUND (05.10., Owner-Direktive "Vollstaendiger UX- und
# Workflow-Audit", synthetisch reproduziert): der bisherige Originalwert-
# Leck-Check war ein NAIVER Teilstring-Vergleich (`original_value in
# text`) statt einer Wortgrenzen-Pruefung - ein Mandant namens "Fischer"
# blockierte dadurch JEDE Antwort, die das voellig unabhaengige Wort
# "Fischereirecht" enthielt (Teilstring-Treffer trotz fehlendem
# inhaltlichen Bezug). Betraf sowohl die eingehende Antwortpruefung als
# auch das ausgehende Final Payload Gate (beide rufen dieselbe
# `_contains_original_value_leak`-Hilfsfunktion auf). ---


def test_compound_word_containing_pseudonymized_surname_is_not_a_false_positive() -> None:
    """Der konkrete, reproduzierte Fehlalarm: "Fischer" (Mandantenname)
    taucht nur als gebundener Wortbestandteil von "Fischereirecht" auf -
    KEIN echter Leck, darf NICHT mehr blockieren."""
    mappings = [
        PseudonymMapping(placeholder="[MANDANT_01]", category="mandant", original_value="Fischer")
    ]
    text = (
        "Fuer die Einspruchsbegruendung ist relevant, ob das "
        "Fischereirecht hier einschlaegig ist."
    )

    reasons = check_response_placeholder_integrity(text, mappings, require_full_coverage=False)

    assert reasons == []


def test_standalone_surname_is_still_detected_as_a_leak_despite_word_boundary_fix() -> None:
    """Gegenprobe zum Fix oben: ein ECHTER, eigenstaendiger Leck-Fall
    desselben Namens muss weiterhin zuverlaessig erkannt werden - die
    Wortgrenzen-Pruefung darf die eigentliche Schutzfunktion nicht
    schwaechen."""
    mappings = [
        PseudonymMapping(placeholder="[MANDANT_01]", category="mandant", original_value="Fischer")
    ]
    text = "Sehr geehrter Herr Fischer, hiermit bestaetigen wir den Eingang Ihres Schreibens."

    reasons = check_response_placeholder_integrity(text, mappings, require_full_coverage=False)

    assert reasons != []
    assert any("Datenschutzverstoss" in r for r in reasons)


def test_outgoing_payload_gate_compound_word_is_not_a_false_positive() -> None:
    """Dieselbe Garantie fuer das AUSGEHENDE Final Payload Gate - eine
    normale anwaltliche Anmerkung ueber "Fischereirecht" darf nicht schon
    VOR dem Claude-Aufruf blockiert werden, nur weil ein Mandant
    "Fischer" heisst."""
    mappings = [
        PseudonymMapping(placeholder="[MANDANT_01]", category="mandant", original_value="Fischer")
    ]
    payload = ClaudeRequestPayload(
        schreibauftrag="chat_response",
        anonymisierter_sachverhalt="Akte: [MANDANT_01]",
        anonymisierte_anwaltliche_anmerkungen="Ist das Fischereirecht hier relevant?",
    )

    reasons = check_payload_placeholder_integrity(payload, mappings)

    assert reasons == []


def test_outgoing_payload_gate_compound_word_fix_does_not_hide_a_real_leak() -> None:
    mappings = [
        PseudonymMapping(placeholder="[MANDANT_01]", category="mandant", original_value="Fischer")
    ]
    payload = ClaudeRequestPayload(
        schreibauftrag="chat_response",
        anonymisierter_sachverhalt="Akte: [MANDANT_01]",
        anonymisierte_anwaltliche_anmerkungen="Bitte an Herrn Fischer persoenlich adressieren.",
    )

    reasons = check_payload_placeholder_integrity(payload, mappings)

    assert reasons != []
    assert any("Datenschutzverstoss" in r for r in reasons)


def test_outgoing_payload_gate_keeps_full_coverage_requirement() -> None:
    """Der AUSGEHENDE Payload-Gate-Aufruf (`check_payload_placeholder_integrity`)
    ruft dieselbe Funktion ohne den neuen Parameter auf und muss deshalb
    UNVERAENDERT die volle Abdeckung verlangen - CHAT-01 betrifft
    ausschliesslich die eingehende Antwortpruefung, niemals das, was an
    Claude gesendet wird."""
    from app.privacy.security_check import check_payload_placeholder_integrity
    from app.privacy.gateway_schema import ClaudeRequestPayload

    mappings = [
        PseudonymMapping(placeholder="[MANDANT_01]", category="person", original_value="Erika Mustermann")
    ]
    payload = ClaudeRequestPayload(
        schreibauftrag="chat_response",
        anonymisierter_sachverhalt="Guten Tag.",
        anonymisierte_argumentationspunkte=[],
        anonymisierte_quellenverweise=[],
        anonymisierte_anwaltliche_anmerkungen=None,
        gewuenschter_stil=None,
        schreibvorlage=None,
    )

    reasons = check_payload_placeholder_integrity(payload, mappings)

    assert reasons != []
    assert any("MANDANT_01" in r for r in reasons)


def test_without_ner_detector_behaves_exactly_as_before() -> None:
    checker = SecurityCheckService()

    result = checker.check(
        "Sehr geehrter Herr Max Mustermann, Az.: 123/24, Frist 15.03.2027.",
        [],
        purpose="formulate_draft",
    )

    # Unveraendertes Verhalten: die Grossschreibungs-Heuristik (nicht der
    # NER-Detector) entscheidet weiterhin allein - "Max Mustermann" wird
    # hier (wie im bestehenden Test test_possible_unrecognized_name_blocks_
    # the_call) als moeglicher unbekannter Name erkannt.
    assert result.passed is False


# --- check_payload_placeholder_integrity: FINAL PAYLOAD GATE, prueft die
# tatsaechlich fertig aufgeteilte ClaudeRequestPayload (nicht nur den
# zusammengefuehrten Text davor - siehe gateway.py::prepare_request). ---


def test_payload_with_correct_placeholders_passes() -> None:
    mappings = [
        PseudonymMapping(placeholder="[MANDANT_01]", category="person", original_value="Erika Mustermann")
    ]
    payload = ClaudeRequestPayload(
        schreibauftrag="formulate_draft",
        anonymisierter_sachverhalt="Mandant [MANDANT_01] bittet um Rueckmeldung.",
        anonymisierte_argumentationspunkte=["[MANDANT_01] handelte fristgerecht."],
    )

    reasons = check_payload_placeholder_integrity(payload, mappings)

    assert reasons == []


def test_payload_missing_placeholder_in_any_field_fails() -> None:
    """Der Platzhalter fehlt im Sachverhalt UND in den Argumentationspunkten -
    genau die Fehlerklasse, die ein Bug im Aufteilungsschritt verursachen
    koennte, ohne dass der vorgelagerte Security-Check (der auf dem noch
    NICHT aufgeteilten Text laeuft) das haette bemerken koennen."""
    mappings = [
        PseudonymMapping(placeholder="[MANDANT_01]", category="person", original_value="Erika Mustermann")
    ]
    payload = ClaudeRequestPayload(
        schreibauftrag="formulate_draft",
        anonymisierter_sachverhalt="Unser Mandant bittet um Rueckmeldung.",
    )

    reasons = check_payload_placeholder_integrity(payload, mappings)

    assert reasons != []
    assert any("MANDANT_01" in r for r in reasons)


def test_payload_with_original_value_leak_fails() -> None:
    mappings = [
        PseudonymMapping(placeholder="[MANDANT_01]", category="person", original_value="Erika Mustermann")
    ]
    payload = ClaudeRequestPayload(
        schreibauftrag="formulate_draft",
        anonymisierter_sachverhalt="Mandant [MANDANT_01], alias Erika Mustermann, bittet um Rueckmeldung.",
    )

    reasons = check_payload_placeholder_integrity(payload, mappings)

    assert reasons != []
    assert any("Datenschutzverstoss" in r for r in reasons)


# --- ECHTER FUND (14.09., Overnight-Direktive §8/§9): realer Security-/
# Privacy-Regressionsfall "Frau Müller". Root Cause (per script-Reproduktion
# in dieser Sitzung bestaetigt): WEDER Presidio/spaCy-NER (ein blosser
# Nachname ohne Vornamen wird in gewoehnlicher Satzmitte nicht zuverlaessig
# als PERSON erkannt) NOCH die bisherige `_find_possible_unrecognized_names`-
# Heuristik (verlangte zwingend ZWEI nicht ausgeschlossene grossgeschriebene
# Woerter - "Frau"/"Herr"/"Herrn" waren aber bewusst ausgeschlossen, wodurch
# ein blosses "Anrede/Rolle + Nachname"-Paar nie geprueft wurde) erkannten
# diesen sehr haeufigen Kanzleitext-Fall. Fix: `_ROLE_OR_TITLE_PREFIX_WORDS`
# loest jetzt gezielt eine Pruefung des unmittelbar folgenden Worts als
# moeglichen Nachnamen aus, auch OHNE Vornamen. Diese Tests sind der Kern
# der in §9 geforderten Privacy Test Matrix fuer Einzelpersonen.
def test_frau_nachname_without_first_name_blocks_the_call() -> None:
    checker = SecurityCheckService()

    result = checker.check(
        "Frau Müller kam gestern vorbei.", [], purpose="chat_response"
    )

    assert result.passed is False
    assert any("Frau Müller" in r for r in result.reasons)


def test_herr_nachname_without_first_name_blocks_the_call() -> None:
    checker = SecurityCheckService()

    result = checker.check(
        "Herr Müller kam gestern vorbei.", [], purpose="chat_response"
    )

    assert result.passed is False
    assert any("Herr Müller" in r for r in result.reasons)


def test_role_word_plus_nachname_blocks_the_call() -> None:
    """Deckt die in §9 explizit genannten Rollen-/Kontextwoerter ab
    (Mandantin/Klägerin/Beklagter), jeweils direkt gefolgt von einem
    Nachnamen ohne Vornamen."""
    checker = SecurityCheckService()

    cases = [
        "Die Mandantin Müller hat angerufen.",
        "Klägerin Müller ist zu einem Termin erschienen.",
        "Der Beklagte Müller wurde geladen.",
    ]
    for text in cases:
        result = checker.check(text, [], purpose="chat_response")
        assert result.passed is False, text
        assert any("Müller" in r for r in result.reasons), text


def test_role_word_alone_without_any_name_does_not_false_positive() -> None:
    """Gegenprobe: ein reines Rollenwort OHNE folgenden Namen (§9: 'die
    Mandantin') darf keinen Namensfund ausloesen - es gibt hier schlicht
    keinen Namen zu erkennen."""
    checker = SecurityCheckService()

    result = checker.check(
        "Die Mandantin hat heute angerufen.", [], purpose="chat_response"
    )

    assert result.passed is True


@pytest.mark.parametrize(
    "role_word",
    [
        "Frau", "Herr", "Herrn",
        "Mandant", "Mandantin",
        "Kläger", "Klägerin",
        "Beklagter", "Beklagte",
        "Zeuge", "Zeugin",
        "Vermieter", "Vermieterin",
        "Rechtsanwalt", "Rechtsanwältin",
    ],
)
def test_all_named_role_words_from_privacy_test_matrix_block_bare_surname(
    role_word: str,
) -> None:
    """§9 der Overnight-Direktive: vollstaendige Privacy Test Matrix fuer
    alle dort namentlich genannten Kontext-/Rollenwoerter, jeweils direkt
    gefolgt von einem Nachnamen OHNE Vornamen - der real gefundene
    Kernfall des "Frau Müller"-Regressionsfalls (§8)."""
    checker = SecurityCheckService()

    result = checker.check(f"{role_word} Schulz war gestern da.", [], purpose="chat_response")

    assert result.passed is False, f"{role_word} Schulz haette blockieren muessen"


def test_multiple_persons_in_one_text_are_all_detected() -> None:
    """§9: mehrere Personen in einem Text - deckt weiterhin korrekt ueber
    die bestehende Presidio/spaCy-NER + Zwei-Wort-Heuristik ab (kein
    Regressionsfund, Gegenprobe zur "Frau Müller"-Luecke)."""
    checker = SecurityCheckService()

    result = checker.check(
        "Anna Müller und Thomas Müller sowie Anna Schmidt und Thomas Schmidt "
        "waren alle anwesend.",
        [],
        purpose="chat_response",
    )

    assert result.passed is False
    combined_reasons = " ".join(result.reasons)
    for name in ("Anna Müller", "Thomas Müller", "Anna Schmidt", "Thomas Schmidt"):
        assert name in combined_reasons


def test_title_stacking_does_not_misreport_the_title_itself_as_the_name() -> None:
    """'Herr Rechtsanwalt Schmidt' - 'Rechtsanwalt' ist selbst ein
    Rollenwort, kein Namensbestandteil; der Fund muss auf den tatsaechlichen
    Nachnamen ('Rechtsanwalt Schmidt', da 'Rechtsanwalt' unmittelbar vor dem
    Nachnamen steht) hindeuten, jedenfalls aber sicher blockieren."""
    checker = SecurityCheckService()

    result = checker.check(
        "Herr Rechtsanwalt Schmidt hat sich gemeldet.", [], purpose="chat_response"
    )

    assert result.passed is False
