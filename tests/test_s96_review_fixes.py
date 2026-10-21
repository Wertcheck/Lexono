"""§96: Regressionstests zu den Befunden aus AUDIT_S95 (H1, H2, M1-M8, N1-N7).

Nur lokal: Loopback-Listener und -Proben, simuliertes Hyper-V/PowerShell (`FakeSystem` aus §94), synthetische Belege.
Keine Systemaenderung, keine externe Verbindung. Ergebnisse belegen nur die Werkzeugkette - keine reale Isolation."""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.legal_bench import answer_specs, isolation_acceptance as acc, isolation_evidence as ev, isolation_gate
from scripts.legal_bench import isolation_host_control as hc, isolation_real as real, owner_keys, owner_sign
from scripts.legal_bench.isolation_evidence import BLOCKIERT, FAIL, PASS
from tests.isolation_test_support import (bundle_items, keylist_payload, make_test_gate, synthetic_policy, write_bundle,
                                          write_keylist)
from tests.test_s94_real_acceptance_path import CLEAN_TREE, READ_ONLY, _full_run, setup  # noqa: F401 - Fixture

LOOP = {("127.0.0.1", 9)}
NOW = datetime.now(timezone.utc)
NETZ = tuple(ev.KANAELE)


def _has(items, text) -> bool:  # noqa: ANN001
    return any(text in i for i in items)


@pytest.fixture
def judge(tmp_path):  # noqa: ANN001, ANN201
    policy_path = synthetic_policy(tmp_path, LOOP)
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    lauf = {"run_id": "lauf-96", "maschine": {"hostname": "vm", "machine_guid": "g"},
            "netzwerkpolitik_sha256": isolation_gate.sha256_file(policy_path),
            "pruefwerkzeug": {"commit": "c" * 40, "sha256": "d" * 64}}
    counter = {"n": 0}

    def run(*hooks):  # noqa: ANN001, ANN202
        items = bundle_items(LOOP, NOW)
        for hook in hooks:
            hook(items)
        counter["n"] += 1
        folder = write_bundle(tmp_path / f"b{counter['n']}", items, lauf=lauf, policy=policy)
        return ev.evaluate(folder, policy=policy, lauf=lauf, reference=NOW, now=NOW, allow_dry_run=True)

    return run


def _seen(name: str, **changes):  # noqa: ANN003, ANN202
    return lambda items: items[name]["beobachtung"].update(changes)


def _status(verdict: dict, test: str) -> tuple[str, list]:
    return verdict["tests"][test]["status"], verdict["tests"][test]["gruende"]


# --- H1: keine verlorenen Treffer am Phasenende --------------------------------------------------------------------------


def test_udp_datagrams_just_before_exit_are_not_lost() -> None:
    """Regression zu §95 P3 (19 von 20 verloren): jetzt muss JEDES Datagramm im Log stehen."""
    lost = 0
    for _ in range(10):
        with acc.CanaryListener([{"ip": "127.0.0.1", "port": 0, "protokoll": "udp"}], nachlauf_s=0.3) as listener:
            listener.mark("nachher")
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(b"x", ("127.0.0.1", listener.targets[0]["port"]))
        lost += 0 if listener.observation("nachher")["treffer"] else 1
    assert lost == 0


def test_tcp_connections_just_before_exit_are_not_lost() -> None:
    with acc.CanaryListener([{"ip": "127.0.0.1", "port": 0, "protokoll": "tcp"}], nachlauf_s=0.3) as listener:
        listener.mark("nachher")
        for _ in range(5):
            with socket.create_connection(("127.0.0.1", listener.targets[0]["port"]), timeout=2):
                pass
    assert len(listener.observation("nachher")["treffer"]) == 5


def test_phase_end_waits_for_quiet_and_reports_completion() -> None:
    with acc.CanaryListener([{"ip": "127.0.0.1", "port": 0, "protokoll": "udp"}], nachlauf_s=0.6) as listener:
        listener.mark("nachher")

        def late() -> None:  # Treffer WAEHREND der Ruhezeit verlaengert sie
            time.sleep(0.3)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(b"spaet", ("127.0.0.1", listener.targets[0]["port"]))

        threading.Thread(target=late).start()
        start = time.monotonic()
    elapsed = time.monotonic() - start
    observation = listener.observation("nachher")
    assert len(observation["treffer"]) == 1 and elapsed >= 0.85  # 0.3 bis zum Treffer + 0.6 Ruhe
    assert observation["abschluss"]["geleert"] is True and observation["abschluss"]["threads_beendet"] is True
    assert observation["abschluss"]["nachlauf_s"] >= 0.6


