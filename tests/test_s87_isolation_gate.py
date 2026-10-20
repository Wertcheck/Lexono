"""§87 (I12): Externes Isolations-Gate - Produktivzustand geschlossen, Negativfaelle, Abbruch vor jedem Provider-Aufruf.

§90: auf Schema v2 (Owner-Schluesselliste, Belegbuendel) umgestellt; weitere Faelle in tests/test_s90_owner_gate.py.
Positive Faelle nur mit Mock-Bestaetigungen aus tests/isolation_test_support.py (Schluessel im Speicher, test-markiert,
nur Loopback). Kein Test schaltet einen echten Lauf frei; die Endpunktsperre (C-14) bleibt unberuehrt."""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest

from scripts.legal_bench import answer_driver, isolation_gate
from scripts.legal_bench.answer_store import RunStore
from scripts.legal_bench import owner_keys
from tests.isolation_test_support import (BLOCKING_FIREWALL, attestation, keylist_payload, make_test_gate, resign,
                                          write_keylist)
from tests.test_s80_benchmark_driver import EchoProvider, FakeEmbeddingProvider, _config

pytestmark = pytest.mark.usefixtures("synthetic_legal_state")
LOOP = {("127.0.0.1", 9)}


def _problems(gate, run_id="qa80-lauf", endpoints=frozenset()) -> list[str]:  # noqa: ANN001
    return gate.problems(run_id=run_id, provider_endpoints=set(endpoints))


# --- Produktivzustand -----------------------------------------------------------------------------------------------------


def test_production_gate_is_closed_today(monkeypatch) -> None:
    monkeypatch.delenv(isolation_gate.ENV_ANCHORS, raising=False)
    monkeypatch.delenv(isolation_gate.ENV_ATTESTATION, raising=False)
    problems = _problems(isolation_gate.IsolationGate.from_environment())
    text = " | ".join(problems)
    for expected in ("kein Vertrauensanker", "kein Wurzelschluessel des Owners festgelegt",
                     "Netzwerkpolitik nicht freigegeben (Status 'ENTWURF')",
                     "kein freigegebener Endpunkt", "DNS-Entscheidung der Netzwerkpolitik OFFEN",
                     "keine Bestaetigung des Owners"):
        assert expected in text, (expected, problems)


