"""Tests fuer app/privacy/presidio_ner.py.

Nutzt bewusst den ECHTEN Presidio-/spaCy-Stack (Praezedenzfall:
tests/test_search_embeddings_real_model.py fuer "echtes Modell, eigene
Testdatei") statt eines Fakes - hier wird gerade geprueft, ob die Presidio-
Integration selbst funktioniert, nicht nur der Aufrufcode drumherum.

CLAUDE.md-Pflicht: ausschliesslich synthetische Beispielsaetze, niemals
echte Mandantendaten."""

from app.privacy.presidio_ner import detect_presidio_entities, get_entity_types, get_pos_tags


def test_detects_person_name_in_german_sentence() -> None:
    text = "Bitte kontaktieren Sie Herrn Dr. Thomas Weber bezüglich der Angelegenheit."

    spans = detect_presidio_entities(text)

    person_spans = [s for s in spans if s.category == "person"]
    assert person_spans
    assert any("Weber" in s.value for s in person_spans)


def test_detects_location() -> None:
    text = "Der Termin findet in Hamburg statt."

    spans = detect_presidio_entities(text)

    ort_spans = [s for s in spans if s.category == "ort"]
    assert any("Hamburg" in s.value for s in ort_spans)


def test_returns_detected_span_with_correct_positions() -> None:
    text = "Kontakt: Julia Neumann ist zuständig."

    spans = detect_presidio_entities(text)

    for span in spans:
        assert text[span.start : span.end] == span.value


def test_empty_text_returns_no_spans() -> None:
    assert detect_presidio_entities("") == []
    assert detect_presidio_entities("   ") == []


def test_common_german_formal_letter_produces_no_or_minimal_false_positives() -> None:
    """Regressionsschutz: Standard-Kanzleiformulierungen ohne echten
    Namen sollen nicht in grossem Umfang faelschlich als PERSON/ORT/
    ORGANISATION markiert werden (score_threshold in presidio_ner.py)."""
    sample = (
        "Sehr geehrte Damen und Herren,\n"
        "vielen Dank für Ihr Schreiben. Wir haben die Unterlagen geprüft "
        "und teilen Ihnen mit, dass der Einspruch gegen den Steuerbescheid "
        "form- und fristgerecht eingelegt wurde. Mit freundlichen Grüßen"
    )

    spans = detect_presidio_entities(sample)

    assert spans == []


def test_result_is_compatible_with_detected_span_interface() -> None:
    from app.privacy.detectors import DetectedSpan

    text = "Frau Sabine Klein aus München meldet sich."

    spans = detect_presidio_entities(text)

    assert all(isinstance(s, DetectedSpan) for s in spans)


def test_title_word_before_an_already_pseudonymized_placeholder_is_not_flagged_again() -> None:
    """ECHTER FUND (realer Abnahme-Test, 13.09.): auf bereits
    pseudonymisiertem Text (zweiter Durchlauf im Restrisiko-Scan,
    security_check.py Punkt 2/3/4) erkannte das Modell einen Anredetitel
    ("Herr"/"Frau") unmittelbar vor einem zu Leerzeichen neutralisierten
    Platzhalter weiterhin als NEUEN PERSON-Treffer - obwohl der eigentliche
    Name bereits korrekt durch den Platzhalter ersetzt worden war. Das
    blockierte real jede Chat-Nachricht einer betroffenen Unterhaltung
    dauerhaft ("moegliche restliche PII gefunden"), obwohl die
    Pseudonymisierung selbst fehlerfrei gearbeitet hatte."""
    text = "Herr [PERSON_06] antwortete am [DATUM_07] und erklärte etwas."

    spans = detect_presidio_entities(text)

    assert spans == []


