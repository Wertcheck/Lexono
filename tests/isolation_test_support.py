"""§87/§90: Test-Bestaetigungen fuer das Isolations-Gate (I12 v2) - NUR fuer Tests.

Erzeugt Wurzel- und Freigabeschluessel (Ed25519) NUR im Speicher, eine test-markierte, vom Test-Wurzelschluessel
signierte Schluesselliste, ein synthetisches Belegbuendel (alle 13 Abnahmetests mit Pflichtbelegen), eine
synthetische, als freigegeben markierte Netzwerkpolitik und eine Bestaetigung - alles in tmp_path. Das echte Repository
bleibt unveraendert (Politik: ENTWURF, kein Wurzelschluessel). Ein Test-Gate laesst nur Loopback-Endpunkte zu und
schaltet nie einen echten Lauf frei. Keine Passphrase, kein privater Schluessel wird auf Platte geschrieben (ausser in
Tests der verschluesselten Schluesseldatei, dort mit zufaelliger Passphrase in tmp_path)."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.legal_bench import isolation_acceptance, isolation_evidence, isolation_gate, owner_keys, owner_sign

PRUEFER = {"name": "Owner (synthetisch, Test)", "rolle": owner_keys.OWNER_ROLE}
BLOCKING_FIREWALL = {p: {"enabled": True, "default_outbound": "Block"} for p in ("Domain", "Private", "Public")}
#: Signiert wird "auf dem Host" - eine andere Identitaet als die gepruefte Maschine (diese Maschine)
HOST_IDENTITY = {"hostname": "owner-host-test", "machine_guid": "00000000-0000-4000-8000-00000000a001"}
PRODUCER = {"E0": "vm-abnahme", "E1": "host-abnahme", "E2": "endpunkt-protokoll"}
SYNTHETIC_PARAMETER = isolation_acceptance.SYNTHETIC_PARAMETER
#: §102 (A3): aktueller Zaehlerstand der (synthetischen) VM beim Gebrauch - 1 min nach dem Zeitanker, gleicher Start
SYNTHETIC_ZEITBASIS = {"tick_ms": isolation_acceptance.SYNTHETIC_ZEITANKER["tick_ms"] + 60_000,
                       "boot_id": isolation_acceptance.SYNTHETIC_ZEITANKER["boot_id"]}


def committed_like_worktree(commit: str) -> dict:
    """NUR Tests: der Arbeitsbaum dieses Test-Checkouts gilt als "committet" (sonst muesste jede Testaenderung erst
    committet werden). Produktiv vergleicht owner_sign gegen `git show <commit>:<pfad>` (§96, M4)."""
    stand = isolation_gate.pruefwerkzeug_stand()
    return {"commit": commit, "sha256": stand["sha256"]}
CANARY_TARGETS = [{"ip": "192.0.2.53", "port": 53, "protokoll": "udp"},  # TEST-NET/Dokumentationsadressen, nur Daten
                  {"ip": "198.51.100.9", "port": 443, "protokoll": "tcp"},
                  {"ip": "2001:db8::7", "port": 443, "protokoll": "tcp"},
                  {"ip": "2001:db8::8", "port": 5000, "protokoll": "udp"}]


def synthetic_policy(tmp_path: Path, endpoints: set[tuple[str, int]]) -> Path:
    policy = json.loads(isolation_gate.POLICY_PATH.read_text(encoding="utf-8"))
    policy = copy.deepcopy(policy)
    policy["status"] = "FREIGEGEBEN"  # nur in dieser Testkopie
    by_port: dict[int, list[str]] = {}
    for ip, port in sorted(endpoints):
        by_port.setdefault(port, []).append(ip)
    template = policy["freigaben"][0]
    policy["freigaben"] = [{**template, "hostname": "lokaler-pruefendpunkt.test", "ip_adressen": ips, "port": port,
                            "freigegeben_durch": "Test", "gueltig_von": "2026-01-01", "gueltig_bis": "2099-01-01"}
                           for port, ips in by_port.items()]
    policy["dns"]["entscheidung"] = "kein_dns_in_der_vm"
    path = tmp_path / "netzwerkpolitik.test.json"
    path.write_text(json.dumps(policy, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


# --- Schluesselliste --------------------------------------------------------------------------------------------------------


def keylist_payload(keys: list[tuple[str, Ed25519PrivateKey]], *, version: int = 1, revoked: list[str] = (),
                    test_only: bool = True, now: datetime | None = None, **overrides) -> dict:  # noqa: ANN003
    now = now or datetime.now(timezone.utc)
    payload = {
        "version": version, "test_only": test_only,
        "ausgestellt": (now - timedelta(hours=1)).isoformat(), "gueltig_bis": (now + timedelta(days=30)).isoformat(),
        "schluessel": [{"key_id": kid, "public_key_hex": key.public_key().public_bytes_raw().hex(),
                        "inhaber": PRUEFER["name"], "rolle": PRUEFER["rolle"],
                        "gueltig_von": (now - timedelta(days=1)).isoformat(),
                        "gueltig_bis": (now + timedelta(days=60)).isoformat()} for kid, key in keys],
        "widerrufen": [{"key_id": kid, "seit": now.isoformat(), "grund": "Test"} for kid in revoked],
    }
    payload.update(overrides)
    return payload


def write_keylist(path: Path, root: Ed25519PrivateKey, payload: dict) -> Path:
    path.write_text(json.dumps(owner_keys.sign_keylist(root, payload), ensure_ascii=False), encoding="utf-8")
    return path


# --- Belegbuendel ----------------------------------------------------------------------------------------------------------


def bundle_items(endpoints: set[tuple[str, int]], now: datetime) -> dict[str, dict]:
    """Ein vollstaendiger PASS-Satz: {Dateiname: {"entry": Manifesteintrag ohne sha256, "beobachtung": dict}}.

    §91: Belege entstehen wie in der realen Abnahme ueber `isolation_acceptance.evidence_content` (Soll/Beobachtung
    getrennt, Bewertung abgeleitet). Hooks veraendern `beobachtung` (Zielsystem anders), `entry` (Manifest anders),
    `patch` (Beleginhalt nachtraeglich anders, z. B. manuell gesetzte Bewertung) oder `raw` (Bytes)."""
    # §92: Nachher-Phase beginnt nach der Gegenprobe (60-50 min) und dem ACL-Export (40 min); §96: Abschluss, Tokens
    canary = isolation_acceptance.default_canary(now, targets=CANARY_TARGETS)
    items: dict[str, dict] = {}
    for test, requirements in isolation_evidence.ANFORDERUNGEN.items():
        for req in requirements:
            klasse = req["klasse"]
            name = f"{test}/{req['art']}_{klasse}.json"
            created = now - (timedelta(minutes=30) if klasse == "E0" else timedelta(minutes=1))
            if (req["art"], klasse) == ("gegenprobe", "E0"):
                created = now - timedelta(minutes=55)  # §98: innerhalb des Vorher-Fensters (60-50 min) geschrieben
            if (test, req["art"]) == ("V1", "firewall_export"):
                created = now - timedelta(minutes=40)  # ACL-Export = Aktivierung: nach der Gegenprobe, vor den Proben
            items[name] = {"entry": {"datei": name, "test": test, "art": req["art"], "erzeuger": PRODUCER[klasse],
                                     "klasse": klasse, "erzeugt": created.isoformat()},
                           "beobachtung": isolation_acceptance.synthetic_beobachtung(
                               test, req["art"], klasse, endpoints=endpoints, now=now, canary=canary)}
    return items


def write_bundle(folder: Path, items: dict[str, dict], *, lauf: dict, policy: dict, modus: str = "trockenlauf") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    entries = []
    for name, item in items.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if "raw" in item:
            raw = item["raw"]
        else:
            entry = item["entry"]
            content = isolation_acceptance.evidence_content(
                str(entry.get("test")), str(entry.get("art")), str(entry.get("klasse")), beobachtung=item["beobachtung"],
                lauf=lauf, policy=policy, umgebung={"erzeuger": entry.get("erzeuger"), "modus": modus},
                erzeugt=entry.get("erzeugt"), strict=False, parameter=item.get("parameter", SYNTHETIC_PARAMETER))
            content.update(item.get("patch", {}))
            raw = json.dumps(content, ensure_ascii=False).encode("utf-8")
        path.write_bytes(raw)
        entries.append({**item["entry"], "sha256": item.get("sha256") or hashlib.sha256(raw).hexdigest()})
    manifest = {"schema": isolation_evidence.MANIFEST_SCHEMA, "lauf": lauf, "belege": entries}
    (folder / isolation_evidence.MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return folder


# --- Gate ------------------------------------------------------------------------------------------------------------------


def _is_ip(value: str) -> bool:
    import ipaddress

    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def make_test_gate(tmp_path: Path, *, run_id: str = "qa80-lauf", endpoints: set[tuple[str, int]] | None = None,  # noqa: PLR0913
                   firewall: dict | None = None, mutate=None, bundle_hook=None, via_tool: bool = True,  # noqa: ANN001
                   modus: str = "trockenlauf", **overrides) -> isolation_gate.IsolationGate:  # noqa: ANN003
    """Gueltiges Test-Gate. `bundle_hook(items)` veraendert die Belege VOR dem Signieren (mit `via_tool=False`, weil
    das Owner-Werkzeug bei Nicht-PASS verweigert); `overrides` = signierte, aber falsche Felder; `mutate` = Aenderung
    NACH dem Signieren (Manipulation)."""
    endpoints = endpoints or {("127.0.0.1", 9)}
    if not all(_is_ip(ip) for ip, _ in endpoints):
        via_tool = False  # z. B. Hostname eines echten Cloud-Endpunkts: V1 waere FAIL, das Werkzeug signiert dann nicht
    now = datetime.now(timezone.utc)
    root, owner = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    folder = tmp_path / "isolation_test"
    folder.mkdir(exist_ok=True)
    policy_path = synthetic_policy(folder, endpoints)
    keylist_path = write_keylist(folder / "schluesselliste.json", root, keylist_payload([("test-key-1", owner)], now=now))
    pinned = (owner_keys.fingerprint(root.public_key()),)
    maschine, stand = isolation_gate.machine_identity(), isolation_gate.pruefwerkzeug_stand()
    lauf = {"run_id": run_id, "maschine": maschine, "netzwerkpolitik_sha256": isolation_gate.sha256_file(policy_path),
            "pruefwerkzeug": stand}
    items = bundle_items(endpoints, now)
    if bundle_hook:
        bundle_hook(items)
    bundle = write_bundle(folder / "belege", items, lauf=lauf,
                          policy=json.loads(policy_path.read_text(encoding="utf-8")), modus=modus)
    common = {"keylist_path": keylist_path, "bundle": bundle, "policy_path": policy_path, "run_id": run_id,
              "maschine": maschine, "pruefer_name": PRUEFER["name"], "test_only": True, "now": now, "hours": 2}
    if via_tool:
        document = owner_sign.sign_attestation(owner, "test-key-1", pinned_roots=pinned,
                                               signer_identity=lambda: HOST_IDENTITY, committed=committed_like_worktree,
                                               **common)
    else:
        keylist, problems = owner_keys.verify_keylist(keylist_path.read_bytes(), pinned_roots=pinned, min_version=1,
                                                      now=now, test_only=True)
        assert not problems, problems
        verdict = owner_sign.judge(bundle, policy_path=policy_path, run_id=run_id, maschine=maschine, tool=stand,
                                   reference=now, allow_dry_run=True)
        payload = owner_sign.attestation_payload(verdict, keylist=keylist, stand=stand,
                                                 **{k: v for k, v in common.items() if k not in ("keylist_path", "bundle")})
        document = {"key_id": "test-key-1", "payload": payload, "signature_hex": ""}
    if overrides or not via_tool:
        document["payload"].update(overrides)
        document["signature_hex"] = owner.sign(isolation_gate.canonical(document["payload"])).hex()
    if mutate:
        mutate(document)
    attestation_path = folder / "bestaetigung.json"
    attestation_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    gate = isolation_gate.IsolationGate.for_tests(
        keylist_path, attestation_path, bundle, policy_path, pinned_roots=pinned,
        firewall=lambda: firewall if firewall is not None else BLOCKING_FIREWALL,
        # §102: der pytest-Prozess selbst hat keine Bytecode-Isolation und laedt Testmodule - die Codequellen-Pruefung
        # wird in test_s102 gezielt geprueft; der Zeitanker der VM ist synthetisch (1 min nach dem Anker)
        codepfad=lambda: [], zeitbasis=lambda: dict(SYNTHETIC_ZEITBASIS))
    gate._test_key, gate._root_key, gate._now = owner, root, now  # fuer Tests, die nachtraeglich neu signieren
    return gate


def resign(gate: isolation_gate.IsolationGate, payload: dict, key: Ed25519PrivateKey | None = None,
           key_id: str = "test-key-1") -> None:
    key = key or gate._test_key
    document = {"key_id": key_id, "payload": payload, "signature_hex": key.sign(isolation_gate.canonical(payload)).hex()}
    Path(gate.attestation_path).write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def attestation(gate: isolation_gate.IsolationGate) -> dict:
    return json.loads(Path(gate.attestation_path).read_text(encoding="utf-8"))
