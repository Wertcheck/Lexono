"""§43 Stufe 1: isolierte Grundlage der Cloud-Zweitmeinung (Antwortvalidierung, Laengengrenze, Vergleich,
Uebernahme). Rein lokal - kein Netz, kein API-Aufruf, kein Gateway-Zweck. Nur synthetische Kurztexte."""

import ast
import copy
import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.privacy.security_check import ALLOWED_PURPOSES
from app.second_opinion.comparison import (
    UNREVIEWED,
    AdoptionRefused,
    Agreement,
    Comparison,
    LocalDeadline,
    LocalSnapshot,
    OpinionOutcome,
    OpinionStatus,
    adopt_hint,
    compare,
    snapshot_from,
)
from app.second_opinion.validation import check_length, dates_mentioned_in, validate_response

SENT = ("Dateiname: Mahnung_2026.pdf\n\nBeispiel Reinigung GmbH, Beispielstadt, 10.07.2026\n2. Mahnung\n"
        "Bitte zahlen Sie letztmalig bis zum 31.07.2026. Termin zur Besprechung am 5.8.2026. "
        "Zustellung erfolgte am 3. Juli 2026.")


def _raw(**fields) -> str:
    data = {"typ": "Mahnung", "frist": ["31.07.2026"], "termin": [], "fristbeginn": []}
    data.update(fields)
    return json.dumps(data, ensure_ascii=False)


# --- Antwortvalidierung -------------------------------------------------------------------------------------------

def test_valid_response_is_accepted_with_dates_from_all_spellings() -> None:
    result = validate_response(_raw(termin=["05.08.2026"], fristbeginn=["03.07.2026"]), SENT)
    assert result.accepted, result.reasons
    assert result.opinion.typ == "Mahnung"
    assert result.opinion.frist == (date(2026, 7, 31),)
    assert result.opinion.termin == (date(2026, 8, 5),)  # im Text "5.8.2026"
    assert result.opinion.fristbeginn == (date(2026, 7, 3),)  # im Text "3. Juli 2026"


def test_code_fence_around_json_is_tolerated_but_prose_is_not() -> None:
    assert validate_response("```json\n" + _raw() + "\n```", SENT).accepted
    assert not validate_response("Hier meine Einschätzung: " + _raw(), SENT).accepted
    assert not validate_response(_raw() + "\nHinweis: bitte prüfen", SENT).accepted


@pytest.mark.parametrize("typ", ["Mahnschreiben", "mahnung", "", None, 3, ["Mahnung"]])
def test_only_allowed_document_types(typ) -> None:
    result = validate_response(_raw(typ=typ), SENT)
    assert not result.accepted and result.opinion is None


@pytest.mark.parametrize("value", ["31.7.2026", "2026-07-31", "31/07/2026", "31.07.26", "31.13.2026", "30.02.2026",
                                   " 31.07.2026", 31072026, None, "31. Juli 2026"])
def test_only_strict_valid_dates(value) -> None:
    assert not validate_response(_raw(frist=[value]), SENT).accepted


def test_invented_date_rejects_the_whole_answer() -> None:
    result = validate_response(_raw(frist=["31.07.2026", "15.08.2026"]), SENT)
    assert not result.accepted
    assert any("nicht im gesendeten Text" in r for r in result.reasons)


def test_date_must_appear_in_the_actually_sent_text_not_elsewhere() -> None:
    """Abgleich nur gegen den gesendeten (pseudonymisierten) Text: ein nur im Original genanntes Datum zaehlt nicht."""
    sent = "Bitte zahlen Sie bis zum 31.07.2026. Geburtsdatum: [GEBURTSDATUM_01]"
    assert not validate_response(_raw(frist=["31.07.2026"], fristbeginn=["14.03.1980"]), sent).accepted


@pytest.mark.parametrize("raw", [
    _raw(frist=["[DATUM_01]"]),
    _raw(typ="Mahnung", termin=["05.08.2026"], fristbeginn=[], frist=[]).replace("[]", '["PERSON_01"]', 1),
    json.dumps({"typ": "Mahnung", "frist": [], "termin": [], "fristbeginn": [], "begruendung": "an [PERSON_1]"}),
])
def test_placeholders_are_never_accepted(raw: str) -> None:
    """Eigene Pruefung (nicht nur Nebenwirkung von Typ-/Datumsregeln): der Grund muss der Platzhalter sein."""
    result = validate_response(raw, SENT)
    assert not result.accepted and result.reasons == ("Platzhalter in der Antwort",)


@pytest.mark.parametrize("raw, reason", [
    (json.dumps({"typ": "Mahnung", "frist": [], "termin": []}), "fehlende Felder"),
    (_raw(begruendung="weil"), "unzulaessige Felder"),
    (_raw(empfehlung="Frist eintragen"), "unzulaessige Felder"),
    (_raw(frist="31.07.2026"), "keine Liste"),
    (_raw(fristdauern=-1), "fristdauern"),
    (_raw(fristdauern=True), "fristdauern"),
    (_raw(fristdauern="2"), "fristdauern"),
])
def test_missing_or_unexpected_fields_are_rejected(raw: str, reason: str) -> None:
    result = validate_response(raw, SENT)
    assert not result.accepted and any(reason in r for r in result.reasons)