@pytest.mark.parametrize("abschluss, reason", [
    (None, "nicht vollstaendig abgeschlossen"),
    ({"geleert": True, "threads_beendet": False, "nachlauf_s": 5.0}, "nicht vollstaendig abgeschlossen"),
    ({"geleert": True, "threads_beendet": True, "nachlauf_s": 0.5}, "Nachlauf ohne Treffer kuerzer"),
], ids=["fehlt", "thread_laeuft", "nachlauf_zu_kurz"])
def test_incomplete_listener_completion_is_never_pass(judge, abschluss, reason) -> None:
    verdict = judge(_seen("V6/kanarien_E1.json", abschluss=abschluss))
    status, reasons = _status(verdict, "V6")
    assert status == FAIL and _has(reasons, reason)


def test_vorher_phase_without_quiet_is_never_pass(judge) -> None:
    verdict = judge(*[_seen(f"{t}/gegenprobe_E1.json", nachlauf_s=0.1) for t in NETZ])
    assert verdict["tests"]["V4"]["status"] == FAIL
    assert _has(verdict["tests"]["V4"]["gruende"], "Vorher-Phase ohne Nachlauf")


def test_kanarien_without_phase_tokens_are_never_pass(judge) -> None:
    verdict = judge(_seen("V5/kanarien_E1.json", phasen_token=None))
    assert verdict["tests"]["V5"]["status"] in (FAIL, BLOCKIERT) and verdict["gesamt"] != PASS


# --- H2: Ablehnung != Sperre ----------------------------------------------------------------------------------------------


def _closed_port(kind=socket.SOCK_STREAM) -> int:  # noqa: ANN001
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_probe_results_are_unambiguous() -> None:
    assert acc.probe_once({"ip": "127.0.0.1", "port": _closed_port(), "protokoll": "tcp"}) == "abgelehnt"
    assert acc.probe_once({"ip": "127.0.0.1", "port": _closed_port(socket.SOCK_DGRAM), "protokoll": "udp"},
                          timeout=2) in ("abgelehnt", "zeitueberschreitung")
    with acc.CanaryListener([{"ip": "127.0.0.1", "port": 0, "protokoll": "tcp"},
                             {"ip": "127.0.0.1", "port": 0, "protokoll": "udp"}]) as listener:
        listener.mark("vorher")
        tcp, udp = listener.targets
        assert acc.probe_once(tcp) == "erreicht" and acc.probe_once(udp) == "erreicht"  # UDP-Echo (N3)


def test_s86_udp_probe_is_meaningful_against_the_canary() -> None:
    """Regression zu §95 P9: die UDP-Probe meldete gegen einen lebenden Listener 'blockiert'."""
    from tests.test_s86_isolation_design import probe

    with acc.CanaryListener([{"ip": "127.0.0.1", "port": 0, "protokoll": "udp"}]) as listener:
        listener.mark("nachher")
        assert probe("udp", "127.0.0.1", listener.targets[0]["port"], timeout=2) == "erreicht"


def test_short_timeout_would_mistake_a_refusal_for_a_block() -> None:
    """Dokumentiert die Windows-Wiederholung nach RST (~2 s): mit zu kurzem Zeitlimit wird aus 'abgelehnt' scheinbar
    'zeitueberschreitung'. Deshalb ist das Zeitlimit der Proben 5 s."""
    port = _closed_port()
    assert acc.probe_once({"ip": "127.0.0.1", "port": port, "protokoll": "tcp"}, timeout=5) == "abgelehnt"
    if os.name == "nt":
        assert acc.probe_once({"ip": "127.0.0.1", "port": port, "protokoll": "tcp"}, timeout=0.5) == "zeitueberschreitung"


@pytest.mark.parametrize("result", ["abgelehnt", "zeitueberschreitung", "fehler"])
def test_calibration_without_reach_is_blocked(judge, result) -> None:
    def change(b):  # noqa: ANN001, ANN202
        for attempt in b["versuche"]:
            if "ipv4/tcp" in attempt["kanaele"]:
                attempt["ergebnis"] = result
    verdict = judge(lambda items: change(items["V4/gegenprobe_E0.json"]["beobachtung"]))
    status, reasons = _status(verdict, "V4")
    assert status == BLOCKIERT and _has(reasons, f"nicht erreicht ({result})")