def test_driver_without_gate_parameter_stops_before_anything(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(isolation_gate.ENV_ANCHORS, raising=False)
    store, provider = RunStore(tmp_path / "laeufe"), EchoProvider()
    result = answer_driver.BenchmarkDriver(_config(tmp_path), store, provider, workdir=tmp_path / "w",
                                           embedding=(FakeEmbeddingProvider(), "fake")).run()
    assert result["status"] == "gestoppt_isolation_nicht_bestaetigt" and provider.payloads == []
    assert not store.exists("qa80-lauf")


def _anchor_file(tmp_path, gate, *, readonly: bool, test_only: bool = True):  # noqa: ANN001, ANN202
    path = write_keylist(tmp_path / "anker.json", gate._root_key,
                         keylist_payload([("test-key-1", gate._test_key)], test_only=test_only))
    if readonly:
        os.chmod(path, stat.S_IREAD)
    return path


def test_writable_trust_anchor_is_not_trusted(tmp_path, monkeypatch) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    monkeypatch.setenv(isolation_gate.ENV_ANCHORS, str(_anchor_file(tmp_path, gate, readonly=False)))
    problems = _problems(isolation_gate.IsolationGate.from_environment())
    assert any("schreibbar - kein Vertrauensnachweis" in p for p in problems)


def test_production_gate_never_accepts_a_test_attestation(tmp_path, monkeypatch) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    anchor = _anchor_file(tmp_path, gate, readonly=True)
    try:
        monkeypatch.setenv(isolation_gate.ENV_ANCHORS, str(anchor))
        monkeypatch.setenv(isolation_gate.ENV_ATTESTATION, str(gate.attestation_path))
        production = isolation_gate.IsolationGate.from_environment()
        problems = _problems(production, endpoints=LOOP)
        assert owner_keys.ROOT_MISSING in problems  # im Code ist kein Wurzelschluessel festgelegt
        assert any("Netzwerkpolitik nicht freigegeben" in p for p in problems)  # echte Politik bleibt ENTWURF
        assert not any("schreibbar" in p for p in problems)
        # selbst mit gepinnter (Test-)Wurzel: eine test-markierte Liste zaehlt im Produktivbetrieb nicht
        production.pinned_roots = gate.pinned_roots
        assert any("Test-Schluesselliste im Produktivbetrieb" in p for p in _problems(production, endpoints=LOOP))
    finally:
        os.chmod(anchor, stat.S_IREAD | stat.S_IWRITE)


# --- Positivfall (nur Mock) -------------------------------------------------------------------------------------------------


def test_valid_mock_attestation_passes_the_test_gate(tmp_path) -> None:
    assert _problems(make_test_gate(tmp_path, endpoints=LOOP), endpoints=LOOP) == []


# --- Negativfaelle ------------------------------------------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)


def _set(path, value):  # noqa: ANN001, ANN202
    def mutate(doc):  # noqa: ANN001, ANN202
        node = doc
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return mutate


CASES = [
    ("keine_bestaetigung", {"delete_attestation": True}, "keine Bestaetigung des Owners"),
    ("unlesbar", {"broken_json": True}, "Bestaetigung unlesbar"),
    ("fremde_pruefstelle", {"mutate": _set(["key_id"], "fremder-key")}, "unbekanntem Schluessel"),
    ("nachtraeglich_manipuliert", {"mutate": _set(["payload", "endpunkte"], [{"ip": "127.0.0.1", "port": 1}])},
     "Signatur der Bestaetigung ungueltig"),
    ("signatur_vertauscht", {"mutate": _set(["signature_hex"], "00" * 64)}, "Signatur der Bestaetigung ungueltig"),
    ("abgelaufen", {"overrides": {"gueltig_von": (NOW - timedelta(hours=5)).isoformat(),
                                  "gueltig_bis": (NOW - timedelta(hours=1)).isoformat()}}, "abgelaufen"),
    ("noch_nicht_gueltig", {"overrides": {"gueltig_von": (NOW + timedelta(hours=1)).isoformat(),
                                          "gueltig_bis": (NOW + timedelta(hours=2)).isoformat()}}, "noch nicht gueltig"),
    ("fenster_zu_lang", {"overrides": {"gueltig_von": (NOW - timedelta(hours=1)).isoformat(),
                                       "gueltig_bis": (NOW + timedelta(days=3)).isoformat()}}, "laenger als 24 Stunden"),
    ("anderer_lauf", {"overrides": {"run_id": "anderer-lauf"}}, "anderen Lauf"),
    ("andere_maschine", {"overrides": {"maschine": {"hostname": "fremde-vm", "machine_guid": "0" * 36}}},
     "andere Maschine"),
    ("ohne_firewall_export", {"overrides": {"firewall_export_sha256": None}}, "Firewall-Export der Bestaetigung"),
    ("fremder_firewall_export", {"overrides": {"firewall_export_sha256": "a" * 64}}, "passt nicht zum Beleg V1"),
    ("anderer_endpunkt", {"overrides": {"endpunkte": [{"ip": "127.0.0.1", "port": 10}]}}, "weicht von der Netzwerkpolitik ab"),
    ("anderer_dns_modus", {"overrides": {"dns_modus": "kontrollierter_resolver"}}, "DNS-Modus"),
    ("pruefer_nicht_schluesselinhaber", {"overrides": {"pruefer": {"name": "Jemand", "rolle": "QA-Test"}}},
     "nicht der Inhaber"),
    ("urteil_unvollstaendig", {"overrides": {"urteil": {"gesamt": "PASS", "tests": {"V1": "PASS"}}}},
     "widerspricht den Belegen"),
    ("altes_abnahmefeld_statt_urteil", {"overrides": {"urteil": None, "abnahme": {"V1": "bestanden"}}},
     "widerspricht den Belegen"),
    ("abnahme_nicht_bestanden", {"via_tool": False, "bundle_hook": lambda items: items[
        "V13/kriterien_E1.json"]["beobachtung"].update(diagnose=[])}, "V13: FAIL"),
    ("schema_unbekannt", {"overrides": {"schema": "fremd/1"}}, "unbekanntes Schema"),
    ("schema_v1", {"overrides": {"schema": "lexono-isolation-attestation/1"}}, "veralteten Schema v1"),
    ("nicht_test_markiert", {"overrides": {"test_only": False}}, "Test-Bestaetigung im Produktivbetrieb bzw. umgekehrt"),
    ("gast_firewall_offen", {"firewall": {"Public": {"enabled": True, "default_outbound": "Allow"}}},
     "Gast-Firewall blockiert ausgehend nicht"),
    ("gast_firewall_unbekannt", {"firewall": {}}, "Gast-Firewall blockiert ausgehend nicht"),
]


@pytest.mark.parametrize("name, spec, expected", CASES, ids=[c[0] for c in CASES])
def test_gate_rejects_faulty_attestations(tmp_path, name, spec, expected) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP, firewall=spec.get("firewall"), mutate=spec.get("mutate"),
                          bundle_hook=spec.get("bundle_hook"), via_tool=spec.get("via_tool", True),
                          **spec.get("overrides", {}))
    if spec.get("delete_attestation"):
        gate.attestation_path.unlink()
    if spec.get("broken_json"):
        gate.attestation_path.write_text("{kaputt", encoding="utf-8")
    problems = _problems(gate, endpoints=LOOP)
    assert any(expected in p for p in problems), (name, problems)


