"""§91 (A3): Host-/VM-Abnahmeskripte im Trockenlauf - Belegteile, Buendelung, Urteil, Belegalter, Sperren.

Nur lokal: synthetische Zielumgebung, ECHTE Kanarien-Listener und Leck-Simulation ausschliesslich auf Loopback, keine
echten Systembefehle (Hyper-V/Firewall), keine Schluessel, keine Provider-Aufrufe. Ein Trockenlauf-Buendel wird von
Produktiv-Urteil und -Gate abgelehnt. Keine reale Abnahme: V1-V13 bleiben NICHT DURCHGEFUEHRT."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.legal_bench import answer_specs, isolation_acceptance as acc, isolation_evidence as ev, owner_sign
from scripts.legal_bench.isolation_evidence import BLOCKIERT, FAIL, NICHT_DURCHGEFUEHRT, PASS
from tests.isolation_test_support import make_test_gate

NETZWERK = ("V1", "V4", "V5", "V6", "V7", "V8", "V10", "V12")
KANARIEN = ("V4", "V5", "V6", "V7", "V8", "V12")


def _statuses(verdict: dict) -> dict[str, str]:
    return {t: r["status"] for t, r in verdict["tests"].items()}


def _has(items: list[str], text: str) -> bool:
    return any(text in i for i in items)


# --- Positiver Trockenlauf -----------------------------------------------------------------------------------------------


def test_dry_run_produces_a_bundle_that_passes_all_13(tmp_path) -> None:
    run = acc.dry_run(tmp_path / "lauf")
    assert run.verdict["gesamt"] == PASS and set(_statuses(run.verdict).values()) == {PASS}, run.verdict
    manifest = json.loads((run.bundle / ev.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert {e["erzeuger"] for e in manifest["belege"]} == set(acc.ROLLEN.values())
    for entry in manifest["belege"]:
        content = json.loads((run.bundle / entry["datei"]).read_text(encoding="utf-8"))
        assert content["test"] == entry["test"] and content["erzeugt"] == entry["erzeugt"]
        assert content["umgebung"]["erzeuger"] == entry["erzeuger"] and content["umgebung"]["modus"] == "trockenlauf"
        assert ev.ERZEUGER[entry["erzeuger"]] == entry["klasse"]
        assert isinstance(content["soll"], dict) and isinstance(content["beobachtung"], dict)  # getrennt
        assert content["soll"]["matrix"] and content["lauf_sha256"] == ev.lauf_sha256(run.lauf)
    # der Kanarienbeleg stammt vom echten Listener (Loopback, ephemere Ports, Laufzeit umfasst die VM-Proben)
    assert run.canary["treffer"] == [] and all(t["port"] > 0 for t in run.canary["ziele"])
    assert {t["ip"] for t in run.canary["ziele"]} == {"127.0.0.1", "::1"}


def test_cli_dry_run(tmp_path, capsys) -> None:
    assert acc.main(["trockenlauf", "--ordner", str(tmp_path / "cli")]) == 0
    out = capsys.readouterr().out
    assert "Gesamturteil: PASS" in out and "synthetisch, Loopback" in out


# --- Lecks und Fehler ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("leak", ["tcp", "tcp6", "udp", "dns"])
def test_simulated_leak_to_a_canary_fails_every_canary_test(tmp_path, leak) -> None:
    run = acc.dry_run(tmp_path / "lauf", leak=leak)
    assert len(run.canary["treffer"]) >= 1 and run.canary["treffer"][0]["quelle"] in ("127.0.0.1", "::1")
    statuses = _statuses(run.verdict)
    assert all(statuses[t] == FAIL for t in KANARIEN), statuses
    assert _has(run.verdict["tests"]["V4"]["gruende"], "Kanarientreffer") and run.verdict["gesamt"] == FAIL


def test_listener_positive_control_is_recorded_and_required(tmp_path) -> None:
    run = acc.dry_run(tmp_path / "lauf")
    assert set(run.canary["kontrolle"]) == {f"{t['ip']}:{t['port']}" for t in run.canary["ziele"]}
    assert run.canary["treffer"] == []  # die eigene Kontrolle zaehlt nicht als Treffer

    def dead(rolle, obs):  # noqa: ANN001, ANN202 - im SELBEN Lauf: ein Listener reagiert bei der Endkontrolle nicht
        if rolle == "host":
            for test in KANARIEN:
                original = obs[(test, "kanarien")]

                def broken_log(original=original):  # noqa: ANN001, ANN202
                    log = original()
                    for check in log["kontrollen"]:
                        if check["zeitpunkt"] == "nachher_ende":
                            check["ok"] = check["ok"][:-1]
                    return log
                obs[(test, "kanarien")] = broken_log

    broken = acc.dry_run(tmp_path / "lauf2", change=dead)
    for test in KANARIEN:
        # §98 (H1): ein bei einer Kontrolle stummer Listener macht das Ergebnis ungueltig (BLOCKIERT), nie PASS
        assert broken.verdict["tests"][test]["status"] in (BLOCKIERT, FAIL)
        assert _has(broken.verdict["tests"][test]["gruende"], "Listener reagierte bei Kontrolle nachher_ende nicht")


def test_network_tests_without_host_observation_are_never_pass(tmp_path) -> None:
    run = acc.dry_run(tmp_path / "lauf", change=lambda rolle, obs: obs.clear() if rolle == "host" else None)
    statuses = _statuses(run.verdict)
    for test in NETZWERK:
        assert statuses[test] in (BLOCKIERT, NICHT_DURCHGEFUEHRT), (test, statuses)
        assert statuses[test] != PASS
    assert statuses["V4"] == BLOCKIERT and _has(run.verdict["tests"]["V4"]["gruende"], "Klasse E1 fehlt")
    assert statuses["V1"] == NICHT_DURCHGEFUEHRT and run.verdict["gesamt"] == BLOCKIERT


def test_missing_endpoint_log_blocks_v3(tmp_path) -> None:
    run = acc.dry_run(tmp_path / "lauf", change=lambda rolle, obs: obs.clear() if rolle == "endpunkt" else None)
    assert run.verdict["tests"]["V3"]["status"] == BLOCKIERT
    assert _has(run.verdict["tests"]["V3"]["gruende"], "Klasse E2 fehlt")


def _raise(*_args):  # noqa: ANN002, ANN202
    raise subprocess.TimeoutExpired("powershell", 120)


@pytest.mark.parametrize("bad", [_raise, lambda: "kein objekt", lambda: {"fehler": "Zugriff verweigert"}],
                         ids=["ausnahme", "kein_objekt", "fehlerobjekt"])
def test_observer_errors_are_recorded_and_fail(tmp_path, bad) -> None:
    def change(rolle, obs):  # noqa: ANN001, ANN202
        if rolle == "vm":
            obs[("V2", "kriterien")] = bad

    run = acc.dry_run(tmp_path / "lauf", change=change)
    assert run.verdict["tests"]["V2"]["status"] == FAIL
    content = json.loads((run.bundle / "V2" / "kriterien_E0.json").read_text(encoding="utf-8"))
    assert "fehler" in content["beobachtung"]  # Fehler festgehalten, nicht verschluckt


def test_observed_deviation_fails(tmp_path) -> None:
    def change(rolle, obs):  # noqa: ANN001, ANN202
        if rolle == "host":
            acl = acc.synthetic_acl({("127.0.0.1", 9)})
            acl.append({"Direction": "Outbound", "Action": "Allow", "RemoteIPAddress": "Any", "RemotePort": "",
                        "Protocol": "", "Weight": 50})
            obs[("V1", "firewall_export")] = lambda: {"acl": acl}

    run = acc.dry_run(tmp_path / "lauf", change=change)
    assert run.verdict["tests"]["V1"]["status"] == FAIL
    assert _has(run.verdict["tests"]["V1"]["gruende"], "erlaubt")


def test_real_skipped_acceptance_tests_never_pass(tmp_path, monkeypatch) -> None:
    """Echte JUnit-Ausgabe der §86-Abnahmetests ausserhalb der Zielumgebung: alle uebersprungen -> FAIL (uneindeutig)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("LEXONO_ISOLATION")}
    report = tmp_path / "junit.xml"
    subprocess.run([sys.executable, "-m", "pytest", "tests/test_s86_isolation_design.py", "-k", "acceptance", "-q",
                    "-p", "no:cacheprovider", f"--junitxml={report}"], cwd=answer_specs.REPO, env=env,
                   capture_output=True, timeout=300, check=False)
    xml = report.read_text(encoding="utf-8")
    results = ev.junit_results(xml)
    assert results and set(results.values()) == {"skipped"}

    def change(rolle, obs):  # noqa: ANN001, ANN202
        if rolle == "vm":
            for test in ("V4", "V5", "V6", "V7", "V8", "V10"):
                obs[(test, "pytest")] = lambda: {"junit_xml": xml}

    run = acc.dry_run(tmp_path / "lauf", change=change)
    for test in ("V4", "V5", "V6", "V7", "V8", "V10"):
        assert run.verdict["tests"][test]["status"] == FAIL
        assert _has(run.verdict["tests"][test]["gruende"], "uneindeutig = FAIL")


