"""Anrede-/Rollenkontext, Folgeerwaehnungen und Feminin-Singular-Kontext (Folgezyklus 13.10.2026).

Getrennter Benchmark mit neuen Namen: scripts/bench_name_followups.py. Echter Presidio-/spaCy-Stack ueber das Gateway,
nur synthetische Daten. Ausgangsbefunde: "Herr Sommer", "Zeugin Koenig", "Rechtsanwaeltin Sommer" vollstaendig im
Klartext; nach "Herr Dr. Graf" blieb die Folgeerwaehnung "Graf hat ..." im Klartext (83 von 1288 Faellen)."""

from __future__ import annotations

import re

import pytest

from app.ai_providers.local_ai_provider import _normalize_document_text
from app.privacy import presidio_ner as pn
from app.privacy.gateway import ClaudePrivacyGateway


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _payload(gateway, text: str, mode: str = "mask") -> str:
    result = gateway.prepare_request(
        purpose="chat_response",
        sachverhalt="Akte: Beispielakte\n[Vertrag] " + _normalize_document_text(text),
        anwaltliche_anmerkungen="Fasse das Dokument zusammen.",
        review_mode=mode,
    )
    assert result.allowed, result.reasons  # weder Pseudonymisierung noch Restrisiko-Scan blockieren
    return result.payload.anonymisierter_sachverhalt


# --- Anrede-/Rollenkontext -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "intro, name",
    [
        ("Herr", "Sommer"), ("Frau", "Winter"), ("Herrn", "Vogel"), ("Herr Dr.", "Graf"), ("Frau Prof.", "König"),
        ("Zeugin", "König"), ("Rechtsanwältin", "Sommer"), ("Mandant", "Horn"), ("Gutachter", "Stein"),
    ],
)
@pytest.mark.parametrize("mode", ["mask", "ask"])
def test_names_after_titles_and_roles_are_protected(gateway, intro, name, mode) -> None:
    payload = _payload(gateway, f"Am 12.03.2026 erschien {intro} {name} in der Kanzlei.", mode)
    assert name not in payload


@pytest.mark.parametrize(
    "sentence, keep",
    [
        ("Die Frau des Mieters war anwesend.", ["Frau", "Mieters"]),
        ("Frau Rechtsanwältin Weber hat geschrieben.", ["Frau"]),
        ("Der Zeuge hat ausgesagt.", ["Zeuge"]),
        ("Zeuge  |  Name  |  Ort", ["Zeuge", "Name", "Ort"]),
    ],
)
def test_title_rule_does_not_mask_common_nouns(gateway, sentence, keep) -> None:
    payload = _payload(gateway, sentence)
    for word in keep:
        assert word in payload, (word, payload)
    if "Weber" in sentence:
        assert "Weber" not in payload


def test_title_rule_ignores_existing_placeholders() -> None:
    assert pn.detect_titled_names("Herr [PERSON_01] hat angerufen.") == []


# --- Folgeerwaehnungen -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("intro", ["Herr Dr.", "Thomas", "Petra", "Frau", "Zeugin"])
@pytest.mark.parametrize(
    "followup",
    [
        "{N} hat die Unterlagen am Folgetag übergeben.",
        "Nach Rücksprache hat die {N} die Unterlagen übergeben.",
        "Das Schreiben ging an {N} zurück.",
    ],
)
@pytest.mark.parametrize("name", ["Graf", "Sommer", "Brockhagen"])
def test_followup_mentions_are_protected(gateway, intro, followup, name) -> None:
    text = f"Am 12.03.2026 erschien {intro} {name} in der Kanzlei. " + followup.format(N=name)
    assert name not in _payload(gateway, text)


def test_first_name_followup_is_protected(gateway) -> None:
    payload = _payload(gateway, "Am 12.03.2026 erschien Petra Brockhagen in der Kanzlei. Petra hat später angerufen.")
    assert "Petra" not in payload and "Brockhagen" not in payload


def test_propagation_does_not_touch_other_words(gateway) -> None:
    payload = _payload(gateway, "Herr Sommer hat den Mietvertrag gekündigt. Die Kündigung ging am 12.03.2026 ein.")
    assert "Mietvertrag" in payload and "Kündigung" in payload and "Sommer" not in payload


# --- Feminin-Singular-Kontext ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["Kaufmann", "Neumann", "Schneider", "Jäger", "Fischer", "Koch"])
@pytest.mark.parametrize(
    "template",
    ["Nach Rücksprache hat die {N} die Unterlagen übergeben.", "Im Termin wurde mit der {N} die Zahlung erörtert."],
)
def test_masculine_person_noun_in_feminine_singular_is_a_name(gateway, name, template) -> None:
    assert name not in _payload(gateway, template.format(N=name))


