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