# --- Rollen, Buendelung, Manipulation -------------------------------------------------------------------------------------


def _part(tmp_path, rolle, name="teil"):  # noqa: ANN001, ANN202
    return acc.EvidencePart(tmp_path / name, rolle, {"run_id": "x"}, {"freigaben": [], "lokal": {"loopback_erlaubt": True}})


def test_role_can_only_write_its_own_evidence_class(tmp_path) -> None:
    vm = _part(tmp_path, "vm", "vm")
    with pytest.raises(acc.AcceptanceRefused, match="kein Pflichtbeleg kanarien in Klasse E0"):
        vm.record("V4", "kanarien", {"treffer": []})  # die VM kann keinen Host-Kanarienbeleg erzeugen
    host = _part(tmp_path, "host", "host")
    with pytest.raises(acc.AcceptanceRefused, match="kein Pflichtbeleg pytest in Klasse E1"):
        host.record("V4", "pytest", {"junit_xml": ""})
    with pytest.raises(acc.AcceptanceRefused, match="unbekannte Rolle"):
        _part(tmp_path, "it", "it")
    with pytest.raises(acc.AcceptanceRefused, match="keine Beobachtung|kein Objekt"):
        vm.record("V2", "kriterien", "true")
    vm.record("V2", "kriterien", {"konto_gruppen_sids": []})
    with pytest.raises(acc.AcceptanceRefused, match="nicht leer"):
        _part(tmp_path, "vm", "vm")  # Belege werden nie ueberschrieben


