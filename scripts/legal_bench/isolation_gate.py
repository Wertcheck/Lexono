"""§87 (I12): Externes Isolations-Gate des Antwort-Benchmarks.

Vor jedem Lauf und vor jedem Aufruf muss eine UNABHAENGIGE, signierte Bestaetigung vorliegen, dass genau diese
Zielumgebung die freigegebene Netzwerkpolitik erfuellt. Fehlt etwas, ist etwas manipuliert, veraltet, fremd oder
widerspruechlich: kein Start bzw. Abbruch vor dem Provider-Aufruf (fail-closed).

Vertrauensmodell:
- Vertrauensanker = oeffentliche Ed25519-Schluessel der Pruefstelle (IT), bereitgestellt AUSSERHALB des Benchmarks in
  einer Datei, die fuer das Benchmark-Konto SCHREIBGESCHUETZT ist (wird geprueft: laesst sich die Datei vom laufenden
  Prozess zum Schreiben oeffnen, gilt sie nicht als Anker). Heute ist KEIN Anker hinterlegt -> Gate geschlossen.
- Bestaetigung = signiertes JSON (kanonisch), gebunden an: Lauf-ID, Maschine (Hostname + MachineGuid), SHA-256 der
  freigegebenen Netzwerkpolitik, SHA-256 des Firewall-Exports vom Host/Perimeter, Endpunkt (IP:Port), DNS-Modus,
  Pruefer (= Inhaber des Signaturschluessels), Gueltigkeit (max. 24 h) und die bestandenen VM-Abnahmetests V1-V13.
- Eine vom Benchmark selbst erzeugte oder veraenderbare Datei ist kein Nachweis: ohne gueltige Signatur eines Ankers
  wird nichts akzeptiert. Der private Schluessel liegt nur bei der Pruefstelle.
- Test-Anker (`IsolationGate.for_tests`) existieren nur im Speicher, sind als Test markiert und lassen NIE einen
  Nicht-Loopback-Endpunkt zu; ein Produktiv-Gate lehnt test-markierte Bestaetigungen ab. Die Endpunktsperre
  (`answer_driver.RealCloudDisabled`, C-14) bleibt davon unberuehrt.

Grenzen: Ob die Pruefstelle wirklich geprueft hat, belegt nur ihre Signatur; Diebstahl des privaten Schluessels oder
ein schreibbarer Anker brechen das Modell (siehe AUDIT_S87.md)."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.legal_bench import answer_specs

SCHEMA = "lexono-isolation-attestation/1"
POLICY_PATH = answer_specs.ROOT / "isolation" / "netzwerkpolitik.json"
MAX_VALIDITY = timedelta(hours=24)
#: VM-Abnahmetests (AUDIT_S87.md), die laut Bestaetigung bestanden sein muessen
REQUIRED_TESTS = tuple(f"V{i}" for i in range(1, 14))
ENV_ANCHORS = "LEXONO_ISOLATION_TRUST_ANCHORS"
ENV_ATTESTATION = "LEXONO_ISOLATION_ATTESTATION"


class IsolationNotConfirmed(RuntimeError):
    """Keine gueltige externe Bestaetigung der Isolation - kein Start, kein Aufruf."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("Isolation nicht bestaetigt: " + "; ".join(reasons[:3]))
        self.reasons = list(reasons)


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def machine_identity() -> dict:
    """Hostname und MachineGuid (Windows, ohne Administratorrechte lesbar). Ohne GUID -> leer (fail-closed)."""
    guid = None
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                guid = winreg.QueryValueEx(key, "MachineGuid")[0]
        except OSError:
            guid = None
    return {"hostname": socket.gethostname().lower(), "machine_guid": guid}


def guest_firewall() -> dict:
    """Gast-Firewall lesen (zweite Linie; nur Plausibilitaet - die Grenze liegt am Host/Perimeter)."""
    if sys.platform != "win32":
        return {}
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-NetFirewallProfile | Select-Object Name,@{n='Enabled';e={[bool]$_.Enabled}},"
                          "@{n='Out';e={[string]$_.DefaultOutboundAction}} | ConvertTo-Json"],
                         capture_output=True, text=True, timeout=60, check=True)
    return {p["Name"]: {"enabled": p["Enabled"] is True, "default_outbound": p["Out"]} for p in json.loads(out.stdout)}


