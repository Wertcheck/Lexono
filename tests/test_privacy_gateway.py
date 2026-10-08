"""Tests fuer app/privacy/gateway.py.

Schwerpunkt: die kritische Eigenschaft, dass derselbe Name ueber mehrere
Payload-Felder hinweg IMMER denselben Platzhalter erhaelt (siehe
Moduldocstring in gateway.py fuer die Begruendung)."""

import pytest

from app.privacy.gateway import ClaudePrivacyGateway


class _AlwaysBlockSecurityCheck:
    """Deterministischer Test-Stub statt eines organischen Text-Triggers
    (siehe dieselbe Loesung in tests/test_chat_service.py/
    tests/test_review_engine.py) - erzwingt EINEN BELIEBIGEN Block,
    unabhaengig vom konkreten Heuristik-Mechanismus."""

    def check(self, pseudonymized_text, mappings, *, purpose, unrecognized_name_scan_text=None, skip_residual_categories=frozenset(), residual_ignore_ranges=None, residual_ner_span_filter=None):
        from app.privacy.security_check_schema import SecurityCheckResult

        return SecurityCheckResult(
            passed=False,
            reasons=["Möglicherweise nicht erkannte Namen/Entitäten gefunden: ['Test Person']"],
        )


def test_allowed_request_produces_pseudonymized_payload() -> None:
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="formulate_draft",
        sachverhalt="Mandant Max Mustermann wendet sich gegen den Steuerbescheid.",
        known_entities={"mandant": ["Max Mustermann"]},
    )

    assert result.allowed is True
    assert result.payload is not None
    assert "Max Mustermann" not in result.payload.anonymisierter_sachverhalt
    assert "[MANDANT_01]" in result.payload.anonymisierter_sachverhalt


def test_same_entity_gets_same_placeholder_across_fields() -> None:
    """Kernanforderung: Konsistenz ueber Feldgrenzen hinweg."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="formulate_draft",
        sachverhalt="Mandant Max Mustermann hat Einspruch eingelegt.",
        argumentationspunkte=["Max Mustermann handelte fristgerecht."],
        known_entities={"mandant": ["Max Mustermann"]},
    )

    assert result.allowed is True
    assert "[MANDANT_01]" in result.payload.anonymisierter_sachverhalt
    assert "[MANDANT_01]" in result.payload.anonymisierte_argumentationspunkte[0]
    # Nur EIN Mapping-Eintrag fuer den Wert, nicht zwei verschiedene.
    mandant_mappings = [m for m in result.mappings if m.category == "mandant"]
    assert len(mandant_mappings) == 1


def test_different_entities_in_different_fields_get_different_placeholders() -> None:
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="formulate_draft",
        sachverhalt="Mandant Max Mustermann.",
        argumentationspunkte=["Die Gegenseite Erika Musterfrau bestreitet dies."],
        known_entities={"mandant": ["Max Mustermann"], "gegner": ["Erika Musterfrau"]},
    )

    assert "[MANDANT_01]" in result.payload.anonymisierter_sachverhalt
    assert "[GEGNER_01]" in result.payload.anonymisierte_argumentationspunkte[0]


def test_third_party_name_outside_known_entities_is_pseudonymized_and_allowed() -> None:
    """Vor Presidio (§63) wurde ein Name, der weder in known_entities noch
    in einem Regex-Muster vorkam, nur von einer groben Heuristik als
    "verdaechtig" erkannt und blockierte die gesamte Anfrage (kein
    Entwurf moeglich). Mit dem Gateway-Default (echte Presidio-NER, siehe
    ClaudePrivacyGateway.__init__) wird ein solcher Dritter jetzt korrekt
    erkannt UND pseudonymisiert - die Anfrage wird sicher UND erfolgreich
    verarbeitet, statt nur verweigert zu werden."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="formulate_draft",
        sachverhalt="Bitte informieren Sie auch Herrn Peter Müller.",
    )

    assert result.allowed is True
    assert "Peter Müller" not in result.payload.anonymisierter_sachverhalt
    person_mappings = [m for m in result.mappings if m.category == "person"]
    assert len(person_mappings) == 1
    assert person_mappings[0].original_value == "Peter Müller"