def _three_parts(tmp_path):  # noqa: ANN001, ANN202
    run = acc.dry_run(tmp_path / "lauf")
    return run, {r: run.folder / f"teil_{r}" for r in acc.ROLLEN}


def test_assemble_refuses_contradictions(tmp_path) -> None:
    run, parts = _three_parts(tmp_path)
    with pytest.raises(acc.AcceptanceRefused, match="existiert bereits"):
        acc.assemble(run.bundle, parts, lauf=run.lauf)
    with pytest.raises(acc.AcceptanceRefused, match="Rolle/Erzeuger widerspricht"):
        acc.assemble(tmp_path / "b1", {"host": parts["vm"], "vm": parts["host"]}, lauf=run.lauf)
    with pytest.raises(acc.AcceptanceRefused, match="anderen Lauf"):
        acc.assemble(tmp_path / "b2", parts, lauf={**run.lauf, "run_id": "fremd"})
    manifest_path = parts["vm"] / acc.PART_MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["belege"][0].update(erzeuger="host-abnahme", klasse="E1")  # VM beansprucht Host-Klasse
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(acc.AcceptanceRefused, match="fremde Klasse"):
        acc.assemble(tmp_path / "b3", parts, lauf=run.lauf)
    assert not (tmp_path / "b3").exists()


