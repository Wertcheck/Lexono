"""§92 (A4): Gegenprobe der Host-Port-ACL - Vorher-Erreichbarkeit ueber den VM-Pfad als Voraussetzung.

Erst wenn ein Kanarienziel VOR Aktivierung der ACL aus der VM erreichbar war (Host-Treffer, Listener mit
Funktionskontrolle), darf das spaetere Ausbleiben von Treffern der ACL zugerechnet werden - je Kanal (IPv4/IPv6,
TCP/UDP/DNS) getrennt. Nur synthetische Daten bzw. Loopback; keine ACL, keine VM, keine Verbindung nach aussen."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.legal_bench import answer_driver, isolation_acceptance as acc, isolation_evidence as ev, isolation_gate, owner_sign
from scripts.legal_bench.answer_store import RunStore
from scripts.legal_bench.isolation_evidence import BLOCKIERT, FAIL, PASS
from tests.isolation_test_support import CANARY_TARGETS, bundle_items, make_test_gate, synthetic_policy, write_bundle
from tests.test_s80_benchmark_driver import EchoProvider, FakeEmbeddingProvider, _config

pytestmark = pytest.mark.usefixtures("synthetic_legal_state")
LOOP = {("127.0.0.1", 9)}
NOW = datetime.now(timezone.utc)
CAL = "gegenprobe_E1.json"
CAL0 = "gegenprobe_E0.json"
KAN = "kanarien_E1.json"
NETZ = tuple(ev.KANAELE)


def _has(items: list[str], text: str) -> bool:
    return any(text in i for i in items)


@pytest.fixture
def judge(tmp_path):  # noqa: ANN001, ANN201
    policy_path = synthetic_policy(tmp_path, LOOP)
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    lauf = {"run_id": "lauf-92", "maschine": {"hostname": "vm", "machine_guid": "g"},
            "netzwerkpolitik_sha256": isolation_gate.sha256_file(policy_path),
            "pruefwerkzeug": {"commit": "c" * 40, "sha256": "d" * 64}}
    counter = {"n": 0}

    def run(*hooks, now=NOW):  # noqa: ANN001, ANN202
        items = bundle_items(LOOP, NOW)
        for hook in hooks:
            hook(items)
        counter["n"] += 1
        folder = write_bundle(tmp_path / f"b{counter['n']}", items, lauf=lauf, policy=policy)
        return ev.evaluate(folder, policy=policy, lauf=lauf, reference=NOW, now=now, allow_dry_run=True)

    return run


def _status(verdict: dict) -> dict[str, str]:
    return {t: r["status"] for t, r in verdict["tests"].items()}


def _on(tests, name, change):  # noqa: ANN001, ANN202
    """Beobachtung der Datei `name` in jedem Test aus `tests` aendern."""
    def hook(items):  # noqa: ANN001, ANN202
        for test in tests:
            key = f"{test}/{name}"
            if key in items:
                change(items[key]["beobachtung"])
    return hook


def _drop_hits(predicate):  # noqa: ANN001, ANN202
    return lambda b: b.update(treffer=[h for h in b["treffer"] if not predicate(h)])


V4_TCP = "198.51.100.9:443"  # einziges IPv4/TCP-Ziel der synthetischen Kanarien
V6_UDP = "2001:db8::8:5000"


# --- Erfolg -------------------------------------------------------------------------------------------------------------


def test_complete_counter_probe_chain_passes(judge) -> None:
    verdict = judge()
    assert verdict["gesamt"] == PASS, verdict
    for test in NETZ:
        arts = {(r["art"], r["klasse"]) for r in ev.ANFORDERUNGEN[test]}
        assert ("gegenprobe", "E1") in arts
        assert (("gegenprobe", "E0") in arts) == (test != "V12")


# --- Vorher nicht erreichbar / tote Listener / fehlende Beobachtung --------------------------------------------------------


def test_blocked_before_on_one_channel_blocks_only_tests_of_that_channel(judge) -> None:
    """IPv4/TCP vorher ohne Treffer: V4, V8, V12 BLOCKIERT - der erfolgreiche IPv6-/UDP-Kanal belegt dafuer nichts."""
    verdict = judge(_on(NETZ, CAL, _drop_hits(lambda h: h["ziel"] == V4_TCP)))
    status = _status(verdict)
    assert {t: status[t] for t in ("V4", "V8", "V12")} == dict.fromkeys(("V4", "V8", "V12"), BLOCKIERT)
    assert {t: status[t] for t in ("V5", "V6", "V7")} == dict.fromkeys(("V5", "V6", "V7"), PASS)
    assert _has(verdict["tests"]["V4"]["gruende"], "Kanal ipv4/tcp vor der ACL aus der VM nicht erreichbar")
    assert _has(verdict["tests"]["V4"]["gruende"], "widerspruechlich: VM meldet 198.51.100.9:443 erreicht")


def test_vm_reports_blocked_before(judge) -> None:
    def blocked(b):  # noqa: ANN001, ANN202
        for attempt in b["versuche"]:
            if "ipv6/udp" in attempt["kanaele"]:
                attempt["ergebnis"] = "zeitueberschreitung"
    verdict = judge(_on(NETZ, CAL0, blocked), _on(NETZ, CAL, _drop_hits(lambda h: h["ziel"] == V6_UDP)))
    assert verdict["tests"]["V5"]["status"] == BLOCKIERT
    assert _has(verdict["tests"]["V5"]["gruende"], "Kanal ipv6/udp vor der ACL nicht erreicht (zeitueberschreitung)")
    assert verdict["tests"]["V12"]["status"] == BLOCKIERT and verdict["tests"]["V4"]["status"] == PASS


def test_dead_listener_blocks(judge) -> None:
    verdict = judge(_on(NETZ, CAL, lambda b: b.update(kontrolle=[k for k in b["kontrolle"] if k != V4_TCP])))
    assert verdict["tests"]["V4"]["status"] == BLOCKIERT
    assert _has(verdict["tests"]["V4"]["gruende"], "Listener ohne Funktionskontrolle")


@pytest.mark.parametrize("name, expected", [(CAL, "Pflichtbeleg gegenprobe in Klasse E1 fehlt"),
                                            (CAL0, "Pflichtbeleg gegenprobe in Klasse E0 fehlt")])
def test_missing_counter_probe_evidence_blocks(judge, name, expected) -> None:
    verdict = judge(lambda items: items.pop(f"V6/{name}"))
    assert verdict["tests"]["V6"]["status"] == BLOCKIERT and _has(verdict["tests"]["V6"]["gruende"], expected)


def test_host_counter_probe_cannot_stand_in_for_the_vm_attempt(judge) -> None:
    """Klasse wird genau verlangt: der E1-Beleg ersetzt den E0-Versuch nicht (und umgekehrt)."""
    verdict = judge(lambda items: items.pop(f"V7/{CAL0}"))
    assert verdict["tests"]["V7"]["status"] == BLOCKIERT


def test_missing_acl_export_blocks_every_network_test(judge) -> None:
    verdict = judge(lambda items: items.pop("V1/firewall_export_E1.json"))
    for test in NETZ:
        assert verdict["tests"][test]["status"] == BLOCKIERT, test
        assert _has(verdict["tests"][test]["gruende"], "ACL-Konfiguration (V1) fehlt")


def test_hits_from_other_sources_or_outside_the_window_do_not_count(judge) -> None:
    def foreign(b):  # noqa: ANN001, ANN202
        for hit in b["treffer"]:
            hit["quelle"] = "192.0.2.200"  # nicht die VM
    verdict = judge(_on(NETZ, CAL, foreign))
    assert {_status(verdict)[t] for t in NETZ} == {BLOCKIERT}

    def outside(b):  # noqa: ANN001, ANN202
        for hit in b["treffer"]:
            hit["zeit"] = (NOW - timedelta(minutes=45)).isoformat()  # nach dem Vorher-Fenster
    verdict = judge(_on(NETZ, CAL, outside))
    assert {_status(verdict)[t] for t in NETZ} == {BLOCKIERT}


@pytest.mark.parametrize("change", [lambda b: b.update(treffer=None), lambda b: b.update(kontrolle=None),
                                    lambda b: b.update(acl_sha256=None), lambda b: b.update(vm_quelle_ips=[]),
                                    lambda b: b.update(aktiv_bis="gestern"), lambda b: b.pop("ziele")],
                         ids=["ohne_treffer", "ohne_kontrolle", "ohne_acl_hash", "ohne_vm_adresse", "zeit_kaputt",
                              "ohne_ziele"])
def test_incomplete_counter_probe_log_is_never_pass(judge, change) -> None:
    verdict = judge(_on(("V6",), CAL, change))
    assert verdict["tests"]["V6"]["status"] in (FAIL, BLOCKIERT), verdict["tests"]["V6"]


# --- Reihenfolge Vorher -> ACL -> Nachher ----------------------------------------------------------------------------------


def test_acl_unchanged_between_before_and_after_blocks(judge) -> None:
    final = hashlib.sha256(json.dumps(acc.synthetic_acl(LOOP), ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    verdict = judge(_on(NETZ, CAL, lambda b: b.update(acl_sha256=final)))
    assert verdict["tests"]["V4"]["status"] == BLOCKIERT
    assert _has(verdict["tests"]["V4"]["gruende"], "gleich der finalen ACL")


def test_order_violations_block(judge) -> None:
    early_acl = lambda items: items["V1/firewall_export_E1.json"]["entry"].update(  # noqa: E731
        erzeugt=(NOW - timedelta(minutes=58)).isoformat())
    verdict = judge(early_acl)
    assert _has(verdict["tests"]["V4"]["gruende"], "ACL-Export vor Ende der Gegenprobe")
    # §96 (M6): keine Uhrenvergleiche VM<->Host mehr - die Nachher-Proben muessen die Tokens der Nachher-Phase tragen
    foreign_token = _on(("V6",), "pytest_E0.json", lambda b: b.update(phasen_token={"start": "5" * 32,
                                                                                   "vm_abschluss": "6" * 32}))
    verdict = judge(foreign_token)
    assert verdict["tests"]["V6"]["status"] == BLOCKIERT
    assert _has(verdict["tests"]["V6"]["gruende"], "tragen nicht die Phasen-Tokens des Kanarienlogs")
    # Nachher-Phase (Host-Uhr) vor dem ACL-Export (Host-Uhr)
    early_after = _on(("V4",), KAN, lambda b: b.update(aktiv_von=(NOW - timedelta(minutes=45)).isoformat()))
    verdict = judge(early_after)
    assert _has(verdict["tests"]["V4"]["gruende"], "Nachher-Phase begann vor dem ACL-Export")
    overlap = _on(("V7",), KAN, lambda b: b.update(aktiv_von=(NOW - timedelta(minutes=52)).isoformat()))
    verdict = judge(overlap)
    assert verdict["tests"]["V7"]["status"] == BLOCKIERT
    assert _has(verdict["tests"]["V7"]["gruende"], "ueberlappen")


# --- Getrennte Kanaele und Pfade ------------------------------------------------------------------------------------------


def test_after_target_without_counter_probe_is_not_covered(judge) -> None:
    extra = {"ip": "192.0.2.99", "port": 8443, "protokoll": "tcp"}  # anderer Pfad/Port, nie gegengeprueft
    verdict = judge(_on(("V4",), KAN, lambda b: (b["ziele"].append(extra), b["kontrolle"].append("192.0.2.99:8443"))))
    assert verdict["tests"]["V4"]["status"] == BLOCKIERT
    assert _has(verdict["tests"]["V4"]["gruende"], "Nachher-Ziel ohne erfolgreiche Gegenprobe")


def test_channel_missing_in_after_log_is_not_covered(judge) -> None:
    verdict = judge(_on(("V5",), KAN, lambda b: (b.update(ziele=[t for t in b["ziele"] if t["port"] != 5000]),
                                                 b.update(kontrolle=[k for k in b["kontrolle"] if k != V6_UDP]))))
    assert verdict["tests"]["V5"]["status"] == BLOCKIERT
    assert _has(verdict["tests"]["V5"]["gruende"], "Kanal ipv6/udp im Nachher-Log nicht geprueft")


def test_channels_are_separate() -> None:
    assert ev.channels({"ip": "192.0.2.53", "port": 53, "protokoll": "udp"}) == {"ipv4/udp", "ipv4/dns"}
    assert ev.channels({"ip": "::1", "port": 1, "protokoll": "tcp"}) == {"ipv6/tcp"}
    assert ev.channels({"ip": "127.0.0.1", "port": 7, "protokoll": "dns"}) == {"ipv4/dns"}
    assert set(ev.KANAELE["V12"]) >= {k for t in ("V4", "V5", "V6", "V7", "V8") for k in ev.KANAELE[t]}
    covered = {k for t in CANARY_TARGETS for k in ev.channels(t)}
    assert set(ev.KANAELE["V12"]) <= covered


# --- Widersprueche, Manipulation, Alter ------------------------------------------------------------------------------------


def test_contradictions_block(judge) -> None:
    def vm_says_blocked(b):  # noqa: ANN001, ANN202
        for attempt in b["versuche"]:
            if attempt["ziel"] == V4_TCP:
                attempt["ergebnis"] = "abgelehnt"
    verdict = judge(_on(("V4",), CAL0, vm_says_blocked))
    assert verdict["tests"]["V4"]["status"] == BLOCKIERT
    assert _has(verdict["tests"]["V4"]["gruende"], "VM meldet 198.51.100.9:443 abgelehnt, Host sah einen Treffer")
    verdict = judge(_on(("V6",), CAL0, lambda b: b.update(vm_quelle_ips=["10.0.0.9"])))
    assert _has(verdict["tests"]["V6"]["gruende"], "verschiedene VM-Adressen")
    verdict = judge(_on(("V7",), CAL0, lambda b: b.update(phasen_token={"start": "7" * 32, "vm_abschluss": "8" * 32})))
    assert _has(verdict["tests"]["V7"]["gruende"], "traegt nicht die Phasen-Tokens der Host-Vorher-Phase")
    unclear = lambda b: [a.update(ergebnis="vielleicht") for a in b["versuche"] if a["ziel"] == V4_TCP]  # noqa: E731
    verdict = judge(_on(("V8",), CAL0, unclear))
    assert verdict["tests"]["V8"]["status"] == BLOCKIERT and _has(verdict["tests"]["V8"]["gruende"], "unklarem Ergebnis")
    # ein Widerspruch im IPv4/TCP-Kanal macht V8 nicht PASS, belegt aber auch fuer V6 (IPv4/UDP) nichts Falsches
    assert verdict["tests"]["V6"]["status"] == PASS and verdict["gesamt"] != PASS


def test_manipulated_or_hand_marked_counter_probe_is_rejected(judge) -> None:
    tampered = lambda items: items[f"V4/{CAL}"].update(sha256="0" * 64)  # noqa: E731
    assert _has(judge(tampered)["probleme"], "manipuliert")
    hand_marked = lambda items: items[f"V4/{CAL}"].setdefault("patch", {}).update(  # noqa: E731
        treffer=[{"ziel": V4_TCP, "quelle": "10.88.0.2", "zeit": (NOW - timedelta(minutes=55)).isoformat()}])
    verdict = judge(_on(("V4",), CAL, _drop_hits(lambda h: True)), hand_marked)
    assert verdict["gesamt"] == BLOCKIERT and _has(verdict["probleme"], "widerspricht der Beobachtung")
    status_pass = lambda items: items[f"V4/{CAL}"].setdefault("patch", {}).update(status="PASS")  # noqa: E731
    verdict = judge(_on(("V4",), CAL, _drop_hits(lambda h: True)), status_pass)
    assert verdict["tests"]["V4"]["status"] == BLOCKIERT  # ein "status" im Beleg wird nicht gelesen


def test_stale_counter_probe_is_rejected(judge) -> None:
    old = lambda items: items[f"V5/{CAL}"]["entry"].update(erzeugt=(NOW - timedelta(hours=25)).isoformat())  # noqa: E731
    assert _has(judge(old)["probleme"], "veraltet")
    assert _has(judge(now=NOW + timedelta(hours=25))["probleme"], "zum Nutzungszeitpunkt veraltet")
    assert ev.MAX_EVIDENCE_AGE_AT_USE["gegenprobe"] <= timedelta(hours=24)


# --- Trockenlauf mit echter Vorher-Probe auf Loopback ---------------------------------------------------------------------


def test_dry_run_counter_probe_uses_real_loopback_probes(tmp_path) -> None:
    run = acc.dry_run(tmp_path / "lauf")
    assert run.verdict["gesamt"] == PASS
    cal = json.loads((run.bundle / "V12" / CAL).read_text(encoding="utf-8"))["beobachtung"]
    attempts = json.loads((run.bundle / "V4" / CAL0).read_text(encoding="utf-8"))["beobachtung"]["versuche"]
    assert {h["phase"] for h in cal["treffer"]} == {"vorher"} and len(cal["treffer"]) == len(cal["ziele"]) == 5
    assert {a["ergebnis"] for a in attempts} == {"erreicht"}  # §96: UDP/DNS mit Echo - "erreicht" statt "gesendet"
    assert run.canary["treffer"] == []  # Vorher-Treffer zaehlen nicht als Nachher-Treffer
    assert _time(run.canary["aktiv_von"]) >= _time(cal["aktiv_bis"])


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def test_dry_run_without_ipv6_before_blocks_ipv6_tests_only(tmp_path) -> None:
    def no_ipv6(rolle, obs):  # noqa: ANN001, ANN202
        if rolle == "host":
            for key in [k for k in obs if k[1] == "gegenprobe"]:
                original = obs[key]
                obs[key] = lambda original=original: (lambda b: {**b, "treffer": [
                    h for h in b["treffer"] if ":" not in h["quelle"]]})(original())
    run = acc.dry_run(tmp_path / "lauf", change=no_ipv6)
    status = _status(run.verdict)
    assert status["V5"] == BLOCKIERT and status["V12"] == BLOCKIERT
    assert {status[t] for t in ("V4", "V6", "V7", "V8")} == {PASS}


@pytest.mark.parametrize("leak", ["tcp", "udp6"])
def test_dry_run_leak_after_acl_still_fails(tmp_path, leak) -> None:
    run = acc.dry_run(tmp_path / "lauf", leak=leak)
    assert run.verdict["gesamt"] == FAIL and run.canary["treffer"][0]["phase"] == "nachher"


def test_listener_phases_and_probe_guard(monkeypatch) -> None:
    monkeypatch.delenv(acc.ENV_REAL, raising=False)
    with acc.CanaryListener([{"ip": "127.0.0.1", "port": 0, "protokoll": "tcp"}]) as listener:
        listener.mark("vorher")
        assert listener.observation("vorher") == {"fehler": "Phase 'vorher' nicht gelaufen bzw. nicht beendet"}
        probe = acc.calibration_probe(listener.targets, vm_ips=["127.0.0.1"], start_token="a" * 32)
        listener.mark("nachher")
        with pytest.raises(acc.AcceptanceRefused, match="einmalig"):
            listener.mark("vorher")
    before = listener.observation("vorher")
    assert probe["versuche"][0]["ergebnis"] == "erreicht" and len(before["treffer"]) == 1
    assert listener.observation("nachher")["treffer"] == []
    with pytest.raises(acc.AcceptanceRefused, match="nur im freigegebenen echten Modus"):
        acc.calibration_probe([{"ip": "192.0.2.1", "port": 1, "protokoll": "tcp"}], vm_ips=["x"], start_token="a" * 32)
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    closed = s.getsockname()[1]
    s.close()
    result = acc.calibration_probe([{"ip": "127.0.0.1", "port": closed, "protokoll": "tcp"}], vm_ips=["127.0.0.1"],
                                   start_token="a" * 32)
    # §96 (H2): geschlossener Port = "abgelehnt" (Paket kam an), nie "blockiert"
    assert result["versuche"][0]["ergebnis"] == "abgelehnt" and result["versuche"][0]["kanaele"] == ["ipv4/tcp"]


# --- Gate, Signierwerkzeug, Treiber erzwingen die Gegenprobe ---------------------------------------------------------------


def test_signing_tool_refuses_without_counter_probe(tmp_path) -> None:
    with pytest.raises(owner_sign.SigningRefused) as refused:
        make_test_gate(tmp_path, endpoints=LOOP, bundle_hook=lambda items: items.pop(f"V8/{CAL}"))
    assert _has(refused.value.reasons, "V8 BLOCKIERT")


def test_gate_and_driver_stop_without_counter_probe(tmp_path) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP, via_tool=False,
                          bundle_hook=_on(NETZ, CAL, _drop_hits(lambda h: h["ziel"] == V6_UDP)))
    problems = gate.problems(run_id="qa80-lauf", provider_endpoints=LOOP)
    assert _has(problems, "V5: BLOCKIERT") and _has(problems, "V12: BLOCKIERT")
    provider = EchoProvider()
    result = answer_driver.BenchmarkDriver(_config(tmp_path), RunStore(tmp_path / "laeufe"), provider,
                                           workdir=tmp_path / "w", embedding=(FakeEmbeddingProvider(), "fake"),
                                           isolation_gate=gate).run()
    assert result["status"] == "gestoppt_isolation_nicht_bestaetigt" and provider.payloads == []


def test_counter_probe_files_are_part_of_the_tool_fingerprint() -> None:
    assert "tests/test_s92_acl_counter_probe.py" in isolation_gate.TOOL_FILES
    assert Path(acc.__file__).as_posix().endswith(next(f for f in isolation_gate.TOOL_FILES if "acceptance" in f))