# --- M1: keine unbeobachteten Zeitfenster ---------------------------------------------------------------------------------


def test_hits_outside_phases_are_reported_and_fail(judge) -> None:
    with acc.CanaryListener([{"ip": "127.0.0.1", "port": 0, "protokoll": "tcp"}], nachlauf_s=0.3) as listener:
        with socket.create_connection(("127.0.0.1", listener.targets[0]["port"]), timeout=2):
            pass  # vor der ersten Phase
        time.sleep(0.2)
        listener.mark("vorher")
        listener.mark("aktivierung")
        with socket.create_connection(("127.0.0.1", listener.targets[0]["port"]), timeout=2):
            pass  # waehrend der ACL-Aktivierung
        time.sleep(0.2)
        listener.mark("nachher")
    after = listener.observation("nachher")
    assert after["treffer"] == [] and {h["phase"] for h in after["ausserhalb_der_phasen"]} == {None, "aktivierung"}
    verdict = judge(_seen("V7/kanarien_E1.json", ausserhalb_der_phasen=after["ausserhalb_der_phasen"]))
    status, reasons = _status(verdict, "V7")
    assert status == FAIL and _has(reasons, "ausserhalb der Phasen")


# --- M4: Fingerabdruck, Arbeitsbaum, Schreibrechte -----------------------------------------------------------------------


def test_tool_fingerprint_covers_execution_relevant_files() -> None:
    files = isolation_gate.tool_files()
    for name in ("scripts/legal_bench/answer_driver.py", "scripts/legal_bench/net_fence.py",
                 "scripts/legal_bench/answer_exec.py", "scripts/legal_bench/answer_run.py",
                 "scripts/legal_bench/answer_store.py", "tests/test_s96_review_fixes.py",
                 "benchmarks/legal_answers_v1/isolation/netzwerkpolitik.json"):
        assert name in files, name
    assert any(f.startswith("app/ai_providers/") for f in files) and any(f.startswith("app/privacy/") for f in files)


def test_committed_stand_detects_uncommitted_files() -> None:
    """Echt (lesend, lokales git): die §96-Testdatei ist nicht im Commit -> kein festgeschriebener Stand."""
    stand = owner_sign.committed_stand("HEAD")
    assert stand["sha256"] is None and stand["fehlend"]


def test_committed_stand_matches_worktree_only_if_contents_match() -> None:
    def batch(mutate=None):  # noqa: ANN001, ANN202 - simuliert `git cat-file --batch` mit dem Arbeitsbaum-Inhalt
        def run(argv, input=b"", **_kw):  # noqa: A002, ANN001, ANN202
            if "ls-tree" in argv:  # §105 (A10): Commit-Dateiliste = Arbeitsbaum
                return subprocess.CompletedProcess(argv, 0, "\n".join(isolation_gate.tool_files()), "")
            out = b""
            for line in input.decode().splitlines():
                name = line.split(":", 1)[1]
                data = (answer_specs.REPO / name).read_bytes()
                data = mutate(name, data) if mutate else data
                out += f"{'0' * 40} blob {len(data)}\n".encode() + data + b"\n"
            return subprocess.CompletedProcess(argv, 0, out, b"")
        return run

    assert owner_sign.committed_stand("x", runner=batch())["sha256"] == isolation_gate.pruefwerkzeug_stand()["sha256"]
    changed = batch(lambda name, data: data + b"# veraendert" if name.endswith("answer_driver.py") else data)
    assert owner_sign.committed_stand("x", runner=changed)["sha256"] != isolation_gate.pruefwerkzeug_stand()["sha256"]


def test_signing_refuses_a_worktree_that_differs_from_the_commit(tmp_path) -> None:
    gate = make_test_gate(tmp_path)
    with pytest.raises(owner_sign.SigningRefused) as refused:
        owner_sign.sign_attestation(gate._test_key, "test-key-1", keylist_path=gate.keylist_path, bundle=gate.evidence_path,
                                    policy_path=gate.policy_path, run_id="qa80-lauf", maschine=isolation_gate.machine_identity(),
                                    pruefer_name="Owner (synthetisch, Test)", hours=2, pinned_roots=gate.pinned_roots,
                                    test_only=True, now=datetime.now(timezone.utc),
                                    signer_identity=lambda: {"hostname": "h", "machine_guid": "g"},
                                    committed=lambda commit: {"commit": commit, "sha256": "0" * 64})
    assert _has(refused.value.reasons, "Arbeitsbaum des Hosts weicht vom Commit ab")