def test_evidence_changed_after_writing_is_refused_and_rejected(tmp_path) -> None:
    run, parts = _three_parts(tmp_path)
    (parts["host"] / "V4" / "kanarien_E1.json").write_text("{}", encoding="utf-8")
    with pytest.raises(acc.AcceptanceRefused, match="nach dem Schreiben veraendert"):
        acc.assemble(tmp_path / "neu", parts, lauf=run.lauf)
    (run.bundle / "V1" / "firewall_export_E1.json").write_bytes(b'{"art": "firewall_export"}')
    verdict = ev.evaluate(run.bundle, policy=json.loads(run.policy_path.read_text(encoding="utf-8")), lauf=run.lauf,
                          reference=datetime.now(timezone.utc), allow_dry_run=True)
    assert verdict["gesamt"] == BLOCKIERT and _has(verdict["probleme"], "manipuliert")


def test_manual_pass_values_in_evidence_do_not_count(tmp_path) -> None:
    run, _ = _three_parts(tmp_path)
    path = run.bundle / "V2" / "kriterien_E0.json"
    content = json.loads(path.read_text(encoding="utf-8"))
    content["beobachtung"]["konto_gruppen_sids"].append(ev.ADMIN_SID)  # Beobachtung: Administrator
    content["kriterien"]["benchmark_konto_ohne_administratorrechte"] = True  # ... aber von Hand "erfuellt"
    content["status"] = PASS
    path.write_text(json.dumps(content), encoding="utf-8")
    manifest_path = run.bundle / ev.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    import hashlib

    for entry in manifest["belege"]:
        if entry["datei"] == "V2/kriterien_E0.json":
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()  # sogar Manifest nachgezogen
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    policy = json.loads(run.policy_path.read_text(encoding="utf-8"))
    verdict = ev.evaluate(run.bundle, policy=policy, lauf=run.lauf, reference=datetime.now(timezone.utc),
                          allow_dry_run=True)
    assert verdict["gesamt"] == BLOCKIERT and _has(verdict["probleme"], "widerspricht der Beobachtung")
    del content["kriterien"]  # ohne mitgeschriebene Bewertung: aus der Beobachtung abgeleitet -> FAIL
    path.write_text(json.dumps(content), encoding="utf-8")
    for entry in manifest["belege"]:
        if entry["datei"] == "V2/kriterien_E0.json":
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    verdict = ev.evaluate(run.bundle, policy=policy, lauf=run.lauf, reference=datetime.now(timezone.utc),
                          allow_dry_run=True)
    assert verdict["tests"]["V2"]["status"] == FAIL


# --- Trockenlauf zaehlt nie produktiv ----------------------------------------------------------------------------------------


def test_dry_run_bundle_is_rejected_in_production(tmp_path) -> None:
    run = acc.dry_run(tmp_path / "lauf")
    policy = json.loads(run.policy_path.read_text(encoding="utf-8"))
    verdict = ev.evaluate(run.bundle, policy=policy, lauf=run.lauf, reference=datetime.now(timezone.utc))
    assert verdict["gesamt"] == BLOCKIERT and _has(verdict["probleme"], "Trockenlauf-Belege zaehlen nicht produktiv")
    judged = owner_sign.judge(run.bundle, policy_path=run.policy_path, run_id=run.lauf["run_id"],
                              maschine=run.lauf["maschine"], tool=run.lauf["pruefwerkzeug"],
                              reference=datetime.now(timezone.utc))
    assert judged["gesamt"] != PASS  # das Owner-Werkzeug wuerde produktiv nicht signieren


# --- Belegalter --------------------------------------------------------------------------------------------------------------