def test_erbschaftsteuerbescheid_document_heading_is_not_flagged_as_person() -> None:
    """ECHTER FUND (24.09., beim Live-E2E-Test des neuen Erbschaftsteuer-
    Komplexfalls "ROADMAP-ALIGNED PRODUCT COMPLETION" reproduziert): das
    isolierte, grossgeschriebene Wort "Erbschaftsteuerbescheid" (Ueber-
    schriftzeile eines echten Dokuments, siehe app/synthetic_data/
    generator.py::generate_complex_case_erbschaftsteuer) wurde vom Modell
    zuverlaessig (Score 0.85) als PERSON erkannt, obwohl es der
    Dokumenttyp-Name ist, nie ein Name. Da nur dieses eine Vorkommen einen
    Platzhalter bekam, das Wort aber an anderer Stelle desselben
    zusammengefuehrten Aktenkontexts erneut woertlich auftauchte, loeste
    das zuverlaessig das ausgehende Final Payload Gate aus
    (`original_value_leaked`, siehe security_check.py) und blockierte JEDE
    KI-Aktion auf einem Erbschaftsteuer-Dokument, noch bevor ein Claude-
    Aufruf erfolgte - live 2/2 mal reproduziert. Der eigentliche Name im
    selben Text (echte PII) muss davon unberuehrt weiterhin erkannt
    werden."""
    text = "Erbschaftsteuerbescheid.\nErblasser: Sandra Neumann. Erwerber: Christian Schulz."

    spans = detect_presidio_entities(text)

    values = [s.value for s in spans]
    assert "Erbschaftsteuerbescheid" not in values
    assert "Sandra Neumann" in values
    assert "Christian Schulz" in values


def test_get_pos_tags_distinguishes_real_names_from_adjective_noun_headings() -> None:
    """ECHTER FUND (realer Abnahme-Test, 13.09.): Dokument-Ueberschriften
    wie "Synthetisches Testdokument" oder "Salvatorische Klausel"
    (Adjektiv + Substantiv) wurden von der reinen Grossschreibungs-
    Heuristik in security_check.py faelschlich wie ein Name behandelt -
    echte Namen sind dagegen PROPN+PROPN getaggt."""
    text = "Synthetisches Testdokument. Salvatorische Klausel. Anna Müller wohnt hier."

    tags = get_pos_tags(text)

    anna_start = text.index("Anna")
    mueller_start = text.index("Müller")
    assert tags[(anna_start, anna_start + 4)] == "PROPN"
    assert tags[(mueller_start, mueller_start + 6)] == "PROPN"

    synthetisches_start = text.index("Synthetisches")
    assert tags[(synthetisches_start, synthetisches_start + len("Synthetisches"))] == "ADJ"

    salvatorische_start = text.index("Salvatorische")
    assert (
        tags[(salvatorische_start, salvatorische_start + len("Salvatorische"))] == "ADJ"
    )


def test_get_pos_tags_handles_empty_text() -> None:
    assert get_pos_tags("") == {}
    assert get_pos_tags("   ") == {}


def test_get_entity_types_distinguishes_real_names_from_foreign_organization_phrases() -> None:
    """ECHTER FUND (Owner-Direktive "Verbleibende False-Positive-Grenze
    der Privacy-Namen-Heuristik beheben", 07.10.): "World"/"Cities" sind
    spaCy mangels Vokabeleintrag UNBEKANNT und werden daher (siehe
    test_get_pos_tags_... oben fuer den Vergleichsfall) auch vom
    POS-Tagger faelschlich als PROPN eingestuft, GENAU wie ein echter
    Name - der POS-Tag allein kann diesen Fall also nicht loesen. SpaCys
    eigene NER-Entitaetstyp-Erkennung (dieselbe Pipeline) unterscheidet
    hier zuverlaessig: "World Cities Report" wird als EIN MISC-Entity
    erkannt, "Peter Müller" dagegen als PER."""
    text = "Was ist der World Cities Report? Peter Müller fragt das."

    entity_types = get_entity_types(text)

    world_start = text.index("World")
    cities_start = text.index("Cities")
    report_start = text.index("Report")
    assert entity_types[(world_start, world_start + len("World"))] == "MISC"
    assert entity_types[(cities_start, cities_start + len("Cities"))] == "MISC"
    assert entity_types[(report_start, report_start + len("Report"))] == "MISC"

    peter_start = text.index("Peter")
    mueller_start = text.index("Müller")
    assert entity_types[(peter_start, peter_start + len("Peter"))] == "PER"
    assert entity_types[(mueller_start, mueller_start + len("Müller"))] == "PER"


def test_get_entity_types_handles_empty_text() -> None:
    assert get_entity_types("") == {}
    assert get_entity_types("   ") == {}


def test_drop_common_noun_persons_drops_noun_only_person_span() -> None:
    from app.privacy.presidio_ner import drop_common_noun_persons

    text = "Und gilt das auch für Gewerbemietverträge?"
    spans = detect_presidio_entities(text)
    assert any(s.category == "person" for s in spans)  # Vorbedingung: NER-Fehlalarm

    assert [s for s in drop_common_noun_persons(text, spans) if s.category == "person"] == []