@pytest.mark.parametrize("raw", [None, "", "   ", "null", "[]", '"Mahnung"', "{", "{'typ': 'Mahnung'}", 42])
def test_missing_or_malformed_answers_are_rejected(raw) -> None:
    result = validate_response(raw, SENT)
    assert not result.accepted and result.opinion is None


def test_contradictory_answer_is_rejected() -> None:
    result = validate_response(_raw(frist=["31.07.2026"], termin=["31.07.2026"]), SENT)
    assert not result.accepted and any("widerspruechlich" in r for r in result.reasons)


def test_size_limits() -> None:
    many = [f"{d:02d}.07.2026" for d in range(1, 32)]
    sent = " ".join(many)
    assert not validate_response(_raw(frist=many), sent).accepted
    assert not validate_response(_raw(frist=["31.07.2026"]) + " " * 5000, SENT).accepted


def test_without_sent_text_nothing_is_accepted() -> None:
    assert not validate_response(_raw(), "").accepted


def test_dates_mentioned_in_text() -> None:
    assert dates_mentioned_in(SENT) == {date(2026, 7, 10), date(2026, 7, 31), date(2026, 8, 5), date(2026, 7, 3)}
    assert dates_mentioned_in("Termin am 31.02.2026 und 1.1.27") == {date(2027, 1, 1)}


# --- Laengengrenze: nie stillschweigend kuerzen -------------------------------------------------------------------

def test_too_long_text_is_not_sent_and_not_truncated() -> None:
    text = "Einleitung. " * 50 + "Frist bis zum 31.12.2026."
    decision = check_length(text, max_chars=200)
    assert not decision.may_send and decision.reason.startswith("zu_lang")
    assert check_length(text, max_chars=len(text)).may_send
    assert not check_length("   ", max_chars=100).may_send
    with pytest.raises(ValueError):
        check_length(text, max_chars=0)
    import app.second_opinion.validation as validation

    assert not [name for name in dir(validation) if "trunc" in name.lower() or "kuerz" in name.lower()]


# --- Vergleich: Cloud getrennt, lokal unveraendert ----------------------------------------------------------------

LOCAL = LocalSnapshot(document_type="Rechnung", deadlines=(
    LocalDeadline(date(2026, 7, 31), "frist"),
    LocalDeadline(date(2026, 7, 10), "frist"),        # lokaler Fehlalarm (Briefdatum)
    LocalDeadline(date(2026, 7, 3), "termin"),
))


def _opinion(**fields):
    result = validate_response(_raw(**fields), SENT)
    assert result.accepted, result.reasons
    return OpinionOutcome(OpinionStatus.OK, result.opinion)


def test_comparison_marks_agreement_and_never_changes_local_results() -> None:
    before = copy.deepcopy(LOCAL)
    comparison = compare(LOCAL, _opinion(termin=["05.08.2026"], fristbeginn=["03.07.2026"]))
    assert LOCAL == before
    by_date = {h.due_date: h for h in comparison.hints}
    assert by_date[date(2026, 7, 31)].agreement is Agreement.MATCH
    assert by_date[date(2026, 7, 10)].agreement is Agreement.ONLY_LOCAL   # nur markiert, nicht entfernt
    assert by_date[date(2026, 8, 5)].agreement is Agreement.ONLY_CLOUD
    assert by_date[date(2026, 7, 3)].agreement is Agreement.DIFFERENT_KIND
    assert comparison.type_agreement is False and comparison.cloud_type == "Mahnung"
    assert LOCAL.document_type == "Rechnung"
    # jedes lokale Datum bleibt im Vergleich sichtbar
    assert {d.due_date for d in LOCAL.deadlines} <= set(by_date)


def test_cloud_hints_are_separate_and_unreviewed() -> None:
    comparison = compare(LOCAL, _opinion(termin=["05.08.2026"]))
    assert comparison.hints and all(h.source == "cloud" and h.review_state == UNREVIEWED for h in comparison.hints)
    assert not any(isinstance(h, LocalDeadline) for h in comparison.hints)


def test_local_results_are_read_only_inputs() -> None:
    class ReadOnly(SimpleNamespace):
        def __setattr__(self, name, value):
            raise AssertionError(f"lokales Objekt veraendert: {name}")

    rows = [ReadOnly(due_date=datetime(2026, 7, 31, 0, 0), reasoning="Mögliche Frist", review_status="unreviewed"),
            ReadOnly(due_date=date(2026, 8, 5), reasoning="Art: Termin", review_status="confirmed"),
            ReadOnly(due_date=date(2026, 9, 1), reasoning="Art: Frist", review_status="rejected"),
            ReadOnly(due_date=None, reasoning="Art: Fristbeginn", review_status="unreviewed"),
            ReadOnly(due_date=None, reasoning="Art: Fristdauer", review_status="unreviewed")]
    snapshot = snapshot_from("Mahnung", rows)
    assert snapshot.deadlines == (LocalDeadline(date(2026, 7, 31), "frist"), LocalDeadline(date(2026, 8, 5), "termin"))
    with pytest.raises(AttributeError):
        snapshot.document_type = "Rechnung"  # type: ignore[misc]


