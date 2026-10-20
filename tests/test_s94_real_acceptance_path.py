"""§94: Echter Abnahmemodus - CLI fuer VM/Host/Endpunkt, Manifest, PowerShell-Normalisierung, V11/V13, Port-ACL.

ALLE Systembefehle laufen gegen ein SIMULIERTES Hyper-V bzw. eine simulierte PowerShell (`FakeSystem`); der
Kanarien-Listener und die Gegenprobe laufen echt, aber nur auf Loopback. Es wird keine ACL, keine Firewall, keine VM
veraendert und keine Verbindung nach aussen aufgebaut. Die Ergebnisse belegen nur die Werkzeugkette."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.legal_bench import isolation_acceptance as acc, isolation_evidence as ev, isolation_gate, owner_sign
from scripts.legal_bench import isolation_host_control as hc, isolation_real as real
from scripts.legal_bench.isolation_evidence import BLOCKIERT, FAIL, PASS
from tests.isolation_test_support import synthetic_policy

VM_ID = {"hostname": "lexono-bench-vm", "machine_guid": "00000000-0000-4000-8000-0000000000c1"}
HOST_ID = {"hostname": "owner-host", "machine_guid": "00000000-0000-4000-8000-0000000000c2"}
LOOP = {("127.0.0.1", 9)}


def _has(items, text) -> bool:  # noqa: ANN001
    return any(text in i for i in items)


# --- Simuliertes System ------------------------------------------------------------------------------------------------


class FakeSystem:
    """Simuliert Hyper-V-/Windows-Cmdlets. Zaehlt jeden Aufruf; veraendert nichts ausserhalb des Objekts."""

    def __init__(self) -> None:
        self.acl: list[dict] = []
        self.calls: list[str] = []
        self.state = "Off"
        self.drop = None  # Weight, das beim Setzen "verloren geht" (Abweichung Soll/Ist)
        self.fail = set()  # Befehlspraefixe, die scheitern
        self.attempt_ok = False
        self.sids = ["S-1-5-32-545", "S-1-5-11"]
        self.firewall: list[dict] = []
        self.public_outbound = "Block"
        self.rollback_fails = False
        self.switch = {"Name": "lexono-abnahme", "SwitchType": "Internal"}
        self.disks = ["C:\\Hyper-V\\lexono\\kind.vhdx"]
        self.vhd = None  # wird im Fixture gesetzt
        self.vethernet = {"kategorie": "Public", "standard_eingehend": "Block", "regeln": []}
        self.adapter_guards = {"MacAddressSpoofing": "Off", "DhcpGuard": "On", "RouterGuard": "On"}
        self.integration = [{"Name": "Heartbeat", "Enabled": True}, {"Name": "Guest Service Interface", "Enabled": False}]
        self.routes = [{"DestinationPrefix": "10.88.0.0/24", "NextHop": "0.0.0.0", "InterfaceAlias": "Ethernet"}]

    def __call__(self, argv, **_kw):  # noqa: ANN001, ANN204
        command = argv[-1]
        self.calls.append(command)
        out, code = "", 0
        if any(command.startswith(f) for f in self.fail):
            return subprocess.CompletedProcess(argv, 1, "", "simulierter Fehler")
        if "Get-NetConnectionProfile" in command:
            out = json.dumps(self.vethernet)
        elif "PersistentStore" in command:
            out = json.dumps({"wert": self.public_outbound})
        elif command.startswith("Set-NetFirewallProfile"):
            value = re.search(r"-DefaultOutboundAction (\w+)", command).group(1)
            is_rollback = value != "Allow"
            if is_rollback and self.rollback_fails:
                code = 1
            elif is_rollback or self.attempt_ok:
                self.public_outbound = value
            else:
                code = 1
        elif "Get-NetFirewallRule -Group" in command:
            out = json.dumps(self.firewall[0]) if len(self.firewall) == 1 else (json.dumps(self.firewall) if self.firewall else "")
        elif command.startswith("New-NetFirewallRule"):
            field = lambda name: re.search(rf"-{name} '([^']*)'", command).group(1)  # noqa: E731
            self.firewall.append({"DisplayName": field("DisplayName"), "Enabled": "True", "Direction": "Inbound",
                                  "Action": "Allow", "Protocol": re.search(r"-Protocol (\w+)", command).group(1),
                                  "LocalPort": re.search(r"-LocalPort (\d+)", command).group(1),
                                  "LocalAddress": field("LocalAddress"),
                                  "RemoteAddress": ",".join(re.findall(r"'([^']+)'", command.split("-RemoteAddress ")[1]
                                                                       .split(" -InterfaceAlias")[0])),
                                  "InterfaceAlias": field("InterfaceAlias"), "Description": field("Description")})
        elif command.startswith("Remove-NetFirewallRule"):
            name = re.search(r"-DisplayName '([^']*)'", command).group(1)
            self.firewall = [r for r in self.firewall if r["DisplayName"] != name]
        elif "Get-VMSwitch" in command:
            out = json.dumps(self.switch)
        elif "Get-VMHardDiskDrive" in command:
            out = json.dumps({"Path": self.disks[0]}) if len(self.disks) == 1 else json.dumps([{"Path": d} for d in self.disks])
        elif command.startswith("Get-VHD"):
            out = json.dumps(self.vhd) if self.vhd else ""
        elif "WindowsIdentity" in command:
            out = json.dumps({"name": "LEXONO-VM\\lexono-bench"})
        elif "Get-NetIPConfiguration" in command:
            out = json.dumps({"InterfaceAlias": "Ethernet", "ipv4": "10.88.0.2", "ipv6": "fd00:88::2", "gateway4": None,
                              "gateway6": None, "dns": None})
        elif "Get-VMNetworkAdapterExtendedAcl" in command:
            out = json.dumps(self.acl[0]) if len(self.acl) == 1 else (json.dumps(self.acl) if self.acl else "")
        elif command.startswith("Add-VMNetworkAdapterExtendedAcl"):
            entry = {"Direction": re.search(r"-Direction (\w+)", command).group(1),
                     "Action": re.search(r"-Action (\w+)", command).group(1),
                     "RemoteIPAddress": re.search(r"-RemoteIPAddress '([^']+)'", command).group(1),
                     "RemotePort": (re.search(r"-RemotePort '([^']+)'", command) or [None, ""])[1],
                     "Protocol": (re.search(r"-Protocol '([^']+)'", command) or [None, ""])[1],
                     "Weight": int(re.search(r"-Weight (\d+)", command).group(1)),
                     "Stateful": "-Stateful $true" in command}
            if entry["Weight"] != self.drop:
                self.acl.append(entry)
        elif command.startswith("Remove-VMNetworkAdapterExtendedAcl"):
            weight = int(re.search(r"-Weight (\d+)", command).group(1))
            self.acl = [e for e in self.acl if e["Weight"] != weight]
        elif "Get-VM -Name" in command:
            out = json.dumps({"state": self.state})
        elif command.startswith(("Remove-Item", "New-VHD")):
            pass
        elif "Get-VMNetworkAdapter -VMName" in command:
            out = json.dumps({"vm_netzadapter": {"Name": "LAN", "SwitchName": "lexono-abnahme", **self.adapter_guards},
                              "gastdienste_aktiv": False, "erweiterte_sitzung": False,
                              "integrationsdienste": self.integration})
        elif "Get-NetRoute" in command:
            out = json.dumps(self.routes)
        elif "Get-NetFirewallProfile" in command:
            out = json.dumps([{"Name": n, "enabled": True, "default_outbound": "Block"} for n in ("Domain", "Private", "Public")])
        elif command.startswith("whoami"):
            out = '"Gruppenname","Typ","SID","Attribute"\n' + "\n".join(f'"G","Gruppe","{s}","x"' for s in self.sids)
        elif "Get-NetAdapter" in command:
            out = json.dumps({"Name": "Ethernet", "Status": "Up", "MacAddress": "00-15-5D-00-00-01"})  # EIN Objekt
        else:
            code = 9
        return subprocess.CompletedProcess(argv, code, out, "")


def _free_ports(n: int) -> list[int]:
    ports, sockets = [], []
    for _ in range(n):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        sockets.append(s)
        ports.append(s.getsockname()[1])
    for s in sockets:
        s.close()
    return ports


@pytest.fixture
def setup(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setenv(acc.ENV_REAL, "1")  # nur in diesem Test; alle Befehle gehen an FakeSystem
    policy_path = synthetic_policy(tmp_path, LOOP)
    parent = tmp_path / "golden.vhdx"
    parent.write_bytes(b"golden-image-synthetisch" * 100)
    policy_export, policy_soll = tmp_path / "richtlinie_export.wfw", tmp_path / "richtlinie_soll.wfw"
    policy_export.write_bytes(b"richtlinie")
    policy_soll.write_bytes(b"richtlinie")
    p = _free_ports(5)
    konfig = {"schema": real.KONFIG_SCHEMA, "run_id": "abnahme-test-1", "vm_name": "lexono-bench", "vm_identitaet": VM_ID,
              "netzwerkpolitik": str(policy_path), "host_ips": {"ipv4": "127.0.0.1", "ipv6": "::1"},
              "vm_ips": ["127.0.0.1", "::1"],
              "kanarien": [{"ip": "127.0.0.1", "port": p[0], "protokoll": "tcp"}, {"ip": "::1", "port": p[1], "protokoll": "tcp"},
                           {"ip": "127.0.0.1", "port": p[2], "protokoll": "udp"}, {"ip": "::1", "port": p[3], "protokoll": "udp"},
                           {"ip": "127.0.0.1", "port": p[4], "protokoll": "dns"}],
              "golden_image": {"eltern_vhdx": str(parent), "kind_vhdx": str(tmp_path / "kind.vhdx"),
                               "soll_sha256": hashlib.sha256(parent.read_bytes()).hexdigest()},
              "protokolle": {"acl_aktivierung": str(tmp_path / "acl_aktivierung.json"),
                             "image_ruecksetzung": str(tmp_path / "image_ruecksetzung.json")},
              "gast_richtlinie": {"export": str(policy_export), "soll": str(policy_soll)},
              "endpunkt": {"vm_ip": "10.88.0.2", "log": None}, "v13": {"schluesselliste": None, "bestaetigung": None},
              "switch": {"name": "lexono-abnahme"}, "erlaubte_gateways": [], "benchmark_konto": "lexono-bench"}
    path = tmp_path / "konfig.json"
    path.write_text(json.dumps(konfig), encoding="utf-8")
    system = FakeSystem()
    system.disks = [konfig["golden_image"]["kind_vhdx"]]
    system.vhd = {"Path": konfig["golden_image"]["kind_vhdx"], "ParentPath": str(parent), "VhdType": "Differencing"}
    os.chmod(parent, stat.S_IREAD)  # Golden Image schreibgeschuetzt (M3)
    yield {"tmp": tmp_path, "konfig_path": path, "konfig": konfig, "system": system,
           "policy": json.loads(policy_path.read_text(encoding="utf-8"))}
    os.chmod(parent, stat.S_IREAD | stat.S_IWRITE)


def _junit_runner(skipped: tuple[str, ...] = ()):  # noqa: ANN202
    """Simuliert pytest in der VM: alle Pflichtknoten bestanden (ausser `skipped`). NUR Test - keine echte Messung."""
    def run(nodes, junit):  # noqa: ANN001, ANN202
        results = {}
        for test, reqs in ev.ANFORDERUNGEN.items():
            for req in reqs:
                if req["art"] == "pytest":
                    for prefix in req["knoten"]:
                        if any(prefix.startswith(n) for n in nodes):
                            node = prefix + "test_beispiel" if prefix.endswith("::") else prefix
                            results[node] = "skipped" if any(s in node for s in skipped) else "passed"
        Path(junit).write_text(acc.junit_xml(results), encoding="utf-8")
        return Path(junit).read_text(encoding="utf-8")
    return run


CLEAN_TREE = lambda _runner: {"exit_code": 0, "porcelain": ""}  # noqa: E731 - simulierter Arbeitsbaum der VM
READ_ONLY = lambda: {"geprueft": 12, "beschreibbar": []}  # noqa: E731 - simulierter Schreibtest der VM


def _full_run(s, *, skipped=(), before_nachher=None, set_acl=True, worktree=CLEAN_TREE, write_test=READ_ONLY,  # noqa: ANN001, ANN202, PLR0913
              set_firewall=True):
    system, tmp, konfig = s["system"], s["tmp"], s["konfig"]
    control = hc.HostControl(konfig["vm_name"], ausfuehren=True, runner=system)
    control.image_zuruecksetzen(Path(konfig["golden_image"]["eltern_vhdx"]), Path(konfig["golden_image"]["kind_vhdx"]),
                                konfig["golden_image"]["soll_sha256"], Path(konfig["protokolle"]["image_ruecksetzung"]))
    if set_firewall:
        control.fw_setzen(konfig, tmp / "firewall.json")
    vm_folder = tmp / "teil_vm"

    def wait(message):  # noqa: ANN001, ANN202
        if "VORHER" in message:
            token = re.search(r"--start-token ([0-9a-f]{32})", message).group(1)
            return real.vm_teil(s["konfig_path"], vm_folder, "vorher", real=True, runner=system, identity=lambda: VM_ID,
                                start_token=token)["vm_abschluss_token"]
        if "AKTIVIERUNG" in message and set_acl:
            control.acl_setzen(s["policy"], Path(konfig["protokolle"]["acl_aktivierung"]))
        if "NACHHER" in message:
            if before_nachher:
                before_nachher()
            return real.vm_teil(s["konfig_path"], vm_folder, "nachher", real=True, runner=system, identity=lambda: VM_ID,
                                manifest_path=tmp / "lauf" / "abnahme_manifest.json", pytest_runner=_junit_runner(skipped),
                                worktree=worktree, write_test=write_test)["vm_abschluss_token"]
        return ""

    result = real.host_lauf(s["konfig_path"], tmp / "lauf", real=True, wait=wait, runner=system, identity=lambda: HOST_ID)
    now = datetime.now(timezone.utc)
    log = tmp / "relay.jsonl"
    log.write_text(json.dumps({"zeit": now.isoformat(), "quelle_ip": "10.88.0.2", "pfad": "/abnahme", "status": 401,
                               "weitergeleitet": False}) + "\n", encoding="utf-8")
    endpoint = real.endpunkt_teil(s["konfig_path"], log, tmp / "teil_endpunkt", von=(now - timedelta(hours=1)).isoformat(),
                                  bis=(now + timedelta(minutes=5)).isoformat(), real=True)
    bundle = real.buendeln(s["konfig_path"], {"vm": vm_folder, "host": Path(result["teil"]), "endpunkt": endpoint},
                           tmp / "buendel")
    lauf, policy = real.lauf_aus_konfig(real.load_konfig(s["konfig_path"]))
    verdict = ev.evaluate(bundle, policy=policy, lauf=lauf, reference=datetime.now(timezone.utc),
                          now=datetime.now(timezone.utc))  # PRODUKTIV-Regeln: kein Trockenlauf erlaubt
    return result, bundle, verdict, lauf


# --- Gesamtablauf (simuliertes System, echter Loopback-Listener) ------------------------------------------------------


def test_full_real_mode_pipeline_with_simulated_system(setup) -> None:
    result, bundle, verdict, _ = _full_run(setup)
    status = {t: r["status"] for t, r in verdict["tests"].items()}
    assert status["V13"] == BLOCKIERT  # kein V13-Beleg konfiguriert (D4 fehlt) - ehrlich nicht PASS
    assert {t: s for t, s in status.items() if t != "V13"} == {t: PASS for t in status if t != "V13"}, {
        t: r["gruende"][:2] for t, r in verdict["tests"].items() if r["status"] != PASS}
    assert verdict["gesamt"] == BLOCKIERT and result["nachher_treffer"] == 0
    manifest = json.loads((bundle / ev.MANIFEST_NAME).read_text(encoding="utf-8"))
    for entry in manifest["belege"]:
        content = json.loads((bundle / entry["datei"]).read_text(encoding="utf-8"))
        assert content["umgebung"]["modus"] == "real" and "soll" in content and "beobachtung" in content
    calls = setup["system"].calls
    assert any(c.startswith("Add-VMNetworkAdapterExtendedAcl") for c in calls)
    assert sum(c.startswith("Set-NetFirewallProfile") for c in calls) == 1  # Aenderungsversuch genau einmal (scheitert)


def test_skipped_acceptance_tests_never_pass(setup) -> None:
    _, _, verdict, _ = _full_run(setup, skipped=("canary_addresses_are_blocked[udp]",))
    assert verdict["tests"]["V6"]["status"] == FAIL and verdict["gesamt"] != PASS


def test_v13_with_unpinned_root_is_not_pass(setup, tmp_path) -> None:
    """Gegenprobe des Produktiv-Gates ohne festgelegten Wurzelschluessel: die Ablehnung belegt nichts ueber das Alter."""
    from tests.isolation_test_support import make_test_gate

    (tmp_path / "g").mkdir()
    gate = make_test_gate(tmp_path / "g")
    konfig = {**setup["konfig"], "v13": {"schluesselliste": str(gate.keylist_path), "bestaetigung": str(gate.attestation_path)}}
    observation = real.gate_gegenprobe(konfig, Path(konfig["netzwerkpolitik"]))
    assert observation["schluesselliste_gueltig"] is False and _has(observation["diagnose"], "kein Wurzelschluessel")
    derived = ev.derive("V13", "kriterien", "E1", observation, {})
    # §96 (N1): ohne gueltige gepinnte Vertrauenskette -> BLOCKIERT (Voraussetzung fehlt), nicht FAIL
    assert derived["kriterien"]["produktiv_gate_lehnt_veraltete_bestaetigung_ab"] == "blockiert"


def test_real_bundle_cannot_be_signed_while_v13_is_open(setup) -> None:
    _, bundle, _, lauf = _full_run(setup)
    verdict = owner_sign.judge(bundle, policy_path=Path(setup["konfig"]["netzwerkpolitik"]), run_id=lauf["run_id"],
                               maschine=lauf["maschine"], tool=lauf["pruefwerkzeug"], reference=datetime.now(timezone.utc))
    assert verdict["gesamt"] != PASS  # das Owner-Werkzeug signiert nur bei PASS


# --- Reihenfolge und Abbruch --------------------------------------------------------------------------------------------


def test_vm_nachher_before_vorher_is_refused(setup) -> None:
    with pytest.raises(acc.AcceptanceRefused, match="kein offener Teil"):
        real.vm_teil(setup["konfig_path"], setup["tmp"] / "vm", "nachher", real=True, runner=setup["system"],
                     identity=lambda: VM_ID, manifest_path=setup["tmp"] / "m.json")


def test_vm_vorher_twice_is_refused(setup) -> None:
    folder = setup["tmp"] / "vm"
    with acc.CanaryListener(setup["konfig"]["kanarien"], mode="real"):
        real.vm_teil(setup["konfig_path"], folder, "vorher", real=True, runner=setup["system"], identity=lambda: VM_ID,
                     start_token="a" * 32)
        with pytest.raises(acc.AcceptanceRefused, match="nicht leer"):
            real.vm_teil(setup["konfig_path"], folder, "vorher", real=True, runner=setup["system"], identity=lambda: VM_ID,
                         start_token="a" * 32)


def test_wrong_machine_is_refused(setup) -> None:
    with pytest.raises(acc.AcceptanceRefused, match="nicht auf der VM"):
        real.vm_teil(setup["konfig_path"], setup["tmp"] / "vm", "vorher", real=True, identity=lambda: HOST_ID,
                     start_token="a" * 32)
    with pytest.raises(acc.AcceptanceRefused, match="nicht auf der geprueften VM"):
        real.host_lauf(setup["konfig_path"], setup["tmp"] / "lauf", real=True, identity=lambda: VM_ID)


def test_missing_acl_activation_aborts_host_run(setup) -> None:
    with pytest.raises(FileNotFoundError):
        _full_run(setup, set_acl=False)
    host_part = setup["tmp"] / "lauf" / "teil_host"
    assert not (host_part / acc.PART_MANIFEST).exists()  # Teil bleibt offen -> nicht buendelbar
    with pytest.raises(acc.AcceptanceRefused, match="Manifest unlesbar"):
        real.buendeln(setup["konfig_path"], {"host": host_part}, setup["tmp"] / "b")


def test_acl_not_empty_before_counter_probe_aborts(setup) -> None:
    hc.HostControl("lexono-bench", ausfuehren=True, runner=setup["system"]).fw_setzen(setup["konfig"],
                                                                                    setup["tmp"] / "fw.json")
    setup["system"].acl = [{"Direction": "Outbound", "Action": "Deny", "RemoteIPAddress": "0.0.0.0/0", "RemotePort": "",
                            "Protocol": "", "Weight": 1, "Stateful": False}]
    with pytest.raises(acc.AcceptanceRefused, match="nicht leer - Abbruch"):
        real.host_lauf(setup["konfig_path"], setup["tmp"] / "lauf", real=True, wait=lambda m: None,
                       runner=setup["system"], identity=lambda: HOST_ID)


def test_manipulated_manifest_is_refused_in_the_vm(setup) -> None:
    def tamper():  # noqa: ANN202
        path = setup["tmp"] / "lauf" / "abnahme_manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["kanarienziele"] = data["kanarienziele"][:1]
        data["lauf"]["run_id"] = "anderer"
        path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(acc.AcceptanceRefused, match="anderem Lauf"):
        _full_run(setup, before_nachher=tamper)


def test_tampered_evidence_between_phases_is_refused(setup) -> None:
    folder = setup["tmp"] / "vm"
    with acc.CanaryListener(setup["konfig"]["kanarien"], mode="real"):
        real.vm_teil(setup["konfig_path"], folder, "vorher", real=True, runner=setup["system"], identity=lambda: VM_ID,
                     start_token="a" * 32)
    (folder / "V4" / "gegenprobe_E0.json").write_text("{}", encoding="utf-8")
    with pytest.raises(acc.AcceptanceRefused, match="seit der letzten Phase veraendert"):
        real.vm_teil(setup["konfig_path"], folder, "nachher", real=True, runner=setup["system"], identity=lambda: VM_ID,
                     manifest_path=setup["tmp"] / "m.json")


# --- Sperren --------------------------------------------------------------------------------------------------------------


def test_real_steps_refused_without_release(setup, monkeypatch) -> None:
    for kwargs in ({"real": False}, {"real": True, "env": False}):
        if kwargs.get("env") is False:
            monkeypatch.delenv(acc.ENV_REAL)
        with pytest.raises(acc.AcceptanceRefused, match="verweigert"):
            real.vm_teil(setup["konfig_path"], setup["tmp"] / "x", "vorher", real=kwargs["real"], identity=lambda: VM_ID,
                         start_token="a" * 32)
        with pytest.raises(acc.AcceptanceRefused, match="verweigert"):
            real.host_lauf(setup["konfig_path"], setup["tmp"] / "y", real=kwargs["real"], identity=lambda: HOST_ID)
    with pytest.raises(acc.AcceptanceRefused, match="Modus 'real' nur"):
        acc.EvidencePart(setup["tmp"] / "z", "vm", {}, {}, mode="real")
    assert setup["system"].calls == []


def test_mixed_dry_run_and_real_parts_are_refused(setup, tmp_path) -> None:
    run = acc.dry_run(tmp_path / "trocken")
    real_part = acc.EvidencePart(tmp_path / "echt", "host", run.lauf, {"freigaben": [], "lokal": {"loopback_erlaubt": True}},
                                 mode="real")
    real_part.close()
    with pytest.raises(acc.AcceptanceRefused, match="mischen Modi"):
        acc.assemble(tmp_path / "b", {"vm": run.folder / "teil_vm", "host": real_part.folder}, lauf=run.lauf)


def test_dry_run_never_enables_real_signing(tmp_path) -> None:
    run = acc.dry_run(tmp_path / "trocken")
    verdict = owner_sign.judge(run.bundle, policy_path=run.policy_path, run_id=run.lauf["run_id"],
                               maschine=run.lauf["maschine"], tool=run.lauf["pruefwerkzeug"],
                               reference=datetime.now(timezone.utc))
    assert verdict["gesamt"] == BLOCKIERT and _has(verdict["probleme"], "Trockenlauf-Belege zaehlen nicht produktiv")


# --- Konfiguration und Manifest -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("change, expected", [
    (lambda k: k.pop("vm_name"), "fehlend"),
    (lambda k: k.update(extra=1), "unbekannt"),
    (lambda k: k.update(run_id="a b"), "run_id"),
    (lambda k: k.update(vm_name="x'; Stop-Computer"), "VM-Name"),
    (lambda k: k["kanarien"].pop(3), "ipv6/udp"),
    (lambda k: k["kanarien"].append({"ip": "192.0.2.1", "port": 5, "protokoll": "tcp"}), "keine Host-Adresse"),
    (lambda k: k["golden_image"].update(soll_sha256="x"), "golden_image"),
    (lambda k: k.update(v13={}), "v13 braucht"),
    (lambda k: k.update(vm_identitaet={"hostname": "x"}), "vm_identitaet"),
    (lambda k: k.update(switch={"name": "x'; y"}), "switch.name"),
    (lambda k: k.update(erlaubte_gateways=["kein-ip"]), "erlaubte_gateways"),
    (lambda k: k.update(benchmark_konto=""), "benchmark_konto"),
], ids=["fehlt", "unbekannt", "run_id", "vm_name", "kanal_fehlt", "fremdes_ziel", "image_hash", "v13", "identitaet",
        "switch", "gateways", "konto"])
def test_konfig_is_strict(setup, change, expected) -> None:
    konfig = json.loads(json.dumps(setup["konfig"]))
    change(konfig)
    setup["konfig_path"].write_text(json.dumps(konfig), encoding="utf-8")
    with pytest.raises(acc.AcceptanceRefused) as refused:
        real.load_konfig(setup["konfig_path"])
    assert _has(refused.value.reasons, expected), refused.value.reasons


def test_manifest_binding(setup) -> None:
    lauf, policy = real.lauf_aus_konfig(real.load_konfig(setup["konfig_path"]))
    acl = hc.normalize_acl(hc.soll_acl(policy))
    manifest = real.abnahme_manifest(setup["konfig"], lauf, policy, acl=acl, start_token="b" * 32)
    real.check_manifest(manifest, lauf)
    with pytest.raises(acc.AcceptanceRefused, match="Start-Token"):
        real.check_manifest(dict(manifest, phasen_start_token=None), lauf)
    assert manifest["lauf_sha256"] == ev.lauf_sha256(lauf) and len(manifest["kanarienziele"]) == 5
    assert manifest["freigegebener_testendpunkt"] == {"ip": "127.0.0.1", "port": 9}
    assert ev.check_rules(manifest["firewall_export_inhalt"], manifest["politik_inhalt"]) == []
    for broken, text in ((dict(manifest, lauf_sha256="0" * 64), "anderem Lauf"),
                         (dict(manifest, lauf={**lauf, "maschine": HOST_ID}), "anderem Lauf"),
                         (dict(manifest, firewall_export_inhalt=None), "ohne ACL-Export"),
                         (dict(manifest, schema="x"), "unbekanntes Schema")):
        with pytest.raises(acc.AcceptanceRefused, match=text):
            real.check_manifest(broken, lauf)
    with pytest.raises(acc.AcceptanceRefused, match="Pruefwerkzeug nicht bestimmbar"):
        real.lauf_aus_konfig(setup["konfig"], tool=lambda: {"commit": None, "sha256": None})


def test_draft_policy_manifest_has_no_endpoint() -> None:
    policy = json.loads(isolation_gate.POLICY_PATH.read_text(encoding="utf-8"))  # echte Politik: ENTWURF
    manifest = real.abnahme_manifest({"kanarien": [], "host_ips": {"ipv4": "10.88.0.1"}}, {"run_id": "x"}, policy, acl=[])
    assert manifest["freigegebener_testendpunkt"] is None  # nichts erfunden -> V3 kann nicht PASS werden


# --- PowerShell-Normalisierung ------------------------------------------------------------------------------------------


def test_powershell_normalization() -> None:
    norm = acc.normalize_ps_json
    assert norm("") == [] and norm(None) == [] and norm("null") == [] and norm("  \r\n") == []
    assert norm('{"b": 1, "a": 2}') == [{"a": 2, "b": 1}]
    assert norm('﻿[{"x": 1}, {"x": 2}]') == [{"x": 1}, {"x": 2}]
    assert json.dumps(norm('{"b": 1, "a": 2}')) == json.dumps(norm('{"a": 2, "b": 1}'))  # deterministisch
    for bad in ("kein json", "1", '"text"', "[1, 2]", "[[{}]]"):
        with pytest.raises(ValueError):
            norm(bad)


def test_acl_normalization_rejects_unclear_values() -> None:
    entry = {"Direction": "Outbound", "Action": "Deny", "RemoteIPAddress": "0.0.0.0/0", "RemotePort": None,
             "Protocol": None, "Weight": 1, "Stateful": False}
    assert hc.normalize_acl([entry])[0]["RemotePort"] == ""
    for change in ({"Direction": 2}, {"Action": 1}, {"Weight": "1"}, {"Stateful": "False"}):
        with pytest.raises(hc.ControlRefused):
            hc.normalize_acl([{**entry, **change}])  # Enum als Zahl (PowerShell 5.1) ist nicht eindeutig
    with pytest.raises(hc.ControlRefused, match="ohne"):
        hc.normalize_acl([{"Direction": "Outbound"}])
    shuffled = [dict(entry, Weight=2, RemoteIPAddress="::/0"), entry]
    assert hc.normalize_acl(shuffled) == hc.normalize_acl(list(reversed(shuffled)))


def test_sid_extraction_from_whoami(setup) -> None:
    setup["system"].sids = ["S-1-5-32-544", "S-1-5-32-545"]
    observation = real.observe_vm_v2(setup["system"], worktree=CLEAN_TREE, write_test=READ_ONLY)
    assert observation["konto_gruppen_sids"] == ["S-1-5-32-544", "S-1-5-32-545"]
    derived = ev.derive("V2", "kriterien", "E0", observation, {})["kriterien"]
    assert derived["benchmark_konto_ohne_administratorrechte"] is False
    assert derived["aenderung_durch_benchmark_konto_gescheitert"] is True
    setup["system"].attempt_ok = True
    changed = ev.derive("V2", "kriterien", "E0", real.observe_vm_v2(setup["system"], worktree=CLEAN_TREE,
                                                                    write_test=READ_ONLY), {})["kriterien"]
    assert changed["aenderung_durch_benchmark_konto_gescheitert"] is False
    assert changed["zustand_nach_aenderungsversuch_unveraendert"] is True  # gelungener Versuch wurde zurueckgenommen
    assert setup["system"].public_outbound == "Block"


# --- Port-ACL setzen, pruefen, entfernen ------------------------------------------------------------------------------------


def test_acl_set_check_remove(setup) -> None:
    system, policy, record = setup["system"], setup["policy"], setup["tmp"] / "aktivierung.json"
    control = hc.HostControl("lexono-bench", ausfuehren=True, runner=system)
    result = control.acl_setzen(policy, record)
    assert [e["Weight"] for e in result["acl_nachher"]] == [1, 2, 10] and result["acl_vorher"] == []
    assert result["acl_nachher_sha256"] == hashlib.sha256(acc._canonical(result["acl_nachher"])).hexdigest()
    assert control.acl_pruefen(policy)["gleich"] is True
    with pytest.raises(hc.ControlRefused, match="existiert bereits"):
        control.acl_setzen(policy, record)
    removed = control.acl_entfernen(policy)
    assert removed["rest"] == [] and system.acl == []


def test_acl_mismatch_rolls_back(setup) -> None:
    system = setup["system"]
    system.drop = 2  # IPv6-Deny kommt nicht an
    control = hc.HostControl("lexono-bench", ausfuehren=True, runner=system)
    with pytest.raises(hc.ControlRefused) as refused:
        control.acl_setzen(setup["policy"], setup["tmp"] / "a.json")
    assert _has(refused.value.reasons, "weicht vom Soll ab") and _has(refused.value.reasons, "Rueckbau: vollstaendig")
    assert system.acl == [] and not (setup["tmp"] / "a.json").exists()


def test_acl_refuses_unknown_state_and_foreign_entries(setup) -> None:
    system = setup["system"]
    foreign = {"Direction": "Outbound", "Action": "Allow", "RemoteIPAddress": "203.0.113.9/32", "RemotePort": "22",
               "Protocol": "TCP", "Weight": 50, "Stateful": True}
    system.acl = [dict(foreign)]
    control = hc.HostControl("lexono-bench", ausfuehren=True, runner=system)
    with pytest.raises(hc.ControlRefused, match="nicht leer"):
        control.acl_setzen(setup["policy"], setup["tmp"] / "a.json")
    with pytest.raises(hc.ControlRefused, match="fremde ACL-Eintraege"):
        control.acl_entfernen(setup["policy"])
    assert system.acl == [foreign] and not any(c.startswith(("Add-", "Remove-")) for c in system.calls)


def test_acl_changes_require_release(setup, monkeypatch) -> None:
    system = setup["system"]
    plan_only = hc.HostControl("lexono-bench", ausfuehren=False, runner=system)
    plan = plan_only.acl_plan(setup["policy"])
    assert len(plan) == 3 and all(p.startswith("Add-VMNetworkAdapterExtendedAcl -VMName 'lexono-bench'") for p in plan)
    assert "-RemoteIPAddress '::/0' -Weight 2" in plan[1] and "-Stateful $true" in plan[2]
    for call in (lambda c: c.acl_setzen(setup["policy"], setup["tmp"] / "a.json"), lambda c: c.acl_entfernen(setup["policy"]),
                 lambda c: c.image_zuruecksetzen(Path("p"), Path("c"), "0" * 64, setup["tmp"] / "r.json")):
        with pytest.raises(hc.ControlRefused, match="nicht freigegeben"):
            call(plan_only)
    monkeypatch.delenv(acc.ENV_REAL)
    with pytest.raises(hc.ControlRefused, match="nicht freigegeben"):
        hc.HostControl("lexono-bench", ausfuehren=True, runner=system).acl_setzen(setup["policy"], setup["tmp"] / "a.json")
    assert system.calls == []
    with pytest.raises(acc.AcceptanceRefused, match="VM-Name"):
        hc.HostControl("x'; Remove-VM", ausfuehren=True, runner=system)


def test_acl_allow_only_from_approved_policy(setup) -> None:
    draft = {**setup["policy"], "status": "ENTWURF"}
    with pytest.raises(hc.ControlRefused, match="FREIGEGEBEN"):
        hc.soll_acl(draft)
    real_draft = json.loads(isolation_gate.POLICY_PATH.read_text(encoding="utf-8"))  # echte Politik ohne Endpunkt
    assert [e["Action"] for e in hc.soll_acl(real_draft)] == ["Deny", "Deny"]


def test_change_commands_are_separate_from_read_commands() -> None:
    for command in acc.REAL_COMMANDS.values():
        assert "Add-VMNetworkAdapterExtendedAcl" not in command and "Remove-VMNetworkAdapterExtendedAcl" not in command
    assert "[string]$_.Direction" in acc.REAL_COMMANDS["host.port_acl"]  # Enum als Text, nicht als Zahl


# --- V11 Golden Image --------------------------------------------------------------------------------------------------------


def test_image_reset_requires_vm_off_and_records_hashes(setup) -> None:
    system, image = setup["system"], setup["konfig"]["golden_image"]
    control = hc.HostControl("lexono-bench", ausfuehren=True, runner=system)
    system.state = "Running"
    with pytest.raises(hc.ControlRefused, match="ausgeschaltet"):
        control.image_zuruecksetzen(Path(image["eltern_vhdx"]), Path(image["kind_vhdx"]), image["soll_sha256"],
                                    setup["tmp"] / "r1.json")
    system.state = "Off"
    record = control.image_zuruecksetzen(Path(image["eltern_vhdx"]), Path(image["kind_vhdx"]), image["soll_sha256"],
                                         setup["tmp"] / "r2.json")
    assert record["image_sha256_vorher"] == record["image_sha256_nachher"] == image["soll_sha256"]
    derived = ev.derive("V11", "kriterien", "E1", record, {})["kriterien"]
    assert derived["golden_image_zurueckgesetzt"] is True and derived["image_hash_vorher_gleich_nachher"] is True
    system.fail = {"New-VHD"}
    with pytest.raises(hc.ControlRefused, match="fehlgeschlagen"):
        control.image_zuruecksetzen(Path(image["eltern_vhdx"]), Path(image["kind_vhdx"]), image["soll_sha256"],
                                    setup["tmp"] / "r3.json")
    failed = json.loads((setup["tmp"] / "r3.json").read_text(encoding="utf-8"))
    assert ev.derive("V11", "kriterien", "E1", failed, {})["kriterien"]["golden_image_zurueckgesetzt"] is False


def test_changed_golden_image_fails_v11(setup) -> None:
    parent = Path(setup["konfig"]["golden_image"]["eltern_vhdx"])
    os.chmod(parent, stat.S_IREAD | stat.S_IWRITE)
    parent.write_bytes(b"veraendert")
    os.chmod(parent, stat.S_IREAD)
    _, _, verdict, _ = _full_run(setup)
    assert verdict["tests"]["V11"]["status"] == FAIL
    assert _has(verdict["tests"]["V11"]["gruende"], "image_hash_vorher_gleich_nachher verletzt")


def test_reset_after_counter_probe_start_blocks_v11() -> None:
    """§96 (M6): Reihenfolge Ruecksetzung -> Gegenprobe nur mit der Host-Uhr (beides Host-Belege)."""
    now = datetime.now(timezone.utc)
    own = [({"art": "kriterien", "klasse": "E1"}, {"beobachtung": {"zuruecksetzen": {"zeit": now.isoformat()}}})]
    assert ev._v11_cross(own, now - timedelta(minutes=1)) == [
        "Ruecksetzung nach Beginn der Gegenprobe - Lauf nicht vom zurueckgesetzten System"]
    assert ev._v11_cross(own, now + timedelta(minutes=1)) == []
    assert ev._v11_cross(own, None) == [
        "keine Gegenprobe nach der Ruecksetzung - Lauf nicht dem zurueckgesetzten System zuzuordnen"]
    assert ev._v11_cross([({"art": "kriterien", "klasse": "E1"}, {"beobachtung": {}})], now) == [
        "Zeitpunkt der Ruecksetzung fehlt oder ist ungueltig"]


def test_incomplete_endpoint_log_is_recorded_as_error(setup) -> None:
    log = setup["tmp"] / "relay.jsonl"
    log.write_text('{"zeit": "2026-10-20T10:00:00+00:00", "quelle_ip": "10.88.0.2"}' + "\n", encoding="utf-8")
    folder = real.endpunkt_teil(setup["konfig_path"], log, setup["tmp"] / "ep", von="2026-10-20T09:00:00+00:00",
                                bis="2026-10-20T11:00:00+00:00", real=True)
    content = json.loads((folder / "V3" / "kriterien_E2.json").read_text(encoding="utf-8"))
    assert "unvollstaendig" in content["beobachtung"]["fehler"]
    assert not any(v is True for v in content["kriterien"].values())


def test_tool_fingerprint_covers_new_files() -> None:
    for name in ("scripts/legal_bench/isolation_real.py", "scripts/legal_bench/isolation_host_control.py",
                 "tests/test_s94_real_acceptance_path.py"):
        assert name in isolation_gate.TOOL_FILES
