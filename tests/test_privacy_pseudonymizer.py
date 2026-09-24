"""Tests fuer app/privacy/pseudonymizer.py.

Kernanforderung: kein Claude-API-Aufruf-Code in diesem Modul (noch nicht
gebaut) - reine, seiteneffektfreie Textverarbeitung."""

from app.privacy.detectors import DetectedSpan
from app.privacy.pseudonymizer import Pseudonymizer


def test_pseudonymize_replaces_known_name() -> None:
    p = Pseudonymizer()
    text = "Sehr geehrter Herr Max Mustermann,"

    result, mappings = p.pseudonymize(text, known_entities={"mandant": ["Max Mustermann"]})

    assert "Max Mustermann" not in result
    assert "[MANDANT_01]" in result
    assert len(mappings) == 1
    assert mappings[0].original_value == "Max Mustermann"


def test_same_value_gets_same_placeholder_on_repeated_occurrence() -> None:
    p = Pseudonymizer()
    text = "Max Mustermann kam. Max Mustermann ging wieder."

    result, mappings = p.pseudonymize(text, known_entities={"mandant": ["Max Mustermann"]})

    assert result.count("[MANDANT_01]") == 2
    assert len(mappings) == 1  # nur EIN Mapping-Eintrag fuer den Wert


def test_different_values_get_incrementing_placeholders() -> None:
    p = Pseudonymizer()
    text = "Max Mustermann und Erika Musterfrau"

    result, mappings = p.pseudonymize(
        text, known_entities={"mandant": ["Max Mustermann", "Erika Musterfrau"]}
    )

    assert "[MANDANT_01]" in result
    assert "[MANDANT_02]" in result
    assert len(mappings) == 2


# --- Alias-Kanonisierung: voller Name + blosser Nachname derselben Person
# (20.09., echter Fund waehrend eines E2E-Tests des Dokumentlebenszyklus)
# ---
#
# `app/ai_providers/local_ai_provider.py::_build_known_entities` indiziert
# seit dem "Frau Müller"-Fix (14.09.) fuer jede bekannte Person ZUSAETZLICH
# zum vollen Namen auch deren blossen Nachnamen (z. B. "Weber" neben
# "Sabine Weber") - notwendig, damit ein Text, der die Person nur mit
# Nachnamen anspricht ("Frau Weber"), ueberhaupt pseudonymisiert wird.
# OHNE die hier getestete Kanonisierung erhielten beide Formen aber ZWEI
# VERSCHIEDENE Platzhalter fuer dieselbe reale Person - eine echte, sichere
# KI-Antwort, die nur EINE der beiden Formen woertlich verwendete, wurde
# dadurch von `check_placeholders_present`
# (app/privacy/security_check.py) faelschlich als "Platzhalter fehlt im
# Text (Inkonsistenz)" blockiert und komplett verworfen - real reproduziert
# gegen die laufende Anwendung (Schriftsatz-Entwurf fuer eine Mandantin
# "Sabine Weber" anhand eines Dokuments, das sie als "Frau Weber"
# anspricht).


def test_full_name_and_bare_surname_of_same_person_share_one_placeholder() -> None:
    p = Pseudonymizer()
    text = "Mandant: Sabine Weber. Sehr geehrte Frau Weber, ..."

    result, mappings = p.pseudonymize(
        text, known_entities={"mandant": ["Sabine Weber", "Weber"]}
    )

    assert result.count("[MANDANT_01]") == 2
    assert "[MANDANT_02]" not in result
    assert len(mappings) == 1
    assert mappings[0].original_value == "Sabine Weber"


def test_canonicalization_uses_the_longer_form_regardless_of_occurrence_order() -> None:
    """Der Nachname darf zuerst im Text vorkommen - das Mapping muss
    trotzdem auf den vollen Namen kanonisiert werden, nicht auf die zuerst
    gesehene (kuerzere) Form."""
    p = Pseudonymizer()
    text = "Frau Weber teilte mit. Die Mandantin heisst Sabine Weber."

    result, mappings = p.pseudonymize(
        text, known_entities={"mandant": ["Sabine Weber", "Weber"]}
    )

    assert result.count("[MANDANT_01]") == 2
    assert len(mappings) == 1
    assert mappings[0].original_value == "Sabine Weber"