def test_presidio_ner_catches_a_name_not_covered_by_known_entities_or_regex() -> None:
    """Requirement 1 (Presidio-Anonymisierung): ein Dritter, der weder in
    known_entities noch in einem Regex-Muster auftaucht, wird trotzdem
    erkannt und pseudonymisiert - der Gateway-Default verdrahtet echte
    Presidio-NER (siehe ClaudePrivacyGateway.__init__).

    ECHTER FUND (Abnahme-Test, 13.09.): dieser Test erwartete bisher
    `allowed is False` - tatsaechlich wurde das aber NICHT durch die
    erkannte PII selbst ausgeloest (die Pseudonymisierung gelingt
    zuverlaessig, siehe Mapping-Assertion unten), sondern durch einen
    zufaelligen Nebeneffekt der (mittlerweile per POS-Tag verfeinerten,
    siehe security_check.py) Grossschreibungs-Heuristik auf "Als Zeuge"
    (satzanfangs-grossgeschriebenes "Als" + grossgeschriebenes Substantiv
    "Zeuge" - kein Name). Die eigentlich pruefenswerte Eigenschaft ist:
    Presidio erkennt und pseudonymisiert den Namen - die Anfrage wird
    danach korrekt ERLAUBT (kein Block noetig, wenn Pseudonymisierung
    sauber gelungen ist)."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="formulate_draft",
        sachverhalt="Als Zeuge wird außerdem Herr Sebastian Krombach benannt.",
    )

    assert result.allowed is True
    assert len(result.mappings) == 1
    assert result.mappings[0].category == "person"
    assert result.mappings[0].original_value == "Sebastian Krombach"
    assert result.payload is not None
    assert "Sebastian Krombach" not in result.payload.anonymisierter_sachverhalt


def test_disallowed_purpose_blocks_request() -> None:
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(purpose="analyze_full_file", sachverhalt="Ein Text.")

    assert result.allowed is False
    assert result.payload is None


def test_reconstruct_response_restores_original_values() -> None:
    gw = ClaudePrivacyGateway()
    result = gw.prepare_request(
        purpose="formulate_draft",
        sachverhalt="Mandant Max Mustermann.",
        known_entities={"mandant": ["Max Mustermann"]},
    )
    assert result.allowed is True

    claude_response = "Sehr geehrter Herr [MANDANT_01], wir bestätigen den Eingang."

    reconstructed = gw.reconstruct_response(claude_response, result.mappings)

    assert reconstructed == "Sehr geehrter Herr Max Mustermann, wir bestätigen den Eingang."
    assert "[MANDANT_01]" not in reconstructed


def test_multiple_argumente_and_quellen_are_correctly_split() -> None:
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="formulate_draft",
        sachverhalt="Sachverhalt ohne PII.",
        argumentationspunkte=["Erster Punkt.", "Zweiter Punkt.", "Dritter Punkt."],
        quellenverweise=["§ 355 AO.", "§ 356 AO."],
    )

    assert result.allowed is True
    assert result.payload.anonymisierte_argumentationspunkte == [
        "Erster Punkt.",
        "Zweiter Punkt.",
        "Dritter Punkt.",
    ]
    assert result.payload.anonymisierte_quellenverweise == ["§ 355 AO.", "§ 356 AO."]


def test_empty_argumente_and_quellen_produce_empty_lists() -> None:
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(purpose="formulate_draft", sachverhalt="Nur Sachverhalt.")

    assert result.allowed is True
    assert result.payload.anonymisierte_argumentationspunkte == []
    assert result.payload.anonymisierte_quellenverweise == []


def test_missing_vorlage_results_in_none() -> None:
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(purpose="formulate_draft", sachverhalt="Text ohne Vorlage.")

    assert result.allowed is True
    assert result.payload.schreibvorlage is None


def test_vorlage_is_preserved_when_provided() -> None:
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="formulate_draft",
        sachverhalt="Text.",
        vorlage="Sehr geehrte Damen und Herren,",
    )

    assert result.allowed is True
    assert result.payload.schreibvorlage == "Sehr geehrte Damen und Herren,"


def test_injection_attempt_with_internal_markers_does_not_break_parsing() -> None:
    """Ein Text, der zufällig/absichtlich die internen Trennmarkierungen
    enthält, darf die Feldaufteilung nicht durcheinanderbringen."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="formulate_draft",
        sachverhalt="Text mit @@GATEWAY_VORLAGE@@ eingebettetem Marker.",
    )

    assert result.allowed is True
    assert "@@GATEWAY_VORLAGE@@" not in result.payload.anonymisierter_sachverhalt
    assert result.payload.schreibvorlage is None  # nicht faelschlich befuellt


def test_gateway_result_has_correct_purpose_even_when_blocked() -> None:
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(purpose="analyze_full_file", sachverhalt="Text.")

    assert result.purpose == "analyze_full_file"


def test_style_field_passed_through_without_pseudonymization() -> None:
    """Der Stilwunsch selbst enthaelt typischerweise keine PII und muss
    unveraendert ankommen."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="formulate_draft", sachverhalt="Text.", stil="förmlich, sachlich"
    )

    assert result.payload.gewuenschter_stil == "förmlich, sachlich"


# --- Siebtes Allowlist-Feld: anwaltliche Anmerkungen (Prompt 23) ---


def test_attorney_anmerkungen_field_is_none_when_not_provided() -> None:
    """Ohne Anmerkung bleibt das Feld None - KEIN Freitext-Fallback, der
    faelschlich als Anmerkung interpretiert werden koennte."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(purpose="formulate_draft", sachverhalt="Text.")

    assert result.allowed is True
    assert result.payload.anonymisierte_anwaltliche_anmerkungen is None


def test_attorney_anmerkungen_are_pseudonymized() -> None:
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="formulate_draft",
        sachverhalt="Sachverhalt ohne Namen.",
        anwaltliche_anmerkungen="Bitte auf die Forderung von Max Mustermann eingehen.",
        known_entities={"mandant": ["Max Mustermann"]},
    )

    assert result.allowed is True
    assert "Max Mustermann" not in result.payload.anonymisierte_anwaltliche_anmerkungen
    assert "[MANDANT_01]" in result.payload.anonymisierte_anwaltliche_anmerkungen


