"""§79: Ausfuehrungswaechter des Antwort-Benchmarks (scripts/legal_bench/answer_exec.py).

Vor JEDEM Provider-Aufruf werden Freigabe, Konfiguration, Commit, Test-Split-Sperre, Plan, Limits und Datenschutz
erneut geprueft; jeder Befund beendet den Lauf, bevor der Provider etwas erhaelt. Gepruefter Pfad: regulaerer
DraftingService -> ClaudePrivacyGateway (Siegel) -> GuardedWriter -> ECHTER Anthropic-Provider gegen den lokalen
Aufzeichnungsserver (toter Proxy, gefaelschter Schluessel). Nur synthetische Daten und eine synthetische, formal gueltige
Testkonfiguration in tmp_path - KEINE echte Freigabe, kein Modell, keine Kosten."""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.privacy.gateway as gateway_module
from app.models import Client, Matter
from app.models.base import Base
from app.privacy.gateway_schema import ClaudeRequestPayload
from scripts.legal_bench import answer_exec, answer_run, answer_specs
from tests.test_drafting_service import _service
from tests.test_privacy_wire_payload import _provider, recorder  # noqa: F401  (Fixture)
from tests.test_s78_benchmark_gate import _valid

pytestmark = pytest.mark.usefixtures("synthetic_legal_state")  # §81, siehe tests/conftest.py
TEXT = "Unsere Mandantin Henrike Wallbaum-Ostertag verlangt die Rückzahlung der Kaution von 4.250,00 EUR."
PROTECT = {"SYNTH": ["Henrike Wallbaum-Ostertag", "Wallbaum-Ostertag"]}


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def config_path(tmp_path) -> Path:
    path = tmp_path / "run_config.qa79.json"
    path.write_text(json.dumps(_valid(), ensure_ascii=False), encoding="utf-8")
    return path


def _guarded(config_path, provider, protect=PROTECT):  # noqa: ANN001, ANN202
    guard = answer_exec.CallGuard(config_path, protect=protect)
    return guard, answer_exec.GuardedWriter(provider, guard)


def _draft(db: Session, writer) -> object:  # noqa: ANN001
    matter = Matter(client=Client(name="Ohne Bezug"), title="Kautionsakte")
    db.add(matter)
    db.commit()
    service, _ = _service(writing_provider=writer)
    return service.create_draft(matter.id, "formulate_draft", db, attorney_anmerkungen=TEXT)


def _first_call(guard) -> dict:  # noqa: ANN001
    return next(iter(guard.planned.values()))


# --- Start nur mit Freigabe -------------------------------------------------------------------------------------------


def test_guard_refuses_to_start_without_approval_with_concrete_reasons() -> None:
    with pytest.raises(answer_run.NotApproved) as exc:
        answer_exec.CallGuard(answer_run.TEMPLATE)
    assert "offen: approvals.datenschutz_freigabe" in exc.value.reasons
    assert "approvals.datenschutz_freigabe" in str(exc.value)  # Grund steht in der Meldung, nicht nur Anzahlen


# --- Positivkontrolle: geplanter Aufruf geht pseudonymisiert ueber die Leitung -------------------------------------


def test_planned_call_passes_the_guard_and_reaches_only_the_local_recorder(recorder, config_path, db) -> None:
    guard, writer = _guarded(config_path, _provider())
    writer.begin(_first_call(guard))
    _draft(db, writer)
    assert guard.aborted is None and len(guard.sent) == 1
    assert len(recorder.requests) == 1 and recorder.requests[0]["path"] == "/v1/messages"
    wire = recorder.requests[0]["raw"].decode("utf-8")
    assert "Wallbaum" not in wire and "4.250,00 EUR" in json.dumps(json.loads(wire), ensure_ascii=False)


# --- Fail-Closed vor dem Provider-Aufruf --------------------------------------------------------------------------------


def _assert_aborted_without_request(recorder, guard, reason: str) -> None:  # noqa: ANN001
    assert guard.aborted is not None and any(reason in r for r in guard.aborted), guard.aborted
    assert recorder.requests == []


def test_unplanned_call_is_refused(recorder, config_path, db) -> None:
    guard, writer = _guarded(config_path, _provider())
    _draft(db, writer)  # kein begin(): Aufruf ausserhalb des Plans
    _assert_aborted_without_request(recorder, guard, "nicht im freigegebenen Plan")


def test_repeated_call_is_refused_and_the_run_stays_aborted(recorder, config_path, db) -> None:
    guard, writer = _guarded(config_path, _provider())
    call = _first_call(guard)
    writer.begin(call)
    _draft(db, writer)
    writer.begin(call)
    _draft(db, writer)
    assert len(recorder.requests) == 1 and any("bereits gesendet" in r for r in guard.aborted)
    writer.begin(list(guard.planned.values())[1])  # auch ein anderer geplanter Aufruf: Lauf bleibt beendet
    _draft(db, writer)
    assert len(recorder.requests) == 1


def test_config_changed_during_the_run_aborts(recorder, config_path, db) -> None:
    guard, writer = _guarded(config_path, _provider())
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data["limits"]["max_cost_eur"] = 1000
    config_path.write_text(json.dumps(data), encoding="utf-8")
    writer.begin(_first_call(guard))
    _draft(db, writer)
    _assert_aborted_without_request(recorder, guard, "waehrend des Laufs geaendert")


