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

    def check(self, pseudonymized_text, mappings, *, purpose, unrecognized_name_scan_text=None):
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