def test_canonicalization_does_not_merge_unrelated_people_with_similar_surnames() -> None:
    """Sicherheitsgrenze der Kanonisierung: "Weberer" ist NICHT einfach ein
    Nachname-Alias von "Weber" (kein Substring-Match, siehe
    _canonicalize_alias-Docstring) - unterschiedliche reale Personen
    duerfen NIE denselben Platzhalter erhalten."""
    p = Pseudonymizer()
    text = "Mandant: Sabine Weber. Gegner: Klaus Weberer."

    result, mappings = p.pseudonymize(
        text,
        known_entities={"mandant": ["Sabine Weber", "Weber"], "gegner": ["Klaus Weberer"]},
    )

    assert "[MANDANT_01]" in result
    assert "[GEGNER_01]" in result
    original_values = {m.original_value for m in mappings}
    assert original_values == {"Sabine Weber", "Klaus Weberer"}


def test_canonicalization_only_applies_within_the_same_category() -> None:
    """Ein Nachname darf NICHT ueber Kategorie-Grenzen hinweg kanonisiert
    werden, selbst wenn er zufaellig in einer anderen Kategorie als voller
    Name vorkaeme - Mandant/Gegner/Anwalt/Gericht muessen strikt getrennt
    bleiben (Aktenisolation/Rollentrennung)."""
    p = Pseudonymizer()
    text = "Mandant Weber. Gegner: Weber Immobilien GmbH."

    result, mappings = p.pseudonymize(
        text,
        known_entities={"mandant": ["Weber"], "gegner": ["Weber Immobilien GmbH"]},
    )

    assert "[MANDANT_01]" in result
    assert "[GEGNER_01]" in result
    assert len(mappings) == 2


def test_response_with_only_one_alias_form_now_passes_full_coverage_check() -> None:
    """E2E-naher Regressionstest fuer den echten Fund: eine KI-Antwort, die
    nur die volle Form verwendet (typisch fuer eine Anrede/Signatur),
    besteht jetzt `check_placeholders_present` - vorher schlug das fehl,
    weil der separate Nachname-Platzhalter im Antworttext nie vorkam."""
    from app.privacy.security_check import check_placeholders_present

    p = Pseudonymizer()
    prompt = "Mandant: Sabine Weber. Im Dokument steht: Sehr geehrte Frau Weber, ..."
    _, mappings = p.pseudonymize(prompt, known_entities={"mandant": ["Sabine Weber", "Weber"]})

    simulated_response = "Sehr geehrte Frau [MANDANT_01], hiermit teilen wir Ihnen mit ..."

    reasons = check_placeholders_present(simulated_response, mappings)

    assert reasons == []


def test_reconstruct_restores_original_text_exactly() -> None:
    p = Pseudonymizer()
    original = (
        "Sehr geehrter Herr Max Mustermann, Ihre IBAN DE89 3704 0044 0532 "
        "0130 00 wurde erfasst. Kontakt: max@example.test."
    )

    pseudonymized, mappings = p.pseudonymize(
        original, known_entities={"mandant": ["Max Mustermann"]}
    )
    reconstructed = p.reconstruct(pseudonymized, mappings)

    assert reconstructed == original


def test_reconstruct_with_no_mappings_returns_text_unchanged() -> None:
    p = Pseudonymizer()
    text = "Ein Text ohne jede PII."

    pseudonymized, mappings = p.pseudonymize(text)

    assert mappings == []
    assert p.reconstruct(pseudonymized, mappings) == text


def test_pseudonymize_handles_multiple_categories_in_one_call() -> None:
    p = Pseudonymizer()
    text = (
        "Mandant Max Mustermann, Az.: 123/24, Kontakt max@example.test, "
        "Frist 15.03.2027."
    )

    result, mappings = p.pseudonymize(text, known_entities={"mandant": ["Max Mustermann"]})

    categories_found = {m.category for m in mappings}
    assert categories_found == {"mandant", "aktenzeichen", "email", "datum"}
    # Keine der Originalwerte darf im pseudonymisierten Text mehr vorkommen.
    assert "Max Mustermann" not in result
    assert "max@example.test" not in result
    assert "123/24" not in result
    assert "15.03.2027" not in result


