"""Nachname nach Artikel als handelnde Partei (26.10.2026) - "Der König hat den Mietvertrag unterschrieben".

Befund (scripts/bench_article_surname_nouns.py, Stand f42e716): 38 von 80 Faellen gingen ohne Rueckfrage und ohne
Maskierung hinaus, weil die Artikel-Lesart ein Personennomen bzw. den staerksten Namensrahmen verlangt (fehlt bei
König/Kaiser/Graf bzw. Winter/Sommer/Hahn). Enge Regel: nur haeufige Nachnamen, die zugleich Gattungswoerter sind, nur
als Subjekt einer Parteihandlung - und NUR als Rueckfrage (Modus "ask"), nicht als vorsichtige Maskierung im Modus
"mask": "Der Bauer hat die Pacht gezahlt" ist laut dokumentierter Entscheidung (test_privacy_feminine_article_names) ein
lesbar zu haltender Negativfall; ob das Wort dort ein Name ist, entscheidet der Mensch. Bekannte Kosten: solche
Rollensaetze erhalten im Chat eine Rueckfrage. Ausschliesslich synthetische Saetze; echter Presidio/spaCy-Stack."""

from __future__ import annotations

import pytest

from app.privacy.gateway import ClaudePrivacyGateway

SACH = "Akte: Beispielakte\n[Vertrag] "
ROLE = "nachname_nach_artikel_handlung"


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _run(gateway, text, mode="ask"):
    return gateway.prepare_request(purpose="formulate_draft", sachverhalt=SACH + text,
                                   anwaltliche_anmerkungen="Erstelle ein Schreiben.", review_mode=mode)


@pytest.mark.parametrize("sentence,word", [
    ("Der König hat den Mietvertrag am 01.02.2025 unterschrieben.", "König"),
    ("Der Kaiser hat am 12.03.2026 angerufen und um Rückruf gebeten.", "Kaiser"),
    ("Der Graf hat mit Schreiben vom 16.03.2024 widersprochen.", "Graf"),
    ("Der Bauer hat die Kaution in Höhe von 1.200,00 EUR gezahlt.", "Bauer"),
    ("Der Sommer hat den Mietvertrag am 01.02.2025 unterschrieben.", "Sommer"),
    ("Am 12.03.2026 hat der Winter die Kaution gezahlt.", "Winter"),
])
def test_surname_after_article_as_acting_party_is_asked(gateway, sentence, word) -> None:
    result = _run(gateway, sentence)
    candidate = next((c for c in result.review_candidates if c.term == word), None)
    assert candidate is not None and ROLE in candidate.roles, [c.term for c in result.review_candidates]


def test_candidate_is_question_only(gateway) -> None:
    result = _run(gateway, "Der König hat den Mietvertrag am 01.02.2025 unterschrieben.")
    assert next(c for c in result.review_candidates if c.term == "König").ask_only is True


def test_mask_mode_is_unchanged_so_the_documented_negative_stays_readable(gateway) -> None:
    """Dokumentierte Entscheidung (test_privacy_feminine_article_names): "Der Bauer hat die Pacht gezahlt" muss ohne
    Rueckfrage lesbar bleiben - im Modus "mask" wird die Absender-/Parteilesart daher NICHT vorsichtig maskiert."""
    result = _run(gateway, "Der Bauer hat die Pacht für das Feld gezahlt.", mode="mask")
    assert result.allowed and "Der Bauer hat die Pacht" in result.payload.anonymisierter_sachverhalt


@pytest.mark.parametrize("sentence,word", [
    ("Der Winter war streng und lang.", "Winter"),              # Gattungswort, keine Parteihandlung
    ("Der Bauer bestellt im Frühjahr das Feld.", "Bauer"),
    ("Der Hahn kräht jeden Morgen um fünf Uhr.", "Hahn"),
    ("Der König regierte das Land über dreißig Jahre.", "König"),
    ("Der Mieter hat die Kaution gezahlt.", "Mieter"),          # Rollenwort, kein Nachname
    ("Der Vermieter hat der Mieterhöhung widersprochen.", "Vermieter"),
    ("Der Bauer hat uns die Unterlagen übergeben.", "Bauer"),   # keine Parteihandlung im engen Sinn
])
def test_no_new_question_without_surname_list_word_or_party_act(gateway, sentence, word) -> None:
    result = _run(gateway, sentence)
    assert not any(c.term == word and ROLE in c.roles for c in result.review_candidates)


def test_not_personal_decision_still_lifts_only_the_question(gateway) -> None:
    from app.privacy.review import PrivacyDecisions

    result = gateway.prepare_request(
        purpose="formulate_draft", sachverhalt=SACH + "Der Bauer hat die Pacht für das Feld gezahlt.",
        anwaltliche_anmerkungen="Erstelle ein Schreiben.", review_mode="ask",
        privacy_decisions=PrivacyDecisions(not_personal=frozenset({"Bauer"})),
    )
    assert "Bauer" not in [c.term for c in result.review_candidates]