@pytest.mark.parametrize("change, criterion", [
    ({"arbeitsbaum": {"exit_code": 0, "porcelain": " M scripts/legal_bench/answer_driver.py"}}, "arbeitsbaum_sauber verletzt"),
    ({"arbeitsbaum": {"exit_code": 128, "porcelain": None}}, "arbeitsbaum_sauber nicht eindeutig"),
    ({"schreibtest": {"geprueft": 12, "beschreibbar": ["scripts/legal_bench/isolation_gate.py"]}},
     "repository_schreibgeschuetzt verletzt"),
    ({"schreibtest": {"geprueft": 0, "beschreibbar": []}}, "repository_schreibgeschuetzt nicht eindeutig"),
    ({"konto_name": "LEXONO-VM\\Administrator"}, "laeuft_als_benchmark_konto verletzt"),
], ids=["arbeitsbaum_geaendert", "git_fehler", "beschreibbar", "nichts_geprueft", "falsches_konto"])
def test_v2_worktree_write_access_and_account(judge, change, criterion) -> None:
    verdict = judge(_seen("V2/kriterien_E0.json", **change))
    status, reasons = _status(verdict, "V2")
    assert status == FAIL and _has(reasons, criterion)


def test_write_access_probe_detects_writable_repository(tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / "scripts" / "legal_bench").mkdir(parents=True)
    (repo / "scripts" / "legal_bench" / "isolation_gate.py").write_text("x", encoding="utf-8")
    result = real.observe_write_access(repo, site_packages=[])
    assert "scripts/legal_bench/isolation_gate.py" in result["beschreibbar"] and "./" in result["beschreibbar"]
    assert not list(repo.rglob(".lexono-schreibtest-*"))  # Probedateien wieder entfernt
    os.chmod(repo / "scripts" / "legal_bench" / "isolation_gate.py", stat.S_IREAD)
    try:
        assert "scripts/legal_bench/isolation_gate.py" not in real.observe_write_access(
            repo, site_packages=[])["beschreibbar"]
    finally:
        os.chmod(repo / "scripts" / "legal_bench" / "isolation_gate.py", stat.S_IREAD | stat.S_IWRITE)


def test_evidence_folders_inside_the_repository_are_refused(setup) -> None:
    with pytest.raises(acc.AcceptanceRefused, match="nie im Repository"):
        real.vm_teil(setup["konfig_path"], answer_specs.REPO / "tmp_belege", "vorher", real=True,
                     identity=lambda: real.load_konfig(setup["konfig_path"])["vm_identitaet"], start_token="a" * 32)
    assert not (answer_specs.REPO / "tmp_belege").exists()


# --- M2: Switch und Netzwerkkonfiguration der VM --------------------------------------------------------------------------


@pytest.mark.parametrize("change, criterion", [
    ({"vm_netzadapter": [{"Name": "LAN", "SwitchName": "Default Switch"}]}, "adapter_am_abnahme_switch verletzt"),
    ({"switch": {"Name": "lexono-abnahme", "SwitchType": "External"}}, "adapter_am_abnahme_switch verletzt"),
    ({"switch": None}, "adapter_am_abnahme_switch nicht eindeutig"),
], ids=["default_switch", "externer_switch", "switch_unbekannt"])
def test_v9_checks_the_actual_switch(judge, change, criterion) -> None:
    verdict = judge(_seen("V9/kriterien_E1.json", **change))
    status, reasons = _status(verdict, "V9")
    assert status == FAIL and _has(reasons, criterion)


@pytest.mark.parametrize("config, criterion", [
    ([{"InterfaceAlias": "Ethernet", "gateway4": ["10.88.0.1"], "gateway6": [], "dns": []}], "standardgateway_wie_soll verletzt"),
    ([{"InterfaceAlias": "Ethernet", "gateway4": [], "gateway6": [], "dns": ["10.88.0.1"]}], "kein_dns_server_in_der_vm verletzt"),
    (None, "standardgateway_wie_soll nicht eindeutig"),
], ids=["gateway", "dns_server", "unbekannt"])
def test_v9_checks_vm_ip_configuration(judge, config, criterion) -> None:
    verdict = judge(_seen("V9/kriterien_E0.json", ip_konfiguration=config))
    status, reasons = _status(verdict, "V9")
    assert status == FAIL and _has(reasons, criterion)


def test_wrong_switch_in_the_simulated_pipeline_fails_v9(setup) -> None:
    setup["system"].switch = {"Name": "Default Switch", "SwitchType": "Internal"}
    _, _, verdict, _ = _full_run(setup)
    assert verdict["tests"]["V9"]["status"] == FAIL