@pytest.mark.parametrize("status", [s for s in OpinionStatus if s is not OpinionStatus.OK])
def test_failure_states_yield_no_hints_and_no_adoption(status: OpinionStatus) -> None:
    comparison = compare(LOCAL, OpinionOutcome(status, detail="synthetisch"))
    assert comparison.status is status and comparison.hints == () and comparison.type_agreement is None
    fake = compare(LOCAL, _opinion(termin=["05.08.2026"])).hints[-1]
    with pytest.raises(AdoptionRefused):
        adopt_hint(comparison, fake, user_confirmed=True, actor="anwalt@kanzlei-qa.invalid")


@pytest.mark.parametrize("status", [s for s in OpinionStatus if s is not OpinionStatus.OK])
def test_adoption_checks_the_status_itself_even_if_hints_are_present(status: OpinionStatus) -> None:
    """Auch ein inkonsistent zusammengesetztes Objekt (Fehlerstatus, aber Hinweise) loest nie eine Uebernahme aus."""
    ok = compare(LOCAL, _opinion(termin=["05.08.2026"]))
    forged = Comparison(status=status, hints=ok.hints, type_agreement=None, cloud_type=None)
    hint = next(h for h in ok.hints if h.agreement is Agreement.ONLY_CLOUD)
    with pytest.raises(AdoptionRefused):
        adopt_hint(forged, hint, user_confirmed=True, actor="anwalt@kanzlei-qa.invalid")


def test_ok_status_without_opinion_counts_as_invalid() -> None:
    comparison = compare(LOCAL, OpinionOutcome(OpinionStatus.OK, None))
    assert comparison.status is OpinionStatus.INVALID and comparison.hints == ()


# --- Uebernahme nur ausdruecklich, nur als neuer unbestaetigter Vorschlag ------------------------------------------

def test_adoption_requires_explicit_user_and_creates_unreviewed_proposal() -> None:
    comparison = compare(LOCAL, _opinion(termin=["05.08.2026"]))
    hint = next(h for h in comparison.hints if h.agreement is Agreement.ONLY_CLOUD)
    for confirmed, actor in ((False, "anwalt@kanzlei-qa.invalid"), (True, ""), (True, "  "), ("yes", "a@b.invalid")):
        with pytest.raises(AdoptionRefused):
            adopt_hint(comparison, hint, user_confirmed=confirmed, actor=actor)  # type: ignore[arg-type]
    proposal = adopt_hint(comparison, hint, user_confirmed=True, actor="anwalt@kanzlei-qa.invalid")
    assert proposal.review_status == "unreviewed" and proposal.review_status != "confirmed"
    assert proposal.due_date == date(2026, 8, 5) and proposal.kind == "termin"
    assert proposal.origin == "cloud_zweitmeinung"
    assert hint.review_state == UNREVIEWED  # der Hinweis selbst wird nicht "bestaetigt"


@pytest.mark.parametrize("agreement", [Agreement.MATCH, Agreement.ONLY_LOCAL, Agreement.DIFFERENT_KIND])
def test_only_cloud_only_dates_can_be_adopted(agreement: Agreement) -> None:
    comparison = compare(LOCAL, _opinion(termin=["05.08.2026"], fristbeginn=["03.07.2026"]))
    hint = next(h for h in comparison.hints if h.agreement is agreement)
    with pytest.raises(AdoptionRefused):
        adopt_hint(comparison, hint, user_confirmed=True, actor="anwalt@kanzlei-qa.invalid")


def test_hint_from_another_comparison_is_refused() -> None:
    other = compare(LocalSnapshot("Mahnung", ()), _opinion(termin=["05.08.2026"]))
    comparison = compare(LOCAL, _opinion())
    with pytest.raises(AdoptionRefused):
        adopt_hint(comparison, other.hints[0], user_confirmed=True, actor="anwalt@kanzlei-qa.invalid")


# --- Isolation: kein Netz, kein Gateway-Zweck, nirgends eingebunden ------------------------------------------------

PACKAGE = Path(__file__).resolve().parents[1] / "app" / "second_opinion"


def test_package_has_no_network_db_or_provider_imports() -> None:
    forbidden = ("httpx", "requests", "socket", "urllib", "anthropic", "openai", "sqlalchemy", "app.ai_providers",
                 "app.privacy.gateway", "app.db", "app.models", "fastapi")
    for path in PACKAGE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                assert not name.startswith(forbidden), f"{path.name}: {name}"


def test_no_new_gateway_purpose_and_not_wired_into_the_app() -> None:
    assert not any("second" in p or "zweit" in p or "frist" in p for p in ALLOWED_PURPOSES)
    app_dir = PACKAGE.parent
    users = [p for p in app_dir.rglob("*.py")
             if PACKAGE not in p.parents and "second_opinion" in p.read_text(encoding="utf-8", errors="ignore")]
    assert users == []