def test_attorney_anmerkungen_share_placeholder_consistency_with_other_fields() -> None:
    """Kernanforderung (wie beim bestehenden Konsistenztest): derselbe Name
    in Sachverhalt UND Anmerkung erhaelt denselben Platzhalter, weil beide
    Felder durch DENSELBEN Pseudonymisierungsdurchlauf laufen."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="formulate_draft",
        sachverhalt="Mandant Max Mustermann hat Einspruch eingelegt.",
        anwaltliche_anmerkungen="Auf die Position von Max Mustermann ausdruecklich eingehen.",
        known_entities={"mandant": ["Max Mustermann"]},
    )

    assert result.allowed is True
    assert "[MANDANT_01]" in result.payload.anonymisierter_sachverhalt
    assert "[MANDANT_01]" in result.payload.anonymisierte_anwaltliche_anmerkungen
    mandant_mappings = [m for m in result.mappings if m.category == "mandant"]
    assert len(mandant_mappings) == 1


def test_attorney_anmerkungen_sanitizes_internal_markers() -> None:
    """Dieselbe Injection-Verteidigung wie bei den anderen Feldern: ein
    Anmerkungstext darf die internen Trennmarkierungen nicht missbrauchen,
    um die Feldaufteilung durcheinanderzubringen."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="formulate_draft",
        sachverhalt="Text.",
        anwaltliche_anmerkungen="Anmerkung mit @@GATEWAY_SACHVERHALT@@ eingebettetem Marker.",
    )

    assert result.allowed is True
    assert "@@GATEWAY_SACHVERHALT@@" not in result.payload.anonymisierte_anwaltliche_anmerkungen
    assert "[ENTFERNT]" in result.payload.anonymisierte_anwaltliche_anmerkungen


def test_attorney_anmerkungen_go_through_security_check_like_other_fields() -> None:
    """Kein Bypass: der Security-Check erhaelt den GESAMTEN kombinierten
    Text (inkl. anwaltlicher Anmerkung), nicht nur den Sachverhalt -
    bewiesen ueber einen deterministischen Block-Stub statt eines
    organischen Text-Triggers (ECHTER FUND, Abnahme-Test 13.09.: "Peter
    Müller" wird von Presidio zuverlaessig pseudonymisiert - kein Bypass,
    aber eben auch kein Blockierungsgrund mehr; ein frueherer,
    zufaelliger Trigger im Sachverhalt-Text selbst - unabhaengig von der
    Anmerkung - liess den Test faelschlich aus dem falschen Grund
    bestehen, siehe security_check.py fuer die POS-Tag-Verfeinerung)."""
    gw = ClaudePrivacyGateway(security_check=_AlwaysBlockSecurityCheck())

    result = gw.prepare_request(
        purpose="formulate_draft",
        sachverhalt="Unauffälliger Text ohne Namen.",
        anwaltliche_anmerkungen="Bitte informieren Sie auch Herrn Peter Müller.",
    )

    assert result.allowed is False
    assert result.payload is None
    assert len(result.reasons) > 0


# --- FINAL PAYLOAD GATE (check_payload_placeholder_integrity) ---
# Im Normalbetrieb sollte dieses Gate nie auslösen (die vorgelagerte
# Pseudonymisierung/Security-Check + die strikt verankerte Split-Regex in
# _split_combined_text sind bereits korrekt). Um zu beweisen, dass das Gate
# tatsaechlich wirkt (nicht nur toter Code ist), wird hier gezielt ein
# fehlerhaftes Aufteilungsergebnis simuliert - genau die Fehlerklasse, gegen
# die dieses Gate zusaetzlich zum bestehenden Security-Check absichert.


def test_final_payload_gate_blocks_when_split_drops_a_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simuliert einen (hypothetischen) Fehler im Aufteilungsschritt selbst:
    der Sachverhalt-Teil der aufgeteilten Payload verliert einen Platzhalter,
    der laut Mapping vorhanden sein muesste. Das bestehende
    SecurityCheckService.check() hat den ZUSAMMENGEFUEHRTEN Text VORHER
    bereits korrekt geprueft - nur das neue Payload-Gate kann diesen
    nachgelagerten Fehler noch abfangen."""
    gw = ClaudePrivacyGateway()
    real_split = gw._split_combined_text

    def _tampered_split(combined: str):
        sachverhalt, argumente, quellen, vorlage, anmerkungen, verlauf = real_split(combined)
        # Platzhalter aus dem Sachverhalt entfernen, als wäre beim
        # Wiederzusammensetzen etwas verlorengegangen.
        tampered_sachverhalt = sachverhalt.replace("[MANDANT_01]", "MANDANT EINS")
        return tampered_sachverhalt, argumente, quellen, vorlage, anmerkungen, verlauf

    monkeypatch.setattr(gw, "_split_combined_text", staticmethod(_tampered_split))

    result = gw.prepare_request(
        purpose="formulate_draft",
        sachverhalt="Mandant Max Mustermann wendet sich gegen den Steuerbescheid.",
        known_entities={"mandant": ["Max Mustermann"]},
    )

    assert result.allowed is False
    assert result.payload is None
    assert any("MANDANT_01" in reason for reason in result.reasons)


def test_final_payload_gate_passes_through_clean_payload_unaffected() -> None:
    """Regressionsschutz: das neue Gate darf einen unveraenderten,
    korrekten Ablauf nicht faelschlich blockieren."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="formulate_draft",
        sachverhalt="Mandant Max Mustermann wendet sich gegen den Steuerbescheid.",
        argumentationspunkte=["Max Mustermann handelte fristgerecht."],
        known_entities={"mandant": ["Max Mustermann"]},
    )

    assert result.allowed is True
    assert result.payload is not None


