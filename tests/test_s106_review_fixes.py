"""§106: Regressionstests zu AUDIT_S105 A6 (Lückenlosigkeit der Zwischenprüfungen) und A5 (Startcheck unabhängig vom
Aufrufweg). Nur lokal: tmp, simuliertes Hyper-V (`FakeSystem`), synthetische Belege. Keine reale Isolation."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.legal_bench import isolation_evidence as ev, isolation_host_control as hc
from scripts.legal_bench.isolation_evidence import BLOCKIERT, PASS
from tests.isolation_test_support import bundle_items, synthetic_policy, write_bundle
from tests.test_s94_real_acceptance_path import FakeSystem, setup  # noqa: F401

LOOP = {("127.0.0.1", 9)}
NOW = datetime.now(timezone.utc)
MAX = ev.MAX_KONTROLL_LUECKE_S


def _has(items, text) -> bool:  # noqa: ANN001
    return any(text in i for i in items)


# === A6: Lückenlosigkeit der periodischen Zwischenprüfungen =============================================================


def _spanning(von: datetime, bis: datetime, step_s: float):  # noqa: ANN001
    """Zeitstempel ab `von` alle step_s bis einschliesslich `bis` (deckt das Fenster ab, wenn step_s <= MAX)."""
    out, t = [], von
    while t < bis:
        out.append(t)
        t += timedelta(seconds=step_s)
    out.append(bis)
    return out


def _checks(times, zp: str = "nachher_waehrend"):  # noqa: ANN001
    return [{"zeitpunkt": zp, "zeit": t.isoformat(), "gleich": True, "ist_sha256": "a" * 64} for t in times]


def _content(dauer_s, fw_times, listener_times, *, von=None, bis=None):  # noqa: ANN001
    von = von if von is not None else NOW - timedelta(seconds=dauer_s if isinstance(dauer_s, (int, float)) else 0)
    bis = bis if bis is not None else NOW
    return {"aktiv_von": von.isoformat() if von else von, "aktiv_bis": bis.isoformat() if bis else bis,
            "dauer_s": dauer_s, "firewall_kontrollen": _checks(fw_times), "kontrollen": _checks(listener_times)}


def test_short_phase_needs_no_interior_checks() -> None:
    assert ev._abdeckung_problem(_content(ev.MIN_NACHLAUF_S, [], [])) is None
    assert ev._abdeckung_problem(_content(MAX, [], [])) is None  # eine Luecke genau = MAX durch Fensteranker abgedeckt


def test_full_window_coverage_passes() -> None:
    dauer = 10 * MAX
    von, bis = NOW - timedelta(seconds=dauer), NOW
    times = _spanning(von, bis, ev.KONTROLL_INTERVALL_S)  # alle 15 s ueber die ganze Phase
    assert ev._abdeckung_problem(_content(dauer, times, times)) is None


def test_bundled_checks_then_trailing_gap_block() -> None:
    """Regression A19: viele Pruefungen geballt am Anfang, danach eine lange Luecke -> BLOCKIERT."""
    dauer = 10 * MAX
    von, bis = NOW - timedelta(seconds=dauer), NOW
    early = _spanning(von, von + timedelta(seconds=MAX), ev.KONTROLL_INTERVALL_S)  # nur erste MAX s abgedeckt
    reason = ev._abdeckung_problem(_content(dauer, early, early))
    assert reason and "lueckenhaft" in reason


def test_step_larger_than_max_blocks() -> None:
    dauer = 6 * MAX
    von, bis = NOW - timedelta(seconds=dauer), NOW
    times = _spanning(von, bis, 2 * MAX)  # Abstand 2x MAX -> Luecke zu gross
    reason = ev._abdeckung_problem(_content(dauer, times, times))
    assert reason and "lueckenhaft" in reason


def test_end_check_does_not_compensate_missing_interior_checks() -> None:
    dauer = 8 * MAX
    reason = ev._abdeckung_problem(_content(dauer, [], []))
    assert reason and "lueckenhaft" in reason


@pytest.mark.parametrize("dauer, text", [(None, "nicht pruefbar"), ("x", "nicht pruefbar")])
def test_missing_phase_duration_blocks(dauer, text) -> None:
    assert text in ev._abdeckung_problem(_content(dauer, [], [], von=NOW - timedelta(seconds=5), bis=NOW))


def test_missing_window_blocks() -> None:
    assert "Phasenfenster" in ev._abdeckung_problem({"dauer_s": 10.0, "firewall_kontrollen": [], "kontrollen": []})


def test_duration_inconsistent_with_window_blocks() -> None:
    """Regression A18: zu klein gemeldete dauer_s bei langem Fenster -> BLOCKIERT."""
    reason = ev._abdeckung_problem(_content(2.0, [], [], von=NOW - timedelta(minutes=30), bis=NOW))
    assert reason and "widerspricht dem Phasenfenster" in reason


def test_reordered_interior_checks_block() -> None:
    dauer = 6 * MAX
    von, bis = NOW - timedelta(seconds=dauer), NOW
    times = _spanning(von, bis, ev.KONTROLL_INTERVALL_S)
    checks = _checks(times)
    checks[1], checks[3] = checks[3], checks[1]
    reason = ev._abdeckung_problem({"aktiv_von": von.isoformat(), "aktiv_bis": bis.isoformat(), "dauer_s": float(dauer),
                                    "firewall_kontrollen": checks, "kontrollen": _checks(times)})
    assert reason and "nicht monoton" in reason


def test_interior_check_without_timestamp_blocks() -> None:
    dauer = 6 * MAX
    von, bis = NOW - timedelta(seconds=dauer), NOW
    checks = _checks(_spanning(von, bis, ev.KONTROLL_INTERVALL_S))
    checks[2].pop("zeit")
    reason = ev._abdeckung_problem({"aktiv_von": von.isoformat(), "aktiv_bis": bis.isoformat(), "dauer_s": float(dauer),
                                    "firewall_kontrollen": checks, "kontrollen": _checks(_spanning(von, bis, ev.KONTROLL_INTERVALL_S))})
    assert reason and "ohne Zeitstempel" in reason


def test_checks_outside_the_window_block() -> None:
    dauer = 6 * MAX
    von, bis = NOW - timedelta(seconds=dauer), NOW
    times = _spanning(von, bis, ev.KONTROLL_INTERVALL_S)
    times[-1] = bis + timedelta(seconds=10 * MAX)  # letzte Pruefung weit nach dem Fenster
    reason = ev._abdeckung_problem(_content(dauer, times, _spanning(von, bis, ev.KONTROLL_INTERVALL_S)))
    assert reason and "ausserhalb des Phasenfensters" in reason


def test_listener_gap_blocks_even_if_firewall_is_complete() -> None:
    dauer = 8 * MAX
    von, bis = NOW - timedelta(seconds=dauer), NOW
    full = _spanning(von, bis, ev.KONTROLL_INTERVALL_S)
    early = _spanning(von, von + timedelta(seconds=MAX), ev.KONTROLL_INTERVALL_S)
    reason = ev._abdeckung_problem(_content(dauer, full, early))  # Firewall vollstaendig, Listener mit Luecke
    assert reason and "Listener" in reason


def _judge(tmp_path, hook) -> dict:  # noqa: ANN001
    policy_path = synthetic_policy(tmp_path, LOOP)
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    from scripts.legal_bench import isolation_gate

    lauf = {"run_id": "lauf-106", "maschine": {"hostname": "vm", "machine_guid": "g"},
            "netzwerkpolitik_sha256": isolation_gate.sha256_file(policy_path),
            "pruefwerkzeug": {"commit": "c" * 40, "sha256": "d" * 64}}
    items = bundle_items(LOOP, NOW)
    hook(items)
    folder = write_bundle(tmp_path / "b", items, lauf=lauf, policy=policy)
    return ev.evaluate(folder, policy=policy, lauf=lauf, reference=NOW, now=NOW, allow_dry_run=True)


def test_evaluation_passes_with_compliant_short_phase(tmp_path) -> None:
    assert _judge(tmp_path, lambda items: None)["gesamt"] == PASS  # synthetische dauer_s klein -> keine Luecke


def test_evaluation_blocks_long_phase_without_interior_checks(tmp_path) -> None:
    """Ende-zu-Ende: ein langes Nachher-Fenster ohne Zwischenpruefungen (nur Start/Ende) -> BLOCKIERT."""
    from scripts.legal_bench.isolation_acceptance import SYNTHETIC_FW_SHA

    def hook(items):  # noqa: ANN001, ANN202
        for name in ("V4", "V5", "V6", "V7", "V8", "V12"):
            b = items[f"{name}/kanarien_E1.json"]["beobachtung"]  # Fenster/dauer_s (35 min) beibehalten (vm_clock)
            keys = list(b["kontrolle"])
            lis = lambda zp, t: {"zeitpunkt": zp, "zeit": t, "ok": keys, "fehlend": []}  # noqa: E731
            fw = lambda zp, t: {"zeitpunkt": zp, "zeit": t, "gleich": True, "ist_sha256": SYNTHETIC_FW_SHA}  # noqa: E731
            von, bis = b["aktiv_von"], b["aktiv_bis"]  # nur Start/Ende, keine Zwischenpruefungen
            b["firewall_kontrollen"] = [fw("nachher_start", von), fw("nachher_ende", bis)]
            b["kontrollen"] = [lis("nachher_start", von), lis("nachher_ende", bis)]
    verdict = _judge(tmp_path, hook)
    status, gruende = verdict["tests"]["V4"]["status"], verdict["tests"]["V4"]["gruende"]
    assert status == BLOCKIERT and _has(gruende, "lueckenhaft")


# === A5: Startcheck unabhängig vom Aufrufweg ============================================================================


def _journal(setup):  # noqa: ANN001
    return setup["tmp"] / "aenderungsjournal.jsonl"


def _ok_startcheck(setup, journal, run_id):  # noqa: ANN001
    Path(setup["konfig"]["golden_image"]["kind_vhdx"]).write_bytes(b"kind")
    hc.HostControl("lexono-bench", ausfuehren=False, runner=setup["system"], journal=journal,
                   run_id=run_id).startcheck(setup["konfig"])


@pytest.mark.parametrize("op", ["acl", "fw", "image"])
def test_direct_method_call_requires_startcheck(setup, op) -> None:
    """§106 (A5): auch der DIREKTE Methodenaufruf (nicht nur die CLI) verlangt einen aktuellen ok-Startcheck."""
    journal = _journal(setup)
    control = hc.HostControl("lexono-bench", ausfuehren=True, runner=setup["system"], journal=journal,
                             run_id=setup["konfig"]["run_id"])  # Produktiv-Default: startcheck_pflicht=True
    with pytest.raises(hc.ControlRefused, match="ohne aktuellen, erfolgreichen Startcheck"):
        if op == "acl":
            control.acl_setzen(setup["policy"], setup["tmp"] / "a.json")
        elif op == "fw":
            control.fw_setzen(setup["konfig"], setup["tmp"] / "f.json")
        else:
            image = setup["konfig"]["golden_image"]
            control.image_zuruecksetzen(Path(image["eltern_vhdx"]), Path(image["kind_vhdx"]), image["soll_sha256"],
                                        setup["tmp"] / "i.json")


def test_without_journal_enforcement_is_not_applied_unit_context(setup) -> None:
    """Dokumentiertes Restrisiko (A5): ohne Aenderungsjournal (Unit-/Ad-hoc-Kontext) greift die Startcheck-Pflicht nicht;
    produktiv fuehrt die CLI IMMER ein Journal. Hier: fw-setzen laeuft ohne Startcheck-Ablehnung durch."""
    control = hc.HostControl("lexono-bench", ausfuehren=True, runner=setup["system"], run_id="r")  # kein Journal
    record = control.fw_setzen(setup["konfig"], setup["tmp"] / "f.json")
    assert setup["system"].firewall and record["run_id"] == setup["konfig"]["run_id"]  # durchgelaufen, keine Ablehnung


def test_method_allows_mutation_after_ok_startcheck(setup) -> None:
    journal = _journal(setup)
    _ok_startcheck(setup, journal, setup["konfig"]["run_id"])
    control = hc.HostControl("lexono-bench", ausfuehren=True, runner=setup["system"], journal=journal,
                             run_id=setup["konfig"]["run_id"])
    record = control.fw_setzen(setup["konfig"], setup["tmp"] / "f.json")  # Startcheck liegt vor -> erlaubt
    assert record["run_id"] == setup["konfig"]["run_id"] and setup["system"].firewall


def test_stale_startcheck_does_not_authorize(setup) -> None:
    journal = _journal(setup)
    old = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    journal.write_text(json.dumps({"vorgang": "startcheck", "lauf": setup["konfig"]["run_id"],
                                   "status": "startcheck_ergebnis", "ok": True, "zeit": old}) + "\n", encoding="utf-8")
    control = hc.HostControl("lexono-bench", ausfuehren=True, runner=setup["system"], journal=journal,
                             run_id=setup["konfig"]["run_id"])
    with pytest.raises(hc.ControlRefused, match="Startcheck"):
        control.fw_setzen(setup["konfig"], setup["tmp"] / "f.json")


def test_cli_and_method_share_the_same_enforcement(setup, capsys) -> None:
    """Die CLI nutzt dieselbe Methoden-Erzwingung (kein separater, umgehbarer Guard)."""
    rc = hc.main(["fw-setzen", "--konfig", str(setup["konfig_path"]), "--ausfuehren", "--protokoll",
                  str(setup["tmp"] / "f.json")])
    assert rc == 2 and "Startcheck" in capsys.readouterr().err
