"""§87: Test-Bestaetigungen fuer das Isolations-Gate (I12) - NUR fuer Tests.

Erzeugt einen Ed25519-Schluessel im Speicher, eine test-markierte Bestaetigung fuer DIESE Maschine und eine synthetische,
als freigegeben markierte Netzwerkpolitik in tmp_path. Das echte Repository bleibt unveraendert (Politik: ENTWURF,
kein Anker). Ein Test-Gate laesst nur Loopback-Endpunkte zu und schaltet nie einen echten Lauf frei."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.legal_bench import isolation_gate

PRUEFER = {"name": "Testpruefstelle (synthetisch)", "rolle": "QA-Test"}
BLOCKING_FIREWALL = {p: {"enabled": True, "default_outbound": "Block"} for p in ("Domain", "Private", "Public")}


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


def make_attestation(key: Ed25519PrivateKey, policy_path: Path, *, run_id: str, endpoints: set[tuple[str, int]],
                     key_id: str = "test-key-1", **overrides) -> dict:
    now = datetime.now(timezone.utc)
    payload = {
        "schema": isolation_gate.SCHEMA, "test_only": True, "run_id": run_id,
        "maschine": isolation_gate.machine_identity(),
        "netzwerkpolitik_sha256": isolation_gate.sha256_file(policy_path),
        "firewall_export_sha256": "a" * 64,
        "endpunkte": [{"ip": ip, "port": port} for ip, port in sorted(endpoints)],
        "dns_modus": "kein_dns_in_der_vm", "pruefer": dict(PRUEFER),
        "abnahme": {t: "bestanden" for t in isolation_gate.REQUIRED_TESTS},
        "gueltig_von": (now - timedelta(minutes=5)).isoformat(), "gueltig_bis": (now + timedelta(hours=2)).isoformat(),
    }
    payload.update(overrides)
    return {"key_id": key_id, "payload": payload, "signature_hex": key.sign(isolation_gate.canonical(payload)).hex()}


def make_test_gate(tmp_path: Path, *, run_id: str = "qa80-lauf", endpoints: set[tuple[str, int]] | None = None,
                   firewall: dict | None = None, mutate=None, **overrides) -> isolation_gate.IsolationGate:  # noqa: ANN001
    endpoints = endpoints or {("127.0.0.1", 9)}
    key = Ed25519PrivateKey.generate()
    folder = tmp_path / "isolation_test"
    folder.mkdir(exist_ok=True)
    policy_path = synthetic_policy(folder, endpoints)
    document = make_attestation(key, policy_path, run_id=run_id, endpoints=endpoints, **overrides)
    if mutate:
        mutate(document)
    attestation_path = folder / "bestaetigung.json"
    attestation_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    anchors = {"test-key-1": {"key": key.public_key(), "inhaber": PRUEFER["name"], "rolle": PRUEFER["rolle"],
                              "gueltig_bis": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}}
    gate = isolation_gate.IsolationGate.for_tests(anchors, attestation_path, policy_path,
                                                  firewall=lambda: firewall if firewall is not None else BLOCKING_FIREWALL)
    gate._test_key = key  # fuer Tests, die nachtraeglich neu signieren
    return gate