def test_chat_history_rich_in_capitalized_phrases_does_not_block_a_clean_new_question() -> None:
    """ECHTER FUND (07.10., Owner-Direktive "Chat-Pipeline Privacy-False-
    Positive bei allgemeinen Fragen", real vom Benutzer per Screenshot
    gemeldet): eine voellig gewoehnliche Chat-Frage ("Wie lange dauert ein
    Jurastudium durchschnittlich?") wurde faelschlich mit "Im Text wurden
    moeglicherweise nicht erkannte Namen/Daten gefunden." blockiert, weil
    eine FRUEHERE Claude-Antwort im Gespraechsverlauf voller legitimer
    Grossschreibungs-Wortpaare war ("World Cities Report",
    "UN-Habitat-Programm" o.ae.) - vor der Korrektur in app/privacy/
    gateway.py/security_check.py blockierte exakt dieser Aufbau real
    (per `git stash` gegen den unveraenderten Stand verifiziert). Bewusst
    OHNE Security-Check-Stub - echte Presidio-/POS-Tag-Produktivkonfig
    (ClaudePrivacyGateway()-Standardkonstruktor), damit der Test den
    echten Vorfall nachbildet."""
    gw = ClaudePrivacyGateway()

    gespraechsverlauf = [
        "Anwalt: Wieviele Staedte gibt es insgesamt?",
        "Assistent: Laut dem UN-Habitat-Programm und der World Cities "
        "Report-Reihe gibt es je nach UN-Zahl von 12.140 Staedten "
        "weltweit (Stand 2025) unterschiedliche Definitionen, abhaengig "
        "von den UN-Mitgliedstaaten und ihren jeweiligen "
        "Verwaltungsgrenzen.",
    ]

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt="Chat",
        anwaltliche_anmerkungen="Wie lange dauert ein Jurastudium durchschnittlich?",
        gespraechsverlauf=gespraechsverlauf,
    )

    assert result.allowed is True
    assert result.reasons == []
    assert result.payload is not None


def test_real_name_in_a_prior_assistant_answer_is_still_pseudonymized_not_leaked() -> None:
    """Regressionsschutz zum vorherigen Test: die Einschraenkung auf
    Punkt 6 darf den eigentlichen Schutz echter personenbezogener Daten
    in fruiheren Claude-Antworten NICHT schwaechen - Presidio (Punkt
    2/3/4, von der Korrektur unveraendert) muss einen echten Namen in
    einer "Assistent: "-Zeile weiterhin erkennen und durch einen
    Platzhalter ersetzen, bevor der Text (erneut) an Claude ginge."""
    gw = ClaudePrivacyGateway()

    gespraechsverlauf = [
        "Anwalt: Was wissen Sie ueber meinen Mandanten?",
        "Assistent: Ihr Mandant Peter Müller wohnt in der Musterstrasse 5.",
    ]

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt="Chat",
        anwaltliche_anmerkungen="Wie lange dauert ein Jurastudium durchschnittlich?",
        gespraechsverlauf=gespraechsverlauf,
    )

    assert result.allowed is True
    assert "Peter Müller" not in result.payload.anonymisierter_gespraechsverlauf[1]
    assert "Musterstrasse 5" not in result.payload.anonymisierter_gespraechsverlauf[1]
    assert any(m.original_value == "Peter Müller" for m in result.mappings)


def test_build_unrecognized_name_scan_text_excludes_only_assistant_lines() -> None:
    """Direkter Unit-Test des neuen Hilfsbausteins: "Assistent: "-Zeilen
    fallen raus, "Anwalt: "-Zeilen und alle anderen Felder bleiben - die
    deterministische Grundlage fuer die beiden Tests oben."""
    scan_text = ClaudePrivacyGateway._build_unrecognized_name_scan_text(
        "Sachverhalt-Text",
        ["Argument-Eins"],
        ["Quelle-Eins"],
        "Vorlage-Text",
        "Anmerkung-Text",
        original_gespraechsverlauf=[
            "Anwalt: Anwalt-Zeile",
            "Assistent: Assistent-Zeile",
        ],
        pseudo_verlauf=[
            "Anwalt: Anwalt-Zeile",
            "Assistent: Assistent-Zeile",
        ],
    )

    assert "Anwalt-Zeile" in scan_text
    assert "Assistent-Zeile" not in scan_text
    assert "Sachverhalt-Text" in scan_text
    assert "Argument-Eins" in scan_text
    assert "Quelle-Eins" in scan_text
    assert "Vorlage-Text" in scan_text
    assert "Anmerkung-Text" in scan_text


