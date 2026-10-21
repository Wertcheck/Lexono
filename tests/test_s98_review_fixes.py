"""§98: Regressionstests zu den Befunden aus AUDIT_S97 (H1, H2, M1-M9, N1-N6).

Nur lokal: Loopback, simuliertes Hyper-V/PowerShell (`FakeSystem` aus §94), synthetische Belege, Prozessabbruch in einem
Kindprozess. Keine Systemaenderung, keine externe Verbindung. Ergebnisse belegen nur die Werkzeugkette."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.legal_bench import answer_specs, isolation_acceptance as acc, isolation_evidence as ev, isolation_gate
from scripts.legal_bench import isolation_host_control as hc, isolation_real as real, owner_sign
from scripts.legal_bench.isolation_evidence import BLOCKIERT, FAIL, PASS
from tests.isolation_test_support import bundle_items, make_test_gate, synthetic_policy, write_bundle
from tests.test_s94_real_acceptance_path import FakeSystem, _full_run, setup  # noqa: F401 - Fixture

LOOP = {("127.0.0.1", 9)}
NOW = datetime.now(timezone.utc)
KANARIEN = ("V4", "V5", "V6", "V7", "V8", "V12")


def _has(items, text) -> bool:  # noqa: ANN001
    return any(text in i for i in items)


@pytest.fixture
def judge(tmp_path):  # noqa: ANN001, ANN201
    policy_path = synthetic_policy(tmp_path, LOOP)
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    lauf = {"run_id": "lauf-98", "maschine": {"hostname": "vm", "machine_guid": "g"},
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


# --- H2/M1: Fingerabdruck ueber allen ausfuehrbaren Code und die Laufzeit ---------------------------------------------------


def test_fingerprint_covers_all_executable_code() -> None:
    files = set(isolation_gate.tool_files())
    for name in ("app/main.py", "app/__init__.py", "app/ai_providers/endpoint_policy.py", "app/ai_providers/factory.py",
                 "tests/conftest.py", "scripts/__init__.py", "scripts/legal_bench/e2e.py", "run.py", "pyproject.toml"):
        assert name in files, name
    app_py = {p.relative_to(answer_specs.REPO).as_posix() for p in (answer_specs.REPO / "app").rglob("*.py")
              if "__pycache__" not in p.parts}
    assert app_py <= files  # jede app-Datei, auch kuenftige


def _fake_site(tmp_path, monkeypatch, files: dict[str, str]):  # noqa: ANN001, ANN202
    import site

    folder = tmp_path / "site-packages"
    folder.mkdir(exist_ok=True)
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(site, "getsitepackages", lambda: [str(folder)])
    return folder


def test_runtime_fingerprint_detects_pth_modules_and_import_env(tmp_path, monkeypatch) -> None:
    folder = _fake_site(tmp_path, monkeypatch, {"a.pth": "import harmlos", "paket.py": "x = 1"})
    monkeypatch.delenv("PYTHONPATH", raising=False)
    base = isolation_gate.laufzeit_stand()["sha256"]
    assert base and isolation_gate.laufzeit_stand()["sha256"] == base  # deterministisch
    (folder / "a.pth").write_text("import boese; boese.run()", encoding="utf-8")
    changed_pth = isolation_gate.laufzeit_stand()["sha256"]
    (folder / "a.pth").write_text("import harmlos", encoding="utf-8")
    (folder / "unbekannt.py").write_text("print('neu')", encoding="utf-8")
    new_module = isolation_gate.laufzeit_stand()["sha256"]
    (folder / "unbekannt.py").unlink()
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "fremd"))
    env_set = isolation_gate.laufzeit_stand()["sha256"]
    assert len({base, changed_pth, new_module, env_set}) == 4


def test_runtime_fingerprint_normalizes_install_paths(tmp_path, monkeypatch) -> None:
    """Editable-.pth enthalten absolute Pfade: Repository/venv werden normalisiert (gleiche Installation = gleich)."""
    data = f"import sys; sys.path.insert(0, r'{answer_specs.REPO}')".encode()
    assert str(answer_specs.REPO).encode() not in isolation_gate._normalize_paths(data)
    assert b"<REPO>" in isolation_gate._normalize_paths(data)


def test_unreadable_runtime_closes_the_gate(tmp_path, monkeypatch) -> None:
    import importlib.metadata

    # laufzeit_stand faellt bei unlesbaren Distributionen fail-closed auf None (ohne aktive Signierung zu verhindern)
    monkeypatch.setattr(importlib.metadata, "distributions", lambda: (_ for _ in ()).throw(OSError("kaputt")))
    assert isolation_gate.pruefwerkzeug_stand()["laufzeit_sha256"] is None
    monkeypatch.undo()
    # und das Gate schliesst, wenn die Laufzeit zum Pruefzeitpunkt nicht bestimmbar ist
    (tmp_path / "x").mkdir()
    gate = make_test_gate(tmp_path / "x")
    gate.tool = lambda: {**isolation_gate.pruefwerkzeug_stand(), "laufzeit_sha256": None}
    assert _has(gate.problems(run_id="qa80-lauf", provider_endpoints=LOOP), "Laufzeitumgebung")


def test_changed_runtime_invalidates_attestation(tmp_path) -> None:
    (tmp_path / "g").mkdir()
    gate = make_test_gate(tmp_path / "g")
    real_stand = isolation_gate.pruefwerkzeug_stand()
    gate.tool = lambda: {**real_stand, "laufzeit_sha256": "e" * 64}
    assert _has(gate.problems(run_id="qa80-lauf", provider_endpoints=LOOP), "Pruefwerkzeug seit der Bestaetigung geaendert")


def test_symlinked_tool_file_is_not_accepted(tmp_path, monkeypatch) -> None:
    link = tmp_path / "link.py"
    try:
        link.symlink_to(tmp_path / "ziel.py")
    except OSError:
        pytest.skip("Symlinks ohne Entwicklermodus/Rechte nicht anlegbar")
    monkeypatch.setattr(isolation_gate, "tool_files", lambda: ("link.py",))
    monkeypatch.setattr(isolation_gate.answer_specs, "REPO", tmp_path)
    assert isolation_gate.pruefwerkzeug_stand()["sha256"] is None


def _batch(mutate=None, missing=()):  # noqa: ANN001, ANN202
    def run(argv, input=b"", **_kw):  # noqa: A002, ANN001, ANN202
        if "ls-tree" in argv:  # §105 (A10): Commit-Dateiliste = Arbeitsbaum (keine geloeschten/hinzugefuegten)
            return subprocess.CompletedProcess(argv, 0, "\n".join(isolation_gate.tool_files()), "")
        out = b""
        for line in input.decode().splitlines():
            name = line.split(":", 1)[1]
            if name in missing:
                out += f"{line} missing\n".encode()
                continue
            data = (answer_specs.REPO / name).read_bytes()
            data = mutate(name, data) if mutate else data
            out += f"{'0' * 40} blob {len(data)}\n".encode() + data + b"\n"
        return subprocess.CompletedProcess(argv, 0, out, b"")
    return run


def test_commit_boundary_detects_changed_untracked_and_unknown_files() -> None:
    stand = isolation_gate.pruefwerkzeug_stand()["sha256"]
    assert owner_sign.committed_stand("x", runner=_batch())["sha256"] == stand
    changed = owner_sign.committed_stand("x", runner=_batch(
        lambda n, d: d + b"\n# geaendert" if n == "app/ai_providers/endpoint_policy.py" else d))
    assert changed["sha256"] != stand
    untracked = owner_sign.committed_stand("x", runner=_batch(missing=("tests/conftest.py",)))
    assert untracked["sha256"] is None and untracked["fehlend"] == "tests/conftest.py"
    broken = owner_sign.committed_stand("x", runner=lambda argv, **kw: subprocess.CompletedProcess(argv, 0, b"kaputt", b""))
    assert broken["sha256"] is None


def test_real_git_reports_uncommitted_tool_files() -> None:
    """Echt (nur lesend): §90-§98 sind uncommittet -> kein festgeschriebener Stand, Signieren unmoeglich."""
    assert owner_sign.committed_stand("HEAD")["sha256"] is None


def test_write_test_covers_app_dirs_site_packages_and_pth(tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / "app" / "documents").mkdir(parents=True)
    (repo / "app" / "documents" / "service.py").write_text("x", encoding="utf-8")
    packages = tmp_path / "site-packages"
    packages.mkdir()
    (packages / "start.pth").write_text("import x", encoding="utf-8")
    result = real.observe_write_access(repo, site_packages=[packages])
    for item in ("app/documents/service.py", "app/documents/", str(packages / "start.pth"), str(packages) + "/"):
        assert item in result["beschreibbar"], (item, result["beschreibbar"])
    assert not list(tmp_path.rglob(".lexono-schreibtest-*"))


# --- H1/M7: Firewall und Listener waehrend der Nachher-Phase ----------------------------------------------------------------


def test_firewall_removed_during_after_phase_blocks_canary_tests(setup) -> None:
    """Regression zu §97 P4 (damals V1-V12 PASS)."""
    system = setup["system"]
    _, _, verdict, _ = _full_run(setup, before_nachher=system.firewall.clear)
    for test in KANARIEN:
        status, reasons = _status(verdict, test)
        assert status == BLOCKIERT and _has(reasons, "Firewall-Regeln"), (test, reasons)
    assert verdict["gesamt"] != PASS


def test_firewall_changed_during_after_phase_blocks(setup) -> None:
    system = setup["system"]
    _, _, verdict, _ = _full_run(setup, before_nachher=lambda: system.firewall[0].update(RemoteAddress="Any"))
    assert _status(verdict, "V4")[0] == BLOCKIERT


@pytest.mark.parametrize("change, reason", [
    (lambda b: b.update(firewall_kontrollen=[]), "Firewall-Kontrolle nachher_start fehlt"),
    (lambda b: b["firewall_kontrollen"][1].update(gleich=False), "fehlend/veraendert/unlesbar"),
    (lambda b: b["firewall_kontrollen"][1].update(ist_sha256="f" * 64), "waehrend der Phase veraendert"),
    (lambda b: b.update(kontrollen=[c for c in b["kontrollen"] if c["zeitpunkt"] != "nachher_ende"]),
     "Listener-Kontrolle nachher_ende fehlt"),
    (lambda b: b["kontrollen"][0].update(ok=[]), "Listener reagierte bei Kontrolle"),
    (lambda b: b.pop("kontrollen"), "Kontrollen fehlen"),
], ids=["fw_fehlt", "fw_ungleich", "fw_veraendert", "listener_ende_fehlt", "listener_stumm", "ohne_kontrollen"])
def test_missing_or_bad_controls_block(judge, change, reason) -> None:
    verdict = judge(lambda items: change(items["V6/kanarien_E1.json"]["beobachtung"]))
    status, reasons = _status(verdict, "V6")
    assert status == BLOCKIERT and _has(reasons, reason), reasons


def test_firewall_changed_between_before_and_after_blocks(judge) -> None:
    def change(items):  # noqa: ANN001, ANN202
        for check in items["V4/gegenprobe_E1.json"]["beobachtung"]["firewall_kontrollen"]:
            check["ist_sha256"] = "a" * 64
    status, reasons = _status(judge(change), "V4")
    assert status == BLOCKIERT and _has(reasons, "zwischen Vorher- und Nachher-Phase veraendert")


def test_periodic_check_records_transient_failures() -> None:
    answers = iter([{"gleich": True, "ist_sha256": "a" * 64}, {"gleich": False, "ist_sha256": None}])

    def check():  # noqa: ANN202
        try:
            return next(answers)
        except StopIteration:
            raise RuntimeError("Firewall nicht lesbar") from None

    with acc.PeriodicCheck(check, interval_s=0.05) as monitor:
        time.sleep(0.4)
    assert monitor.results[0]["gleich"] is True and monitor.results[1]["gleich"] is False
    assert monitor.results[2]["gleich"] is False and "RuntimeError" in monitor.results[2]["fehler"]
    assert {r["zeitpunkt"] for r in monitor.results} == {"nachher_waehrend"}


def test_control_packets_are_identified_by_content_not_target() -> None:
    """§98 (N4): ein gleichzeitiger VM-Treffer auf dasselbe Ziel bleibt ein Treffer."""
    with acc.CanaryListener([{"ip": "127.0.0.1", "port": 0, "protokoll": "tcp"},
                             {"ip": "127.0.0.1", "port": 0, "protokoll": "udp"}], nachlauf_s=0.3) as listener:
        listener.mark("nachher")
        tcp, udp = listener.targets
        stop = threading.Event()

        def vm_like() -> None:
            while not stop.is_set():
                with socket.create_connection(("127.0.0.1", tcp["port"]), timeout=2):
                    pass
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.sendto(b"vm", ("127.0.0.1", udp["port"]))
                time.sleep(0.05)

        thread = threading.Thread(target=vm_like)
        thread.start()
        result = listener.control_check("nachher_ende")
        stop.set()
        thread.join()
    observation = listener.observation("nachher")
    assert set(result["ok"]) == {f"127.0.0.1:{tcp['port']}", f"127.0.0.1:{udp['port']}"}
    assert {h["protokoll"] for h in observation["treffer"]} == {"tcp", "udp"}  # VM-Treffer nicht als Kontrolle gezaehlt


def test_empty_or_unreadable_port_lists_are_not_safe() -> None:
    assert ev._risky_rules([{"Name": "x", "Profile": "Any", "LocalPort": [], "InterfaceAlias": []}],
                           "Public", "vEthernet (lexono-abnahme)") is None


# --- M2/M3/N3: Proben und DNS ------------------------------------------------------------------------------------------------


def test_hanging_probe_process_is_not_a_block(monkeypatch) -> None:
    from tests import test_s86_isolation_design as s86

    def hang(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise subprocess.TimeoutExpired("python", 20)

    monkeypatch.setattr(s86.subprocess, "run", hang)
    assert s86.probe("tcp", "127.0.0.1", 9) == "prozess_timeout"
    monkeypatch.setattr(s86.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, b"", b"Traceback"))
    assert s86.probe("tcp", "127.0.0.1", 9) == "prozessfehler"  # Absturz des Kindprozesses
    monkeypatch.setattr(s86.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 6, b"", b""))
    assert s86.probe("dns", "localhost", 9) == "keine_aufloesung"  # kein Sperrnachweis


def test_dns_query_probe_is_meaningful() -> None:
    from tests.test_s86_isolation_design import probe

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    closed = s.getsockname()[1]
    s.close()
    assert probe("dnsquery", "127.0.0.1", closed, timeout=2) in ("abgelehnt", "zeitueberschreitung")
    with acc.CanaryListener([{"ip": "127.0.0.1", "port": 0, "protokoll": "dns"}]) as listener:
        listener.mark("nachher")
        assert probe("dnsquery", "127.0.0.1", listener.targets[0]["port"], timeout=2) == "erreicht"
    assert len(listener.observation("nachher")["treffer"]) == 1  # die DNS-Abfrage wurde am Host gesehen


@pytest.mark.parametrize("output, expected", [
    (b"DNS request timed out.\n    timeout was 1 seconds.\nServer:  UnKnown\nAddress:  10.88.0.1", "zeitueberschreitung"),
    ("Zeitüberschreitung der DNS-Anforderung".encode("cp850"), "zeitueberschreitung"),
    (b"*** UnKnown can't find x.invalid: Non-existent domain\nServer:  UnKnown\nAddress:  10.88.0.1", "erreicht"),
    (b"*** x wurde von UnKnown nicht gefunden: No response from server.\nServer:  UnKnown\nAddress:  127.0.0.1",
     "abgelehnt"),
    (b"Server:  UnKnown\nAddress:  10.88.0.1", "fehler"),  # nur Kopfzeilen = keine Aussage
    (b"Zeitzone", "fehler"),  # frueher: Teilstring "Zeit" galt als Zeitueberschreitung
], ids=["timeout_en", "timeout_de", "nxdomain", "keine_antwort", "nur_kopf", "teilstring"])
def test_nslookup_output_is_interpreted_strictly(output, expected) -> None:
    from tests.test_s86_isolation_design import _nslookup_result

    assert _nslookup_result(output) == expected


def test_v7_dns_probe_result_is_meaningful_not_a_blind_block() -> None:
    """§106 (A9, Verhaltenstest statt Quelltextpruefung): Eine DNS-Antwort ohne Resolver bzw. ohne Antwortmarker darf
    NICHT als Sperre (zeitueberschreitung) gelten; nur eine echte Zeitueberschreitung zaehlt."""
    from tests.test_s86_isolation_design import _nslookup_result

    assert _nslookup_result(b"Server:  UnKnown\nAddress:  10.0.0.1") == "fehler"  # nur Kopf -> keine Aussage
    assert _nslookup_result(b"") == "fehler"
    timeout_en = b"DNS request timed out.\n    timeout was 1 seconds.\nServer:  UnKnown"
    assert _nslookup_result(timeout_en) == "zeitueberschreitung"  # nur die echte Zeitueberschreitung sperrt
    assert _nslookup_result(b"*** x can't find y: Non-existent domain\nServer:  r\nAddress:  10.0.0.1") == "erreicht"


# --- M4-M6: Journal, Abbruch an jedem Schritt, Stromausfall, alte Laeufe --------------------------------------------------------


class _Interrupt(FakeSystem):
    def __init__(self, at: int, prefix: str) -> None:
        super().__init__()
        self.at, self.prefix, self.count = at, prefix, 0

    def __call__(self, argv, **kw):  # noqa: ANN001, ANN204
        if argv[-1].startswith(self.prefix):
            self.count += 1
            if self.count == self.at:
                raise KeyboardInterrupt
        return super().__call__(argv, **kw)


@pytest.mark.parametrize("step", [1, 2, 3])
def test_acl_set_interrupted_at_each_step_is_rolled_back(setup, step) -> None:
    system = _Interrupt(step, "Add-VMNetworkAdapterExtendedAcl")
    control = hc.HostControl("lexono-bench", ausfuehren=True, runner=system, journal=setup["tmp"] / "j.jsonl", run_id="r1",
                             startcheck_pflicht=False)
    with pytest.raises(KeyboardInterrupt):
        control.acl_setzen(setup["policy"], setup["tmp"] / "acl.json")
    assert system.acl == [] and not (setup["tmp"] / "acl.json").exists()
    statuses = [e["status"] for e in hc.read_journal(setup["tmp"] / "j.jsonl")]
    assert statuses[0] == "geplant" and "abgebrochen" in statuses and statuses[-1] == "zurueckgebaut"
    assert hc.journal_offen(hc.read_journal(setup["tmp"] / "j.jsonl")) == []


@pytest.mark.parametrize("step", [1, 3, 5])
def test_firewall_set_interrupted_at_each_step_is_rolled_back(setup, step) -> None:
    system = _Interrupt(step, "New-NetFirewallRule")
    control = hc.HostControl("lexono-bench", ausfuehren=True, runner=system, journal=setup["tmp"] / "j.jsonl", run_id="r1",
                             startcheck_pflicht=False)
    with pytest.raises(KeyboardInterrupt):
        control.fw_setzen(setup["konfig"], setup["tmp"] / "fw.json")
    assert system.firewall == []
    assert hc.read_journal(setup["tmp"] / "j.jsonl")[-1]["status"] == "zurueckgebaut"


def test_failed_rollback_is_recorded_as_incomplete(setup) -> None:
    class Broken(FakeSystem):
        def __call__(self, argv, **kw):  # noqa: ANN001, ANN204
            if argv[-1].startswith("Remove-VMNetworkAdapterExtendedAcl"):
                return subprocess.CompletedProcess(argv, 1, "", "verweigert")
            return super().__call__(argv, **kw)

    system = Broken()
    system.drop = 2  # Abweichung -> Rueckbau, der aber scheitert
    control = hc.HostControl("lexono-bench", ausfuehren=True, runner=system, journal=setup["tmp"] / "j.jsonl", run_id="r1",
                             startcheck_pflicht=False)
    with pytest.raises(hc.ControlRefused, match="UNVOLLSTAENDIG"):
        control.acl_setzen(setup["policy"], setup["tmp"] / "acl.json")
    open_items = hc.journal_offen(hc.read_journal(setup["tmp"] / "j.jsonl"))
    assert open_items and open_items[0]["status"] == "unvollstaendig"


_POWER_LOSS = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["LEXONO_ISOLATION_ACCEPTANCE"] = "1"
from scripts.legal_bench import isolation_host_control as hc
from tests.test_s94_real_acceptance_path import FakeSystem
konfig = json.loads(open(sys.argv[2], encoding="utf-8").read())
class PowerLoss(FakeSystem):
    count = 0
    def __call__(self, argv, **kw):
        if argv[-1].startswith("New-NetFirewallRule"):
            PowerLoss.count += 1
            if PowerLoss.count == int(sys.argv[4]):
                os._exit(9)  # Stromausfall: kein except, kein finally, kein Rueckbau
        return super().__call__(argv, **kw)
control = hc.HostControl("lexono-bench", ausfuehren=True, runner=PowerLoss(), journal=sys.argv[3], run_id=konfig["run_id"],
                         startcheck_pflicht=False)
control.fw_setzen(konfig, sys.argv[3] + ".protokoll")
'''


@pytest.mark.parametrize("step", [1, 3])
def test_power_loss_leaves_an_open_journal_entry_that_startcheck_reports(setup, step) -> None:
    script, journal = setup["tmp"] / "power.py", setup["tmp"] / "journal.jsonl"
    script.write_text(_POWER_LOSS, encoding="utf-8")
    result = subprocess.run([sys.executable, str(script), str(answer_specs.REPO), str(setup["konfig_path"]), str(journal),
                             str(step)], capture_output=True, timeout=120, check=False)
    assert result.returncode == 9, result.stderr
    entries = hc.read_journal(journal)
    assert entries[-1]["status"] == "begonnen"  # letzter Befehl ohne Ergebnis
    open_items = hc.journal_offen(entries)
    assert len(open_items) == 1 and open_items[0]["vorgang"] == "fw-setzen"
    control = hc.HostControl("lexono-bench", ausfuehren=False, runner=setup["system"], journal=journal)
    assert _has(control.startcheck(setup["konfig"])["befunde"], "offener Vorgang")


def test_leftovers_of_an_old_run_are_removed_selectively(setup) -> None:
    system, journal = setup["system"], setup["tmp"] / "j.jsonl"
    old = dict(setup["konfig"], run_id="alter-lauf")
    hc.HostControl("lexono-bench", ausfuehren=True, runner=system, journal=journal, run_id="alter-lauf",
                   startcheck_pflicht=False).fw_setzen(old, setup["tmp"] / "fw_alt.json")
    foreign = {**system.firewall[0], "DisplayName": "Lexono-Abnahme alter-lauf TCP-445 10.0.0.1",
               "Description": "run_id=alter-lauf;ablauf=x"}  # gleiches Namensschema, aber NICHT im Journal
    system.firewall.append(foreign)
    new = hc.HostControl("lexono-bench", ausfuehren=True, runner=system, journal=journal, run_id=setup["konfig"]["run_id"])
    with pytest.raises(hc.ControlRefused, match="fremde Regeln"):
        new.fw_entfernen(setup["konfig"])  # aktueller Lauf: fremde Regeln -> nichts
    result = new.fw_entfernen(setup["konfig"], lauf="alter-lauf")
    assert len(result["entfernt"]) == 5 and system.firewall == [foreign] and result["unberuehrt"] == [foreign]
    no_journal = hc.HostControl("lexono-bench", ausfuehren=True, runner=system, run_id="x")
    with pytest.raises(hc.ControlRefused, match="kein Journaleintrag"):
        no_journal.fw_entfernen(setup["konfig"], lauf="alter-lauf")


def test_acl_leftovers_of_an_old_run_are_removed_selectively(setup) -> None:
    system, journal = setup["system"], setup["tmp"] / "j.jsonl"
    hc.HostControl("lexono-bench", ausfuehren=True, runner=system, journal=journal, run_id="alter-lauf",
                   startcheck_pflicht=False).acl_setzen(setup["policy"], setup["tmp"] / "acl_alt.json")
    foreign = {"Direction": "Outbound", "Action": "Allow", "RemoteIPAddress": "203.0.113.5/32", "RemotePort": "22",
               "Protocol": "TCP", "Weight": 77, "Stateful": True}
    system.acl.append(dict(foreign))
    control = hc.HostControl("lexono-bench", ausfuehren=True, runner=system, journal=journal, run_id="neu")
    result = control.acl_entfernen({"freigaben": []}, lauf="alter-lauf")
    assert len(result["entfernt"]) == 3 and system.acl == [foreign] and result["unberuehrt"] == [foreign]


def test_status_files_of_several_runs_are_kept(setup) -> None:
    with pytest.raises(acc.AcceptanceRefused):
        _full_run(setup, set_firewall=False)
    with pytest.raises(acc.AcceptanceRefused):
        real.host_lauf(setup["konfig_path"], setup["tmp"] / "lauf", real=True, wait=lambda m: "",
                       runner=setup["system"], identity=lambda: {"hostname": "h", "machine_guid": "g"})
    files = sorted((setup["tmp"] / "lauf").glob(f"{real.STATUS_PREFIX}*.json"))
    assert len(files) == 2  # nichts ueberschrieben


def test_startcheck_reports_missing_child_disk(setup) -> None:
    control = hc.HostControl("lexono-bench", ausfuehren=False, runner=setup["system"])
    assert _has(control.startcheck(setup["konfig"])["befunde"], "Kinddatentraeger fehlt")


# --- M8/M9/N6: V9-Erweiterungen und Abdeckung -------------------------------------------------------------------------------


@pytest.mark.parametrize("change, criterion", [
    (lambda b: b["vm_netzadapter"][0].update(MacAddressSpoofing="On"), "mac_spoofing_aus verletzt"),
    (lambda b: b["vm_netzadapter"][0].update(DhcpGuard="Off"), "dhcp_guard_an verletzt"),
    (lambda b: b["vm_netzadapter"][0].update(RouterGuard="Off"), "router_guard_an verletzt"),
    (lambda b: b["vm_netzadapter"][0].update(RouterGuard=None), "router_guard_an nicht eindeutig"),
    (lambda b: b["integrationsdienste"].append({"Name": "Key-Value Pair Exchange", "Enabled": True}),
     "integrationsdienste_minimal verletzt"),
    (lambda b: b["integrationsdienste"].append({"Name": "Unbekannter Dienst", "Enabled": True}),
     "integrationsdienste_minimal verletzt"),
    (lambda b: b.update(integrationsdienste=[]), "integrationsdienste_minimal nicht eindeutig"),
], ids=["mac_spoofing", "dhcp_guard", "router_guard", "router_guard_unklar", "kvp", "unbekannter_dienst", "keine_dienste"])
def test_v9_adapter_protection_and_integration_channels(judge, change, criterion) -> None:
    verdict = judge(lambda items: change(items["V9/kriterien_E1.json"]["beobachtung"]))
    status, reasons = _status(verdict, "V9")
    assert status == FAIL and _has(reasons, criterion), reasons


def test_v9_routes_via_foreign_gateways_fail(judge) -> None:
    verdict = judge(_seen("V9/kriterien_E0.json", routen=[{"DestinationPrefix": "0.0.0.0/1", "NextHop": "10.88.0.99"}]))
    status, reasons = _status(verdict, "V9")
    assert status == FAIL and _has(reasons, "keine_routen_ueber_fremde_gateways verletzt")


def test_uncovered_paths_are_reported_in_every_verdict(judge) -> None:
    verdict = judge()
    assert verdict["gesamt"] == PASS and len(verdict["nicht_abgedeckt"]) >= 5
    assert _has(verdict["nicht_abgedeckt"], "hvsocket") and _has(verdict["nicht_abgedeckt"], "Broadcast/Multicast")


# --- N5: Uhr der VM ---------------------------------------------------------------------------------------------------------


def _shift_vm(minutes: float):  # noqa: ANN202
    def hook(items):  # noqa: ANN001, ANN202
        for item in items.values():
            if item["entry"]["klasse"] == "E0":
                created = datetime.fromisoformat(item["entry"]["erzeugt"]) + timedelta(minutes=minutes)
                item["entry"]["erzeugt"] = created.isoformat()
    return hook


def test_vm_clock_within_tolerance_passes(judge) -> None:
    assert judge(_shift_vm(3))["gesamt"] == PASS


def test_vm_clock_skew_blocks_every_vm_based_test(judge) -> None:
    verdict = judge(_shift_vm(-12))
    for test in ("V2", "V4", "V9", "V13"):
        status, reasons = _status(verdict, test)
        assert status == BLOCKIERT and _has(reasons, "VM-Uhr weicht"), (test, reasons)
    assert verdict["tests"]["V1"]["status"] == PASS  # reiner Host-Beleg


def test_contradictory_vm_times_block(judge) -> None:
    def hook(items):  # noqa: ANN001, ANN202
        items["V4/gegenprobe_E0.json"]["entry"]["erzeugt"] = (NOW - timedelta(minutes=40)).isoformat()
        items["V5/pytest_E0.json"]["entry"]["erzeugt"] = (NOW - timedelta(minutes=34)).isoformat()
    verdict = judge(hook)
    assert _status(verdict, "V4")[0] == BLOCKIERT
    assert _has(verdict["tests"]["V4"]["gruende"], "VM-")


def test_tool_fingerprint_lists_this_file() -> None:
    assert "tests/test_s98_review_fixes.py" in isolation_gate.tool_files()
    assert os.path.isfile(answer_specs.REPO / "tests" / "test_s98_review_fixes.py")
