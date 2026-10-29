"""§81: Haertung von Freigaben, Kosten (C-7), Ablage (C-11) und Klaerung unklarer Aufrufe (E4).

Nur synthetische Konfigurationen in tmp_path (KEINE Freigabe), Mock-Provider ohne Netzwerk, simulierte Usage-Daten.
Externe Provideraufrufe: 0; Kosten: 0 EUR."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from scripts.legal_bench import answer_driver, answer_exec, answer_run, answer_specs
from scripts.legal_bench.answer_store import RunStore, StoreError
from tests.test_s78_benchmark_gate import _valid
from tests.fake_embedding_provider import FakeEmbeddingProvider
from tests.test_s80_benchmark_driver import EchoProvider, _Crash, _config, _driver, _test_gate

OWNER = "owner_freigabe_lauf_und_kosten"


def _reasons(config) -> str:  # noqa: ANN001
    r = answer_run.check(config)
    assert r["runnable"] is False
    return " | ".join(r["open"] + r["violations"])


# --- 1. Freigaben ------------------------------------------------------------------------------------------------------


@pytest.mark.usefixtures("synthetic_legal_state")
@pytest.mark.parametrize("mutate, expected", [
    (lambda c: c["approvals"][OWNER].update(datum=(date.today() + timedelta(days=1)).isoformat()), "in der Zukunft"),
    (lambda c: c["approvals"]["datenschutz_freigabe"].update(datum="2026-01-01"), "vor der letzten Neusperre"),
    (lambda c: c["approvals"]["datenschutz_freigabe"].update(datum="2026-02-30"), "datenschutz_freigabe: ungueltig"),
    (lambda c: c["approvals"][OWNER].update(plan_fingerprint="0" * 64), "gilt fuer einen anderen Plan"),
    (lambda c: c["approvals"][OWNER].pop("plan_fingerprint"), "owner_freigabe_lauf_und_kosten: ungueltig"),
    (lambda c: c["approvals"]["datenschutz_freigabe"].update(signatur="geprueft"), "datenschutz_freigabe: ungueltig"),
    (lambda c: c["approvals"]["datenschutz_freigabe"].update(entscheidung="Freigegeben"), "datenschutz_freigabe: ungueltig"),
    (lambda c: c["scope"].update(repetitions=2, ) or c["limits"].update(max_calls=12), "gilt fuer einen anderen Plan"),
], ids=["zukunft", "veraltet_vor_sperre", "unmoegliches_datum", "fremder_plan", "ohne_plan_bindung", "zusatzfeld_signatur",
        "entscheidung_variante", "umfang_nach_freigabe_geaendert"])
def test_manipulated_or_stale_approvals_are_rejected(mutate, expected) -> None:
    config = _valid()
    mutate(config)
    assert expected in _reasons(config)


@pytest.mark.usefixtures("synthetic_legal_state")
def test_new_commit_or_changed_lock_invalidates_the_owner_approval(monkeypatch, tmp_path) -> None:
    config = _valid()
    assert answer_run.check(config)["runnable"] is True
    monkeypatch.setattr(answer_run, "_git_commit", lambda: "d" * 40)
    reasons = _reasons(config)
    assert "lexono_commit: veraltet" in reasons and "gilt fuer einen anderen Plan" in reasons
    monkeypatch.undo()
    monkeypatch.setattr(answer_run, "legal_state", lambda: {"open_specs": {}, "rubric_open": False})
    lock_copy = tmp_path / "LOCK.json"
    shutil.copy(answer_specs.LOCK_PATH, lock_copy)
    lock_copy.write_text(lock_copy.read_text(encoding="utf-8") + " ", encoding="utf-8")  # Bytes geaendert
    monkeypatch.setattr(answer_specs, "LOCK_PATH", lock_copy)
    assert "gilt fuer einen anderen Plan" in _reasons(config)


def test_legal_approvals_contradict_the_open_repository_state() -> None:
    """Ohne Fixture = echter Stand: Spezifikationen offen, Rubrik ohne Schwellen -> eingetragene Freigaben widersprechen."""
    reasons = _reasons(_valid())
    for sid in ("LA-D005", "LA-D006", "LA-T004"):
        assert f"Widerspruch - {sid} hat Status 'entwurf_juristische_freigabe_offen'" in reasons
    assert "juristische_freigabe_schwellen_und_gewichte: Widerspruch" in reasons


def test_repository_specs_and_rubric_are_still_open() -> None:
    state = answer_run.legal_state()
    assert set(state["open_specs"]) == {"LA-D005", "LA-D006", "LA-T004"} and state["rubric_open"] is True


# --- 2. Kosten (C-7) -----------------------------------------------------------------------------------------------------


@pytest.mark.usefixtures("synthetic_legal_state")
@pytest.mark.parametrize("field, value", [
    ("price_eur_per_million_input_tokens", math.inf), ("price_eur_per_million_output_tokens", math.nan),
    ("price_eur_per_million_output_tokens", -1.0), ("price_eur_per_million_input_tokens", "3.0"),
    ("price_eur_per_million_input_tokens", True), ("max_cost_eur", math.inf), ("price_per_call_eur_estimate", math.inf),
])
def test_invalid_prices_and_budgets_are_rejected(field, value) -> None:
    config = _valid()
    config["limits"][field] = value
    assert f"limits.{field}: ungueltig" in _reasons(config)


@pytest.mark.usefixtures("synthetic_legal_state")
def test_estimate_below_worst_case_output_cost_is_a_contradiction() -> None:
    config = _valid()
    config["limits"].update(price_per_call_eur_estimate=0.01, price_eur_per_million_output_tokens=15.0)  # 4000 Tok = 0.06
    assert "price_per_call_eur_estimate: Widerspruch" in _reasons(config)


LIMITS = {"price_eur_per_million_input_tokens": 3.0, "price_eur_per_million_output_tokens": 15.0}


@pytest.mark.parametrize("usage", [
    (None, 10), (10, None), (-1, 10), (10, -1), (True, 10), (10.5, 10), ("10", 10), (10, 5000),
], ids=["ohne_input", "ohne_output", "negativ_in", "negativ_out", "bool", "kommazahl", "text", "ueber_max_tokens"])
def test_unreliable_usage_means_unknown_cost(usage) -> None:
    result = SimpleNamespace(input_tokens=usage[0], output_tokens=usage[1])
    cost, info = answer_exec.actual_cost(result, LIMITS, max_tokens=4000)
    assert cost is None and "UNBEKANNT" in info["quelle"]


def test_cost_is_rounded_up_never_down_to_zero() -> None:
    assert answer_exec.actual_cost(SimpleNamespace(input_tokens=1, output_tokens=0), LIMITS, 4000)[0] == 0.000003
    tiny = {"price_eur_per_million_input_tokens": 0.1, "price_eur_per_million_output_tokens": 0.1}
    assert answer_exec.actual_cost(SimpleNamespace(input_tokens=1, output_tokens=0), tiny, 4000)[0] == 0.000001
    assert answer_exec.actual_cost(SimpleNamespace(input_tokens=1000, output_tokens=500), LIMITS, 4000)[0] == 0.0105


class _ImplausibleUsage(EchoProvider):
    def write(self, payload):  # noqa: ANN001
        result = super().write(payload)
        result.output_tokens = 99_999  # mehr als max_tokens 4000
        return result


@pytest.mark.usefixtures("synthetic_legal_state")
def test_implausible_usage_stops_the_run(tmp_path) -> None:
    provider = _ImplausibleUsage()
    result = _driver(_config(tmp_path), RunStore(tmp_path / "laeufe"), provider, tmp_path).run()
    assert result["status"] == "gestoppt_kosten_unbekannt" and len(provider.payloads) == 1
    assert result["state"]["ledger"]["unbekannt"] == ["LA-D005.Q1.r1"]


# --- 3. Ablage (C-11) ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["..", ".", "../ausbruch", ".versteckt", "a/b", "a\\b", "", "x" * 200])
def test_run_and_call_ids_cannot_escape_the_store(tmp_path, bad) -> None:
    store = RunStore(tmp_path / "laeufe")
    with pytest.raises(StoreError):
        store.run_dir(bad)


@pytest.mark.skipif(sys.platform != "win32", reason="Verzeichnis-Junction nur unter Windows")
def test_junction_into_the_repository_is_refused(tmp_path) -> None:
    link = tmp_path / "verweis"
    out = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(answer_specs.REPO / "benchmarks")],
                         capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("Junction konnte nicht angelegt werden")
    try:
        with pytest.raises(StoreError):
            RunStore(link / "laeufe")
    finally:
        subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)  # entfernt nur die Verknuepfung
    assert (answer_specs.REPO / "benchmarks" / "legal_answers_v1" / "LOCK.json").exists()


@pytest.mark.usefixtures("synthetic_legal_state")
@pytest.mark.skipif(sys.platform != "win32", reason="ACL-Pruefung ueber icacls")
def test_widened_access_rights_stop_the_run(tmp_path) -> None:
    store = RunStore(tmp_path / "laeufe")
    provider = EchoProvider(usage=False)
    _driver(_config(tmp_path), store, provider, tmp_path).run()
    subprocess.run(["icacls", str(store.run_dir("qa80-lauf")), "/grant", "*S-1-1-0:(R)"], capture_output=True, check=True)
    assert any("Zugriffsrechte erweitert" in e for e in store.verify("qa80-lauf"))
    result = _driver(_config(tmp_path), store, provider, tmp_path).run()
    assert result["status"] == "gestoppt_ablage_beschaedigt" and len(provider.payloads) == 1


@pytest.mark.usefixtures("synthetic_legal_state")
@pytest.mark.parametrize("damage", ["invalid_json", "tmp_leftover", "truncated"])
def test_damaged_state_file_stops_without_request(tmp_path, damage) -> None:
    store = RunStore(tmp_path / "laeufe")
    provider = EchoProvider(usage=False)
    _driver(_config(tmp_path), store, provider, tmp_path).run()
    state_path = store.run_dir("qa80-lauf") / "state.json"
    if damage == "invalid_json":
        state_path.write_text("{kaputt", encoding="utf-8")
    elif damage == "tmp_leftover":
        (state_path.parent / "state.json.tmp").write_text("{}", encoding="utf-8")
    else:
        state_path.write_text(state_path.read_text(encoding="utf-8")[:40], encoding="utf-8")
    result = _driver(_config(tmp_path), store, provider, tmp_path).run()
    assert result["status"] == "gestoppt_ablage_beschaedigt" and len(provider.payloads) == 1


# --- 4. Absturz und Klaerung (E4) ----------------------------------------------------------------------------------------

pytest_e4 = pytest.mark.usefixtures("synthetic_legal_state")

CLARIFY = {"person": "Testperson (synthetisch)", "rolle": "QA", "begruendung": "Recorder-Protokoll ausgewertet, siehe Beleg",
           "beleg": "lokaler Recorder: Anfrageprotokoll des Testlaufs"}


@pytest_e4
def test_crash_before_the_call_resumes_normally(tmp_path, monkeypatch) -> None:
    store, provider = RunStore(tmp_path / "laeufe"), EchoProvider()
    original = answer_exec.CallGuard.precheck
    state = {"n": 0}

    def crash_once_before_second(self, key):  # noqa: ANN001
        state["n"] += 1
        if state["n"] == 2:
            raise _Crash()
        return original(self, key)

    monkeypatch.setattr(answer_exec.CallGuard, "precheck", crash_once_before_second)
    with pytest.raises(_Crash):
        _driver(_config(tmp_path), store, provider, tmp_path).run()
    monkeypatch.setattr(answer_exec.CallGuard, "precheck", original)
    assert "LA-D005.Q2.r1" not in store.load_state("qa80-lauf")["calls"]  # vor dem Aufruf: kein unklarer Zustand
    result = _driver(_config(tmp_path), store, provider, tmp_path).run()
    assert result["status"] == "abgeschlossen" and len(provider.payloads) == 2


@pytest_e4
def test_crash_during_call_cleared_as_sent_with_cost_is_never_resent(tmp_path) -> None:
    store = RunStore(tmp_path / "laeufe")
    crashing = EchoProvider(fail_at=1, error=_Crash())
    with pytest.raises(_Crash):
        _driver(_config(tmp_path), store, crashing, tmp_path).run()
    blocked = _driver(_config(tmp_path), store, EchoProvider(), tmp_path).run()
    assert blocked["status"] == "gestoppt_unklarer_aufruf"
    answer_driver.resolve_unclear(store, "qa80-lauf", "LA-D005.Q1.r1", entscheidung="als_gesendet_werten",
                                  tatsaechlich_eur=0.0105, **CLARIFY)
    provider = EchoProvider()
    result = _driver(_config(tmp_path), store, provider, tmp_path).run()
    assert result["status"] == "unvollstaendig"  # Q1 hat keine Antwort (als gesendet geklaert) - ehrlich unvollstaendig
    statuses = {cid: c["status"] for cid, c in result["state"]["calls"].items()}
    assert statuses["LA-D005.Q1.r1"] == "geklaert_als_gesendet" and statuses["LA-D005.Q2.r1"] == "erledigt"
    assert len(provider.payloads) == 1  # nur Q2 - Q1 wurde nicht erneut gesendet
    assert result["state"]["ledger"]["tatsaechlich_eur"] >= 0.0105
    assert result["state"]["klaerungen"][0]["entscheidung"] == "als_gesendet_werten"


@pytest_e4
def test_cleared_as_sent_without_cost_keeps_the_run_stopped(tmp_path) -> None:
    store = RunStore(tmp_path / "laeufe")
    with pytest.raises(_Crash):
        _driver(_config(tmp_path), store, EchoProvider(fail_at=1, error=_Crash()), tmp_path).run()
    answer_driver.resolve_unclear(store, "qa80-lauf", "LA-D005.Q1.r1", entscheidung="als_gesendet_werten", **CLARIFY)
    provider = EchoProvider()
    result = _driver(_config(tmp_path), store, provider, tmp_path).run()
    assert result["status"] == "gestoppt_vor_aufruf" and "Kosten unbekannt" in " ".join(result["diagnose"])
    assert provider.payloads == []


@pytest_e4
def test_crash_before_provider_cleared_as_not_sent_runs_exactly_once(tmp_path, monkeypatch) -> None:
    store = RunStore(tmp_path / "laeufe")
    original = answer_driver._Environment.perform

    def crash(self, call):  # noqa: ANN001
        raise _Crash()  # nach "in_bearbeitung", vor jedem Provider-Aufruf

    monkeypatch.setattr(answer_driver._Environment, "perform", crash)
    with pytest.raises(_Crash):
        _driver(_config(tmp_path), store, EchoProvider(), tmp_path).run()
    monkeypatch.setattr(answer_driver._Environment, "perform", original)
    assert store.load_state("qa80-lauf")["calls"]["LA-D005.Q1.r1"]["status"] == "in_bearbeitung"
    answer_driver.resolve_unclear(store, "qa80-lauf", "LA-D005.Q1.r1", entscheidung="nicht_gesendet_belegt", **CLARIFY)
    provider = EchoProvider()
    result = _driver(_config(tmp_path), store, provider, tmp_path).run()
    assert result["status"] == "abgeschlossen" and len(provider.payloads) == 2
    again = _driver(_config(tmp_path), store, provider, tmp_path).run()
    assert again["status"] == "abgeschlossen" and len(provider.payloads) == 2  # kein Doppelaufruf


@pytest_e4
def test_crash_after_provider_answer_is_unclear_and_not_resent(tmp_path, monkeypatch) -> None:
    store = RunStore(tmp_path / "laeufe")
    provider = EchoProvider()
    monkeypatch.setattr(RunStore, "write_record", lambda *a, **k: (_ for _ in ()).throw(_Crash()))
    with pytest.raises(_Crash):
        _driver(_config(tmp_path), store, provider, tmp_path).run()
    monkeypatch.undo()
    monkeypatch.setattr(answer_run, "legal_state", lambda: {"open_specs": {}, "rubric_open": False})
    assert len(provider.payloads) == 1
    result = _driver(_config(tmp_path), store, provider, tmp_path).run()
    assert result["status"] == "gestoppt_unklarer_aufruf" and len(provider.payloads) == 1


@pytest_e4
@pytest.mark.parametrize("kwargs, expected", [
    ({"entscheidung": "wiederholen"}, "entscheidung muss"),
    ({"begruendung": "zu kurz"}, "begruendung"),
    ({"beleg": ""}, "beleg"),
    ({"person": " "}, "person und rolle"),
    ({"entscheidung": "nicht_gesendet_belegt", "tatsaechlich_eur": 1.0}, "tatsaechlich_eur"),
    ({"tatsaechlich_eur": math.inf}, "tatsaechlich_eur"),
])
def test_clarification_requires_complete_traceable_input(tmp_path, kwargs, expected) -> None:
    store = RunStore(tmp_path / "laeufe")
    with pytest.raises(_Crash):
        _driver(_config(tmp_path), store, EchoProvider(fail_at=1, error=_Crash()), tmp_path).run()
    args = {**CLARIFY, "entscheidung": "als_gesendet_werten", **kwargs}
    with pytest.raises(StoreError) as exc:
        answer_driver.resolve_unclear(store, "qa80-lauf", "LA-D005.Q1.r1", **args)
    assert expected in str(exc.value)
    assert store.load_state("qa80-lauf")["calls"]["LA-D005.Q1.r1"]["status"] == "in_bearbeitung"  # bleibt gesperrt


@pytest_e4
def test_clarification_refuses_clear_calls_and_damaged_store(tmp_path) -> None:
    store = RunStore(tmp_path / "laeufe")
    _driver(_config(tmp_path), store, EchoProvider(usage=False), tmp_path).run()
    with pytest.raises(StoreError):  # Aufruf ist erledigt, nicht unklar
        answer_driver.resolve_unclear(store, "qa80-lauf", "LA-D005.Q1.r1", entscheidung="nicht_gesendet_belegt", **CLARIFY)
    (store.run_dir("qa80-lauf") / "state.json").write_text("{}", encoding="utf-8")
    with pytest.raises(StoreError):
        answer_driver.resolve_unclear(store, "qa80-lauf", "LA-D005.Q1.r1", entscheidung="nicht_gesendet_belegt", **CLARIFY)


@pytest_e4
def test_after_clarification_changed_config_still_stops(tmp_path) -> None:
    store = RunStore(tmp_path / "laeufe")
    config = _config(tmp_path)
    with pytest.raises(_Crash):
        _driver(config, store, EchoProvider(fail_at=1, error=_Crash()), tmp_path).run()
    answer_driver.resolve_unclear(store, "qa80-lauf", "LA-D005.Q1.r1", entscheidung="nicht_gesendet_belegt", **CLARIFY)
    data = json.loads(config.read_text(encoding="utf-8"))
    data["limits"]["max_cost_eur"] = 12
    config.write_text(json.dumps(data), encoding="utf-8")
    provider = EchoProvider()
    result = _driver(config, store, provider, tmp_path).run()
    assert result["status"] == "gestoppt_geaendert" and provider.payloads == []


@pytest_e4
def test_privacy_finding_stops_the_run_permanently(tmp_path) -> None:
    store = RunStore(tmp_path / "laeufe")
    first = answer_driver.BenchmarkDriver(_config(tmp_path), store, EchoProvider(), workdir=tmp_path / "work",
                                          isolation_gate=_test_gate(tmp_path, None),
                                          embedding=(FakeEmbeddingProvider(), "fake"),
                                          protect={"SYNTH": ["Mieter"]}).run()
    assert first["status"] == "gestoppt_abgebrochen_vor_versand"
    provider = EchoProvider()
    result = _driver(_config(tmp_path), store, provider, tmp_path).run()  # Standard-Schutzliste, gleicher Lauf
    assert result["status"] == "gestoppt_datenschutz_frueher" and provider.payloads == []