# --- M3: V11 an den tatsaechlichen Datentraeger gebunden ------------------------------------------------------------------


@pytest.mark.parametrize("change, criterion", [
    ({"vm_datentraeger": ["C:\\VM\\anderer.vhdx"]}, "vm_nutzt_kinddatentraeger verletzt"),
    ({"vm_datentraeger": ["C:\\Hyper-V\\lexono\\kind.vhdx", "C:\\VM\\zweite.vhdx"]}, "vm_nutzt_kinddatentraeger verletzt"),
    ({"kind_vhd": {"Path": "C:\\Hyper-V\\lexono\\kind.vhdx", "ParentPath": "C:\\VM\\fremd.vhdx",
                   "VhdType": "Differencing"}}, "kind_haengt_am_golden_image verletzt"),
    ({"kind_vhd": {"Path": "C:\\Hyper-V\\lexono\\kind.vhdx", "ParentPath": "C:\\Hyper-V\\lexono\\golden.vhdx",
                   "VhdType": "Dynamic"}}, "kind_haengt_am_golden_image verletzt"),
    ({"eltern_schreibgeschuetzt": False}, "golden_image_schreibgeschuetzt verletzt"),
], ids=["andere_platte", "zweite_platte", "fremde_eltern", "kein_differenzdatentraeger", "golden_beschreibbar"])
def test_v11_is_bound_to_the_vm_disk(judge, change, criterion) -> None:
    verdict = judge(_seen("V11/kriterien_E1.json", **change))
    status, reasons = _status(verdict, "V11")
    assert status == FAIL and _has(reasons, criterion)


def test_other_vm_disk_in_the_simulated_pipeline_fails_v11(setup) -> None:
    setup["system"].disks = ["C:\\VM\\nicht_das_golden_image.vhdx"]
    _, _, verdict, _ = _full_run(setup)
    assert verdict["tests"]["V11"]["status"] == FAIL


def test_writable_golden_image_fails_v11(setup) -> None:
    os.chmod(setup["konfig"]["golden_image"]["eltern_vhdx"], stat.S_IREAD | stat.S_IWRITE)
    _, _, verdict, _ = _full_run(setup)
    assert verdict["tests"]["V11"]["status"] == FAIL
    assert _has(verdict["tests"]["V11"]["gruende"], "golden_image_schreibgeschuetzt verletzt")


# --- M5: wirksamer Policy Store ----------------------------------------------------------------------------------------------


def test_guest_firewall_is_read_from_the_active_store() -> None:
    assert "-PolicyStore ActiveStore" in acc.REAL_COMMANDS["vm.gast_firewall"]
    assert "-PolicyStore ActiveStore" in Path(isolation_gate.__file__).read_text(encoding="utf-8")


# --- M6: keine Uhrenvergleiche zwischen VM und Host -----------------------------------------------------------------------


def test_vm_clock_is_checked_against_host_phases(judge) -> None:
    """§96: keine Reihenfolge-Pruefung ueber VM-Uhr gegen Host-Uhr (informative VM-Zeiten aendern nichts).
    §98 (N5): stattdessen Plausibilitaet der VM-Uhr - liegt sie ausserhalb der Host-Phasen (> 5 min), BLOCKIERT."""
    def informative(items):  # noqa: ANN001, ANN202
        for test in ("V4", "V5", "V6", "V7", "V8"):
            for attempt in items[f"{test}/gegenprobe_E0.json"]["beobachtung"]["versuche"]:
                attempt["zeit_vm"] = "2001-01-01T00:00:00+00:00"
    assert judge(informative)["gesamt"] == PASS

    def skew(items):  # noqa: ANN001, ANN202
        for test in ("V4", "V5", "V6", "V7", "V8"):
            items[f"{test}/pytest_E0.json"]["entry"]["erzeugt"] = (NOW - timedelta(minutes=45)).isoformat()
    verdict = judge(skew)
    assert verdict["tests"]["V4"]["status"] == BLOCKIERT and _has(verdict["tests"]["V4"]["gruende"], "VM-")


def test_listener_measures_durations_monotonically() -> None:
    with acc.CanaryListener([{"ip": "127.0.0.1", "port": 0, "protokoll": "tcp"}], nachlauf_s=0.2) as listener:
        listener.mark("vorher")
        time.sleep(0.3)
        listener.mark("nachher")
    assert listener.observation("vorher")["dauer_s"] >= 0.3 + 0.2 - 0.05