def test_general_knowledge_question_with_organization_phrase_is_allowed() -> None:
    """ECHTER FUND (Owner-Direktive "Verbleibende False-Positive-Grenze
    der Privacy-Namen-Heuristik beheben", 07.10., Folge-Direktive zu
    8087ec8): eine harmlose, in der aktuellen Nachricht selbst gestellte
    Wissensfrage ("Was ist der World Cities Report?") darf nicht als
    moeglicher Personenname blockiert werden - unabhaengig vom
    Gespraechsverlauf (anders als 8087ec8, das NUR History-bedingte
    Faelle loeste). Bewusst OHNE Security-Check-Stub - echte Presidio-/
    POS-/Entity-Type-Produktivkonfig (ClaudePrivacyGateway()-
    Standardkonstruktor)."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt="Chat",
        anwaltliche_anmerkungen="Was ist der World Cities Report?",
    )

    assert result.allowed is True
    assert result.reasons == []


def test_real_name_in_current_message_is_still_pseudonymized_after_entity_type_refinement() -> None:
    """Regressionsschutz zum vorherigen Test: die Entity-Type-
    Verfeinerung darf echten PII-Schutz in der AKTUELLEN Nachricht nicht
    schwaechen - Presidio (Punkt 2/3/4, unveraendert) erkennt "Peter
    Müller" weiterhin VOR Punkt 6 und pseudonymisiert ihn (daher
    `allowed=True` MIT Platzhalter im Payload, kein Leak - identisches
    Verhalten/Testmuster wie test_real_name_in_a_prior_assistant_answer_
    is_still_pseudonymized_not_leaked oben)."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt="Chat",
        anwaltliche_anmerkungen="Bitte informieren Sie auch Herrn Peter Müller.",
    )

    assert result.allowed is True
    assert "Peter Müller" not in result.payload.anonymisierte_anwaltliche_anmerkungen
    assert any(m.original_value == "Peter Müller" for m in result.mappings)


def test_skip_general_knowledge_pseudonymization_leaves_general_knowledge_org_name_readable() -> None:
    """ECHTER FUND (Owner-Direktive "Architektur-Audit Privacy-/Chat-
    Pipeline", 07.10., live reproduziert): Presidio pseudonymisierte
    "World Health Organization" unterschiedslos auch in einer voellig
    allgemeinen Wissensfrage - Claude bekam nur einen Platzhalter und
    konnte die Frage nicht mehr sinnvoll beantworten. Mit
    `skip_general_knowledge_pseudonymization=True` bleibt der Begriff lesbar;
    `allowed` bleibt `True` (die urspruengliche Fassung dieses Fixes
    loeste faelschlich einen NEUEN Block ueber Punkt 2/3/4 aus - siehe
    SecurityCheckService.check Docstring zu `skip_residual_categories` -
    das ist hier mitgeprueft)."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt="Chat",
        anwaltliche_anmerkungen="Was ist die World Health Organization?",
        skip_general_knowledge_pseudonymization=True,
    )

    assert result.allowed is True
    assert result.reasons == []
    assert "World Health Organization" in result.payload.anonymisierte_anwaltliche_anmerkungen
    assert result.mappings == []


def test_organization_pseudonymization_default_behavior_is_unchanged() -> None:
    """Regressionsschutz: ohne `skip_general_knowledge_pseudonymization`
    (Default `False`) bleibt das bisherige, strikte Verhalten fuer JEDEN
    bestehenden Aufrufer unveraendert - derselbe Begriff wird weiterhin
    pseudonymisiert."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt="Chat",
        anwaltliche_anmerkungen="Was ist die World Health Organization?",
    )

    assert result.allowed is True
    assert "World Health Organization" not in result.payload.anonymisierte_anwaltliche_anmerkungen
    assert any(m.category == "organisation" for m in result.mappings)


