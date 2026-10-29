"""§74: Beendigungstermin als eigene Art, mehrteilige Fristfelder, abgeschlossene Feldzeilen.

"Das Mietverhältnis wird ordentlich zum 30.04.2026 gekündigt" nennt den Zeitpunkt, zu dem ein Verhaeltnis enden soll -
kein Fristende (bis §73: "Art: Frist"). Eigene Art "beendigung": Datum, Textstelle, Begruendung und Unsicherheit bleiben,
ueber Wirksamkeit oder Kuendigungsfrist wird nichts ausgesagt. Entwickelt am Dev-Fall LE-D009; positive und negative
Proben zu Fristenden, Fristbeginn, Dokumentdaten, mehrteiligen Feldern und mehreren/unvollstaendigen Terminen."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.deadlines.extractor import PlaceholderDeadlineExtractor
from app.deadlines.review import kind_from_reasoning, kind_label, type_label
from app.deadlines.service import DeadlineAnalysisService, finding_keys
from app.models import Client, Deadline, Document, Matter
from app.models.base import Base


def _kinds(text: str) -> dict[date, str]:
    return {p.due_date: kind_from_reasoning(p.reasoning, from_analysis=True)
            for p in PlaceholderDeadlineExtractor().extract(text) if p.due_date}


def _proposal(text: str, due: date):
    return next(p for p in PlaceholderDeadlineExtractor().extract(text) if p.due_date == due)


# --- Beendigungstermin ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, due",
    [
        ("Das Mietverhältnis wird ordentlich zum 30.04.2026 gekündigt.", date(2026, 4, 30)),
        ("Hiermit kündigen wir das Arbeitsverhältnis ordentlich zum 30.04.2026.", date(2026, 4, 30)),
        ("Hiermit kündigen wir das Arbeitsverhältnis fristgerecht zum 30.04.2026.", date(2026, 4, 30)),
        ("Wir kündigen außerordentlich und fristlos, hilfsweise ordentlich zum 30.06.2026.", date(2026, 6, 30)),
        ("Außerordentlich kündigen wir das Mietverhältnis mit Auslauffrist zum 15.03.2026.", date(2026, 3, 15)),
        ("Unter Einhaltung der Kündigungsfrist von drei Monaten kündigen wir zum 30.06.2026.", date(2026, 6, 30)),
        ("Das Arbeitsverhältnis endet zum 30.06.2026.", date(2026, 6, 30)),
        ("Der Vertrag wird zum 31.12.2026 beendet.", date(2026, 12, 31)),
        ("Ihre Kündigung zum 31.03.2026 bestätigen wir.", date(2026, 3, 31)),
        ("Widerspruch: Fristende 31.08.2026, zwei Monate vor der Beendigung zum 31.10.2026.", date(2026, 10, 31)),
    ],
)
def test_termination_dates_are_their_own_kind(text: str, due: date) -> None:
    assert _kinds(text).get(due) == "beendigung", _kinds(text)
    proposal = _proposal(text, due)
    assert "Art: Beendigungstermin (kein Fristende)" in proposal.reasoning and "Art: Frist" not in proposal.reasoning
    # keine Wirksamkeitsaussage, Unsicherheit sichtbar, Textstelle/Originalangabe erhalten
    assert "ob die Beendigung wirksam ist" in proposal.reasoning and "NICHT geprüft" in proposal.reasoning
    assert "NICHT als verbindlich bestätigt" in proposal.reasoning
    assert proposal.raw_date_text == due.strftime("%d.%m.%Y") and proposal.source_text and proposal.confidence <= 0.5
    assert "mehrere mögliche Fristenden" not in proposal.reasoning


def test_duration_in_a_termination_sentence_is_not_proposed_separately() -> None:
    """Wie bis §73 (damals "Art: Frist"): eine Dauer im Satz eines Beendigungstermins wird kein eigener Vorschlag."""
    proposals = PlaceholderDeadlineExtractor().extract(
        "Wir kündigen das Mietverhältnis, das innerhalb von drei Monaten zu räumen ist, ordentlich zum 30.06.2026.")
    assert [p.due_date for p in proposals] == [date(2026, 6, 30)]
    assert "Dauer 'drei Monaten'" in proposals[0].reasoning


@pytest.mark.parametrize(
    "text, due",
    [
        # Gegenproben: das ENDE einer Frist ist ein Fristende
        ("Die Kündigungsfrist endet zum 30.06.2026.", date(2026, 6, 30)),
        ("Die Widerspruchsfrist endet zum 30.06.2026.", date(2026, 6, 30)),
        ("Die Frist läuft mit Ablauf zum 30.06.2026 ab.", date(2026, 6, 30)),
        ("Wir erwarten Ihre Stellungnahme bis zum 15.04.2026.", date(2026, 4, 15)),
        ("Den Widerspruch bitte ich bis spätestens 31.08.2026 zu erklären.", date(2026, 8, 31)),
        ("Räumung erwarten wir spätestens am 02.11.2026.", date(2026, 11, 2)),
        ("Widerspruch: Fristende 31.08.2026.", date(2026, 8, 31)),
        ("Kündigungsschutzklage: Fristende 05.03.2026.", date(2026, 3, 5)),
    ],
)
def test_real_deadline_ends_stay_deadlines(text: str, due: date) -> None:
    assert _kinds(text) == {due: "frist"}


@pytest.mark.parametrize(
    "text",
    [
        "Hilfsweise wird das Mietverhältnis zum nächstmöglichen Termin gekündigt.",  # unvollstaendig: kein Datum
        "Die Kündigung vom 27.01.2026 liegt vor.",  # Dokumentdatum
        "Kündigungsschreiben, Beispielstadt, 27.01.2026",
        "Das Kündigungsschreiben lag erst am 29.01.2026 in meinem Briefkasten.",
        "Mietvertrag vom 01.04.2009",
    ],
)
def test_termination_context_without_termination_date_gives_no_dated_proposal(text: str) -> None:
    assert _kinds(text) == {}


@pytest.mark.parametrize(
    "text",
    [
        "Die Kündigung ist Ihrer Mandantin am 28.01.2026 zugegangen.",
        "Zugang der Kündigung am 15.01.2026.",
        "Die Kündigung ging am 12.02.2026 zu.",
    ],
)
def test_receipt_of_a_termination_is_a_start_not_a_termination(text: str) -> None:
    proposals = PlaceholderDeadlineExtractor().extract(text)
    assert proposals and all(p.due_date is None and "Art: Fristbeginn" in p.reasoning for p in proposals)


def test_two_termination_dates_in_one_sentence_stay_separate_and_unresolved() -> None:
    kinds = _kinds("Wir kündigen ordentlich zum 30.04.2026, hilfsweise zum 31.05.2026.")
    assert kinds == {date(2026, 4, 30): "beendigung", date(2026, 5, 31): "beendigung"}


# --- mehrteilige Feldbezeichnungen und abgeschlossene Feldzeilen --------------------------------------------------------


@pytest.mark.parametrize(
    "text, due",
    [
        ("Frist für Stellungnahme: 20.03.2026", date(2026, 3, 20)),
        ("Frist zur Klageerhebung: 05.03.2026", date(2026, 3, 5)),
        ("Frist zur Stellungnahme an die Gegenseite: 06.03.2026.", date(2026, 3, 6)),
        ("Frist für die Zahlung: 14.04.2026", date(2026, 4, 14)),
    ],
)
def test_multiword_deadline_field_labels_are_explicit(text: str, due: date) -> None:
    assert _kinds(text) == {due: "frist"}


@pytest.mark.parametrize(
    "text",
    [
        "Aktenvermerk 17.02.2026\nFrist zur Klageerhebung: drei Wochen ab Zugang",
        "Aktenvermerk vom 18.02.2026 - Az. 26/0244-MR\nFrist für Stellungnahme: siehe Schreiben",
        "Frist für Stellungnahme: siehe Schreiben vom 16.02.2026",
        "Frist zur Klageerhebung: offen",
    ],
)
def test_multiword_labels_do_not_attach_document_dates(text: str) -> None:
    assert _kinds(text) == {}


def test_a_finished_field_line_does_not_merge_with_the_next_deadline() -> None:
    """LE-D009: zwei VERSCHIEDENE Fristen auf zwei Zeilen sind keine Alternativen eines Satzes (§72-Hinweis)."""
    text = "Frist für Stellungnahme: 06.03.2026\nRäumung und Herausgabe erwarten wir spätestens am 02.11.2026."
    proposals = [p for p in PlaceholderDeadlineExtractor().extract(text) if p.due_date]
    assert {p.due_date for p in proposals} == {date(2026, 3, 6), date(2026, 11, 2)}
    assert all("mehrere mögliche Fristenden" not in p.reasoning for p in proposals)


def test_soft_wrap_inside_a_field_value_still_joins() -> None:
    assert _kinds("Hinweis: Die Zahlung erfolgt bis zum\n31.07.2026 auf das bekannte Konto.") == {date(2026, 7, 31): "frist"}


def test_time_of_day_line_is_no_field_label() -> None:
    assert _kinds("Die Frist endet am\nUhrzeit 10:30, also am 15.05.2026.") == {date(2026, 5, 15): "frist"}


def test_precautionary_and_latest_alternatives_are_still_marked() -> None:
    reasons = [p.reasoning for p in PlaceholderDeadlineExtractor().extract(
        "Zugang streitig; vorsorglich Fristende 05.02.2026, spätestens 06.02.2026.") if p.due_date]
    assert len(reasons) == 2 and all("mehrere mögliche Fristenden" in r for r in reasons)


# --- Anzeige, Speicher, Schluessel ------------------------------------------------------------------------------------


def _deadline(reasoning: str) -> Deadline:
    return Deadline(matter_id="m", document_id="d", source_text="30.04.2026 :: ...", reasoning=reasoning)


def test_labels_and_type_column() -> None:
    termination = _deadline("Platzhalter-Fristerkennung (regelbasiert, kein LLM). Art: Beendigungstermin (kein Fristende) - x")
    deadline = _deadline("Platzhalter-Fristerkennung (regelbasiert, kein LLM). Art: Frist - Fristende ausdrücklich ('bis').")
    assert kind_label(termination) == "Möglicher Beendigungstermin (kein Fristende)" and type_label(termination) == "Beendigungstermin"
    assert kind_label(deadline) == "Mögliche Frist (Fristende genannt)" and type_label(deadline) == "Frist"


def test_stored_legacy_suggestions_are_not_reinterpreted() -> None:
    legacy = _deadline("Platzhalter-Fristerkennung (regelbasiert, kein LLM). Art: Frist - Fristende ausdrücklich im Text "
                       "genannt ('zum (Beendigung/Ablauf)').")
    assert kind_label(legacy) == "Mögliche Frist (Fristende genannt)" and type_label(legacy) == "Frist"


def test_finding_key_of_a_termination_matches_the_legacy_deadline_key() -> None:
    assert finding_keys([("beendigung", "30.04.2026")]) == finding_keys([("frist", "30.04.2026")])
    assert finding_keys([("termin", "30.04.2026")]) != finding_keys([("frist", "30.04.2026")])


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_reanalysis_keeps_a_legacy_termination_suggestion_without_duplicate(db_session: Session) -> None:
    """Bis §73 gespeicherter Vorschlag ("Art: Frist", Schluessel frist|30.04.2026|1): eine erneute Analyse nach §74
    erkennt denselben Fund wieder und legt KEINEN zweiten Vorschlag fuer dasselbe Datum an."""
    client = Client(name="Testmandantin")
    matter = Matter(client=client, title="Testakte")
    text = "Hiermit kündigen wir das Arbeitsverhältnis ordentlich zum 30.04.2026."
    db_session.add_all([client, matter])
    db_session.commit()
    document = Document(file_path="/tmp/k.pdf", matter_id=matter.id, extracted_text=text)
    db_session.add(document)
    db_session.commit()
    legacy = Deadline(matter_id=matter.id, document_id=document.id, source_text="30.04.2026 :: " + text,
                      due_date=date(2026, 4, 30), finding_key="frist|30.04.2026|1",
                      reasoning="Platzhalter-Fristerkennung (regelbasiert, kein LLM). Art: Frist - Fristende ausdrücklich "
                                "im Text genannt ('zum (Beendigung/Ablauf)').")
    db_session.add(legacy)
    db_session.commit()

    result = DeadlineAnalysisService(PlaceholderDeadlineExtractor()).reanalyze_document(
        document, db_session, actor="anwalt@kanzlei.test")

    assert result.created == [] and result.already_known == 1
    assert db_session.query(Deadline).filter_by(document_id=document.id).count() == 1


def test_new_termination_suggestion_is_stored_unconfirmed_with_its_kind(db_session: Session) -> None:
    client = Client(name="Testmandantin")
    matter = Matter(client=client, title="Testakte")
    db_session.add_all([client, matter])
    db_session.commit()
    document = Document(file_path="/tmp/k.pdf", matter_id=matter.id,
                        extracted_text="Das Mietverhältnis wird ordentlich zum 30.04.2026 gekündigt.")
    db_session.add(document)
    db_session.commit()

    created = DeadlineAnalysisService(PlaceholderDeadlineExtractor()).analyze_document(document, db_session)

    assert len(created) == 1
    stored = created[0]
    assert stored.review_status == "unreviewed" and stored.due_date == date(2026, 4, 30)
    assert type_label(stored) == "Beendigungstermin" and "Art: Beendigungstermin" in stored.reasoning
    assert stored.finding_key == "frist|30.04.2026|1"


# --- Benchmark-Fall LE-D009 (offline E2E, Fake-Embedding) ------------------------------------------------------------


def test_le_d009_end_to_end_separates_terminations_deadlines_and_negatives(tmp_path) -> None:
    import sys
    from pathlib import Path

    from scripts.legal_bench import corpus, e2e

    if e2e._tesseract() is None:
        pytest.skip("Tesseract nicht verfuegbar - Scan waere nicht auswertbar")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fake_embedding_provider import FakeEmbeddingProvider

    case = next(c for c in corpus.load_cases() if c["id"] == "LE-D009")
    row = e2e.run([case], tmp_path, embedding=(FakeEmbeddingProvider(), "fake"))["results"][0]
    assert row["deadlines"]["missing"] == [] and row["deadlines"]["not_in_reference"] == ["31.10.2026"]
    assert row["beendigungstermine"]["recall"] == "1/1" and row["beendigungstermine"]["als_frist"] == []
    assert row["negativdaten_als_frist"] == [] and row["referenzfrist_als_beendigung"] == []
    assert row["critical"] == []