# --- M7: eigene Host-Firewall-Regeln, Reste, Rueckbau ---------------------------------------------------------------------


def _control(setup):  # noqa: ANN001, ANN202
    return hc.HostControl(setup["konfig"]["vm_name"], ausfuehren=True, runner=setup["system"])


def test_firewall_rules_are_narrow_and_removable(setup) -> None:
    control, konfig = _control(setup), setup["konfig"]
    record = control.fw_setzen(konfig, setup["tmp"] / "fw.json")
    assert len(record["regeln"]) == 5 and control.fw_pruefen(konfig)["gleich"] is True
    for rule in record["regeln"]:
        assert rule["Direction"] == "Inbound" and rule["InterfaceAlias"] == "vEthernet (lexono-abnahme)"
        assert rule["LocalPort"].isdigit() and "Any" not in rule["RemoteAddress"]
    assert control.fw_entfernen(konfig)["rest"] == [] and setup["system"].firewall == []


def test_firewall_leftovers_block_setup_and_foreign_rules_block_removal(setup) -> None:
    control, konfig = _control(setup), setup["konfig"]
    control.fw_setzen(konfig, setup["tmp"] / "fw1.json")
    with pytest.raises(hc.ControlRefused, match="Reste in der Firewall-Gruppe"):
        control.fw_setzen(konfig, setup["tmp"] / "fw2.json")
    setup["system"].firewall.append({**setup["system"].firewall[0], "DisplayName": "Fremde Regel", "LocalPort": "445"})
    with pytest.raises(hc.ControlRefused, match="fremde Regeln"):
        control.fw_entfernen(konfig)
    assert len(setup["system"].firewall) == 6  # nichts entfernt


def test_partial_firewall_failure_is_rolled_back(setup) -> None:
    setup["system"].fail = {"New-NetFirewallRule -DisplayName 'Lexono-Abnahme abnahme-test-1 UDP"}
    with pytest.raises(hc.ControlRefused) as refused:
        _control(setup).fw_setzen(setup["konfig"], setup["tmp"] / "fw.json")
    assert _has(refused.value.reasons, "Rueckbau: vollstaendig") and setup["system"].firewall == []


def test_expired_or_foreign_run_rules_are_not_accepted(setup) -> None:
    control, konfig = _control(setup), setup["konfig"]
    control.fw_setzen(konfig, setup["tmp"] / "fw.json", hours=1)
    control.clock = lambda: datetime.now(timezone.utc) + timedelta(hours=2)
    check = control.fw_pruefen(konfig)
    assert check["gleich"] is False and _has(check["probleme"], "abgelaufen")
    other = dict(konfig, run_id="anderer-lauf")
    assert _has(_control(setup).fw_pruefen(other)["probleme"], "anderem Lauf")


def test_startcheck_finds_leftovers(setup) -> None:
    control, konfig, system = _control(setup), setup["konfig"], setup["system"]
    assert _has(control.startcheck(konfig)["befunde"], "Kinddatentraeger fehlt")  # §98 (N1)
    Path(konfig["golden_image"]["kind_vhdx"]).write_bytes(b"kind")
    assert control.startcheck(konfig)["ok"] is True
    control.fw_setzen(konfig, setup["tmp"] / "fw.json")
    control.acl_setzen(setup["policy"], Path(konfig["protokolle"]["acl_aktivierung"]))
    system.state = "Running"
    findings = control.startcheck(konfig)
    assert findings["ok"] is False
    for text in ("Port-ACL nicht leer", "Firewall-Gruppe", "VM laeuft", "acl_aktivierung existiert"):
        assert _has(findings["befunde"], text), text


def test_host_run_requires_own_firewall_rules(setup) -> None:
    with pytest.raises(acc.AcceptanceRefused, match="Host-Firewall-Regeln fehlen"):
        _full_run(setup, set_firewall=False)
    status = _status_files(setup)[-1]
    assert status["abgebrochen"] is True and status["rueckbau_noetig"] == []  # nichts gesetzt -> nichts zurueckzubauen


def test_aborted_host_run_writes_a_rollback_order(setup) -> None:
    with pytest.raises(FileNotFoundError):
        _full_run(setup, set_acl=False)
    status = _status_files(setup)[-1]
    assert status["abgebrochen"] is True and status["phase"] == "aktivierung"
    # §98 (M6): aus dem Ist-Zustand: ACL nie gesetzt -> kein acl-entfernen; eigene Firewall-Regeln vorhanden
    assert len(status["rueckbau_noetig"]) == 1 and status["rueckbau_noetig"][0].startswith("fw-entfernen --lauf")