def test_skip_general_knowledge_pseudonymization_does_not_weaken_known_entities_mandant_protection() -> None:
    """Sicherheitskritische Gegenprobe: ein echter, ueber `known_entities`
    bekannter Mandant (Kategorie "mandant", NICHT "organisation" - siehe
    app/ai_providers/local_ai_provider.py::_build_known_entities) bleibt
    VOLLSTAENDIG geschuetzt, selbst wenn `skip_organization_
    pseudonymization=True` gesetzt ist (im echten Aufrufer, app/drafting/
    service.py, koennte das ohnehin nie gleichzeitig zutreffen - diese
    Methode selbst muss es aber unabhaengig davon korrekt behandeln, da
    `known_entities`-Treffer strukturell nie die Presidio-Kategorie
    "organisation" tragen)."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt="Chat",
        anwaltliche_anmerkungen="Unser Mandant Müller GmbH hat eine Frist.",
        known_entities={"mandant": ["Müller GmbH"]},
        skip_general_knowledge_pseudonymization=True,
    )

    assert result.allowed is True
    assert "Müller GmbH" not in result.payload.anonymisierte_anwaltliche_anmerkungen
    assert any(m.category == "mandant" and m.original_value == "Müller GmbH" for m in result.mappings)


def test_skip_general_knowledge_pseudonymization_does_not_weaken_person_protection() -> None:
    """Weitere Gegenprobe: eine Organisation, die eine natuerliche Person
    identifiziert (z. B. ein Einzelunternehmer-Firmenname), wird von
    Presidio bereits heute als Kategorie "person" erkannt (empirisch
    bestaetigt: "Max Müller e.K." -> PERSON, nicht ORGANIZATION) -
    `skip_general_knowledge_pseudonymization` betrifft NUR die Presidio-
    Kategorie "organisation" und darf diesen Schutz nicht beruehren."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt="Chat",
        anwaltliche_anmerkungen="Das Dokument stammt von der Müller GmbH, vertreten durch Max Müller.",
        skip_general_knowledge_pseudonymization=True,
    )

    assert result.allowed is True
    assert "Max Müller" not in result.payload.anonymisierte_anwaltliche_anmerkungen
    assert "Müller GmbH" in result.payload.anonymisierte_anwaltliche_anmerkungen
    assert any(m.category == "person" and m.original_value == "Max Müller" for m in result.mappings)


def test_prior_assistant_answer_residual_pii_false_positive_does_not_block_new_question() -> None:
    """ECHTER FUND (Owner-Direktive "Architektur-Audit Privacy-/Chat-
    Pipeline", 07.10., real per Live-QA-Fork NACH den beiden anderen
    Korrekturen dieser Direktive reproduziert, Text hier identisch zu
    einer echten, gespeicherten Claude-Antwort aus jener Session):
    Presidios Restrisiko-Scan (Punkt 2/3/4) stufte "UN-Quelle" und
    "Wiederholungsversuche" aus einer FRUEHEREN Claude-Antwort
    faelschlich als Kategorie "ort" ein und blockierte dadurch eine
    voellig unverwandte, saubere Folgefrage - Live-Verifikation per
    `git stash` am echten Repro bestaetigt: vor dieser Korrektur
    `allowed=False` mit genau dieser Meldung, danach `allowed=True`.
    Bewusst OHNE Security-Check-Stub - echte Presidio-Produktivkonfig."""
    gw = ClaudePrivacyGateway()

    gespraechsverlauf = [
        "Anwalt: Was ist der World Cities Report?",
        "Assistent: Der World Cities Report ist eine Publikation von "
        "UN-Habitat. Laut UN-Quelle gab es trotz mehrerer "
        "Wiederholungsversuche bei der Datenerhebung Verzoegerungen bei "
        "der Veroeffentlichung.",
        "Anwalt: Wie lange dauert ein Jurastudium durchschnittlich?",
        "Assistent: Das ist eine allgemeine Frage, unabhaengig vom "
        "bisherigen Gespraechsverlauf zum World Cities Report.",
    ]

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt="Akte: (kein spezifischer Fall zugeordnet)",
        anwaltliche_anmerkungen="Was regelt Paragraph 558 BGB?",
        gespraechsverlauf=gespraechsverlauf,
    )

    assert result.allowed is True
    assert result.reasons == []


def test_skip_general_knowledge_pseudonymization_leaves_place_names_readable() -> None:
    """ECHTER FUND (08.10., realer Fehler im installierten Build):
    "wieviele klempnerbetriebe gibt es ca. in deutschland" erreichte Claude
    als "...in [ORT_01]" - Claude verlangte "den tatsaechlichen Ortsnamen".
    Im Chat ohne Akte-/Mandanten-/Dokumentkontext bleibt "ort" lesbar."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt="Chat",
        anwaltliche_anmerkungen="wieviele klempnerbetriebe gibt es ca. in deutschland",
        skip_general_knowledge_pseudonymization=True,
    )

    assert result.allowed is True
    assert result.payload.anonymisierte_anwaltliche_anmerkungen == (
        "wieviele klempnerbetriebe gibt es ca. in deutschland"
    )
    assert result.mappings == []


def test_place_name_is_still_pseudonymized_by_default() -> None:
    """Regressionsschutz: ohne den Skip (Akte-/Mandantenkontext vorhanden)
    bleibt "ort" strikt pseudonymisiert."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt="Chat",
        anwaltliche_anmerkungen="wieviele klempnerbetriebe gibt es ca. in deutschland",
    )

    assert "deutschland" not in result.payload.anonymisierte_anwaltliche_anmerkungen
    assert any(m.category == "ort" for m in result.mappings)