def test_stale_commit_aborts(recorder, config_path, db, monkeypatch) -> None:
    guard, writer = _guarded(config_path, _provider())
    monkeypatch.setattr(answer_run, "_git_commit", lambda: "f" * 40)  # neuer Commit nach dem Start
    writer.begin(_first_call(guard))
    _draft(db, writer)
    _assert_aborted_without_request(recorder, guard, "lexono_commit: veraltet")


def test_test_split_lock_violation_aborts(recorder, config_path, db, monkeypatch) -> None:
    guard, writer = _guarded(config_path, _provider())
    monkeypatch.setattr(answer_specs, "check_lock", lambda *a, **k: [
        "LOCK: benchmarks/legal_e2e_v1/cases/test/LE-T004.json wurde geaendert (QA-Simulation)"])
    writer.begin(_first_call(guard))
    _draft(db, writer)
    _assert_aborted_without_request(recorder, guard, "LE-T004.json wurde geaendert")


def test_changed_case_or_spec_changes_the_plan_and_aborts(recorder, config_path, db, monkeypatch) -> None:
    guard, writer = _guarded(config_path, _provider())
    monkeypatch.setattr(answer_specs, "case_fingerprint", lambda case: "0" * 64)
    writer.begin(_first_call(guard))
    _draft(db, writer)
    _assert_aborted_without_request(recorder, guard, "Aufrufplan veraendert")


def test_cost_limit_is_rechecked_per_call(recorder, config_path, db) -> None:
    guard, writer = _guarded(config_path, _provider())
    guard.config["limits"]["price_per_call_eur_estimate"] = 20  # z. B. Preisaenderung: naechster Aufruf ueber Limit
    writer.begin(_first_call(guard))
    _draft(db, writer)
    _assert_aborted_without_request(recorder, guard, "max_cost_eur")


def test_failed_calls_limit_aborts(config_path, db) -> None:
    class _Failing:
        def __init__(self) -> None:
            self.calls = 0

        def write(self, payload):  # noqa: ANN001
            self.calls += 1
            raise RuntimeError("Provider-Fehler (synthetisch)")

    provider = _Failing()
    guard, writer = _guarded(config_path, provider)
    calls = list(guard.planned.values())
    writer.begin(calls[0])
    _draft(db, writer)
    assert guard.failed_calls == 1 and guard.aborted is None
    writer.begin(calls[1])  # abort_after_failed_calls = 1 in der Testkonfiguration
    _draft(db, writer)
    assert provider.calls == 1 and any("abort_after_failed_calls" in r for r in guard.aborted)


def test_privacy_finding_in_payload_aborts_before_sending(recorder, config_path, db) -> None:
    # Ein Wert, den der Gateway bewusst lesbar laesst, wird fuer diesen Test als geschuetzt erklaert: der Waechter
    # muss den "Befund" finden und den Versand verhindern (unabhaengige zweite Pruefung nach dem Gateway).
    guard, writer = _guarded(config_path, _provider(), protect={"SYNTH": ["4.250,00 EUR"]})
    writer.begin(_first_call(guard))
    _draft(db, writer)
    _assert_aborted_without_request(recorder, guard, "Datenschutzbefund im Payload")
    assert guard.privacy_findings == ["SYNTH:4.2..."]


def test_unsealed_payload_is_refused(recorder, config_path) -> None:
    guard, _ = _guarded(config_path, _provider())
    with pytest.raises(answer_exec.ExecutionAborted) as exc:
        guard.authorize(answer_exec.call_key(_first_call(guard)),
                        ClaudeRequestPayload(schreibauftrag="formulate_draft", anonymisierter_sachverhalt=TEXT))
    assert any("nicht vom Datenschutz-Gateway versiegelt" in r for r in exc.value.reasons)
    assert recorder.requests == []


def test_privacy_check_failure_blocks_before_the_guard(recorder, config_path, db, monkeypatch) -> None:
    """B1 + Waechter: faellt die Namenserkennung aus, entsteht gar keine Payload - der Waechter wird nie erreicht."""
    monkeypatch.setattr(gateway_module, "detect_presidio_entities",
                        lambda text: (_ for _ in ()).throw(RuntimeError("NER aus (QA)")))
    guard, writer = _guarded(config_path, _provider())
    writer.begin(_first_call(guard))
    result = _draft(db, writer)
    assert result.success is False and result.blocked_reasons == [gateway_module.PRIVACY_CHECK_FAILED_REASON]
    assert guard.sent == [] and recorder.requests == []


# --- Statisch: kein eigener Cloud-Zugang, keine Ausfuehrung -------------------------------------------------------------


def test_answer_exec_builds_no_provider_and_has_no_entry_point() -> None:
    source = Path(answer_exec.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    forbidden = {"anthropic", "httpx", "requests", "socket", "urllib.request", "app.ai_providers.factory",
                 "app.ai_providers.anthropic_writing_provider"}
    assert not imported & forbidden, imported & forbidden
    assert "__main__" not in source and "argparse" not in imported
    names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    assert not names & {"main", "run", "execute", "run_benchmark"}