def test_successful_run_still_requires_rollback(setup) -> None:
    _full_run(setup)
    status = _status_files(setup)[-1]
    assert status["abgebrochen"] is False
    assert [t.split(" ")[0] for t in status["rueckbau_noetig"]] == ["acl-entfernen", "fw-entfernen"]


def _status_files(setup) -> list[dict]:  # noqa: ANN001
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((setup["tmp"] / "lauf").glob(f"{real.STATUS_PREFIX}*.json"))]


# --- M8: Host-Dienste in der Vorher-Phase ----------------------------------------------------------------------------------


@pytest.mark.parametrize("guard, expected", [
    ({"kategorie": "Public", "standard_eingehend": "Block", "regeln": [
        {"Name": "SMB", "Profile": "Any", "LocalPort": ["445"], "InterfaceAlias": []}]}, False),
    ({"kategorie": "Public", "standard_eingehend": "Block", "regeln": [
        {"Name": "RPC", "Profile": "Public", "LocalPort": ["RPC"], "InterfaceAlias": ["vEthernet (lexono-abnahme)"]}]}, False),
    ({"kategorie": "Public", "standard_eingehend": "Block", "regeln": [
        {"Name": "Dyn", "Profile": "Any", "LocalPort": ["49152-65535"], "InterfaceAlias": []}]}, False),
    ({"kategorie": "Public", "standard_eingehend": "Block", "regeln": [
        {"Name": "nur Domain", "Profile": "Domain", "LocalPort": ["445"], "InterfaceAlias": []}]}, True),
    ({"kategorie": "Public", "standard_eingehend": "Block", "regeln": [
        {"Name": "anderer Adapter", "Profile": "Any", "LocalPort": ["445"], "InterfaceAlias": ["WLAN"]}]}, True),
    ({"kategorie": "Public", "standard_eingehend": "NotConfigured", "regeln": []}, None),
    ({"kategorie": "Public", "standard_eingehend": "Allow", "regeln": []}, False),
], ids=["smb_any", "rpc_vethernet", "dynamische_ports", "anderes_profil", "anderer_adapter", "nicht_konfiguriert", "allow"])
def test_v9_host_services_during_counter_probe(guard, expected) -> None:
    observation = acc.synthetic_beobachtung("V9", "kriterien", "E1", endpoints=LOOP, now=NOW)
    observation["vethernet_schutz"] = guard
    derived = ev.derive("V9", "kriterien", "E1", observation, {"freigaben": [{"ip_adressen": ["127.0.0.1"], "port": 9}],
                                                                "lokal": {"loopback_erlaubt": True}},
                        {"parameter": {"switch": "lexono-abnahme"}})
    assert derived["kriterien"]["host_dienste_in_vorher_phase_geschuetzt"] is expected


# --- N1/N2: V13 semantisch, BLOCKIERT ohne Vertrauenskette -----------------------------------------------------------------


@pytest.mark.parametrize("observation, expected", [
    ({"diagnose": [ev.MSG_ATTESTATION_EXPIRED], "schluesselliste_gueltig": False, "schluessel_status": "unbekannt"},
     "blockiert"),
    ({"diagnose": [ev.MSG_ATTESTATION_EXPIRED], "schluesselliste_gueltig": True, "schluessel_status": "widerrufen"},
     "blockiert"),
    ({"diagnose": ["Schluessel des Owners abgelaufen"], "schluesselliste_gueltig": True, "schluessel_status": "aktiv"},
     False),
    ({"diagnose": [ev.MSG_ATTESTATION_EXPIRED + " (x)"], "schluesselliste_gueltig": True, "schluessel_status": "aktiv"},
     False),
    ({"diagnose": [ev.MSG_ATTESTATION_EXPIRED], "schluesselliste_gueltig": True, "schluessel_status": "aktiv"}, True),
], ids=["ohne_kette", "widerrufen", "nur_schluessel_abgelaufen", "kein_exakter_text", "gueltig"])
def test_v13_is_semantic(observation, expected) -> None:
    assert ev.derive("V13", "kriterien", "E1", observation, {})["kriterien"][
        "produktiv_gate_lehnt_veraltete_bestaetigung_ab"] == expected