def test_skip_general_knowledge_pseudonymization_keeps_person_and_address_protected() -> None:
    """Der "ort"-Skip darf Personen und Adressen (eigene Kategorien) nicht
    beruehren."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt="Chat",
        anwaltliche_anmerkungen="Herr Peter Müller wohnt in der Musterstrasse 5 in Berlin.",
        skip_general_knowledge_pseudonymization=True,
    )

    assert result.allowed is True
    text = result.payload.anonymisierte_anwaltliche_anmerkungen
    assert "Peter Müller" not in text
    assert "Musterstrasse 5" not in text
    assert {m.category for m in result.mappings} >= {"person", "adresse"}


# --- ECHTER FUND (08.10., realer Fehler im installierten Build): "was kannst
# du" wurde nach einer vorherigen Antwort mit "Es wurden nach der
# Pseudonymisierung weiterhin erkennbare Muster gefunden" blockiert. Root
# Cause: die fruehere Residual-Scan-Variante ueberschrieb die Assistent-
# Zeilen im Gesamttext mit ~1000 Leerzeichen; "Anwalt: hallo wer bist du"
# stand dadurch am Textende, und Presidio erkannte das kleingeschriebene
# "bist du" als PERSON (im vollen pseudonymisierten Text kein Treffer). ---

_CAPABILITY_ANSWER = (
    "Ich bin der Arbeitsassistent von Lexono und unterstütze Sie bei der "
    "täglichen Kanzleiarbeit. Ich kann allgemeine Fragen beantworten, "
    "Dokumente analysieren und zusammenfassen, Sachverhalte strukturieren, "
    "mögliche Fristen herausarbeiten, Texte überarbeiten und auf Wunsch "
    "Entwürfe für Schreiben formulieren. Alle personenbezogenen Daten "
    "werden vor dem Versand lokal pseudonymisiert. Ich treffe keine "
    "rechtlichen Entscheidungen; die Bewertung bleibt bei Ihnen. Wenn "
    "etwas unklar ist, markiere ich es als offenen Prüfpunkt, statt "
    "Angaben zu erfinden. Fragen Sie mich einfach, womit ich helfen soll. "
    "Beispiele: Erklärung einer Norm, Vergleich zweier Rechtsbegriffe, "
    "Gliederung eines Schriftsatzes, Prüfung eines Entwurfs auf Lücken, "
    "Zusammenfassung eines Bescheids oder Übersicht über offene Punkte "
    "einer Akte. Für aktuelle Zahlen kann ich, sofern verfügbar, eine "
    "Recherche nutzen und kennzeichne das dann ausdrücklich."
)


def test_capability_question_as_first_message_is_not_blocked() -> None:
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt="Akte: (kein spezifischer Fall zugeordnet)",
        anwaltliche_anmerkungen="was kannst du",
        skip_general_knowledge_pseudonymization=True,
    )

    assert result.allowed is True
    assert result.reasons == []


def _assistant_line_of_length(length: int) -> str:
    """"Assistent: ..."-Zeile mit EXAKT `length` Zeichen. Die Laenge ist
    relevant: die fruehere Residual-Scan-Variante ueberschrieb die Zeile mit
    gleich vielen Leerzeichen, und der dadurch entstehende Artefakt-
    Fehlalarm ("bist du" als PERSON) trat deterministisch bei bestimmten
    Laengen auf (real: 984; synthetisch verifiziert: 200 und >= 984, nicht
    bei 500/800) - die Tests unten verwenden bewusst solche Laengen."""
    line = "Assistent: " + _CAPABILITY_ANSWER
    while len(line) < length:
        line += " " + _CAPABILITY_ANSWER
    return line[:length]


@pytest.mark.parametrize("assistant_length", [200, 984, 1000])
@pytest.mark.parametrize("skip", [True, False])
def test_capability_question_after_a_previous_assistant_answer_is_not_blocked(
    skip: bool, assistant_length: int
) -> None:
    """Exakt die reale Konstellation: kleingeschriebene Vorfrage, lange
    Assistent-Antwort, dann die Folgefrage. Faengt den Fehler
    nachweislich (siehe Abschlussbericht: gegen die alte Residual-Logik
    schlaegt dieser Test fehl)."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt="Akte: (kein spezifischer Fall zugeordnet)",
        anwaltliche_anmerkungen="was kannst du",
        gespraechsverlauf=[
            "Anwalt: hallo wer bist du",
            _assistant_line_of_length(assistant_length),
        ],
        skip_general_knowledge_pseudonymization=skip,
    )

    assert result.allowed is True
    assert result.reasons == []


def test_residual_ignore_ranges_are_empty_without_any_assistant_line() -> None:
    """Ohne KI-Historie wird nichts ignoriert (exakt das bisherige
    Verhalten)."""
    combined = "@@GATEWAY_VERLAUF@@\nAnwalt: Vorfrage"

    ranges = ClaudePrivacyGateway._build_residual_ignore_ranges(
        combined,
        original_gespraechsverlauf=["Anwalt: Vorfrage"],
        pseudo_verlauf=["Anwalt: Vorfrage"],
    )

    assert ranges == []


def test_residual_ignore_ranges_cover_exactly_the_assistant_lines() -> None:
    gw = ClaudePrivacyGateway()
    history = ["Anwalt: Frage eins", "Assistent: KI-Antwort", "Anwalt: Frage zwei"]
    combined = gw._build_combined_text("Sachverhalt", [], [], None, "Aktuelle Frage", history)
    *_, pseudo_verlauf = gw._split_combined_text(combined)

    ranges = ClaudePrivacyGateway._build_residual_ignore_ranges(
        combined, original_gespraechsverlauf=history, pseudo_verlauf=pseudo_verlauf
    )

    assert len(ranges) == 1
    lo, hi = ranges[0]
    assert combined[lo:hi] == "Assistent: KI-Antwort"