def test_changed_policy_or_unattested_provider_endpoint_is_rejected(tmp_path) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    assert any("nicht bestaetigt" in p for p in _problems(gate, endpoints={("127.0.0.1", 4444)}))
    policy = json.loads(gate.policy_path.read_text(encoding="utf-8"))
    policy["proxy"]["erlaubt"] = True
    gate.policy_path.write_text(json.dumps(policy), encoding="utf-8")
    assert any("Netzwerkpolitik seit der Bestaetigung geaendert" in p for p in _problems(gate, endpoints=LOOP))


def test_test_gate_never_allows_a_non_loopback_endpoint(tmp_path) -> None:
    gate = make_test_gate(tmp_path, endpoints={("192.0.2.10", 443)})  # TEST-NET, nur als Daten - keine Verbindung
    assert any("nur Loopback-Endpunkte" in p for p in _problems(gate, endpoints={("192.0.2.10", 443)}))


def test_unreadable_firewall_or_anchor_expiry_fails_closed(tmp_path) -> None:
    def broken():  # noqa: ANN202
        raise OSError("powershell nicht verfuegbar")

    gate = make_test_gate(tmp_path, endpoints=LOOP)
    gate.firewall = broken
    assert any("Gast-Firewall nicht pruefbar" in p for p in _problems(gate, endpoints=LOOP))
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    payload = keylist_payload([("test-key-1", gate._test_key)])
    payload["schluessel"][0].update(gueltig_von=(NOW - timedelta(days=60)).isoformat(),
                                    gueltig_bis=(NOW - timedelta(days=1)).isoformat())
    write_keylist(gate.keylist_path, gate._root_key, payload)
    assert any("Schluessel des Owners abgelaufen" in p for p in _problems(gate, endpoints=LOOP))


def test_machine_without_guid_fails_closed(tmp_path) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    gate.identity = lambda: {"hostname": "x", "machine_guid": None}
    assert any("andere Maschine" in p for p in _problems(gate, endpoints=LOOP))


# --- Treiber: Abbruch vor jedem Provider-Aufruf -----------------------------------------------------------------------------


def _run(tmp_path, gate, provider=None):  # noqa: ANN001, ANN202
    provider = provider or EchoProvider()
    result = answer_driver.BenchmarkDriver(_config(tmp_path), RunStore(tmp_path / "laeufe"), provider,
                                           workdir=tmp_path / "w", embedding=(FakeEmbeddingProvider(), "fake"),
                                           isolation_gate=gate).run()
    return result, provider