@pytest.mark.parametrize(
    "sentence, keep",
    [
        ("Die Steuer wurde mit 1.200,00 EUR festgesetzt.", ["Steuer", "1.200,00"]),
        ("Die Kammer hat die Berufung zurückgewiesen.", ["Kammer", "Berufung"]),
        ("Mit der Kammer wurde der Streitwert von 12.400,00 EUR erörtert.", ["Kammer", "Streitwert", "12.400,00"]),
        ("Die Fischer haben die Netze eingeholt.", ["Fischer", "Netze"]),  # Plural
        ("Die Mieter hat die Miete gemindert.", ["Mieter", "Miete"]),  # Rollenwort, Probe 0
        ("Die Kläger ist nicht erschienen.", ["Kläger"]),
        ("Die Auftraggeber hat die Abnahme verweigert.", ["Auftraggeber", "Abnahme"]),
        ("Die Leiter ist umgefallen.", ["Leiter"]),
    ],
)
def test_feminine_context_rule_keeps_common_nouns(gateway, sentence, keep) -> None:
    payload = _payload(gateway, sentence)
    for word in keep:
        assert word in payload, (word, payload)


def test_masculine_person_noun_check() -> None:
    assert pn._is_masculine_person_noun("Kaufmann") and pn._is_masculine_person_noun("Fischer")
    assert pn._is_masculine_person_noun("Koch")  # Koechin (Umlaut-Movierung)
    assert not pn._is_masculine_person_noun("Kammer")  # "Kaemmerin" waere ein Fehlalarm auf "die Kammer"
    assert not pn._is_masculine_person_noun("Steuer")


def test_masculine_article_occupational_surname_is_masked_non_interactively(gateway) -> None:
    """Frueher bekannte Grenze: maskuliner Artikel + Berufsname ohne Einfuehrung. Seit dem Folgezyklus eine Rueckfrage
    (review.py, `_is_masculine_occupation_after_article`); nicht-interaktiv vorsichtshalber maskiert."""
    assert "Fischer" not in _payload(gateway, "Der Fischer hat am 12.03.2026 angerufen.")


# --- Red-Team-Befunde (unabhaengiger Review, Folgezyklus 13.10.2026) --------------------------------------------------


@pytest.mark.parametrize(
    "sentence, keep",
    [
        ("Die Klägerin trägt vor, dass der Mieter Mängel angezeigt hat. Die Mängel betreffen die Heizung.", ["Mängel"]),
        ("Es ist streitig, ob der Mieter Schadensersatz verlangen kann.", ["Schadensersatz"]),
        ("Im Ortstermin hat der Sachverständige Feuchtigkeit festgestellt.", ["Feuchtigkeit"]),
        ("Hätte der Vermieter Kaution fristgerecht abgerechnet, wäre die Klage entbehrlich. Die Kaution betrug 2.550,00 EUR.",
         ["Kaution", "2.550,00"]),
        ("Die Klägerin ist Mieterin der Wohnung.", ["Wohnung"]),
        ("Mieter  Wohnung  Fläche", ["Wohnung", "Fläche"]),
        ("Herr Vorsitzender, wir beantragen Vertagung.", ["Vorsitzender", "Vertagung"]),
        ("Frau Kollegin, der Streitwert beträgt 12.400,00 EUR.", ["Kollegin", "Streitwert", "12.400,00"]),
    ],
)
def test_role_word_followed_by_object_is_not_a_name(gateway, sentence, keep) -> None:
    payload = _payload(gateway, sentence)
    for word in keep:
        assert word in payload, (word, payload)


@pytest.mark.parametrize(
    "text, name",
    [
        ("Die Zeugin Licht hat ausgesagt. Licht ergänzte später.", "Licht"),  # Satzanfang
        ("Der Mieter Kern hat gekündigt.", "Kern"),
        ("Sehr geehrter Herr\nSonntag,\nbitte zahlen Sie.", "Sonntag"),  # Zeilenumbruch (-> zwei Leerzeichen)
        ("Hr. Gast und Fr. Kern waren anwesend.", "Gast"),
        ("Hr. Gast und Fr. Kern waren anwesend.", "Kern"),
        ("Herr von Sonntag hat geschrieben. Sonntags Schreiben ging ein.", "Sonntag"),  # Partikel + Genitiv
        ("Herr van der Sommer hat geschrieben.", "Sommer"),
        ("Frau Sonntag-Wiesner rief an. Später rief Sonntag erneut an.", "Sonntag"),  # Bindestrich-Bestandteil
        ("Herr Klein hat geschrieben. KLEIN verlangt Zahlung.", "KLEIN"),  # Versalien
        ("Die Eheleute Vogel haben die Miete gemindert.", "Vogel"),
        ("Mit freundlichen Grüßen\ngez. Vogel\nRechtsanwalt", "Vogel"),
        ("gez. Sommer", "Sommer"),
        ("Ansprechpartner: Sommer", "Sommer"),
        ("Sommer, Klaus\nGeschäftszeichen: 12/2026", "Sommer"),  # invertierte Namenszeile
        ("Frau Rechtsanwältin Weber hat angerufen.", "Weber"),
    ],
)
def test_red_team_bypasses_are_closed(gateway, text, name) -> None:
    payload = _payload(gateway, text)
    assert not re.search(r"(?<![\wÄÖÜäöüß])" + re.escape(name) + r"(?![\wÄÖÜäöüß])", payload), payload