def test_drop_common_noun_persons_keeps_spans_with_a_proper_noun() -> None:
    from app.privacy.presidio_ner import drop_common_noun_persons

    for text in ("Schreiben an Schmidt wegen der Miete.", "Herr Müller hat Kontakt aufgenommen."):
        spans = detect_presidio_entities(text)
        kept = drop_common_noun_persons(text, spans)
        assert [s.value for s in kept if s.category == "person"] == [
            s.value for s in spans if s.category == "person"
        ]
        assert any(s.category == "person" for s in kept)


def test_drop_common_noun_persons_keeps_other_categories_and_fails_closed() -> None:
    from app.privacy.detectors import DetectedSpan
    from app.privacy.presidio_ner import drop_common_noun_persons

    text = "Hamburg und Gewerbemietverträge"
    ort = DetectedSpan(category="ort", start=0, end=7, value="Hamburg")
    # Span ausserhalb jeder POS-Token-Abdeckung -> unbekannt -> behalten
    unknown = DetectedSpan(category="person", start=500, end=510, value="XxxxxXxxxx")
    result = drop_common_noun_persons(text, [ort, unknown])

    assert ort in result and unknown in result


def test_normalize_organisation_spans_drops_fragments_starting_with_a_legal_form() -> None:
    from app.privacy.detectors import DetectedSpan
    from app.privacy.presidio_ner import normalize_organisation_spans

    text = "An Elektro Lindqvist KG  z. H. der Geschaeftsfuehrung"
    start = text.find("KG  z. H.")
    fragment = DetectedSpan(
        category="organisation", start=start, end=len(text), value=text[start:]
    )

    assert normalize_organisation_spans(text, [fragment]) == []


def test_normalize_organisation_spans_extends_a_name_by_the_following_legal_form() -> None:
    from app.privacy.detectors import DetectedSpan
    from app.privacy.presidio_ner import normalize_organisation_spans

    text = "An Elektro Lindqvist KG  z. H. der Geschaeftsfuehrung"
    name = DetectedSpan(category="organisation", start=3, end=20, value="Elektro Lindqvist")

    result = normalize_organisation_spans(text, [name])

    assert [(s.start, s.value) for s in result] == [(3, "Elektro Lindqvist KG")]


def test_normalize_organisation_spans_leaves_other_cases_untouched() -> None:
    from app.privacy.detectors import DetectedSpan
    from app.privacy.presidio_ner import normalize_organisation_spans

    text = "Die Agentur Muster und Herr Peter Seitz aus Berlin."
    spans = [
        DetectedSpan(category="organisation", start=4, end=19, value="Agentur Muster"),
        DetectedSpan(category="person", start=24, end=40, value="Peter Seitz"),
        DetectedSpan(category="ort", start=45, end=51, value="Berlin"),
    ]

    assert normalize_organisation_spans(text, spans) == spans


def test_normalize_organisation_spans_drops_a_single_initial_flagged_as_place_or_org() -> None:
    """ECHTER FUND (Real-E2E 08.10.): "z. H." - die Zweiterkennung meldete
    "ort 'H.'" im Restrisiko-Scan und blockierte den Auftrag."""
    from app.privacy.detectors import DetectedSpan
    from app.privacy.presidio_ner import normalize_organisation_spans

    text = "An [ORGANISATION_02]  z. H. der Geschaeftsfuehrung"
    start = text.find("H.")
    initial_as_place = DetectedSpan(category="ort", start=start, end=start + 2, value="H.")
    initial_as_org = DetectedSpan(category="organisation", start=start, end=start + 2, value="H.")
    real_place = DetectedSpan(category="ort", start=0, end=2, value="An")

    result = normalize_organisation_spans(text, [initial_as_place, initial_as_org, real_place])

    assert result == [real_place]


def test_normalize_organisation_spans_keeps_a_person_span_starting_with_an_initial() -> None:
    """Personen bleiben unberuehrt - "H. Herrn Peter" darf nicht freigegeben werden."""
    from app.privacy.detectors import DetectedSpan
    from app.privacy.presidio_ner import normalize_organisation_spans

    text = "An Muster Bau GmbH  z. H. Herrn Peter Beispiel"
    start = text.find("H. Herrn Peter")
    person = DetectedSpan(category="person", start=start, end=start + 14, value="H. Herrn Peter")

    assert normalize_organisation_spans(text, [person]) == [person]