def test_v13_without_trust_chain_is_blocked_not_failed(judge) -> None:
    verdict = judge(_seen("V13/kriterien_E1.json", schluesselliste_gueltig=False, schluessel_status="unbekannt"))
    status, reasons = _status(verdict, "V13")
    assert status == BLOCKIERT and _has(reasons, "Voraussetzung fehlt")


def test_v13_gate_probe_with_a_pinned_test_root(setup, tmp_path) -> None:
    """Mit gepinnter (Test-)Wurzel und produktiver Schluesselliste: aktiver Schluessel, exakte Ablaufmeldung."""
    (tmp_path / "g").mkdir()
    gate = make_test_gate(tmp_path / "g")
    keylist = write_keylist(tmp_path / "liste.json", gate._root_key,
                            keylist_payload([("test-key-1", gate._test_key)], test_only=False))
    konfig = {**setup["konfig"], "v13": {"schluesselliste": str(keylist), "bestaetigung": str(gate.attestation_path)}}
    os.chmod(keylist, stat.S_IREAD)
    try:
        observation = real.gate_gegenprobe(konfig, Path(konfig["netzwerkpolitik"]), pinned_roots=gate.pinned_roots)
    finally:
        os.chmod(keylist, stat.S_IREAD | stat.S_IWRITE)
    assert observation["schluesselliste_gueltig"] is True and observation["schluessel_status"] == "aktiv"
    assert ev.MSG_ATTESTATION_EXPIRED in observation["diagnose"]
    assert owner_keys.ROOT_MISSING not in observation["diagnose"]


# --- N5: V12-Auswertung gehoert zum Kanarienlog ------------------------------------------------------------------------------


def test_v12_report_must_match_the_canary_log(judge) -> None:
    verdict = judge(_seen("V12/kriterien_E1.json", kanarien_logs_sha256=["f" * 64]))
    status, reasons = _status(verdict, "V12")
    assert status == BLOCKIERT and _has(reasons, "nicht auf das Kanarienlog dieses Buendels")


# --- N6: V2-Aenderungsversuch zuletzt und mit Ruecknahme --------------------------------------------------------------------


def test_successful_change_attempt_is_rolled_back_and_fails(setup) -> None:
    system = setup["system"]
    system.attempt_ok = True
    observation = real.observe_vm_v2(system, worktree=CLEAN_TREE, write_test=READ_ONLY)
    assert observation["aenderungsversuch"]["exit_code"] == 0 and observation["aenderungsversuch"]["rueckbau_exit"] == 0
    assert system.public_outbound == "Block"
    criteria = ev.derive("V2", "kriterien", "E0", observation, {}, {"parameter": {"benchmark_konto": "lexono-bench"}})["kriterien"]
    assert criteria["aenderung_durch_benchmark_konto_gescheitert"] is False  # V2 FAIL - aber Zustand wiederhergestellt
    assert criteria["zustand_nach_aenderungsversuch_unveraendert"] is True


def test_failed_rollback_prevents_pass(setup) -> None:
    system = setup["system"]
    system.attempt_ok, system.rollback_fails = True, True
    observation = real.observe_vm_v2(system, worktree=CLEAN_TREE, write_test=READ_ONLY)
    criteria = ev.derive("V2", "kriterien", "E0", observation, {})["kriterien"]
    assert observation["aenderungsversuch"]["rueckbau_exit"] == 1 and system.public_outbound == "Allow"
    assert criteria["zustand_nach_aenderungsversuch_unveraendert"] is False


def test_change_attempt_runs_after_all_probes(setup) -> None:
    _full_run(setup)
    calls = setup["system"].calls
    attempt = max(i for i, c in enumerate(calls) if c.startswith("Set-NetFirewallProfile -Profile Public -DefaultOutboundAction Allow"))
    reads = [i for i, c in enumerate(calls) if "Get-NetIPConfiguration" in c]
    assert reads and max(reads) < attempt  # Netzwerkbeobachtung vor dem Versuch, Proben (pytest) ohnehin davor


def test_full_simulated_pipeline_still_passes_except_v13(setup) -> None:
    _, _, verdict, _ = _full_run(setup)
    status = {t: r["status"] for t, r in verdict["tests"].items()}
    assert status.pop("V13") == BLOCKIERT and set(status.values()) == {PASS}, verdict  # SIMULATION, keine Isolation


def test_no_test_writes_into_the_repository() -> None:
    assert not list(answer_specs.REPO.glob(".lexono-schreibtest-*"))
    assert not (answer_specs.REPO / "tmp_belege").exists()
