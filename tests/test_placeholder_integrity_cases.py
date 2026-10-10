"""Platzhalter-Integritaet der Modellantwort: gueltige, unbekannte, doppelte und fehlende Platzhalter (12.10.2026).

Hintergrund: Claude schrieb in ca. 14 % der Laeufe im Empfaengerblock "[ADRESSE_01]", obwohl kein Adress-Platzhalter
bereitgestellt war. Die Ausgabepruefung blockiert das KORREKT (fail-closed); es gibt KEINE automatische Zuordnung
unbekannter Platzhalter. Diese Tests sichern das Verhalten ab (synthetische Werte)."""

from __future__ import annotations

import pytest

from app.privacy.pseudonymizer import PseudonymMapping
from app.privacy.security_check import check_response_placeholder_integrity

MAPPINGS = [
    PseudonymMapping(placeholder="[PERSON_01]", category="person", original_value="Olaf Thiessen"),
    PseudonymMapping(placeholder="[ORT_01]", category="ort", original_value="Beispielstadt"),
    PseudonymMapping(placeholder="[DATUM_01]", category="datum", original_value="30.11.2026"),
]


def _issues(text: str, **kw) -> list[str]:
    return check_response_placeholder_integrity(text, MAPPINGS, **kw)


def test_valid_placeholders_pass() -> None:
    assert _issues("[PERSON_01] aus [ORT_01] muss bis [DATUM_01] zahlen.") == []


def test_duplicate_use_of_a_valid_placeholder_is_allowed() -> None:
    assert _issues("[PERSON_01] schuldet. Wir fordern [PERSON_01] auf, bis [DATUM_01] zu zahlen; [PERSON_01] bleibt in [ORT_01].") == []


@pytest.mark.parametrize("invented", ["[ADRESSE_01]", "[PERSON_02]", "[ORT_09]", "[FIRMA_01]"])
def test_unknown_placeholder_is_blocked_never_mapped_automatically(invented: str) -> None:
    issues = _issues(f"An die Gegenseite\n{invented}\n\n[PERSON_01] aus [ORT_01] bis [DATUM_01].")
    assert issues, "ein unbekannter Platzhalter muss fail-closed gemeldet werden"
    assert any(invented in issue or "Platzhalter" in issue for issue in issues)


def test_unknown_placeholder_is_blocked_even_if_all_known_ones_are_present_and_valid() -> None:
    assert _issues("[PERSON_01] [ORT_01] [DATUM_01] und [ADRESSE_01]")


@pytest.mark.parametrize("altered", ["[person_01]", "[PERSON_1]", "[PERSON_001]", "[Person_01]"])
def test_altered_placeholder_spelling_is_blocked(altered: str) -> None:
    text = f"{altered} aus [ORT_01] bis [DATUM_01]"
    if altered == "[PERSON_1]":  # Muster verlangt mindestens eine Ziffer; abweichende Nummer = unbekannt
        assert _issues(text)
    else:
        assert _issues(text)


def test_missing_placeholder_matters_only_when_full_coverage_is_required() -> None:
    text = "[PERSON_01] aus [ORT_01] muss zahlen."  # [DATUM_01] fehlt
    assert _issues(text, require_full_coverage=True), "Pflichtmodus: fehlender Platzhalter wird gemeldet"
    assert _issues(text, require_full_coverage=False) == []  # Chat-/Teilantworten duerfen Platzhalter weglassen


def test_prose_without_brackets_is_not_treated_as_a_placeholder() -> None:
    assert _issues("Die Anschrift (ADRESSE_01 genannt) bleibt offen; [PERSON_01] aus [ORT_01] bis [DATUM_01].") == []


def test_original_value_leak_is_still_blocked_next_to_valid_placeholders() -> None:
    assert _issues("[PERSON_01] und Olaf Thiessen wohnen in [ORT_01] bis [DATUM_01].")


def test_insert_hint_for_missing_data_is_allowed_as_prompted() -> None:
    assert _issues("An die Gegenseite\n[Anschrift einsetzen]\n[Datum einsetzen]\n[PERSON_01] aus [ORT_01] bis [DATUM_01].") == []