def test_normalize_turns_a_place_followed_by_a_company_name_and_legal_form_into_an_organisation() -> None:
    """ECHTER FUND (Real-E2E 08.10., Request-Capture der installierten .exe):
    "Ostsee Anlagenbau KG" erschien als "[ORT_01] Anlagenbau KG"."""
    from app.privacy.detectors import DetectedSpan
    from app.privacy.presidio_ner import normalize_organisation_spans

    text = "An Ostsee Anlagenbau KG, Hafenkai 3"
    place = DetectedSpan(category="ort", start=3, end=9, value="Ostsee")

    result = normalize_organisation_spans(text, [place])

    assert [(s.category, s.value) for s in result] == [("organisation", "Ostsee Anlagenbau KG")]


def test_normalize_keeps_a_place_that_is_not_part_of_a_company_name() -> None:
    from app.privacy.detectors import DetectedSpan
    from app.privacy.presidio_ner import normalize_organisation_spans

    text = "Der Termin findet in Hamburg statt, die Firma Muster GmbH liegt in Bremen."
    hamburg = DetectedSpan(category="ort", start=text.find("Hamburg"), end=text.find("Hamburg") + 7, value="Hamburg")
    bremen = DetectedSpan(category="ort", start=text.find("Bremen"), end=text.find("Bremen") + 6, value="Bremen")

    assert normalize_organisation_spans(text, [hamburg, bremen]) == [hamburg, bremen]


def test_sentence_initial_imperative_flagged_as_person_is_dropped() -> None:
    """ECHTER FUND (Real-E2E 08.10.): "Ueberarbeite das Schreiben ..." blockierte die
    Anweisung ("weiterhin erkennbare Muster: person")."""
    from app.privacy.detectors import DetectedSpan
    from app.privacy.presidio_ner import drop_sentence_initial_imperatives

    text = "Überarbeite das Schreiben an die Immobilien Westfeld KG: kürzer."
    span = DetectedSpan(category="person", start=0, end=11, value="Überarbeite")

    assert drop_sentence_initial_imperatives(text, [span]) == []


def test_real_names_stay_protected_by_the_imperative_rule() -> None:
    from app.privacy.detectors import DetectedSpan
    from app.privacy.presidio_ner import (
        detect_presidio_entities,
        drop_sentence_initial_imperatives,
    )

    for text in ("Herr Müller schreibt an Frau Schmidt.", "Schreiben an Schmidt wegen der Miete."):
        spans = detect_presidio_entities(text)
        kept = drop_sentence_initial_imperatives(text, spans)
        assert [s.value for s in kept if s.category == "person"] == [
            s.value for s in spans if s.category == "person"
        ]
        assert any(s.category == "person" for s in kept)
    # Mehrwort-Treffer und Treffer mitten im Satz bleiben unberuehrt
    multi = DetectedSpan(category="person", start=0, end=13, value="Peter Weber")
    mid = DetectedSpan(category="person", start=15, end=22, value="Schmidt")
    text = "Peter Weber, und Schmidt kommen."
    assert drop_sentence_initial_imperatives(text, [multi, mid]) == [multi, mid]


def test_ner_span_is_cut_at_a_line_boundary_and_keeps_the_protected_head() -> None:
    """ECHTER FUND (Real-E2E 08.10., Fall A): "Tobias Brandt  Musterweg 12" wurde EINE
    Person; die Anrede "Brandt" bekam eine zweite - erfundene Unstimmigkeit."""
    from app.privacy.detectors import DetectedSpan
    from app.privacy.presidio_ner import normalize_ner_span_boundaries

    text = "Herrn Tobias Brandt  Musterweg 12, 3. OG links"
    value = "Tobias Brandt  Musterweg 12"
    span = DetectedSpan(category="person", start=6, end=6 + len(value), value=value)

    result = normalize_ner_span_boundaries(text, [span])

    assert [(s.category, s.start, s.end, s.value) for s in result] == [("person", 6, 19, "Tobias Brandt")]


