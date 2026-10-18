"""§76: Antwortqualitaets-Benchmark `benchmarks/legal_answers_v1` - Konzept, Spezifikationen, Validator, Vorpruefung.

Kein Lauf mit echten Modellantworten, keine Cloud-Aufrufe. Geprueft wird, dass die Spezifikationen an Akte, Referenz
und freigegebene Quellen gebunden sind, Aenderungen der Referenz erkannt werden, der Test-Split eingefroren ist und die
Vorpruefung NUR Hinweise liefert (keine Note). Die Beispielantworten sind von Hand geschrieben, keine Modellausgaben."""

from __future__ import annotations

import copy

import pytest

from scripts.legal_bench import answer_specs, corpus


def _env():
    rubric = answer_specs.load_rubric()
    register = corpus.validate_register(corpus.load_register(), corpus.ValidationReport())
    cases = {c["id"]: c for c in corpus.load_cases()}
    specs = {s["id"]: s for s in answer_specs.load_specs()}
    return rubric, register, cases, specs


def _errors(spec: dict) -> list[str]:
    rubric, register, cases, _ = _env()
    report = answer_specs.SpecReport()
    answer_specs.validate_spec(spec, cases, register, rubric, report)
    return report.errors


def test_all_specs_are_valid_and_cover_dev_and_test() -> None:
    assert answer_specs.validate_all().ok, answer_specs.validate_all().errors
    _, _, cases, specs = _env()
    assert {s["split"] for s in specs.values()} == {"dev", "test"}
    for spec in specs.values():
        assert cases[spec["case"]].get("solution"), spec["id"]  # nur Faelle mit belegter Referenzloesung
        assert spec["status"] == "entwurf_juristische_freigabe_offen"  # keine Zuverlaessigkeit behauptet


def test_rubric_separates_dimensions_and_marks_qualified_review() -> None:
    rubric = answer_specs.load_rubric()
    dims = {d["id"]: d for d in rubric["dimensions"]}
    assert set(dims) == {"ergebnis", "rechtsgrundlagen", "tatsachen", "fristen", "unsicherheiten",
                         "unbelegte_behauptungen", "gefaehrliche_fehler"}
    for dim in ("ergebnis", "unbelegte_behauptungen", "gefaehrliche_fehler"):
        assert dims[dim]["evaluation"] == "juristische_pruefung"
    assert dims["gefaehrliche_fehler"]["disqualifying"] is True
    assert any("Wortlautvergleich" in rule for rule in rubric["rules"])


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda s: s["criteria"][1].update(norm="§ 999"), "§ 999"),
        (lambda s: s["criteria"][3].update(fact="F99"), "F99"),
        (lambda s: s["criteria"][5].update(deadline="31.12.2099"), "31.12.2099"),
        (lambda s: s.update(source_refs=["Q-OPENJUR"]), "source_refs"),
        (lambda s: s.update(case_fingerprint="0" * 64), "Fingerabdruck"),
        (lambda s: s["criteria"][0].update(evaluation="teilautomatisch"), "juristische Pruefung"),
        (lambda s: s.update(dangerous_errors=[]), "gefaehrlichen Fehler"),
        (lambda s: s.update(criteria=[c for c in s["criteria"] if c["dimension"] != "fristen"]), "fristen"),
        (lambda s: s["criteria"][4].update(accept_any=[["etwas ganz anderes"]]), "Aktenwert"),
    ],
)
def test_validator_rejects_untraceable_or_unsafe_specs(mutate, expected) -> None:
    _, _, _, specs = _env()
    spec = copy.deepcopy(specs["LA-D005"])
    mutate(spec)
    assert any(expected in e for e in _errors(spec)), _errors(spec)


def test_test_split_spec_must_stay_frozen() -> None:
    _, _, _, specs = _env()
    spec = copy.deepcopy(specs["LA-T004"])
    spec.pop("frozen")
    assert any("eingefroren" in e for e in _errors(spec))


def test_changed_reference_is_detected_by_fingerprint() -> None:
    _, _, cases, specs = _env()
    case = copy.deepcopy(cases["LE-D005"])
    before = answer_specs.case_fingerprint(case)
    case["reference"]["deadlines"].append({"date": "01.01.2030", "kind": "x", "doc": "D1"})
    assert answer_specs.case_fingerprint(case) != before == specs["LA-D005"]["case_fingerprint"]


# --- Vorpruefung: Hinweise, keine Note ---------------------------------------------------------------------------------

REFERENCE_LIKE = (
    "Dem Mieter steht das Vorkaufsrecht zu: auch der Verkauf an eine Gesellschaft mit denselben Gesellschaftern ist ein "
    "Verkauf an einen Dritten (§ 577 Abs. 1 BGB). Die Teilungserklärung vom 12.06.2019 erfolgte nach der Überlassung. "
    "Ausübung innerhalb von zwei Monaten (§ 469 Abs. 2 BGB); da der Zugang streitig ist, vorsorglich bis 28.04.2026.")


def _precheck(answer: str, sid: str = "LA-D005") -> dict:
    _, _, cases, specs = _env()
    spec = specs[sid]
    return answer_specs.precheck(spec, cases[spec["case"]], answer)


def test_precheck_finds_paraphrases_and_never_grades() -> None:
    result = _precheck(REFERENCE_LIKE)
    assert result["art"] == "vorpruefung" and "Keine Bewertung" in result["hinweis"]
    assert all(c["status"] == "hinweis_gefunden" for c in result["kriterien"].values()), result["kriterien"]
    assert set(result["gefaehrliche_fehler"].values()) == {"kein_hinweis"}
    assert not any(key in result for key in ("note", "score", "bestanden", "punkte"))


def test_negated_result_raises_a_suspicion_although_terms_match() -> None:
    """Grenze des Wortlautvergleichs: "steht ... nicht zu" trifft die Ergebnisvariante UND den Verdacht - genau
    deshalb entscheidet ueber das Ergebnis eine qualifizierte Person."""
    result = _precheck(REFERENCE_LIKE.replace("Dem Mieter steht das Vorkaufsrecht zu", "Dem Mieter steht das Vorkaufsrecht nicht zu"))
    assert result["kriterien"]["K1"]["status"] == "hinweis_gefunden"
    assert result["gefaehrliche_fehler"]["G1"] == "verdacht"


def test_unsafe_later_deadline_is_flagged_as_suspicion() -> None:
    result = _precheck("Die Klage ist zu erheben; die Frist endet sicher am 06.02.2026.", "LA-T004")
    assert result["gefaehrliche_fehler"]["G2"] == "verdacht" and result["kriterien"]["K7"]["status"] == "nicht_gefunden"


def test_citations_outside_file_and_source_are_listed_for_review() -> None:
    result = _precheck(REFERENCE_LIKE + " Vgl. auch BGH VIII ZR 99/25 und § 566 BGB.")
    assert "§ 566" in result["nicht_in_akte_oder_quelle"]
    assert "VIII ZR 99/25" in result["nicht_in_akte_oder_quelle"]  # Aktenzeichen mit roemischer Senatsnummer
    assert "§ 577" not in result["nicht_in_akte_oder_quelle"]
