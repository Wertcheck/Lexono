"""§71: quellenbasierte Faelle mit Referenzloesung (benchmarks/legal_e2e_v1: LE-D005, LE-D006, LE-T004).

- Validator: Referenzloesung nur fuer abgeleitete Faelle aus freigegebenen Quellen, Kriterien aus den Akten ableitbar.
- Rubrik (scripts/legal_bench/solution_score.py): Ergebnis, Normen, entscheidende Fakten, Fristen, offene Punkte
  GETRENNT - eine Referenzantwort besteht, falsche/luckenhafte Antworten scheitern (keine Textaehnlichkeit).
- E2E (offline, Fake-Embedding, kein Cloud-Aufruf): tatsaechlich gesendeter Payload enthaelt die Grundlage der
  Referenzloesung; keine Lecks; Dokumentfreigabe (Modus C) haelt das auffaellige Dokument zurueck.
Die juristische Qualitaet ECHTER Modellantworten ist hier NICHT gemessen (Offline-Writer)."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

from scripts.legal_bench import corpus, e2e
from scripts.legal_bench.solution_score import payload_basis, score_answer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_embedding_provider import FakeEmbeddingProvider  # noqa: E402

SOURCE_CASES = ("LE-D005", "LE-D006", "LE-T004")


def _case(cid: str) -> dict:
    return next(c for c in corpus.load_cases() if c["id"] == cid)


def _errors(case: dict) -> list[str]:
    register = corpus.validate_register(corpus.load_register(), corpus.ValidationReport())
    report = corpus.ValidationReport()
    corpus.validate_case(case, register, report)
    return report.errors


# --- Korpus und Validator --------------------------------------------------------------------------------------------


def test_source_cases_are_valid_and_use_only_released_sources() -> None:
    assert corpus.validate_corpus().ok
    register = {s["id"]: s for s in corpus.load_register()["sources"]}
    for cid in SOURCE_CASES:
        case = _case(cid)
        assert not case["synthetic"] and case["derivation"] == "abgeleitet" and "solution" in case
        for ref in case["source_refs"]:
            assert register[ref]["nutzungsrechte"]["status"] == "nutzbar"
            assert any(cid in used for used in register[ref]["verwendet_in"])
    for sid in ("Q-OPENREWI-VERWALTUNGSRECHT-KLAUSUR", "Q-OPENREWI-GRUNDRECHTE-FALLBUCH", "Q-ZJS-UEBUNGSFAELLE"):
        assert register[sid]["nutzungsrechte"]["status"] == "nicht_geprueft" and register[sid]["verwendet_in"] == []


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda c: c["solution"]["required_norms"].append("§ 999 BGB"), "§ 999 BGB"),
        (lambda c: c["solution"]["decisive_facts"].append("F99"), "F99"),
        (lambda c: c["solution"]["open_points"][0]["any_terms"].append("Erfundener Begriff"), "Erfundener Begriff"),
        (lambda c: c["solution"]["deadlines"].append("31.12.2099"), "31.12.2099"),
        (lambda c: c["solution"]["result_terms"].append("Mietminderung"), "Mietminderung"),
        (lambda c: c.update(synthetic=True, source_refs=[]), "abgeleitete Faelle"),
        (lambda c: c["solution"]["source"].update(ecli="ECLI:DE:BGH:2099:ERFUNDEN"), "belegtes Zitat"),
    ],
)
def test_the_validator_rejects_invented_or_unbacked_solution_criteria(mutate, expected) -> None:
    case = copy.deepcopy(_case("LE-D005"))
    mutate(case)
    assert any(expected in e for e in _errors(case)), _errors(case)


# --- Rubrik ----------------------------------------------------------------------------------------------------------

D005_REFERENCE_ANSWER = (
    "Dem Mieter steht das Vorkaufsrecht zu: Auch wenn beide Gesellschaften dieselben Gesellschafter haben, ist der "
    "Verkauf an die Uferblick Bestand GmbH & Co. KG ein Verkauf an einen Dritten (§ 577 Abs. 1 BGB, BGH VIII ZR 18/24). Das Wohnungseigentum wurde "
    "mit Teilungserklärung vom 12.06.2019 nach der Überlassung begründet; Kaufvertrag vom 24.02.2026, Kaufpreis "
    "287.500,00 EUR. Ausübung schriftlich gegenüber der Verkäuferin innerhalb von zwei Monaten (§ 469 Abs. 2 BGB). Der "
    "Zugang ist streitig (28.02.2026 laut Verkäuferin): vorsorglich bis 28.04.2026 ausüben; Zustellnachweis anfordern.")


def test_the_reference_answer_passes_every_criterion() -> None:
    result = score_answer(_case("LE-D005"), D005_REFERENCE_ANSWER)
    assert result["bestanden"], result


@pytest.mark.parametrize(
    "change, failed",
    [
        (lambda a: a.replace("Dem Mieter steht das Vorkaufsrecht zu", "Ein Vorkaufsrecht besteht nicht"), "ergebnis"),
        (lambda a: a.replace("§ 469 Abs. 2 BGB", "der Frist"), "normen_fehlend"),
        (lambda a: a.replace("vorsorglich bis 28.04.2026 ausüben", "rechtzeitig ausüben"), "fristen_fehlend"),
        (lambda a: a.replace("; Zustellnachweis anfordern", ""), "offene_punkte_fehlend"),
        (lambda a: a.replace("12.06.2019", "einem früheren Datum"), "fakten_fehlend"),
    ],
)
def test_wrong_or_incomplete_answers_fail_the_specific_criterion(change, failed) -> None:
    result = score_answer(_case("LE-D005"), change(D005_REFERENCE_ANSWER))
    assert not result["bestanden"]
    if failed == "ergebnis":
        assert not result["ergebnis"]["ok"] and result["ergebnis"]["falsches_ergebnis"]
    else:
        assert result[failed], result


def test_payload_basis_of_the_full_file_is_complete() -> None:
    for cid in SOURCE_CASES:
        case = _case(cid)
        full = "\n".join(corpus.doc_text(d) for d in case["documents"])
        assert payload_basis(case, full)["vollstaendig"], cid


# --- E2E (offline) -----------------------------------------------------------------------------------------------------

# §71 gemessen: in LE-T004 maskierte die NER "Visa-Antraege" als Organisation (F8 im Payload unlesbar). §72 behoben
# (`is_admin_procedure_compound`, entwickelt am Dev-Fall LE-D007) - keine bekannte Luecke mehr.
KNOWN_BASIS_GAPS: set[str] = set()


@pytest.fixture(scope="module")
def outcome(tmp_path_factory: pytest.TempPathFactory) -> dict:
    if e2e._tesseract() is None:
        pytest.skip("Tesseract nicht verfuegbar - Scans waeren nicht auswertbar")
    cases = [c for c in corpus.load_cases() if c["id"] in SOURCE_CASES]
    result = e2e.run(cases, tmp_path_factory.mktemp("legal_e2e_s71"), embedding=(FakeEmbeddingProvider(), "fake"))
    result["summary"] = e2e.summarise(result["results"])
    return result


def test_no_privacy_finding_in_the_source_cases(outcome: dict) -> None:
    for split in ("dev", "test"):
        summary = outcome["summary"][split]
        assert summary["kritisch"] == [] and summary["pii_leaks"] == 0 and summary["fremde_akte_im_payload"] == 0, summary


def test_the_payload_contains_the_basis_of_the_reference_solution(outcome: dict) -> None:
    gaps = set(outcome["summary"]["dev"]["loesungsbasis_luecken"]) | set(outcome["summary"]["test"]["loesungsbasis_luecken"])
    assert gaps <= KNOWN_BASIS_GAPS, sorted(gaps - KNOWN_BASIS_GAPS)
    assert outcome["summary"]["dev"]["loesungsbasis_vollstaendig"] == "4/4"
    assert outcome["summary"]["test"]["loesungsbasis_vollstaendig"] == "2/2"


def test_documents_facts_and_chronology_are_extracted(outcome: dict) -> None:
    for split in ("dev", "test"):
        summary = outcome["summary"][split]
        found, total = map(int, summary["extraktion_werte"].split("/"))
        assert found == total > 0 and summary["dokumentfehler"] == 0
        present, total = map(int, summary["erhalt_chronologie"].split("/"))
        assert present == total > 0


def test_deadline_recall_of_the_source_cases(outcome: dict) -> None:
    rows = {r["case"]: r for r in outcome["results"]}
    assert rows["LE-D005"]["deadlines"]["missing"] == [] and rows["LE-D006"]["deadlines"]["missing"] == []
    # §71: die VORSORGLICHE Frist 05.02.2026 ("vorsorglich Fristende 05.02.2026, spaetestens 06.02.2026") fehlte;
    # §72 behoben ("Fristende" ohne Doppelpunkt, entwickelt am Dev-Fall LE-D007)
    assert rows["LE-T004"]["deadlines"]["missing"] == []


def test_document_release_withholds_the_uncertain_document(outcome: dict) -> None:
    probe = outcome["freigabe"]
    assert probe["status"] == "payload" and probe["freigegebenes_im_payload"]
    assert not probe["zurueckgehaltenes_im_payload"] and not probe["kritisch"]
    assert probe["zustaende"]["schreiben.txt"] == "decision"


def test_short_form_of_a_three_part_organisation_name_is_protected() -> None:
    """§71-Befund LE-T004: "Generalkonsulat Valdoria" im Aktentitel - vorher stand "Generalkonsulat" im Klartext."""
    from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
    from app.models import Client, Matter, Party

    class _Query:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *args):
            return self

        def all(self):
            return self.rows

    class _Db:
        def query(self, model):
            return _Query([Party(matter_id="m", name="Generalkonsulat der Republik Valdoria", role="Gegner")])

    matter = Matter(id="m", client=Client(name="Jelena Radovanović"), title="t")
    known = RuleBasedLocalAIProvider()._build_known_entities("m", matter, _Db())
    assert "Generalkonsulat Valdoria" in known["gegner"] and "Valdoria" in known["gegner"]