def test_ner_span_without_any_letter_is_dropped_so_the_date_detector_wins() -> None:
    """ECHTER FUND (Real-E2E 08.10., Fall A): "30.11.2026.  " wurde als Ort gewertet und
    verdraengte den Datums-Detektor; die Frist erschien als "Ortsplatzhalter"."""
    from app.privacy.detectors import DetectedSpan
    from app.privacy.presidio_ner import normalize_ner_span_boundaries

    text = "Die Zustimmung erbitten wir bis zum 30.11.2026.  Die Erhoehung betraegt 64,00 EUR."
    start = text.find("30.11.2026.")
    span = DetectedSpan(category="ort", start=start, end=start + 13, value="30.11.2026.  ")

    assert normalize_ner_span_boundaries(text, [span]) == []


def test_single_line_spans_with_letters_are_left_untouched() -> None:
    from app.privacy.detectors import DetectedSpan
    from app.privacy.presidio_ner import normalize_ner_span_boundaries

    spans = [
        DetectedSpan(category="person", start=0, end=13, value="Karin Albrecht"[:13]),
        DetectedSpan(category="ort", start=20, end=33, value="Beispielstadt"),
    ]

    assert normalize_ner_span_boundaries("Karin Albrech  Beispielstadt", spans) == spans


# --- Fachbegriffe (Qualitaetslauf 10.10.2026): Ganzwort-Ausnahme fuer belegte Fehlalarme -----------------------

import pytest  # noqa: E402

_PROVEN_TECHNICAL_TERMS = ["Attika", "Verblechung", "Verblechungen", "Bitumenbahn", "Sicherheitseinbehalt", "Prozessvollmacht"]
_SENTENCES = [
    "Die {X} ist nicht ordnungsgemäß ausgeführt worden.",
    "An der {X} zeigt sich ein erheblicher Schaden an mehreren Stellen.",
    "Wir verlangen die Beseitigung der {X} bis zum 30.11.2026.",
]


@pytest.mark.parametrize("term", _PROVEN_TECHNICAL_TERMS)
def test_proven_technical_nouns_are_not_pseudonymized_as_person_or_place(term: str) -> None:
    """POSITIV: reproduzierte Fehlalarme (spaCy-NER: seltenes Fachsubstantiv -> LOCATION) werden nicht mehr
    zu Platzhaltern (sonst Over-Pseudonymisierung und Fail-Closed-Fehlblocks, siehe
    scripts/diagnose_ner_false_positives.py)."""
    for sentence in _SENTENCES:
        values = [s.value for s in detect_presidio_entities(sentence.format(X=term))]
        assert not any(term.lower() in v.lower() for v in values), (sentence, values)


def test_real_names_and_addresses_next_to_those_terms_stay_protected() -> None:
    """NEGATIV: echte Personen, Orte und Anschriften im selben Satz werden weiterhin erkannt."""
    text = "Herr Olaf Thiessen, Gewerbering 6, 24105 Beispielstadt, hat die Verblechungen und die Attika geprüft."
    values = [s.value for s in detect_presidio_entities(text)]
    assert "Olaf Thiessen" in values
    assert any("Gewerbering" in v for v in values)
    assert any("Beispielstadt" in v for v in values)
    assert not any(v.lower() in {"verblechungen", "attika"} for v in values)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Die Bitumenbahn GmbH hat geliefert.", "Bitumenbahn GmbH"),
        ("Bitumenbahn Meier KG liefert.", "Bitumenbahn Meier KG"),
        ("Die Attika Bau GmbH hat geliefert.", "Attika Bau GmbH"),
    ],
)
def test_company_names_containing_such_a_word_remain_protected(text: str, expected: str) -> None:
    """NEGATIV: die Ausnahme gilt nur fuer den GANZEN Treffer (Gleichheit), nie als Teilstring - Firmennamen
    mit einem dieser Woerter bleiben geschuetzt (Verhalten identisch zu vor der Aenderung)."""
    values = [s.value for s in detect_presidio_entities(text)]
    assert expected in values


def test_exception_list_stays_small_and_documented() -> None:
    """Schutz vor schleichender Aufweichung: jede Aufnahme braucht einen Beleg (Kommentar in presidio_ner.py)."""
    from app.privacy.presidio_ner import _NEVER_ENTITY_WORDS

    assert len(_NEVER_ENTITY_WORDS) <= 48
    assert all(w == w.lower() and " " not in w for w in _NEVER_ENTITY_WORDS)