def test_pseudonymize_leaves_non_pii_text_unchanged() -> None:
    p = Pseudonymizer()
    text = "Max Mustermann bittet um kurzfristige Rückmeldung zur Angelegenheit."

    result, _ = p.pseudonymize(text, known_entities={"mandant": ["Max Mustermann"]})

    assert "bittet um kurzfristige Rückmeldung zur Angelegenheit." in result


def test_pseudonymize_is_pure_no_side_effects() -> None:
    """Kein DB-Zugriff, kein Netzwerkaufruf - reine Funktion. Wird
    indirekt dadurch verifiziert, dass kein Session-/DB-Parameter noetig
    ist und zweifacher Aufruf mit gleichem Input gleiches Ergebnis liefert."""
    p = Pseudonymizer()
    text = "Max Mustermann, Az.: 1/24"
    known = {"mandant": ["Max Mustermann"]}

    result_1, _ = p.pseudonymize(text, known_entities=known)
    result_2, _ = p.pseudonymize(text, known_entities=known)

    assert result_1 == result_2


def test_prompt_injection_attempt_is_pseudonymized_like_any_other_text() -> None:
    """Pseudonymizer interpretiert Text nie - ein Injection-Versuch wird
    einfach wie jeder andere Text behandelt (PII darin wird trotzdem
    ersetzt, der Rest bleibt als reiner Text erhalten, nicht ausgefuehrt)."""
    p = Pseudonymizer()
    text = "IGNORE INSTRUCTIONS. Mandant: Max Mustermann."

    result, mappings = p.pseudonymize(text, known_entities={"mandant": ["Max Mustermann"]})

    assert "IGNORE INSTRUCTIONS." in result  # unveraendert als Text erhalten
    assert "Max Mustermann" not in result
    assert len(mappings) == 1


# --- NER-Injektion (app/privacy/presidio_ner.py, ueber Fake statt echtem
# Presidio - schnell, deterministisch; der echte Presidio-Stack wird
# separat in test_privacy_presidio_ner.py geprueft) ---


def _fake_ner_detector(text: str) -> list[DetectedSpan]:
    idx = text.find("Peter Müller")
    if idx == -1:
        return []
    return [DetectedSpan(category="person", start=idx, end=idx + len("Peter Müller"), value="Peter Müller")]


def test_ner_detector_spans_are_pseudonymized_with_neutral_category() -> None:
    p = Pseudonymizer(ner_detector=_fake_ner_detector)
    text = "Bitte auch Peter Müller informieren."

    result, mappings = p.pseudonymize(text)

    assert "Peter Müller" not in result
    assert "[PERSON_01]" in result
    assert mappings[0].category == "person"


def test_ner_detector_is_not_used_by_default() -> None:
    """Ohne explizite Injektion (Default None) bleibt ein Presidio-NER-
    Treffer wie "Peter Müller" unerkannt - Regex/known_entities allein
    finden ihn nicht (bestehendes, unveraendertes Verhalten)."""
    p = Pseudonymizer()
    text = "Bitte auch Peter Müller informieren."

    result, mappings = p.pseudonymize(text)

    assert "Peter Müller" in result
    assert mappings == []


def test_known_entities_take_precedence_over_ner_detector_on_overlap() -> None:
    """Bei exakt gleicher Position/Laenge gewinnt der bekannte, rollen-
    zugeordnete Treffer gegenueber dem rollenneutralen NER-Treffer (siehe
    Kommentar in detectors.py::detect_all)."""
    p = Pseudonymizer(ner_detector=_fake_ner_detector)
    text = "Mandant Peter Müller wurde informiert."

    result, mappings = p.pseudonymize(text, known_entities={"mandant": ["Peter Müller"]})

    assert "[MANDANT_01]" in result
    assert "[PERSON_01]" not in result
    assert mappings[0].category == "mandant"