@pytest.mark.parametrize("spec", [
    {"overrides": {"gueltig_bis": (NOW - timedelta(minutes=1)).isoformat()}},
    {"mutate": _set(["payload", "run_id"], "qa80-lauf-x")},
    {"overrides": {"maschine": {"hostname": "fremd", "machine_guid": "1" * 36}}},
    {"firewall": {"Public": {"enabled": False, "default_outbound": "Block"}}},
], ids=["abgelaufen", "manipuliert", "fremde_maschine", "firewall_aus"])
def test_driver_stops_before_any_provider_call(tmp_path, spec) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP, firewall=spec.get("firewall"), mutate=spec.get("mutate"),
                          **spec.get("overrides", {}))
    result, provider = _run(tmp_path, gate)
    assert result["status"] == "gestoppt_isolation_nicht_bestaetigt" and provider.payloads == [], result["diagnose"]


def test_attestation_expiring_mid_run_stops_before_the_next_call(tmp_path) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    calls = {"n": 0}

    def clock():  # noqa: ANN202
        calls["n"] += 1  # 1 = Start, 2 = vor Aufruf 1, 3 = vor Aufruf 2
        return datetime.now(timezone.utc) + (timedelta(hours=3) if calls["n"] >= 3 else timedelta(0))

    gate.now = clock
    result, provider = _run(tmp_path, gate)
    assert len(provider.payloads) == 1 and result["status"] == "gestoppt_isolation_nicht_bestaetigt"
    assert "LA-D005.Q2.r1: Bestaetigung abgelaufen" in result["diagnose"][0]


class _PolicyChangingProvider(EchoProvider):
    def __init__(self, policy_path) -> None:  # noqa: ANN001
        super().__init__()
        self.policy_path = policy_path

    def write(self, payload):  # noqa: ANN001
        policy = json.loads(self.policy_path.read_text(encoding="utf-8"))
        policy["lokal"]["loopback_erlaubt"] = False
        self.policy_path.write_text(json.dumps(policy), encoding="utf-8")  # Konfiguration aendert sich im Lauf
        return super().write(payload)


def test_policy_change_during_run_stops_before_the_next_call(tmp_path) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    result, provider = _run(tmp_path, gate, _PolicyChangingProvider(gate.policy_path))
    assert len(provider.payloads) == 1 and result["status"] == "gestoppt_isolation_nicht_bestaetigt"
    assert any("Netzwerkpolitik seit der Bestaetigung geaendert" in r for r in result["diagnose"])


def test_attestation_signature_covers_every_bound_field(tmp_path) -> None:
    """Jedes gebundene Feld ist signiert: nachtraegliche Aenderung eines beliebigen Feldes macht die Signatur ungueltig."""
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    document = attestation(gate)
    for field in document["payload"]:
        changed = json.loads(json.dumps(document))
        changed["payload"][field] = "veraendert"
        gate.attestation_path.write_text(json.dumps(changed), encoding="utf-8")
        assert any("Signatur der Bestaetigung ungueltig" in p for p in _problems(gate, endpoints=LOOP)), field
    resign(gate, document["payload"])
    assert _problems(gate, endpoints=LOOP) == []
    assert BLOCKING_FIREWALL  # Testfirewall nur im Speicher


def test_vm_acceptance_matrix_matches_the_gate_and_claims_nothing() -> None:
    matrix = json.loads((isolation_gate.POLICY_PATH.parent / "vm_abnahme.json").read_text(encoding="utf-8"))
    assert matrix["status"] == "NICHT DURCHGEFUEHRT"
    assert tuple(t["id"] for t in matrix["tests"]) == isolation_gate.REQUIRED_TESTS
    for test in matrix["tests"]:
        assert test["status"] == "NICHT DURCHGEFUEHRT", test["id"]
        assert all(test[k] for k in ("thema", "soll", "durchfuehrung", "beleg", "rolle")), test["id"]
    themes = " ".join(t["thema"] + t["soll"] for t in matrix["tests"])
    for topic in ("IPv6", "UDP", "DNS", "Golden Image", "Kanarien", "unabhaengig", "Alternative", "Start-Gate"):
        assert topic in themes, topic