_GENERIC_ROLE_TERMS = ["Partei", "Amt", "Beklagte", "Bund", "Gemeinde", "Kommune", "Kreis", "Landgericht", "Stadt", "Verbraucherzentrale"]


@pytest.mark.parametrize("term", _GENERIC_ROLE_TERMS)
def test_generic_role_and_institution_nouns_are_not_pseudonymized(term: str) -> None:
    """POSITIV: das nackte Gattungswort wird nicht zum Platzhalter (sonst blockiert der Leak-Check jede Claude-Antwort,
    die das Wort selbst verwendet; gemessen: 4 von 10 Schriftsaetzen eines Vertragsfalls)."""
    for sentence in (
        "Die {X} ist verpflichtet, die vereinbarte Leistung zu erbringen.",
        "Die andere {X} ist unverzüglich schriftlich zu informieren.",
    ):
        values = [s.value.strip().lower() for s in detect_presidio_entities(sentence.format(X=term))]
        assert term.lower() not in values, (sentence, values)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Zuständig ist das Landgericht Hamburg für den Rechtsstreit.", "Hamburg"),
        ("Die Stadt Beispielstadt hat den Bescheid erlassen.", "Beispielstadt"),
        ("Die Verbraucherzentrale Hamburg hat die Beschwerde geprüft.", "Hamburg"),
        ("Die Partei Die Linke hat den Antrag gestellt.", "Linke"),
    ],
)
def test_specific_names_next_to_generic_words_stay_protected(text: str, expected: str) -> None:
    """NEGATIV: der konkrete Name im selben Satz wird weiterhin erkannt."""
    values = " ".join(s.value for s in detect_presidio_entities(text))
    assert expected in values


@pytest.mark.parametrize("term", ["Maengel", "Maengeln"])
def test_transliterated_maengel_is_not_pseudonymized_but_umlaut_variant_never_was(term: str) -> None:
    for sentence in ("Die {X} sind bis zum 03.07.2026 zu beseitigen.", "Der Auftragnehmer haftet für die {X} an der Anlage."):
        values = [s.value.strip().lower() for s in detect_presidio_entities(sentence.format(X=term))]
        assert term.lower() not in values, (sentence, values)
    assert [s.value for s in detect_presidio_entities("Die Mängel sind zu beseitigen.")] == []


def test_transliteration_does_not_hide_real_names() -> None:
    """NEGATIV: echte Namen in transliterierter Umgebung bleiben erkannt (inkl. Namen mit ue/oe)."""
    text = "Herr Olaf Thiessen und Frau Henrike Mueller haben die Maengel geruegt."
    values = " ".join(s.value for s in detect_presidio_entities(text))
    assert "Thiessen" in values and "Mueller" in values


def test_transliterated_multi_document_case_passes_the_gateway_without_a_residual_scan_block() -> None:
    """Fall L (Werkvertrag, Abnahmeprotokoll, Maengelruege, Antwort; ae/oe/ue-Schreibweise): VORHER 4 von 4 Laeufen durch
    "Lichtkuppel" im Restrisiko-Scan blockiert. Namen und Anschriften bleiben dabei pseudonymisiert."""
    from app.privacy.gateway import ClaudePrivacyGateway

    text = (
        "[Vertrag] Die Kuestenkontor Verwaltungs GmbH beauftragt die Dachbau Thiessen GmbH & Co. KG. "
        "An der Attika Nordseite sind die Verblechungen nicht fachgerecht gestossen. Die westliche Lichtkuppel ist nicht dicht "
        "angeschlossen. Wir fordern Sie auf, die Maengel Attika und Lichtkuppel bis zum 24.07.2026 zu beseitigen. "
        "Ansprechpartner ist Herr Olaf Thiessen, Gewerbering 6, 24105 Beispielstadt. Die Lichtkuppel erst nach Abnahme beschaedigt."
    )
    result = ClaudePrivacyGateway().prepare_request(
        purpose="formulate_draft", sachverhalt=text, argumentationspunkte=[], quellenverweise=[], stil=None, vorlage=None,
        anwaltliche_anmerkungen="Erstelle ein Schreiben.", known_entities=None, gespraechsverlauf=[],
        skip_general_knowledge_pseudonymization=False,
    )
    assert result.allowed, result.reasons
    payload = result.payload.anonymisierter_sachverhalt
    assert "Thiessen" not in payload and "Gewerbering" not in payload and "Kuestenkontor" not in payload