def _writable_by_this_process(path: Path) -> bool:
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND)  # oeffnet nur, schreibt nichts
    except OSError:
        return False
    os.close(fd)
    return True


def load_trust_anchors(path: Path) -> dict:
    """Anker-Datei der IT: {"anchors": [{"key_id", "public_key_hex", "inhaber", "rolle", "gueltig_bis"}]}."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    anchors = {}
    for entry in data.get("anchors", []):
        anchors[entry["key_id"]] = {"key": Ed25519PublicKey.from_public_bytes(bytes.fromhex(entry["public_key_hex"])),
                                    "inhaber": entry["inhaber"], "rolle": entry["rolle"],
                                    "gueltig_bis": entry["gueltig_bis"]}
    return anchors


@dataclass
class IsolationGate:
    anchors: dict
    attestation_path: Path | None
    policy_path: Path = POLICY_PATH
    test_only: bool = False
    anchor_problems: list[str] = field(default_factory=list)
    identity: object = machine_identity
    firewall: object = guest_firewall
    now: object = lambda: datetime.now(timezone.utc)  # noqa: E731

    @classmethod
    def from_environment(cls) -> IsolationGate:
        """Produktiv: Anker und Bestaetigung NUR aus von der IT bereitgestellten Dateien."""
        problems: list[str] = []
        anchors: dict = {}
        anchor_path = os.environ.get(ENV_ANCHORS)
        if not anchor_path:
            problems.append("kein Vertrauensanker der Pruefstelle hinterlegt (IT/Owner) - Gate geschlossen")
        elif not Path(anchor_path).is_file():
            problems.append("Vertrauensanker-Datei nicht vorhanden")
        elif _writable_by_this_process(Path(anchor_path)):
            problems.append("Vertrauensanker ist fuer den Benchmark-Prozess schreibbar - kein Vertrauensnachweis")
        else:
            try:
                anchors = load_trust_anchors(Path(anchor_path))
            except Exception as exc:  # noqa: BLE001 - unlesbarer Anker = geschlossen
                problems.append(f"Vertrauensanker unlesbar ({type(exc).__name__})")
        attestation = os.environ.get(ENV_ATTESTATION)
        return cls(anchors=anchors, attestation_path=Path(attestation) if attestation else None, anchor_problems=problems)

    @classmethod
    def for_tests(cls, anchors: dict, attestation_path: Path, policy_path: Path, **kwargs) -> IsolationGate:  # noqa: ANN003
        """NUR fuer Tests: Anker im Speicher, Bestaetigung muss test-markiert sein, nur Loopback-Endpunkte."""
        return cls(anchors=anchors, attestation_path=attestation_path, policy_path=policy_path, test_only=True, **kwargs)

    def problems(self, *, run_id: str | None, provider_endpoints: set[tuple[str, int]],
                 check_firewall: bool = True) -> list[str]:
        problems = list(self.anchor_problems)
        if not self.anchors and not problems:
            problems.append("kein Vertrauensanker - Gate geschlossen")
        # --- Politik ------------------------------------------------------------------------------------------------
        try:
            policy = json.loads(Path(self.policy_path).read_text(encoding="utf-8"))
            policy_sha = sha256_file(self.policy_path)
        except (OSError, ValueError) as exc:
            return problems + [f"Netzwerkpolitik nicht lesbar ({type(exc).__name__})"]
        if policy.get("status") != "FREIGEGEBEN":
            problems.append(f"Netzwerkpolitik nicht freigegeben (Status {policy.get('status')!r})")
        endpoints = [f for f in policy.get("freigaben", []) if f.get("ip_adressen") and f.get("port")]
        if not endpoints:
            problems.append("kein freigegebener Endpunkt in der Netzwerkpolitik")
        if not policy.get("dns", {}).get("entscheidung"):
            problems.append("DNS-Entscheidung der Netzwerkpolitik OFFEN")
        approved = {(ip, int(f["port"])) for f in endpoints for ip in f["ip_adressen"]}
        # --- Bestaetigung -------------------------------------------------------------------------------------------
        if self.attestation_path is None or not Path(self.attestation_path).is_file():
            return problems + ["keine Bestaetigung der Pruefstelle vorhanden"]
        try:
            document = json.loads(Path(self.attestation_path).read_text(encoding="utf-8"))
            payload, signature, key_id = document["payload"], bytes.fromhex(document["signature_hex"]), document["key_id"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return problems + [f"Bestaetigung unlesbar ({type(exc).__name__})"]
        anchor = self.anchors.get(key_id)
        if anchor is None:
            return problems + [f"Bestaetigung mit unbekanntem Schluessel {key_id!r} (fremde Pruefstelle)"]
        try:
            anchor["key"].verify(signature, canonical(payload))
        except Exception:  # noqa: BLE001 - jede Abweichung = ungueltig
            return problems + ["Signatur der Bestaetigung ungueltig (manipuliert oder fremd)"]
        now = self.now()
        try:
            valid_from = datetime.fromisoformat(payload["gueltig_von"])
            valid_to = datetime.fromisoformat(payload["gueltig_bis"])
            anchor_to = datetime.fromisoformat(anchor["gueltig_bis"])
        except (KeyError, ValueError, TypeError):
            return problems + ["Gueltigkeitsangaben fehlen oder sind ungueltig"]
        if payload.get("schema") != SCHEMA:
            problems.append("Bestaetigung hat ein unbekanntes Schema")
        if bool(payload.get("test_only")) != self.test_only:
            problems.append("Test-Bestaetigung im Produktivbetrieb bzw. umgekehrt - abgelehnt")
        if not valid_from <= now <= valid_to:
            problems.append("Bestaetigung abgelaufen oder noch nicht gueltig")
        if valid_to - valid_from > MAX_VALIDITY:
            problems.append("Gueltigkeitsfenster der Bestaetigung laenger als 24 Stunden")
        if now > anchor_to:
            problems.append("Schluessel der Pruefstelle abgelaufen")
        if payload.get("run_id") != run_id:
            problems.append(f"Bestaetigung gilt fuer einen anderen Lauf ({payload.get('run_id')!r})")
        identity = self.identity()
        if not identity.get("machine_guid") or payload.get("maschine") != identity:
            problems.append("Bestaetigung gilt fuer eine andere Maschine/VM (Hostname oder MachineGuid)")
        if payload.get("netzwerkpolitik_sha256") != policy_sha:
            problems.append("Netzwerkpolitik seit der Bestaetigung geaendert")
        if not payload.get("firewall_export_sha256") or len(str(payload.get("firewall_export_sha256"))) != 64:
            problems.append("Bestaetigung ohne Hash des Firewall-Exports vom Host/Perimeter")
        attested = {(e.get("ip"), e.get("port")) for e in payload.get("endpunkte", [])}
        if attested != approved:
            problems.append("Endpunkt der Bestaetigung weicht von der Netzwerkpolitik ab")
        if not set(provider_endpoints) <= attested:
            problems.append(f"Provider-Endpunkt {sorted(provider_endpoints)} nicht bestaetigt")
        if self.test_only and any(not ip.startswith("127.") and ip != "::1" for ip, _ in attested | set(provider_endpoints)):
            problems.append("Test-Gate darf nur Loopback-Endpunkte zulassen")
        if payload.get("dns_modus") != policy.get("dns", {}).get("entscheidung"):
            problems.append("DNS-Modus der Bestaetigung weicht von der Netzwerkpolitik ab")
        pruefer = payload.get("pruefer") or {}
        if not pruefer.get("name") or pruefer.get("name") != anchor["inhaber"] or pruefer.get("rolle") != anchor["rolle"]:
            problems.append("Pruefer der Bestaetigung ist nicht der Inhaber des Signaturschluessels")
        results = payload.get("abnahme") or {}
        missing = [t for t in REQUIRED_TESTS if results.get(t) != "bestanden"]
        if missing:
            problems.append(f"VM-Abnahme nicht vollstaendig bestanden ({', '.join(missing[:5])})")
        if check_firewall:
            try:
                firewall = self.firewall()
            except Exception as exc:  # noqa: BLE001 - nicht pruefbar = geschlossen
                firewall = {}
                problems.append(f"Gast-Firewall nicht pruefbar ({type(exc).__name__})")
            if not firewall or any(not p.get("enabled") or str(p.get("default_outbound")).lower() != "block"
                                   for p in firewall.values()):
                problems.append("Gast-Firewall blockiert ausgehend nicht (Widerspruch zur Bestaetigung)")
        return problems

    def require(self, *, run_id: str | None, provider_endpoints: set[tuple[str, int]]) -> None:
        problems = self.problems(run_id=run_id, provider_endpoints=provider_endpoints)
        if problems:
            raise IsolationNotConfirmed(problems)
