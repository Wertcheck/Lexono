"""§90 (I12 v2): Owner-Gate - Schluesselliste, Widerruf, Rotation, Belegbuendel, automatisches Urteil, Signierwerkzeug.

Nur lokale, synthetische Testdaten (Schluessel im Speicher bzw. in tmp_path, TEST-NET-Adressen nur als Daten, Loopback-
Endpunkte). Kein Test baut eine Verbindung nach aussen auf oder schaltet einen echten Lauf frei; das Produktiv-Gate
bleibt geschlossen (kein Wurzelschluessel festgelegt, Politik ENTWURF). Die Endpunktsperre C-14 bleibt unberuehrt."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.legal_bench import answer_driver, answer_specs, isolation_evidence, isolation_gate, owner_keys, owner_sign
from scripts.legal_bench.answer_store import RunStore
from scripts.legal_bench.isolation_evidence import BLOCKIERT, FAIL, NICHT_DURCHGEFUEHRT, PASS
from scripts.legal_bench.isolation_acceptance import junit_xml, required_nodeids, synthetic_acl
from tests.isolation_test_support import (HOST_IDENTITY, PRUEFER, committed_like_worktree, attestation, bundle_items, keylist_payload,
                                          make_test_gate, resign, write_bundle, write_keylist)
from tests.test_s80_benchmark_driver import EchoProvider, FakeEmbeddingProvider, _config

pytestmark = pytest.mark.usefixtures("synthetic_legal_state")
LOOP = {("127.0.0.1", 9)}
NOW = datetime.now(timezone.utc)
FAST_N = 2 ** 14  # nur Tests: schnellere Ableitung; das Produktivminimum (2^17) wird gesondert geprueft


def _problems(gate, run_id="qa80-lauf", endpoints=LOOP) -> list[str]:  # noqa: ANN001
    return gate.problems(run_id=run_id, provider_endpoints=set(endpoints))


def _has(problems: list[str], text: str) -> bool:
    return any(text in p for p in problems)


# --- Produktivzustand -------------------------------------------------------------------------------------------------------


def test_production_has_no_root_key_and_stays_closed(monkeypatch) -> None:
    assert owner_keys.PINNED_ROOT_FINGERPRINTS == ()  # kein Wurzelschluessel erfunden
    assert owner_keys.MIN_KEYLIST_VERSION >= 1
    for env in (isolation_gate.ENV_ANCHORS, isolation_gate.ENV_ATTESTATION, isolation_gate.ENV_EVIDENCE):
        monkeypatch.delenv(env, raising=False)
    gate = isolation_gate.IsolationGate.from_environment()
    assert not gate.test_only
    problems = _problems(gate)
    assert owner_keys.ROOT_MISSING in problems and _has(problems, "keine Bestaetigung des Owners")
    matrix = json.loads((isolation_gate.POLICY_PATH.parent / "vm_abnahme.json").read_text(encoding="utf-8"))
    assert matrix["status"] == "NICHT DURCHGEFUEHRT"  # keine reale Abnahme behauptet


def test_production_mode_path_works_only_with_a_pinned_root_and_readonly_anchor(tmp_path) -> None:
    """Produktivmodus (test_only=False) mit einer NUR fuer diesen Test gepinnten Wurzel: zeigt, dass der echte Pfad
    funktioniert, sobald der Owner Wurzel, Liste, Belege und Bestaetigung bereitstellt - und nur dann."""
    import os
    import stat

    test_gate = make_test_gate(tmp_path, endpoints=LOOP, modus="real")
    keylist_path = write_keylist(tmp_path / "anker_produktiv.json", test_gate._root_key,
                                 keylist_payload([("test-key-1", test_gate._test_key)], test_only=False))
    gate = isolation_gate.IsolationGate(keylist_path=keylist_path, attestation_path=test_gate.attestation_path,
                                        evidence_path=test_gate.evidence_path, policy_path=test_gate.policy_path,
                                        pinned_roots=test_gate.pinned_roots, firewall=test_gate.firewall,
                                        codepfad=test_gate.codepfad, zeitbasis=test_gate.zeitbasis)
    assert _has(_problems(gate), "schreibbar - kein Vertrauensnachweis")
    os.chmod(keylist_path, stat.S_IREAD)
    try:
        assert _has(_problems(gate), "Test-Bestaetigung im Produktivbetrieb")
        document = _sign(test_gate, keylist_path=keylist_path, test_only=False)
        Path(gate.attestation_path).write_text(json.dumps(document), encoding="utf-8")
        assert _problems(gate) == []
        gate.codepfad = isolation_gate.codepfad_probleme  # §102 (A1): echte Pruefung im pytest-Prozess ohne Isolation
        assert _has(_problems(gate), "Codequelle: Bytecode-Isolation fehlt")
        gate.codepfad, gate.zeitbasis = test_gate.codepfad, isolation_gate.vm_zeitbasis  # §102 (A3): andere Boot-ID
        assert _has(_problems(gate), "Boot-ID") or _has(_problems(gate), "Zeitanker")
        gate.zeitbasis = test_gate.zeitbasis
        gate.pinned_roots = ()
        assert owner_keys.ROOT_MISSING in _problems(gate)
    finally:
        os.chmod(keylist_path, stat.S_IREAD | stat.S_IWRITE)


def test_repository_contains_no_private_key_files() -> None:
    for folder in ("scripts", "benchmarks", "app", "gateway"):
        for path in (answer_specs.REPO / folder).rglob("*.json"):
            assert owner_keys.KEYFILE_SCHEMA not in path.read_text(encoding="utf-8", errors="ignore"), path


# --- Schluesselliste --------------------------------------------------------------------------------------------------------


def _verify(raw: bytes, root, **kwargs):  # noqa: ANN001, ANN202
    options = {"pinned_roots": (owner_keys.fingerprint(root.public_key()),), "min_version": 1, "now": NOW,
               "test_only": True, **kwargs}
    return owner_keys.verify_keylist(raw, **options)


def _signed(root, payload) -> bytes:  # noqa: ANN001
    return json.dumps(owner_keys.sign_keylist(root, payload)).encode("utf-8")


def test_valid_keylist_is_accepted() -> None:
    root, owner = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    raw = _signed(root, keylist_payload([("k1", owner)], now=NOW))
    keylist, problems = _verify(raw, root)
    assert problems == [] and set(keylist.anchors) == {"k1"} and keylist.version == 1
    assert keylist.sha256 == hashlib.sha256(raw).hexdigest()


def _payload_with(owner, **changes):  # noqa: ANN001, ANN003, ANN202
    payload = keylist_payload([("k1", owner)], now=NOW)
    for key, value in changes.items():
        if key.startswith("key_"):
            payload["schluessel"][0][key[4:]] = value
        else:
            payload[key] = value
    return payload


KEYLIST_CASES = [
    ("abgelaufen", {"gueltig_bis": (NOW - timedelta(minutes=1)).isoformat()}, "abgelaufen"),
    ("zu_lang_gueltig", {"ausgestellt": (NOW - timedelta(days=1)).isoformat(),
                         "gueltig_bis": (NOW + timedelta(days=95)).isoformat()}, "laenger als 90 Tage"),
    ("ohne_version", {"version": None}, "ohne Version"),
    ("ohne_zeitzone", {"gueltig_bis": "2099-01-01T00:00:00"}, "Gueltigkeitsangaben der Schluesselliste"),
    ("produktiv_markiert", {"test_only": False}, "Test-Schluesselliste im Produktivbetrieb"),
    ("ohne_schluessel", {"schluessel": []}, "ohne Freigabeschluessel"),
    ("falsche_rolle", {"key_rolle": "IT"}, "Rolle muss 'Owner'"),
    ("schluessel_zu_lang", {"key_gueltig_bis": (NOW + timedelta(days=200)).isoformat()}, "max. 90 Tage"),
    ("aktiv_und_widerrufen", {"widerrufen": [{"key_id": "k1", "seit": NOW.isoformat()}]}, "zugleich aktiv und widerrufen"),
    ("widerruf_unvollstaendig", {"widerrufen": [{"key_id": "k9"}]}, "Widerrufseintrag unvollstaendig"),
]


@pytest.mark.parametrize("name, changes, expected", KEYLIST_CASES, ids=[c[0] for c in KEYLIST_CASES])
def test_faulty_keylist_is_rejected_entirely(name, changes, expected) -> None:
    root, owner = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    keylist, problems = _verify(_signed(root, _payload_with(owner, **changes)), root)
    assert keylist is None and _has(problems, expected), (name, problems)


def test_keylist_signature_root_pin_and_separation() -> None:
    root, other, owner = (Ed25519PrivateKey.generate() for _ in range(3))
    good = _signed(root, keylist_payload([("k1", owner)], now=NOW))
    assert owner_keys.verify_keylist(good, pinned_roots=(), min_version=1, now=NOW, test_only=True)[1] == \
        [owner_keys.ROOT_MISSING]
    assert _has(_verify(_signed(other, keylist_payload([("k1", owner)], now=NOW)), root)[1], "fremder Wurzelschluessel")
    tampered = json.loads(good)
    tampered["payload"]["schluessel"][0]["public_key_hex"] = other.public_key().public_bytes_raw().hex()
    assert _has(_verify(json.dumps(tampered).encode(), root)[1], "Signatur der Schluesselliste ungueltig")
    assert _has(_verify(b"{kaputt", root)[1], "Schluesselliste unlesbar")
    doubled = keylist_payload([("k1", owner), ("k1", other)], now=NOW)
    assert _has(_verify(_signed(root, doubled), root)[1], "doppelt")
    assert _has(_verify(_signed(root, keylist_payload([("k1", root)], now=NOW)), root)[1], "Trennung verletzt")


def test_keylist_rollback_below_minimum_version_is_rejected() -> None:
    root, owner = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    old = _signed(root, keylist_payload([("k1", owner)], version=1, now=NOW))
    assert _has(_verify(old, root, min_version=2)[1], "veraltet")


# --- Widerruf und Rotation am Gate --------------------------------------------------------------------------------------------


def _rotate(gate, *, revoke_old: bool, version: int = 2) -> Ed25519PrivateKey:  # noqa: ANN001
    new = Ed25519PrivateKey.generate()
    keys = [("test-key-2", new)] + ([] if revoke_old else [("test-key-1", gate._test_key)])
    write_keylist(gate.keylist_path, gate._root_key,
                  keylist_payload(keys, version=version, revoked=["test-key-1"] if revoke_old else []))
    return new


def _bind_to_current_keylist(payload: dict, gate, version: int) -> dict:  # noqa: ANN001
    return {**payload, "schluesselliste": {"version": version,
                                           "sha256": hashlib.sha256(Path(gate.keylist_path).read_bytes()).hexdigest()}}


def test_revoked_key_is_rejected_even_with_a_valid_signature(tmp_path) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    assert _problems(gate) == []
    payload = attestation(gate)["payload"]
    _rotate(gate, revoke_old=True)
    assert _has(_problems(gate), "'test-key-1' ist widerrufen")
    resign(gate, _bind_to_current_keylist(payload, gate, 2))  # sogar neu an die neue Liste gebunden: widerrufen bleibt
    assert _has(_problems(gate), "ist widerrufen")


def test_rotation_new_key_works_and_old_binding_is_rejected(tmp_path) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    payload = attestation(gate)["payload"]
    new = _rotate(gate, revoke_old=False)
    assert _has(_problems(gate), "andere Schluesselliste")  # Bestaetigung an Liste v1 gebunden
    resign(gate, payload, key=new, key_id="test-key-2")
    assert _has(_problems(gate), "andere Schluesselliste")
    resign(gate, _bind_to_current_keylist(payload, gate, 2), key=new, key_id="test-key-2")
    assert _problems(gate) == []
    resign(gate, _bind_to_current_keylist(payload, gate, 2))  # alter Schluessel, ueberlappend noch aktiv
    assert _problems(gate) == []


def test_rollback_to_an_old_keylist_is_rejected_by_minimum_version(tmp_path) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    gate.min_keylist_version = 2  # nach einem Widerruf per Review angehoben
    assert _has(_problems(gate), "Schluesselliste veraltet")


def test_attestation_signed_by_root_or_unknown_key_is_rejected(tmp_path) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    payload = attestation(gate)["payload"]
    resign(gate, payload, key=gate._root_key, key_id="test-key-1")
    assert _has(_problems(gate), "Signatur der Bestaetigung ungueltig")
    resign(gate, payload, key=gate._root_key, key_id="wurzel")
    assert _has(_problems(gate), "unbekanntem Schluessel 'wurzel'")


def test_keylist_change_or_removal_closes_the_gate(tmp_path) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    data = json.loads(Path(gate.keylist_path).read_text(encoding="utf-8"))
    data["payload"]["version"] = 7
    Path(gate.keylist_path).write_text(json.dumps(data), encoding="utf-8")
    assert _has(_problems(gate), "Signatur der Schluesselliste ungueltig")
    Path(gate.keylist_path).unlink()
    assert _has(_problems(gate), "Schluesselliste) nicht vorhanden")


# --- Belegbuendel und Urteil -------------------------------------------------------------------------------------------------


def _lauf(policy_path: Path) -> dict:
    return {"run_id": "lauf-x", "maschine": HOST_IDENTITY, "netzwerkpolitik_sha256": isolation_gate.sha256_file(policy_path),
            "pruefwerkzeug": {"commit": "c" * 40, "sha256": "d" * 64}}


@pytest.fixture
def judged(tmp_path):  # noqa: ANN001, ANN201
    from tests.isolation_test_support import synthetic_policy

    policy_path = synthetic_policy(tmp_path, LOOP)
    policy = json.loads(policy_path.read_text(encoding="utf-8"))

    def run(hook=None, *, lauf=None, reference=None, extra_file=None):  # noqa: ANN001, ANN202
        items = bundle_items(LOOP, NOW)
        if hook:
            hook(items)
        folder = tmp_path / f"belege_{len(list(tmp_path.glob('belege_*')))}"
        write_bundle(folder, items, lauf=_lauf(policy_path), policy=policy)
        if extra_file:
            (folder / extra_file).write_text("{}", encoding="utf-8")
        return isolation_evidence.evaluate(folder, policy=policy, lauf=lauf or _lauf(policy_path),
                                           reference=reference or NOW, now=NOW, allow_dry_run=True), folder

    return run


def test_complete_bundle_passes_all_13_tests(judged) -> None:
    verdict, _ = judged()
    assert verdict["gesamt"] == PASS and verdict["probleme"] == []
    assert {t: r["status"] for t, r in verdict["tests"].items()} == dict.fromkeys(isolation_evidence.REQUIRED_TESTS, PASS)
    assert verdict["firewall_export_sha256"] and len(verdict["manifest_sha256"]) == 64


def _drop(prefix: str):  # noqa: ANN202
    return lambda items: [items.pop(k) for k in [k for k in items if k.startswith(prefix)]]


def _seen(name: str, **changes):  # noqa: ANN003, ANN202
    """Zielsystem anders beobachtet (Beobachtung aendern)."""
    return lambda items: items[name]["beobachtung"].update(changes)


def _patched(name: str, **changes):  # noqa: ANN003, ANN202
    """Beleginhalt nachtraeglich anders (z. B. von Hand gesetzte Bewertung)."""
    return lambda items: items[name].setdefault("patch", {}).update(changes)


def _junit(name: str, test: str, **outcomes):  # noqa: ANN003, ANN202
    results = {**required_nodeids(test), **{k.replace("__", "::"): v for k, v in outcomes.items()}}
    return _seen(name, junit_xml=junit_xml(results))


def _acl(name: str, change):  # noqa: ANN001, ANN202
    def hook(items):  # noqa: ANN001, ANN202
        acl = synthetic_acl(LOOP)
        change(acl)
        items[name]["beobachtung"]["acl"] = acl
    return hook


VERDICT_CASES = [
    ("test_ohne_belege", _drop("V5/"), "V5", NICHT_DURCHGEFUEHRT, "kein Beleg"),
    ("pflichtbeleg_fehlt", _drop("V4/kanarien"), "V4", BLOCKIERT, "Pflichtbeleg kanarien in Klasse E1 fehlt"),
    ("nur_selbsttest_statt_host", lambda items: items["V6/kanarien_E1.json"]["entry"].update(
        erzeuger="vm-abnahme", klasse="E0"), "V6", BLOCKIERT, "nur schwaechere Klasse"),
    ("e2_fehlt", _drop("V3/kriterien_E2"), "V3", BLOCKIERT, "Klasse E2 fehlt"),
    ("kanarientreffer", _seen("V4/kanarien_E1.json", treffer=[{"von": "vm", "ziel": "198.51.100.9:443"}]),
     "V4", FAIL, "Kanarientreffer"),
    # §96 (H1): ein Listener, der nicht vollstaendig geleert/beendet wurde, belegt kein "kein Treffer"
    ("listener_nicht_abgeschlossen", _seen("V8/kanarien_E1.json", abschluss={"geleert": False, "threads_beendet": True,
                                                                            "nachlauf_s": 2.0, "fehler": 0}),
     "V8", FAIL, "nicht vollstaendig abgeschlossen"),
    ("kanarien_ohne_ipv6", _seen("V5/kanarien_E1.json", ziele=[{"ip": "192.0.2.1", "port": 443, "protokoll": "tcp"}],
                                 kontrolle=["192.0.2.1:443"]),
     "V5", FAIL, "ipv6 nicht ab"),
    ("pytest_uebersprungen", _junit("V7/pytest_E0.json", "V7", **{
        (isolation_evidence.S86 + "test_acceptance_dns_is_blocked").replace("::", "__"): "skipped"}),
     "V7", FAIL, "uneindeutig = FAIL"),
    ("pytest_fehlgeschlagen", _junit("V13/pytest_E0.json", "V13", **{"tests/test_s90_owner_gate.py__test_beispiel": "failed"}),
     "V13", FAIL, "fehlgeschlagen"),
    ("pytest_pflichttest_fehlt", _seen("V10/pytest_E0.json", junit_xml=junit_xml({"tests/anderer.py::t": "passed"})),
     "V10", FAIL, "nicht im Protokoll"),
    ("junit_unlesbar", _seen("V8/pytest_E0.json", junit_xml="<testsuite><kaputt"), "V8", FAIL, "ohne Ergebnisse"),
    ("junit_mit_entitaet", _seen("V8/pytest_E0.json", junit_xml="<!DOCTYPE x [<!ENTITY a 'b'>]><testsuite/>"),
     "V8", FAIL, "ohne Ergebnisse"),
    ("kriterium_unklar", _seen("V9/kriterien_E1.json", gastdienste_aktiv=None), "V9", FAIL, "nicht eindeutig erfuellt"),
    ("beobachter_fehler", _seen("V11/kriterien_E1.json", zuruecksetzen=None, fehler="TimeoutExpired"),
     "V11", FAIL, "nicht eindeutig erfuellt"),
    ("kriterium_verletzt", lambda items: items["V2/kriterien_E0.json"]["beobachtung"]["konto_gruppen_sids"].append(
        "S-1-5-32-544"), "V2", FAIL, "benchmark_konto_ohne_administratorrechte verletzt"),
    ("aenderungsversuch_gelungen", lambda items: items["V2/kriterien_E0.json"]["beobachtung"].update(
        aenderungsversuch={"exit_code": 0}), "V2", FAIL, "aenderung_durch_benchmark_konto_gescheitert verletzt"),
    ("zusaetzliche_allow_regel", _acl("V1/firewall_export_E1.json", lambda acl: acl.append(
        {"Direction": "Outbound", "Action": "Allow", "RemoteIPAddress": "198.51.100.7", "RemotePort": "8080",
         "Protocol": "TCP", "Weight": 20})), "V1", FAIL, "nicht freigegeben"),
    ("ipv6_nicht_gesperrt", _acl("V1/firewall_export_E1.json", lambda acl: acl.pop(1)), "V1", FAIL,
     "nicht standardmaessig blockiert"),
    ("udp_erlaubt", _acl("V1/firewall_export_E1.json", lambda acl: acl.append(
        {"Direction": "Outbound", "Action": "Allow", "RemoteIPAddress": "127.0.0.1", "RemotePort": "53",
         "Protocol": "UDP", "Weight": 20})), "V1", FAIL, "UDP erlaubt"),
    ("vpn_rdp_ueber_acl_moeglich", _acl("V9/kriterien_E1.json", lambda acl: acl.append(
        {"Direction": "Outbound", "Action": "Allow", "RemoteIPAddress": "203.0.113.5", "RemotePort": "3389",
         "Protocol": "TCP", "Weight": 20})), "V9", FAIL, "kein_vpn verletzt"),
    ("endpunkt_leitete_weiter", lambda items: items["V3/kriterien_E2.json"]["beobachtung"]["eintraege"][0].update(
        weitergeleitet=True), "V3", FAIL, "keine_weiterleitung_an_modell_endpunkt verletzt"),
    ("endpunkt_ohne_eingang", _seen("V3/kriterien_E2.json", eintraege=[]), "V3", FAIL,
     "eingehende_verbindung_protokolliert verletzt"),
    ("image_hash_abweichend", _seen("V11/kriterien_E1.json", image_sha256_nachher="f" * 64), "V11", FAIL,
     "image_hash_vorher_gleich_nachher verletzt"),
    ("hostregeln_veraendert", _seen("V10/kriterien_E1.json", acl_nachher_sha256="e" * 64), "V10", FAIL,
     "aenderung_aus_der_vm_an_hostregeln_gescheitert verletzt"),
    ("gate_nimmt_veraltete_an", _seen("V13/kriterien_E1.json", diagnose=[]), "V13", FAIL, "verletzt"),
]


@pytest.mark.parametrize("name, hook, test, status, reason", VERDICT_CASES, ids=[c[0] for c in VERDICT_CASES])
def test_verdict_per_test(judged, name, hook, test, status, reason) -> None:
    verdict, _ = judged(hook)
    result = verdict["tests"][test]
    assert result["status"] == status and _has(result["gruende"], reason), (name, result)
    assert verdict["gesamt"] != PASS
    others = [r["status"] for t, r in verdict["tests"].items() if t != test]
    assert set(others) == {PASS}, name  # nur der betroffene Test aendert sich


BUNDLE_CASES = [
    ("klasse_widerspricht_erzeuger", lambda items: items["V12/kanarien_E1.json"]["entry"].update(erzeuger="vm-abnahme"),
     {}, "passt nicht zum Erzeuger"),
    ("hash_manipuliert", lambda items: items["V4/kanarien_E1.json"].update(sha256="0" * 64), {}, "manipuliert"),
    ("beleg_veraltet", lambda items: items["V2/kriterien_E0.json"]["entry"].update(
        erzeugt=(NOW - timedelta(hours=25)).isoformat()), {}, "veraltet oder aus der Zukunft"),
    ("beleg_aus_der_zukunft", lambda items: items["V2/kriterien_E0.json"]["entry"].update(
        erzeugt=(NOW + timedelta(hours=1)).isoformat()), {}, "aus der Zukunft"),
    ("pfad_ausserhalb", lambda items: items.update({"../aussen.json": {**items["V1/firewall_export_E1.json"],
                                                                       "entry": {**items["V1/firewall_export_E1.json"]["entry"],
                                                                                 "datei": "../aussen.json"}}}),
     {}, "ausserhalb des Buendels"),
    ("unbekannter_test", lambda items: items["V2/kriterien_E0.json"]["entry"].update(test="V14"), {}, "unbekannter Test"),
    ("inhalt_widerspricht_manifest", _patched("V3/pytest_E0.json", test="V4"), {}, "widerspricht dem Manifest"),
    ("manuelles_pass_kriterium", _patched("V2/kriterien_E0.json", kriterien={"gast_firewall_alle_profile_aktiv": True}),
     {}, "widerspricht der Beobachtung"),
    ("manuelles_pytest_passed", lambda items: (items["V7/pytest_E0.json"]["beobachtung"].update(junit_xml=junit_xml(
        {isolation_evidence.S86 + "test_acceptance_dns_is_blocked": "skipped"})), items["V7/pytest_E0.json"].setdefault(
        "patch", {}).update(ergebnisse={isolation_evidence.S86 + "test_acceptance_dns_is_blocked": "passed"})),
     {}, "widerspricht der Beobachtung"),
    ("manueller_status_pass_ignoriert", None, {}, None),
    ("ohne_beobachtung", _patched("V9/kriterien_E0.json", beobachtung=None), {}, "Soll und Beobachtung nicht getrennt"),
    ("fremder_lauf_im_beleg", _patched("V9/kriterien_E0.json", lauf_sha256="0" * 64), {}, "an anderen Lauf"),
    ("zeit_im_beleg_abweichend", _patched("V9/kriterien_E0.json", erzeugt=NOW.isoformat()), {}, "Zeit bzw. Erzeuger"),
    ("erzeuger_im_beleg_abweichend", _patched("V9/kriterien_E0.json", umgebung={"erzeuger": "host-abnahme",
                                                                                 "modus": "trockenlauf"}),
     {}, "Zeit bzw. Erzeuger"),
    ("unbekannter_modus", _patched("V9/kriterien_E0.json", umgebung={"erzeuger": "vm-abnahme", "modus": "manuell"}),
     {}, "Modus 'manuell'"),
    ("unlesbarer_beleg", lambda items: items["V3/pytest_E0.json"].update(raw=b"{kaputt"), {}, "unlesbar"),
    ("eintrag_unvollstaendig", lambda items: items["V3/pytest_E0.json"]["entry"].pop("erzeuger"), {}, "unvollstaendig"),
    ("nicht_gefuehrte_datei", None, {"extra_file": "V1/nachgeschoben.json"}, "nicht im Manifest gefuehrte Dateien"),
    ("anderer_lauf", None, {"lauf": {"run_id": "fremd"}}, "anderem Lauf"),
]


@pytest.mark.parametrize("name, hook, kwargs, reason", BUNDLE_CASES, ids=[c[0] for c in BUNDLE_CASES])
def test_invalid_bundle_blocks_every_test(judged, name, hook, kwargs, reason) -> None:
    if reason is None:  # ein zusaetzliches "status": "PASS" im Beleg aendert nichts - es wird nicht gelesen
        verdict, _ = judged(_patched("V5/pytest_E0.json", status="PASS", urteil="PASS"))
        assert verdict["gesamt"] == PASS
        verdict, _ = judged(lambda items: (_drop("V5/kanarien")(items),
                                           _patched("V5/pytest_E0.json", status="PASS")(items)))
        assert verdict["tests"]["V5"]["status"] == BLOCKIERT
        return
    verdict, _ = judged(hook, **kwargs)
    assert verdict["gesamt"] == BLOCKIERT and _has(verdict["probleme"], reason), (name, verdict["probleme"])
    assert {r["status"] for r in verdict["tests"].values()} == {BLOCKIERT}


def test_unexpected_types_fail_closed(tmp_path, judged) -> None:
    verdict, _ = judged(lambda items: items["V2/kriterien_E0.json"]["entry"].update(erzeuger={"x": 1}))
    assert verdict["gesamt"] == BLOCKIERT and _has(verdict["probleme"], "ungueltigen Feldtypen")
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    payload = attestation(gate)["payload"]
    resign(gate, {**payload, "endpunkte": [{"ip": ["127.0.0.1"], "port": 9}]})  # unhashbarer Typ
    assert _problems(gate) == ["Isolationspruefung fehlgeschlagen (TypeError) - fail-closed"]


def test_missing_or_unreadable_bundle(tmp_path, judged) -> None:
    from tests.isolation_test_support import synthetic_policy

    policy = json.loads(synthetic_policy(tmp_path, LOOP).read_text(encoding="utf-8"))
    missing = isolation_evidence.evaluate(tmp_path / "fehlt", policy=policy, lauf={}, reference=NOW)
    assert missing["gesamt"] == NICHT_DURCHGEFUEHRT and missing["manifest_sha256"] is None
    _, folder = judged()
    (folder / isolation_evidence.MANIFEST_NAME).write_text("{kaputt", encoding="utf-8")
    broken = isolation_evidence.evaluate(folder, policy=policy, lauf={}, reference=NOW)
    assert broken["gesamt"] == BLOCKIERT and _has(broken["probleme"], "Manifest des Belegbuendels unlesbar")


def test_rule_checker_rejects_hostnames_instead_of_crashing() -> None:
    from tests.test_s86_isolation_design import POLICY, _export

    policy = {**POLICY, "freigaben": [{**POLICY["freigaben"][0], "ip_adressen": ["api.example.invalid"], "port": 443}]}
    rule = {"name": "x", "enabled": True, "direction": "Outbound", "action": "Allow", "protocol": "TCP",
            "remote_addresses": ["relay.example.invalid"], "remote_ports": ["443"]}
    errors = isolation_evidence.check_rules(_export(rules=[rule]), policy)
    assert _has(errors, "keine IP-Adresse - kein Start") and _has(errors, "Zieladresse 'relay.example.invalid'")


def test_overall_priority_never_turns_unclear_into_pass() -> None:
    overall = isolation_evidence.overall
    assert overall([PASS] * 13) == PASS
    assert overall([PASS] * 12 + [NICHT_DURCHGEFUEHRT]) == NICHT_DURCHGEFUEHRT
    assert overall([PASS, NICHT_DURCHGEFUEHRT, BLOCKIERT]) == BLOCKIERT
    assert overall([BLOCKIERT, FAIL, PASS]) == FAIL
    assert overall([]) == BLOCKIERT and overall(["bestanden"] * 13) == BLOCKIERT


def test_requirements_cover_the_acceptance_matrix() -> None:
    matrix = json.loads((isolation_gate.POLICY_PATH.parent / "vm_abnahme.json").read_text(encoding="utf-8"))
    assert tuple(t["id"] for t in matrix["tests"]) == tuple(isolation_evidence.ANFORDERUNGEN)
    critical_network = ("V1", "V4", "V5", "V6", "V7", "V8", "V10", "V12")
    for test in critical_network:  # Netzwerkgrenze: nie nur Selbsttest (AUDIT_S89 §2.1)
        assert any(r["klasse"] in ("E1", "E2") for r in isolation_evidence.ANFORDERUNGEN[test]), test
    assert any(r["klasse"] == "E2" for r in isolation_evidence.ANFORDERUNGEN["V3"])


# --- Gate: Belege an die Freigabe gebunden ------------------------------------------------------------------------------------


def test_evidence_tampering_after_signing_is_rejected(tmp_path) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    path = Path(gate.evidence_path) / "V4" / "kanarien_E1.json"
    content = json.loads(path.read_text(encoding="utf-8"))
    content["treffer"] = []  # gleicher Inhalt, andere Bytes -> anderer Hash
    path.write_text(json.dumps(content, indent=2), encoding="utf-8")
    assert _has(_problems(gate), "V4/kanarien_E1.json' manipuliert")


def test_manifest_rewritten_to_match_tampered_evidence_is_rejected(tmp_path) -> None:
    """Wer Beleg UND Manifest anpasst, aendert den Manifest-Hash - der ist signiert."""
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    bundle = Path(gate.evidence_path)
    manifest = json.loads((bundle / isolation_evidence.MANIFEST_NAME).read_text(encoding="utf-8"))
    path = bundle / "V12" / "kanarien_E1.json"
    content = json.loads(path.read_text(encoding="utf-8"))
    content["ziele"] = content["beobachtung"]["ziele"] = content["ziele"][:1]
    path.write_text(json.dumps(content), encoding="utf-8")
    for entry in manifest["belege"]:
        if entry["datei"] == "V12/kanarien_E1.json":
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (bundle / isolation_evidence.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    problems = _problems(gate)
    assert _has(problems, "Belegbuendel weicht von der Bestaetigung ab") and _has(problems, "V12: FAIL")


def test_missing_bundle_is_rejected(tmp_path) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    gate.evidence_path = None
    problems = _problems(gate)
    assert _has(problems, "kein Belegbuendel") and _has(problems, "nicht vollstaendig PASS")


def test_manual_pass_marking_does_not_replace_evidence(tmp_path) -> None:
    """Signierte Bestaetigung behauptet PASS fuer alle Tests, die Belege tragen das nicht -> abgelehnt."""
    gate = make_test_gate(tmp_path, endpoints=LOOP, via_tool=False, bundle_hook=_drop("V7/"),
                          urteil={"gesamt": PASS, "tests": dict.fromkeys(isolation_evidence.REQUIRED_TESTS, PASS)})
    problems = _problems(gate)
    assert _has(problems, "V7: NICHT DURCHGEFUEHRT") and _has(problems, "widerspricht den Belegen")


def test_changed_or_undeterminable_tool_is_rejected(tmp_path) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    real = gate.tool()
    gate.tool = lambda: {**real, "sha256": "e" * 64}
    assert _has(_problems(gate), "Pruefwerkzeug seit der Bestaetigung geaendert")
    gate.tool = lambda: {"commit": None, "sha256": real["sha256"]}
    assert _has(_problems(gate), "Pruefwerkzeug nicht bestimmbar")


def test_tool_fingerprint_covers_gate_and_tests() -> None:
    assert "scripts/legal_bench/isolation_gate.py" in isolation_gate.TOOL_FILES
    assert "tests/test_s90_owner_gate.py" in isolation_gate.TOOL_FILES
    stand = isolation_gate.pruefwerkzeug_stand()
    assert stand["commit"] and len(stand["sha256"]) == 64


# --- Signierwerkzeug ---------------------------------------------------------------------------------------------------------


def _sign(gate, **changes):  # noqa: ANN001, ANN003, ANN202
    options = {"keylist_path": gate.keylist_path, "bundle": gate.evidence_path, "policy_path": gate.policy_path,
               "run_id": "qa80-lauf", "maschine": isolation_gate.machine_identity(), "pruefer_name": PRUEFER["name"],
               "hours": 2, "pinned_roots": gate.pinned_roots, "test_only": True, "now": datetime.now(timezone.utc),
               "signer_identity": lambda: HOST_IDENTITY, "committed": committed_like_worktree, **changes}
    key = options.pop("key", gate._test_key)
    key_id = options.pop("key_id", "test-key-1")
    return owner_sign.sign_attestation(key, key_id, **options)


def test_signing_tool_produces_an_attestation_the_gate_accepts(tmp_path) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    document = _sign(gate)
    Path(gate.attestation_path).write_text(json.dumps(document), encoding="utf-8")
    assert _problems(gate) == []
    assert document["payload"]["urteil"]["gesamt"] == PASS and document["payload"]["pruefer"]["rolle"] == "Owner"


def test_signing_tool_refuses_without_pass(tmp_path) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    (Path(gate.evidence_path) / "V11" / "kriterien_E1.json").unlink()
    with pytest.raises(owner_sign.SigningRefused) as refused:
        _sign(gate)
    assert _has(refused.value.reasons, "V11/kriterien_E1.json' fehlt")
    assert _has(refused.value.reasons, "Urteil nicht PASS")


@pytest.mark.parametrize("changes, expected", [
    ({"signer_identity": isolation_gate.machine_identity}, "geprueften Maschine"),
    ({"hours": 30}, "zwischen 0 und 24 Stunden"),
    ({"key_id": "test-key-9"}, "nicht aktiv"),
    ({"key": Ed25519PrivateKey.generate()}, "passt nicht zum Eintrag"),
    ({"pruefer_name": "Jemand anderes"}, "nicht der Inhaber"),
    ({"pinned_roots": ()}, "kein Wurzelschluessel"),
    ({"tool": lambda: {"commit": None, "sha256": None}}, "Pruefwerkzeug auf dem Host nicht bestimmbar"),
    ({"run_id": "anderer-lauf"}, "anderem Lauf"),
], ids=["auf_der_vm", "zu_lange", "unbekannter_schluessel", "falsche_schluesseldatei", "fremder_pruefer",
        "ohne_wurzel", "werkzeug_unbestimmt", "anderer_lauf"])
def test_signing_tool_refusals(tmp_path, changes, expected) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    with pytest.raises(owner_sign.SigningRefused) as refused:
        _sign(gate, **changes)
    assert _has(refused.value.reasons, expected), refused.value.reasons


def test_signing_tool_refuses_a_revoked_key(tmp_path) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    _rotate(gate, revoke_old=True)
    with pytest.raises(owner_sign.SigningRefused) as refused:
        _sign(gate)
    assert _has(refused.value.reasons, "nicht aktiv (widerrufen")


# --- Verschluesselte Schluesseldateien ---------------------------------------------------------------------------------------


@pytest.fixture
def key_dir(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    """Schluesseldateien NUR ausserhalb des Repositorys (tmp_path liegt ausserhalb)."""
    assert not tmp_path.resolve().is_relative_to(answer_specs.REPO.resolve())
    return tmp_path


def _passphrase() -> str:
    import secrets

    return secrets.token_urlsafe(24)  # zufaellig je Test, nirgends gespeichert


def test_key_file_roundtrip_and_failures(key_dir) -> None:
    secret = _passphrase()
    info = owner_sign.create_key(key_dir / "freigabe.json", purpose="freigabe", key_id="owner-1", passphrase=secret,
                                 n=FAST_N)
    raw = (key_dir / "freigabe.json").read_text(encoding="utf-8")
    key_id, key = owner_sign.load_private_key(key_dir / "freigabe.json", secret, purpose="freigabe", min_n=FAST_N)
    seed_hex = key.private_bytes_raw().hex()
    assert key_id == "owner-1" and info["public_key_hex"] == key.public_key().public_bytes_raw().hex()
    assert seed_hex not in raw and secret not in raw and "fingerprint_sha256" in info
    with pytest.raises(owner_keys.KeyFileError, match="nicht entschluesselbar"):
        owner_sign.load_private_key(key_dir / "freigabe.json", secret + "x", purpose="freigabe", min_n=FAST_N)
    with pytest.raises(owner_keys.KeyFileError, match="nicht fuer den Zweck"):
        owner_sign.load_private_key(key_dir / "freigabe.json", secret, purpose="wurzel", min_n=FAST_N)
    with pytest.raises(owner_keys.KeyFileError, match="zu schwach"):
        owner_sign.load_private_key(key_dir / "freigabe.json", secret, purpose="freigabe")  # Produktivminimum 2^17
    document = json.loads(raw)
    document["key_id"] = "owner-2"  # authentifizierte Zusatzdaten
    with pytest.raises(owner_keys.KeyFileError, match="nicht entschluesselbar"):
        owner_keys.decrypt_private_key(document, secret, purpose="freigabe", min_n=FAST_N)
    with pytest.raises(owner_sign.SigningRefused, match="nicht ueberschrieben"):
        owner_sign.create_key(key_dir / "freigabe.json", purpose="freigabe", key_id="x", passphrase=secret, n=FAST_N)


def test_key_file_rules(key_dir) -> None:
    with pytest.raises(owner_keys.KeyFileError, match="zu kurz"):
        owner_sign.create_key(key_dir / "k.json", purpose="freigabe", key_id="k", passphrase="kurz", n=FAST_N)
    with pytest.raises(owner_sign.SigningRefused, match="nicht im Repository"):
        owner_sign.create_key(answer_specs.REPO / "tmp_schluessel.json", purpose="freigabe", key_id="k",
                              passphrase=_passphrase(), n=FAST_N)
    assert not (answer_specs.REPO / "tmp_schluessel.json").exists()
    assert not (key_dir / "k.json").exists()


def test_production_key_derivation_meets_the_minimum() -> None:
    assert owner_keys.SCRYPT_N >= 2 ** 17 and owner_keys.SCRYPT_R >= 8 and owner_keys.MIN_PASSPHRASE_LENGTH >= 15


def test_cli_never_accepts_a_passphrase_argument(capsys) -> None:
    for argv in (["schluessel-erzeugen", "--zweck", "wurzel", "--key-id", "k", "--ausgabe", "x.json", "--passphrase", "p"],
                 ["schluessel-erzeugen", "--zweck", "wurzel", "--key-id", "k", "--ausgabe", "x.json", "--passwort", "p"]):
        with pytest.raises(SystemExit):
            owner_sign.main(argv)
    source = Path(owner_sign.__file__).read_text(encoding="utf-8")
    assert "getpass.getpass" in source and "os.environ" not in source


def test_cli_root_signs_keylist_and_owner_signs_attestation(key_dir, monkeypatch) -> None:
    """Kompletter Ablauf ueber die Funktionen des Werkzeugs: Wurzel -> Liste -> Freigabeschluessel -> Bestaetigung."""
    root_secret, owner_secret = _passphrase(), _passphrase()
    owner_sign.create_key(key_dir / "wurzel.json", purpose="wurzel", key_id="root-1", passphrase=root_secret, n=FAST_N)
    info = owner_sign.create_key(key_dir / "freigabe.json", purpose="freigabe", key_id="owner-1",
                                 passphrase=owner_secret, n=FAST_N)
    _, root = owner_sign.load_private_key(key_dir / "wurzel.json", root_secret, purpose="wurzel", min_n=FAST_N)
    _, owner = owner_sign.load_private_key(key_dir / "freigabe.json", owner_secret, purpose="freigabe", min_n=FAST_N)
    assert info["public_key_hex"] == owner.public_key().public_bytes_raw().hex()
    document = owner_sign.sign_keylist(root, keylist_payload([("owner-1", owner)]), now=datetime.now(timezone.utc))
    with pytest.raises(owner_sign.SigningRefused, match="Trennung verletzt"):
        owner_sign.sign_keylist(root, keylist_payload([("owner-1", root)]), now=datetime.now(timezone.utc))
    gate = make_test_gate(key_dir, endpoints=LOOP)
    Path(gate.keylist_path).write_text(json.dumps(document), encoding="utf-8")
    gate.pinned_roots = (owner_keys.fingerprint(root.public_key()),)
    Path(gate.attestation_path).write_text(json.dumps(_sign(gate, key=owner, key_id="owner-1")), encoding="utf-8")
    assert _problems(gate) == []


# --- Treiber: Abbruch vor jedem Provider-Aufruf -------------------------------------------------------------------------------


def _run(tmp_path, gate, provider=None):  # noqa: ANN001, ANN202
    provider = provider or EchoProvider()
    result = answer_driver.BenchmarkDriver(_config(tmp_path), RunStore(tmp_path / "laeufe"), provider,
                                           workdir=tmp_path / "w", embedding=(FakeEmbeddingProvider(), "fake"),
                                           isolation_gate=gate).run()
    return result, provider


def test_driver_runs_with_valid_owner_attestation(tmp_path) -> None:
    result, provider = _run(tmp_path, make_test_gate(tmp_path, endpoints=LOOP))
    assert result["status"] != "gestoppt_isolation_nicht_bestaetigt" and provider.payloads, result["diagnose"]


@pytest.mark.parametrize("breaker", ["beleg_manipuliert", "schluessel_widerrufen", "buendel_fehlt", "werkzeug_geaendert",
                                     "schluesselliste_fehlt"])
def test_driver_stops_before_any_provider_call(tmp_path, breaker) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    if breaker == "beleg_manipuliert":
        (Path(gate.evidence_path) / "V1" / "firewall_export_E1.json").write_text("{}", encoding="utf-8")
    elif breaker == "schluessel_widerrufen":
        _rotate(gate, revoke_old=True)
    elif breaker == "buendel_fehlt":
        gate.evidence_path = tmp_path / "gibt_es_nicht"
    elif breaker == "werkzeug_geaendert":
        gate.tool = lambda: {"commit": "f" * 40, "sha256": "f" * 64}
    else:
        Path(gate.keylist_path).unlink()
    result, provider = _run(tmp_path, gate)
    assert result["status"] == "gestoppt_isolation_nicht_bestaetigt" and provider.payloads == [], result["diagnose"]


class _Tamperer(EchoProvider):
    """Veraendert waehrend des ersten Aufrufs Belege bzw. widerruft den Schluessel - der zweite Aufruf darf nicht starten."""

    def __init__(self, action) -> None:  # noqa: ANN001
        super().__init__()
        self.action = action

    def write(self, payload):  # noqa: ANN001
        if not self.payloads:
            self.action()
        return super().write(payload)


@pytest.mark.parametrize("change", ["beleg", "widerruf"])
def test_change_during_run_stops_before_the_next_call(tmp_path, change) -> None:
    gate = make_test_gate(tmp_path, endpoints=LOOP)
    if change == "beleg":
        action = lambda: (Path(gate.evidence_path) / "V7" / "kanarien_E1.json").write_text("{}", encoding="utf-8")  # noqa: E731
    else:
        action = lambda: _rotate(gate, revoke_old=True)  # noqa: E731
    result, provider = _run(tmp_path, gate, _Tamperer(action))
    assert len(provider.payloads) == 1 and result["status"] == "gestoppt_isolation_nicht_bestaetigt", result["diagnose"]
