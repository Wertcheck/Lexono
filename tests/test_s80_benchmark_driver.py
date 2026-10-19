"""§80: Treiber (E1), sichere Ablage (E2), Kostenkontrolle (E3), Abbruch und Fortsetzung (E4) des Antwort-Benchmarks.

Nur synthetische Akten des Korpus, Mock-Provider und der lokale Aufzeichnungsserver (echter Anthropic-Provider gegen
127.0.0.1, toter Proxy, gefaelschter Schluessel). Die "Kosten" der Mock-Provider sind simulierte Usage-Metadaten -
tatsaechlich entstehen keine Kosten. Die Laufkonfiguration ist synthetisch und liegt nur in tmp_path (KEINE Freigabe).
Ablage nur in tmp_path (ausserhalb des Repositorys)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import app.privacy.gateway as gateway_module
from app.ai_providers.claude_writing_provider import ClaudeWritingResult
from scripts.legal_bench import answer_driver, answer_exec, answer_run, answer_specs
from scripts.legal_bench.answer_store import MARKER, RunStore, StoreError, access_principals
from tests.test_privacy_wire_payload import FAKE_KEY, _provider, recorder  # noqa: F401  (Fixture)
from tests.test_s78_benchmark_gate import _valid, bind_owner

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_embedding_provider import FakeEmbeddingProvider  # noqa: E402

pytestmark = pytest.mark.usefixtures("synthetic_legal_state")  # §81, siehe tests/conftest.py
PRICE_IN, PRICE_OUT = 3.0, 15.0  # EUR je 1 Mio. Tokens (synthetisch)
USAGE_IN, USAGE_OUT = 1000, 500
COST = round((USAGE_IN * PRICE_IN + USAGE_OUT * PRICE_OUT) / 1_000_000, 6)  # 0.0105 EUR je Mock-Aufruf (simuliert)


class EchoProvider:
    """Mock ohne Netzwerk: antwortet mit dem pseudonymisierten Sachverhalt (Platzhalter bleiben erhalten) und
    simulierten Usage-Metadaten."""

    def __init__(self, *, usage: bool = True, fail_at: int | None = None, error: BaseException | None = None) -> None:
        self.payloads: list = []
        self.usage, self.fail_at, self.error = usage, fail_at, error

    def write(self, payload):  # noqa: ANN001
        self.payloads.append(payload)
        if self.fail_at is not None and len(self.payloads) == self.fail_at:
            raise self.error
        body = payload.anonymisierter_sachverhalt[:1200]
        return ClaudeWritingResult(text=f"Sehr geehrte Damen und Herren,\n\n{body}\n\nMit freundlichen Gruessen",
                                   token_count=None, input_tokens=USAGE_IN if self.usage else None,
                                   output_tokens=USAGE_OUT if self.usage else None)


class _Crash(BaseException):
    """Simulierter Prozessabsturz (wird von keinem `except Exception` abgefangen)."""


def _config(tmp_path: Path, *, specs=("LA-D005",), splits=("dev",), failed=6, **limits) -> Path:
    c = _valid()
    c["run_id"] = "qa80-lauf"
    c["scope"].update(specs=list(specs), splits=list(splits), repetitions=1)
    c["limits"].update(abort_after_failed_calls=failed, price_eur_per_million_input_tokens=PRICE_IN,
                       price_eur_per_million_output_tokens=PRICE_OUT, **limits)
    bind_owner(c)  # §81: Owner-Freigabe an den geaenderten Plan binden
    path = tmp_path / "run_config.qa80.json"
    path.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def store(tmp_path) -> RunStore:
    return RunStore(tmp_path / "laeufe")


def _driver(config: Path, store: RunStore, provider, tmp_path: Path) -> answer_driver.BenchmarkDriver:  # noqa: ANN001
    return answer_driver.BenchmarkDriver(config, store, provider, workdir=tmp_path / "work",
                                         embedding=(FakeEmbeddingProvider(), "fake"))


def _statuses(result: dict) -> dict:
    return {cid: c["status"] for cid, c in result["state"]["calls"].items()}


# --- E1 Erfolg: Chat und Schriftsatz getrennt, ueber den Lexono-Pfad ---------------------------------------------------


def test_successful_run_chat_and_draft_are_stored_with_hashes_and_actual_costs(tmp_path, store) -> None:
    provider = EchoProvider()
    result = _driver(_config(tmp_path), store, provider, tmp_path).run()
    assert result["status"] == "abgeschlossen", result["diagnose"]
    assert _statuses(result) == {"LA-D005.Q1.r1": "erledigt", "LA-D005.Q2.r1": "erledigt"}
    assert len(provider.payloads) == 2 and all(answer_exec_sealed(p) for p in provider.payloads)
    kinds = {store.read_record("qa80-lauf", cid)["kind"] for cid in result["state"]["calls"]}
    assert kinds == {"chat", "schriftsatz"}
    for cid, entry in result["state"]["calls"].items():
        record = store.read_record("qa80-lauf", cid)
        assert record["record_sha256"] == entry["record_sha256"] and answer_run.validate_answer_record(record) == []
        assert record["kosten"] == {"tatsaechlich_eur": COST, "geschaetzt_eur": 0.5}
        assert record["usage"]["quelle"] == "Provider-Usage x freigegebener Tokenpreis"
        assert FAKE_KEY not in json.dumps(record) and "x-api-key" not in json.dumps(record)
    ledger = result["state"]["ledger"]
    assert ledger["tatsaechlich_eur"] == round(2 * COST, 6) and ledger["geschaetzt_eur"] == 1.0
    assert ledger["unbekannt"] == [] and ledger["restbudget_eur"] == round(10 - 2 * COST, 6)
    assert store.verify("qa80-lauf") == []


def answer_exec_sealed(payload) -> bool:  # noqa: ANN001
    from app.privacy.gateway_schema import is_gateway_sealed

    return is_gateway_sealed(payload)


def test_wire_run_over_local_recorder_all_specs_no_leaks(tmp_path, store, recorder) -> None:
    """Alle drei Spezifikationen (6 Aufrufe) ueber den ECHTEN Provider an den lokalen Recorder."""
    config = _config(tmp_path, specs=("LA-D005", "LA-D006", "LA-T004"), splits=("dev", "test"))
    result = _driver(config, store, _provider(), tmp_path).run()
    statuses = _statuses(result)
    assert set(statuses) == {f"{s}.{t}.r1" for s in ("LA-D005", "LA-D006", "LA-T004") for t in ("Q1", "Q2")}
    sent = [c for c in result["state"]["calls"].values() if c["gesendet"]]
    assert len(recorder.requests) == len(sent) == 6
    protect = answer_exec.protected_values()
    from scripts.legal_bench.e2e import _leaked

    for request in recorder.requests:
        assert request["path"] == "/v1/messages" and request["headers"].get("x-api-key") == FAKE_KEY
        text = json.dumps(json.loads(request["raw"]), ensure_ascii=False)
        assert not [v for vs in protect.values() for v in vs if _leaked(v, text)]


# --- E1/E3 Fail-Closed vor jedem Aufruf --------------------------------------------------------------------------------


def test_unapproved_config_creates_nothing_and_sends_nothing(tmp_path, store) -> None:
    config = _config(tmp_path)
    data = json.loads(config.read_text(encoding="utf-8"))
    data["approvals"]["datenschutz_freigabe"] = None
    config.write_text(json.dumps(data), encoding="utf-8")
    provider = EchoProvider()
    result = _driver(config, store, provider, tmp_path).run()
    assert result["status"] == "gestoppt_nicht_freigegeben" and "offen: approvals.datenschutz_freigabe" in result["diagnose"]
    assert provider.payloads == [] and not store.exists("qa80-lauf")


def test_real_cloud_endpoint_is_refused(tmp_path, store, monkeypatch) -> None:
    from app.ai_providers.anthropic_writing_provider import AnthropicClaudeWritingProvider

    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    real = AnthropicClaudeWritingProvider(api_key=FAKE_KEY, model="claude-sonnet-5")  # offizieller Endpunkt, nur konstruiert
    with pytest.raises(answer_driver.RealCloudDisabled):
        _driver(_config(tmp_path), store, real, tmp_path).run()
    monkeypatch.setenv("LEXONO_GATEWAY_URL", "https://relay.example.invalid")
    with pytest.raises(answer_driver.RealCloudDisabled):
        _driver(_config(tmp_path), store, EchoProvider(), tmp_path).run()
    assert not store.exists("qa80-lauf")


def test_missing_cost_metadata_marks_unknown_and_stops(tmp_path, store) -> None:
    provider = EchoProvider(usage=False)
    result = _driver(_config(tmp_path), store, provider, tmp_path).run()
    assert result["status"] == "gestoppt_kosten_unbekannt" and len(provider.payloads) == 1
    record = store.read_record("qa80-lauf", "LA-D005.Q1.r1")
    assert record["kosten"]["tatsaechlich_eur"] is None and "UNBEKANNT" in record["usage"]["quelle"]
    assert result["state"]["ledger"]["unbekannt"] == ["LA-D005.Q1.r1"]
    again = _driver(_config(tmp_path), store, provider, tmp_path).run()  # Wiederanlauf: weiterhin gestoppt
    assert again["status"] == "gestoppt_vor_aufruf" and "Kosten unbekannt" in " ".join(again["diagnose"])
    assert len(provider.payloads) == 1


def test_budget_exceeded_stops_before_the_call(tmp_path, store) -> None:
    # Schaetzung 0.5 je Aufruf, Budget 1.0: Plan (2 Aufrufe) passt; tatsaechliche Kosten des ersten Aufrufs zu hoch
    provider = EchoProvider()
    global USAGE_IN
    old = USAGE_IN
    try:
        USAGE_IN = 200_000  # 0.6 EUR + 0.0075 -> Restbudget < Schaetzung des naechsten Aufrufs
        result = _driver(_config(tmp_path, max_cost_eur=1.0), store, provider, tmp_path).run()
    finally:
        USAGE_IN = old
    assert result["status"] == "gestoppt_vor_aufruf" and len(provider.payloads) == 1
    assert "Restbudget" in " ".join(result["diagnose"])


def test_provider_error_and_timeout_are_failures_with_unknown_cost(tmp_path, store) -> None:
    for error in (RuntimeError("Provider 500 (synthetisch)"), TimeoutError("Zeitueberschreitung (synthetisch)")):
        run_store = RunStore(tmp_path / f"laeufe_{type(error).__name__}")
        provider = EchoProvider(fail_at=1, error=error)
        result = _driver(_config(tmp_path), run_store, provider, tmp_path).run()
        assert result["status"] == "gestoppt_fehlgeschlagen", result
        assert _statuses(result) == {"LA-D005.Q1.r1": "fehlgeschlagen"} and len(provider.payloads) == 1
        assert result["state"]["ledger"]["unbekannt"] == ["LA-D005.Q1.r1"]
        assert type(error).__name__ in " ".join(result["diagnose"])


def test_privacy_check_failure_blocks_without_provider_request(tmp_path, store, monkeypatch) -> None:
    monkeypatch.setattr(gateway_module, "detect_presidio_entities",
                        lambda text: (_ for _ in ()).throw(RuntimeError("NER aus (QA)")))
    provider = EchoProvider()
    result = _driver(_config(tmp_path), store, provider, tmp_path).run()
    assert result["status"] == "gestoppt_blockiert" and provider.payloads == []
    entry = result["state"]["calls"]["LA-D005.Q1.r1"]
    assert entry["gesendet"] is False and entry["cost_eur"] == 0.0


def test_privacy_finding_in_payload_aborts_before_sending(tmp_path, store) -> None:
    provider = EchoProvider()
    driver = answer_driver.BenchmarkDriver(_config(tmp_path), store, provider, workdir=tmp_path / "work",
                                           embedding=(FakeEmbeddingProvider(), "fake"), protect={"SYNTH": ["Mieter"]})
    result = driver.run()
    assert result["status"] == "gestoppt_abgebrochen_vor_versand" and provider.payloads == []
    assert "Datenschutzbefund im Payload" in " ".join(result["diagnose"])


# --- E4 Abbruch und Fortsetzung ---------------------------------------------------------------------------------------


def test_crash_between_calls_resumes_only_missing_calls_without_duplicates(tmp_path, store, monkeypatch) -> None:
    provider = EchoProvider()
    original = RunStore.save_state

    def crash_after_first_done(self, run_id, state):  # noqa: ANN001
        original(self, run_id, state)
        if any(c.get("status") == "erledigt" for c in state.get("calls", {}).values()):
            raise _Crash()

    monkeypatch.setattr(RunStore, "save_state", crash_after_first_done)
    with pytest.raises(_Crash):
        _driver(_config(tmp_path), store, provider, tmp_path).run()
    monkeypatch.setattr(RunStore, "save_state", original)
    assert len(provider.payloads) == 1 and store.verify("qa80-lauf") == []
    result = _driver(_config(tmp_path), store, provider, tmp_path).run()
    assert result["status"] == "abgeschlossen" and len(provider.payloads) == 2  # nur der fehlende Aufruf
    assert [e["event"] for e in result["state"]["events"]][:2] == ["gestartet", "fortgesetzt"]
    assert result["state"]["ledger"]["tatsaechlich_eur"] == round(2 * COST, 6)
    finished = _driver(_config(tmp_path), store, provider, tmp_path).run()  # erneuter Start: nichts mehr zu tun
    assert finished["status"] == "abgeschlossen" and len(provider.payloads) == 2


def test_crash_during_a_call_is_never_repeated_automatically(tmp_path, store) -> None:
    provider = EchoProvider(fail_at=1, error=_Crash())
    with pytest.raises(_Crash):
        _driver(_config(tmp_path), store, provider, tmp_path).run()
    result = _driver(_config(tmp_path), store, EchoProvider(), tmp_path).run()
    assert result["status"] == "gestoppt_unklarer_aufruf" and "LA-D005.Q1.r1" in result["diagnose"][0]
    assert "keine automatische Wiederholung" in result["diagnose"][0]


@pytest.mark.parametrize("change, expected", [
    ("config", "config_sha256"),
    ("commit", "lexono_commit: veraltet"),
    ("freigabe", "approvals.datenschutz_freigabe"),
])
def test_resume_stops_with_diagnosis_after_changes(tmp_path, store, monkeypatch, change, expected) -> None:
    config = _config(tmp_path)
    provider = EchoProvider(usage=False)  # erster Aufruf, dann Stopp (Kosten unbekannt) - danach Aenderung
    _driver(config, store, provider, tmp_path).run()
    data = json.loads(config.read_text(encoding="utf-8"))
    if change == "config":
        data["limits"]["max_cost_eur"] = 11
        config.write_text(json.dumps(data), encoding="utf-8")
    elif change == "commit":
        monkeypatch.setattr(answer_run, "_git_commit", lambda: "e" * 40)
    else:
        data["approvals"]["datenschutz_freigabe"]["entscheidung"] = "widerrufen"
        config.write_text(json.dumps(data), encoding="utf-8")
    result = _driver(config, store, EchoProvider(), tmp_path).run()
    assert result["status"].startswith("gestoppt") and expected in " ".join(result["diagnose"]), result["diagnose"]
    assert len(provider.payloads) == 1


def test_manipulated_lock_stops_resume_and_fresh_start(tmp_path, store, monkeypatch) -> None:
    monkeypatch.setattr(answer_specs, "check_lock", lambda *a, **k: ["LOCK: Eintraege passen nicht zum letzten "
                                                                       "Historieneintrag (Handbearbeitung ohne --update-lock?)"])
    provider = EchoProvider()
    result = _driver(_config(tmp_path), store, provider, tmp_path).run()
    assert result["status"] == "gestoppt_nicht_freigegeben" and provider.payloads == []
    assert "Handbearbeitung" in " ".join(result["diagnose"])


def test_lock_changed_mid_run_stops_before_next_call(tmp_path, store, monkeypatch) -> None:
    provider = EchoProvider()
    original = answer_exec.CallGuard.precheck
    calls = {"n": 0}

    def tamper_before_second(self, key):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 2:
            monkeypatch.setattr(answer_specs, "check_lock", lambda *a, **k: ["LOCK: rubric.json wurde geaendert (QA)"])
        return original(self, key)

    monkeypatch.setattr(answer_exec.CallGuard, "precheck", tamper_before_second)
    result = _driver(_config(tmp_path), store, provider, tmp_path).run()
    assert result["status"] == "gestoppt_vor_aufruf" and len(provider.payloads) == 1
    assert "rubric.json wurde geaendert" in " ".join(result["diagnose"])


# --- E2 Ablage: Integritaet, Zugriff, Loeschung, Ort ----------------------------------------------------------------------


@pytest.mark.parametrize("damage, expected", [
    ("record", "veraendert"), ("state", "state.json veraendert"), ("extra", "unbekannter Datensatz"),
    ("missing", "fehlt"),
])
def test_damaged_store_stops_without_request(tmp_path, store, damage, expected) -> None:
    provider = EchoProvider(usage=False)
    _driver(_config(tmp_path), store, provider, tmp_path).run()
    run_dir = store.run_dir("qa80-lauf")
    record_path = run_dir / "calls" / "LA-D005.Q1.r1.json"
    if damage == "record":
        data = json.loads(record_path.read_text(encoding="utf-8"))
        data["answer_text"] += " (manipuliert)"
        record_path.write_text(json.dumps(data), encoding="utf-8")
    elif damage == "state":
        data = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        data["ledger"]["unbekannt"] = []
        (run_dir / "state.json").write_text(json.dumps(data), encoding="utf-8")
    elif damage == "extra":
        (run_dir / "calls" / "LA-D005.Q9.r1.json").write_text("{}", encoding="utf-8")
    else:
        record_path.unlink()
    result = _driver(_config(tmp_path), store, provider, tmp_path).run()
    assert result["status"] == "gestoppt_ablage_beschaedigt" and expected in result["diagnose"][0]
    assert len(provider.payloads) == 1


def test_store_refuses_repository_and_lexono_data_dirs(tmp_path, monkeypatch) -> None:
    with pytest.raises(StoreError):
        RunStore(answer_specs.REPO / "benchmark_laeufe")
    with pytest.raises(StoreError):
        RunStore(answer_specs.REPO.parent)  # Elternverzeichnis des Repositorys
    monkeypatch.setenv("LEXONO_DATA_DIR", str(tmp_path / "lexono_daten"))
    with pytest.raises(StoreError):
        RunStore(tmp_path / "lexono_daten" / "bench")


def test_store_access_is_restricted_to_the_current_account(tmp_path, store) -> None:
    _driver(_config(tmp_path), store, EchoProvider(), tmp_path).run()
    run_dir = store.run_dir("qa80-lauf")
    for path in (store.base, run_dir, run_dir / "state.json", run_dir / "calls" / "LA-D005.Q1.r1.json"):
        principals = access_principals(path)
        assert len(principals) == 1, (path.name, principals)
        if sys.platform == "win32":
            import os

            assert principals[0].lower().endswith(os.environ["USERNAME"].lower())


def test_controlled_deletion(tmp_path, store) -> None:
    _driver(_config(tmp_path), store, EchoProvider(), tmp_path).run()
    with pytest.raises(StoreError):
        store.delete("qa80-lauf", confirm="falsch")
    unmarked = store.base / "fremd"
    unmarked.mkdir()
    with pytest.raises(StoreError):
        store.delete("fremd", confirm="fremd")  # ohne Markierung nie loeschen
    assert unmarked.exists()
    store.delete("qa80-lauf", confirm="qa80-lauf")
    assert not store.run_dir("qa80-lauf").exists() and not (store.base / "qa80-lauf" / MARKER).exists()


def test_record_is_never_overwritten(tmp_path, store) -> None:
    _driver(_config(tmp_path), store, EchoProvider(), tmp_path).run()
    with pytest.raises(StoreError):
        store.write_record("qa80-lauf", "LA-D005.Q1.r1", {"answer_text": "zweiter Versuch"})


def test_no_benchmark_answer_reaches_repository_or_production_db(tmp_path, store) -> None:
    import subprocess

    before = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=answer_specs.REPO).stdout
    _driver(_config(tmp_path), store, EchoProvider(), tmp_path).run()
    after = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=answer_specs.REPO).stdout
    assert before == after
    source = Path(answer_driver.__file__).read_text(encoding="utf-8")
    assert "app.db.session" not in source and "sqlite:///:memory:" in source


# --- Datensparsamkeit der Ablage ---------------------------------------------------------------------------------------


def test_stored_answers_are_pseudonymized_and_store_has_no_protected_values(tmp_path, store) -> None:
    from scripts.legal_bench.e2e import _leaked

    _driver(_config(tmp_path), store, EchoProvider(), tmp_path).run()
    protect = [v for vs in answer_exec.protected_values().values() for v in vs]
    for path in store.run_dir("qa80-lauf").rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert not [v for v in protect if _leaked(v, text)], path.name
    record = store.read_record("qa80-lauf", "LA-D005.Q1.r1")
    assert record["antwort_art"].startswith("pseudonymisiert") and "[" in record["answer_text"]
    assert len(record["antwort_lokal_sha256"]) == 64


def test_model_answer_with_protected_value_is_not_stored_and_stops(tmp_path, store) -> None:
    driver = answer_driver.BenchmarkDriver(_config(tmp_path), store, EchoProvider(), workdir=tmp_path / "work",
                                           embedding=(FakeEmbeddingProvider(), "fake"), protect={"SYNTH": ["Gruessen"]})
    result = driver.run()
    assert result["status"] == "gestoppt_datenschutzbefund_antwort"
    assert not (store.run_dir("qa80-lauf") / "calls" / "LA-D005.Q1.r1.json").exists()
    assert store.verify("qa80-lauf") == []


@pytest.mark.parametrize("module", ["answer_driver", "answer_store", "answer_exec"])
def test_modules_build_no_provider_and_have_no_entry_point(module) -> None:
    import ast
    import importlib

    source = Path(importlib.import_module(f"scripts.legal_bench.{module}").__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    forbidden = {"anthropic", "httpx", "requests", "socket", "urllib.request", "app.ai_providers.factory",
                 "app.ai_providers.anthropic_writing_provider", "app.db.session", "argparse"}
    assert not imported & forbidden, imported & forbidden
    assert "__main__" not in source