def test_rubrum_does_not_block_fail_closed(gateway) -> None:
    """Eine erprobte Rubrum-Regel ("Sommer ./. Baugesellschaft") wurde verworfen: der Punkt in "./." galt im Imperativ-Filter
    als Satzende, der Restrisiko-Scan fand den Treffer dennoch -> Blockade. Hier: kein Block (Rubrum = offene Grenze)."""
    _payload(gateway, "In Sachen Sommer ./. Baugesellschaft wegen Werklohn")  # assert allowed in _payload


@pytest.mark.parametrize("office", ["Vorsitzender", "Kollegin", "Präsident", "Staatsanwalt"])
def test_office_titles_after_salutation_are_not_names(office) -> None:
    assert [span.value for span in pn.detect_titled_names(f"Herr {office}, bitte.")] == []


# --- Verbfinale Saetze (Befund installierter Build 38361af: "hat die Zeugin Koenig ausgesagt" im Klartext) -------------


@pytest.mark.parametrize(
    "sentence, name",
    [
        ("Nach Rücksprache hat die Zeugin König ausgesagt.", "König"),
        ("Nach Rücksprache hat der Zeuge Sommer ausgesagt.", "Sommer"),
        ("Nach Rücksprache hat die Zeugin Brockhagen angerufen.", "Brockhagen"),
    ],
)
def test_name_after_role_in_verb_final_clause_is_protected(gateway, sentence, name) -> None:
    assert name not in _payload(gateway, sentence)


@pytest.mark.parametrize(
    "sentence, keep",
    [
        ("Nach Rücksprache hat der Sachverständige Feuchtigkeit festgestellt.", "Feuchtigkeit"),
        ("Nach Rücksprache hat der Mieter Mängel angezeigt.", "Mängel"),
        ("Nach Rücksprache hat der Vermieter Kaution abgerechnet.", "Kaution"),
        ("Hätte der Vermieter Kaution fristgerecht abgerechnet, wäre alles gut.", "Kaution"),
        ("Nach Rücksprache hat der Gutachter Proben entnommen.", "Proben"),
    ],
)
def test_object_after_role_in_verb_final_clause_stays_readable(gateway, sentence, keep) -> None:
    assert keep in _payload(gateway, sentence)


# Bekannte Grenze: der Parser liest manche Namen in dieser Stellung als Objekt ("hat die Mandantin Licht unterschrieben":
# Licht = oa) - dann nicht von einem echten Objekt zu unterscheiden.
@pytest.mark.xfail(reason="Parser liest Namen als Objekt des Partizips", strict=False)
def test_known_limit_name_parsed_as_object(gateway) -> None:
    assert "Licht" not in _payload(gateway, "Nach Rücksprache hat die Mandantin Licht unterschrieben.")


# --- Maskuliner Artikel + Berufsname: Rueckfrage statt Entscheidung (Folgezyklus 13.10.2026) ---------------------------


def _ask(gateway, text: str):
    return gateway.prepare_request(
        purpose="chat_response",
        sachverhalt="Akte: Beispielakte\n[Vertrag] " + _normalize_document_text(text),
        anwaltliche_anmerkungen="Fasse das Dokument zusammen.",
        review_mode="ask",
    )


@pytest.mark.parametrize("name", ["Fischer", "Kaufmann", "Neumann", "Koch"])
def test_masculine_article_occupational_surname_is_asked(gateway, name) -> None:
    result = _ask(gateway, f"Der {name} hat am 12.03.2026 angerufen.")
    # Rueckfrage - oder bereits automatisch erkannt (dann ohne Rueckfrage pseudonymisiert, z. B. "Neumann").
    assert (result.review_required and name in [c.term for c in result.review_candidates]) or (
        result.allowed and name not in result.payload.anonymisierter_sachverhalt
    )
    assert name not in _payload(gateway, f"Der {name} hat am 12.03.2026 angerufen.")  # nicht-interaktiv: maskiert


@pytest.mark.parametrize(
    "sentence",
    [
        "Der Mieter hat angerufen.", "Der Richter hat entschieden.", "Der alte Fischer hat angerufen.",
        "Die Fischer haben die Netze eingeholt.", "Der Kaufmann im Sinne des HGB haftet für die Verbindlichkeiten.",
        "Der Vermieter hat die Kaution einbehalten.", "Der Sachverständige hat die Feuchtigkeit gemessen.",
    ],
)
def test_masculine_occupation_rule_does_not_ask_for_roles_and_definitions(gateway, sentence) -> None:
    result = _ask(gateway, sentence)
    assert not result.review_required, [c.term for c in result.review_candidates]