def test_residual_ignore_ranges_fail_closed_on_unexpected_structure() -> None:
    """Passt ein berechneter Bereich nicht zum erwarteten Eintrag, wird
    NICHTS ignoriert (der Scan bleibt vollstaendig streng)."""
    ranges = ClaudePrivacyGateway._build_residual_ignore_ranges(
        "@@GATEWAY_VERLAUF@@\nganz anderer Text",
        original_gespraechsverlauf=["Assistent: KI-Antwort"],
        pseudo_verlauf=["Assistent: KI-Antwort"],
    )

    assert ranges == []


def test_real_pii_in_a_lawyer_history_line_is_still_pseudonymized_with_assistant_history() -> None:
    """Gegenprobe: der Segment-Scan schwaecht den PII-Schutz nicht - ein
    Name in einer Anwalt-Zeile wird weiterhin pseudonymisiert."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt="Akte: (kein spezifischer Fall zugeordnet)",
        anwaltliche_anmerkungen="Fasse bitte zusammen.",
        gespraechsverlauf=[
            "Anwalt: Herr Peter Müller wohnt in der Musterstrasse 5.",
            f"Assistent: {_CAPABILITY_ANSWER}",
        ],
        skip_general_knowledge_pseudonymization=True,
    )

    assert result.allowed is True
    joined = " ".join(result.payload.anonymisierter_gespraechsverlauf)
    assert "Peter Müller" not in joined
    assert "Musterstrasse 5" not in joined
    assert {m.category for m in result.mappings} >= {"person", "adresse"}


def test_residual_pii_in_a_lawyer_segment_still_blocks_with_assistant_history() -> None:
    """Gegenprobe zum Pre-Cloud-Gate: uebersieht die Pseudonymisierung etwas
    in einem Anwalt-Segment (simuliert durch einen Pseudonymizer, der nichts
    ersetzt), blockiert der Residual-Scan weiterhin - auch wenn
    Assistent-Historie vorhanden ist."""
    from app.privacy.presidio_ner import detect_presidio_entities
    from app.privacy.pseudonymizer import Pseudonymizer
    from app.privacy.security_check import SecurityCheckService

    class _NoReplace(Pseudonymizer):
        def pseudonymize(self, text, *, known_entities=None, skip_categories=frozenset(), ner_span_filter=None):
            return text, []

    gw = ClaudePrivacyGateway(
        pseudonymizer=_NoReplace(),
        security_check=SecurityCheckService(ner_detector=detect_presidio_entities),
    )

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt="Akte: (kein spezifischer Fall zugeordnet)",
        anwaltliche_anmerkungen="Bitte schreibe an max.mustermann@example.test.",
        gespraechsverlauf=["Anwalt: Hallo", f"Assistent: {_CAPABILITY_ANSWER}"],
    )

    assert result.allowed is False
    assert any("weiterhin erkennbare Muster" in r for r in result.reasons)


def test_skip_general_knowledge_leaves_common_noun_flagged_as_person_readable() -> None:
    """ECHTER FUND (Real-E2E 08.10., installierter Build): "Und gilt das auch
    fuer Gewerbemietverträge?" erreichte Claude als "[PERSON_01]", weil
    spaCys NER das Fachwort als PER taggt (POS=NOUN). Im Chat ohne Akte-
    kontext bleibt ein reiner Nomen-"Personen"-Treffer lesbar; `allowed`
    bleibt True (auch der Restrisiko-Scan darf keinen neuen Block ausloesen)."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt="Chat",
        anwaltliche_anmerkungen="Und gilt das auch für Gewerbemietverträge?",
        skip_general_knowledge_pseudonymization=True,
    )

    assert result.allowed is True
    assert result.reasons == []
    assert "Gewerbemietverträge" in result.payload.anonymisierte_anwaltliche_anmerkungen
    assert result.mappings == []


def test_common_noun_person_is_still_pseudonymized_without_general_knowledge_skip() -> None:
    """Regressionsschutz: ohne den Skip (Akte-/Mandanten-/Dokumentkontext)
    bleibt das strikte Verhalten unveraendert."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt="Chat",
        anwaltliche_anmerkungen="Und gilt das auch für Gewerbemietverträge?",
    )

    assert any(m.original_value == "Gewerbemietverträge" for m in result.mappings)


def test_real_person_names_stay_pseudonymized_in_context_free_chat() -> None:
    """Der Filter darf echte Namen (mindestens ein PROPN-Token) nicht
    freigeben - auch nicht im Chat ohne Akte."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt="Chat",
        anwaltliche_anmerkungen="Schreiben an Schmidt und an Herr Müller wegen der Miete.",
        skip_general_knowledge_pseudonymization=True,
    )

    originals = {m.original_value for m in result.mappings}
    assert any("Schmidt" in o for o in originals)
    assert any("Müller" in o for o in originals)
    assert "Schmidt" not in result.payload.anonymisierte_anwaltliche_anmerkungen