def test_evidence_older_than_24h_at_use_is_rejected(tmp_path) -> None:
    run = acc.dry_run(tmp_path / "lauf")
    policy = json.loads(run.policy_path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    ok = ev.evaluate(run.bundle, policy=policy, lauf=run.lauf, reference=now, now=now + timedelta(hours=23),
                     allow_dry_run=True)
    assert ok["gesamt"] == PASS
    late = ev.evaluate(run.bundle, policy=policy, lauf=run.lauf, reference=now, now=now + timedelta(hours=25),
                       allow_dry_run=True)
    assert late["gesamt"] == BLOCKIERT and _has(late["probleme"], "zum Nutzungszeitpunkt veraltet")


def test_gate_closes_when_the_oldest_evidence_passes_24h_although_attestation_is_valid(tmp_path) -> None:
    """Vor §91: Beleg 23 h alt beim Signieren + Bestaetigung 2 h gueltig -> bis zu 25 h alt genutzt. Jetzt: Ablehnung."""
    old = (datetime.now(timezone.utc) - timedelta(hours=23)).isoformat()
    gate = make_test_gate(tmp_path, bundle_hook=lambda items: items["V9/kriterien_E1.json"]["entry"].update(
        erzeugt=old))
    assert gate.problems(run_id="qa80-lauf", provider_endpoints={("127.0.0.1", 9)}) == []
    gate.now = lambda: datetime.now(timezone.utc) + timedelta(hours=1, minutes=30)
    problems = gate.problems(run_id="qa80-lauf", provider_endpoints={("127.0.0.1", 9)})
    assert _has(problems, "V9/kriterien_E1.json' zum Nutzungszeitpunkt veraltet")


def test_age_limits_are_defined_for_every_evidence_type() -> None:
    assert set(ev.MAX_EVIDENCE_AGE_AT_USE) == set(ev.ARTEN)
    assert all(limit <= timedelta(hours=24) for limit in ev.MAX_EVIDENCE_AGE_AT_USE.values())


# --- Echte Befehle und Listener: gesperrt ---------------------------------------------------------------------------------


def test_real_commands_are_refused_before_any_process(monkeypatch) -> None:
    started: list = []
    runner = lambda *a, **k: started.append(a)  # noqa: E731
    monkeypatch.delenv(acc.ENV_REAL, raising=False)
    for key in acc.REAL_COMMANDS:
        with pytest.raises(acc.AcceptanceRefused, match="verweigert"):
            acc.run_real(key, real=True, vm="lexono-bench", runner=runner)
    monkeypatch.setenv(acc.ENV_REAL, "1")
    with pytest.raises(acc.AcceptanceRefused, match="verweigert"):
        acc.run_real("host.port_acl", real=False, vm="lexono-bench", runner=runner)
    with pytest.raises(acc.AcceptanceRefused, match="VM-Name"):
        acc.run_real("host.port_acl", real=True, vm="x'; Remove-Item C:\\ -Recurse; '", runner=runner)
    assert started == []
    acc.run_real("host.port_acl", real=True, vm="lexono-bench", runner=runner)  # nur mit Freigabe UND real=True
    assert "-VMName 'lexono-bench'" in started[0][0][-1]


def test_real_commands_only_read_except_the_mandatory_failing_change_attempt() -> None:
    for key, command in acc.REAL_COMMANDS.items():
        changing = any(verb in command for verb in ("Set-", "New-", "Remove-", "Add-", "Restore-", "Enable-", "Disable-"))
        # §96 (N6): der V2-Aenderungsversuch und seine Ruecknahme sind die einzigen Aenderungen (nur in der VM)
        assert changing == (key in ("vm.aenderungsversuch", "vm.aenderungsversuch_rueckbau")), key


def test_canary_listener_only_on_loopback_without_release(monkeypatch) -> None:
    monkeypatch.delenv(acc.ENV_REAL, raising=False)
    with pytest.raises(acc.AcceptanceRefused, match="nur im freigegebenen echten Modus"):
        acc.CanaryListener([{"ip": "192.168.137.1", "port": 0, "protokoll": "tcp"}], mode="real")
    with pytest.raises(acc.AcceptanceRefused, match="nur im freigegebenen echten Modus"):
        acc.CanaryListener([{"ip": "0.0.0.0", "port": 0, "protokoll": "tcp"}])
    with pytest.raises(acc.AcceptanceRefused, match="unbekanntes Protokoll"):
        acc.CanaryListener([{"ip": "127.0.0.1", "port": 0, "protokoll": "icmp"}])
    assert acc.CanaryListener([]).observation() == {"fehler": "Listener nicht gestartet bzw. nicht beendet"}


def test_leak_simulation_only_against_loopback() -> None:
    with pytest.raises(acc.AcceptanceRefused, match="nur gegen Loopback"):
        acc._leak([{"ip": "192.0.2.1", "port": 1, "protokoll": "tcp"}], "tcp")


# --- Ableitungen ------------------------------------------------------------------------------------------------------------


def test_acl_conversion() -> None:
    acl = acc.synthetic_acl({("127.0.0.1", 9)})
    export = ev.acl_to_export(acl)
    assert export["profiles"][0]["default_outbound"] == "Block"
    assert export["rules"][0]["remote_addresses"] == ["127.0.0.1"]  # /32 -> Einzeladresse
    assert ev.acl_to_export(acl[:1])["profiles"][0]["default_outbound"] == "Allow"  # IPv6 nicht gesperrt
    inbound = [*acl, {"Direction": "Inbound", "Action": "Allow", "RemoteIPAddress": "Any", "Protocol": ""}]
    assert ev.acl_to_export(inbound)["rules"] == export["rules"]
    partial = [{**acl[0], "Protocol": "TCP"}, acl[1]]  # Deny nur fuer TCP ist kein Standard-Block
    assert ev.acl_to_export(partial)["profiles"][0]["default_outbound"] == "Allow"
    assert ev.acl_to_export([])["profiles"][0]["enabled"] is False
    with pytest.raises(TypeError):
        ev.acl_to_export({"acl": 1})


def test_junit_parsing_is_strict() -> None:
    assert ev.junit_results(acc.junit_xml({"tests/a.py::t": "passed", "tests/a.py::u": "skipped"})) == {
        "tests/a.py::t": "passed", "tests/a.py::u": "skipped"}
    for bad in ("<!DOCTYPE x><testsuite/>", "<testsuite/>", None,
                "<testsuite><testcase classname='a' name='t'/><testcase classname='a' name='t'><failure/></testcase>"
                "</testsuite>"):
        with pytest.raises((ValueError, TypeError)):
            ev.junit_results(bad)


def test_every_criteria_requirement_has_a_derivation_and_a_synthetic_observation() -> None:
    now = datetime.now(timezone.utc)
    for test, reqs in ev.ANFORDERUNGEN.items():
        for req in reqs:
            observation = acc.synthetic_beobachtung(test, req["art"], req["klasse"], endpoints={("127.0.0.1", 9)}, now=now)
            derived = ev.derive(test, req["art"], req["klasse"], observation, {
                "freigaben": [{"ip_adressen": ["127.0.0.1"], "port": 9}], "lokal": {"loopback_erlaubt": True}},
                {"parameter": acc.SYNTHETIC_PARAMETER})
            if req["art"] == "kriterien":
                assert set(derived["kriterien"]) == set(req["kriterien"]), test
                assert all(v is True for v in derived["kriterien"].values()), (test, derived)
            # leere Beobachtung -> nie True
            empty = ev.derive(test, req["art"], req["klasse"], {}, {"freigaben": [], "lokal": {"loopback_erlaubt": True}})
            if req["art"] == "kriterien":
                assert not any(v is True for v in empty["kriterien"].values()), (test, empty)


def test_acceptance_matrix_still_claims_nothing() -> None:
    matrix = json.loads(acc.MATRIX_PATH.read_text(encoding="utf-8"))
    assert matrix["status"] == "NICHT DURCHGEFUEHRT"
    assert {t["status"] for t in matrix["tests"]} == {"NICHT DURCHGEFUEHRT"}
    assert Path(acc.__file__).name in " ".join(__import__("scripts.legal_bench.isolation_gate",
                                                          fromlist=["TOOL_FILES"]).TOOL_FILES)
