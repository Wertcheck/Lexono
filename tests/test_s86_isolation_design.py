"""§86: Entwurf der extern durchgesetzten Netzwerkisolation - Politik, Regelpruefer, Abnahme-Proben.

Diese Datei setzt KEINE Isolation um. Sie enthaelt:
1. die Pruefung der Netzwerkpolitik (`benchmarks/legal_answers_v1/isolation/netzwerkpolitik.json`);
2. einen Regelpruefer fuer einen Firewall-Export, den IT/Host AUSSERHALB des Benchmark-Prozesses erzeugen (fehlende oder
   abweichende Regeln = kein Start) - lokal mit synthetischen Exporten getestet;
3. Abnahme-Proben, die in GETRENNTEN Prozessen laufen (frischer Python-Interpreter ohne Zaun, natives ctypes-connect,
   curl.exe, nslookup.exe) - die Sperre muss von aussen wirken, nicht durch Lexono-Code.

Sicherheit: `_guard_target` verweigert jedes Nicht-Loopback-Ziel VOR dem Prozessstart, solange nicht ausdruecklich die
freigegebene Zielumgebung gesetzt ist (LEXONO_ISOLATION_ACCEPTANCE=1 und ein Manifest von IT). Lokal laufen deshalb nur
Selbsttests der Proben gegen Loopback; die Abnahme in der Zielumgebung wird uebersprungen (NICHT VERIFIZIERT)."""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "benchmarks" / "legal_answers_v1" / "isolation" / "netzwerkpolitik.json"
POLICY = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
DNS_PORTS = {53, 853, 5353, 5355, 137}
SYSTEM32 = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32"


# --- 1. Politik ----------------------------------------------------------------------------------------------------------


def test_policy_is_a_default_deny_draft_without_invented_decisions() -> None:
    assert POLICY["status"] == "ENTWURF" and POLICY["c14"] == "GESPERRT"
    assert all(value == "blockieren" for value in POLICY["standard"].values())
    assert set(POLICY["standard"]) >= {"ausgehend", "ipv4", "ipv6", "tcp", "udp", "icmp"}
    assert POLICY["durchsetzung"]["ort"] == "ausserhalb_des_benchmark_prozesses"
    assert POLICY["durchsetzung"]["pruefung_unabhaengig_vom_benchmark_prozess"] is True
    assert {"python_audit_hook", "prozess_zulassungsliste"} <= set(POLICY["durchsetzung"]["nicht_als_grenze"])
    for freigabe in POLICY["freigaben"]:  # nichts erfunden: Endpunkt, Adressen, Port, Freigebende OFFEN
        assert freigabe["hostname"] is None and freigabe["ip_adressen"] is None and freigabe["port"] is None
        assert freigabe["freigegeben_durch"] is None and freigabe["tls_pflicht"] is True
    assert all(p["verwaltet_durch"] is None for p in POLICY["durchsetzung"]["durchsetzungspunkte"])
    assert POLICY["proxy"]["erlaubt"] is False and POLICY["dns"]["entscheidung"] is None
    assert set(POLICY["fehlerverhalten"].values()) <= {"kein_start", "lauf_abbrechen_und_melden"}


# --- 2. Regelpruefer (Export von ausserhalb des Benchmark-Prozesses) ------------------------------------------------------


def check_rules(export: dict, policy: dict) -> list[str]:
    """Prueft einen Firewall-Export gegen die Politik. Jede Abweichung = Fehler; Fehler = kein Start (fail-closed)."""
    errors: list[str] = []
    if not isinstance(export, dict) or not export.get("profiles") or "rules" not in export:
        return ["Export unvollstaendig oder unlesbar - kein Start"]
    for profile in export["profiles"]:
        if profile.get("enabled") is not True:
            errors.append(f"Profil {profile.get('name')}: Firewall nicht aktiv")
        if str(profile.get("default_outbound")).lower() != "block":
            errors.append(f"Profil {profile.get('name')}: ausgehend nicht standardmaessig blockiert")
    allowed: dict[str, set] = {}
    for freigabe in policy["freigaben"]:
        if not freigabe.get("ip_adressen") or not freigabe.get("port"):
            errors.append("Freigabe des Endpunkts OFFEN (Adresse/Port) - kein Start")
            continue
        for ip in freigabe["ip_adressen"]:
            allowed.setdefault(str(ipaddress.ip_address(ip)), set()).add(int(freigabe["port"]))
    loopback_ok = policy["lokal"]["loopback_erlaubt"]
    for rule in export["rules"]:
        if rule.get("enabled") is not True or str(rule.get("direction")).lower() != "outbound" \
                or str(rule.get("action")).lower() != "allow":
            continue
        name = rule.get("name", "?")
        protocol = str(rule.get("protocol", "any")).lower()
        addresses = [str(a) for a in rule.get("remote_addresses") or ["any"]]
        ports = {str(p) for p in rule.get("remote_ports") or ["any"]}
        if protocol != "tcp":
            errors.append(f"Regel {name}: ausgehend {protocol.upper()} erlaubt (nur TCP zum Endpunkt zulaessig)")
            continue
        if "any" in (a.lower() for a in addresses) or any("-" in a or "/" in a for a in addresses):
            errors.append(f"Regel {name}: Zieladresse nicht auf den Endpunkt begrenzt ({addresses})")
            continue
        if "any" in (p.lower() for p in ports) or any(p.isdigit() and int(p) in DNS_PORTS for p in ports):
            errors.append(f"Regel {name}: Zielport nicht begrenzt bzw. DNS-Port ({sorted(ports)})")
            continue
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if ip.is_loopback and loopback_ok:
                continue
            if str(ip) not in allowed or not {int(p) for p in ports} <= allowed[str(ip)]:
                errors.append(f"Regel {name}: {address}:{sorted(ports)} ist nicht freigegeben")
    return errors


def _export(default_outbound: str = "Block", rules=None) -> dict:  # noqa: ANN001
    return {"profiles": [{"name": n, "enabled": True, "default_outbound": default_outbound}
                         for n in ("Domain", "Private", "Public")], "rules": rules or []}


_POLICY_FILLED = {**POLICY, "freigaben": [{**POLICY["freigaben"][0], "ip_adressen": ["192.0.2.10"], "port": 443}]}
_ALLOW = {"name": "endpunkt", "enabled": True, "direction": "Outbound", "action": "Allow", "protocol": "TCP",
          "remote_addresses": ["192.0.2.10"], "remote_ports": ["443"]}


def test_rule_checker_accepts_only_the_approved_endpoint() -> None:
    """Synthetische Freigabe (TEST-NET-Adresse, nur im Test) - zeigt, was die Abnahme verlangt."""
    assert check_rules(_export(rules=[_ALLOW]), _POLICY_FILLED) == []


@pytest.mark.parametrize("export, expected", [
    (_export("Allow", [_ALLOW]), "nicht standardmaessig blockiert"),
    (_export("NotConfigured", [_ALLOW]), "nicht standardmaessig blockiert"),
    ({"profiles": [], "rules": []}, "unvollstaendig"),
    (_export(rules=[_ALLOW, {**_ALLOW, "name": "dns", "protocol": "UDP", "remote_ports": ["53"]}]), "UDP erlaubt"),
    (_export(rules=[{**_ALLOW, "remote_addresses": ["Any"]}]), "nicht auf den Endpunkt begrenzt"),
    (_export(rules=[{**_ALLOW, "remote_addresses": ["192.0.2.0/24"]}]), "nicht auf den Endpunkt begrenzt"),
    (_export(rules=[{**_ALLOW, "remote_ports": ["Any"]}]), "Zielport nicht begrenzt"),
    (_export(rules=[{**_ALLOW, "remote_ports": ["853"]}]), "DNS-Port"),
    (_export(rules=[_ALLOW, {**_ALLOW, "name": "proxy", "remote_addresses": ["198.51.100.7"], "remote_ports": ["8080"]}]),
     "nicht freigegeben"),
    (_export(rules=[{**_ALLOW, "remote_addresses": ["2001:db8::10"]}]), "nicht freigegeben"),  # IPv6 nicht freigegeben
], ids=["standard_zulassen", "standard_nicht_konfiguriert", "export_leer", "udp_dns", "beliebige_adresse",
        "adressbereich", "beliebiger_port", "dot_port", "zusaetzlicher_proxy", "ipv6_ziel"])
def test_rule_checker_rejects_missing_or_faulty_rules(export, expected) -> None:
    errors = check_rules(export, _POLICY_FILLED)
    assert any(expected in e for e in errors), errors


def test_rule_checker_refuses_start_while_endpoint_decision_is_open() -> None:
    assert any("Freigabe des Endpunkts OFFEN" in e for e in check_rules(_export(rules=[_ALLOW]), POLICY))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-Firewall")
def test_current_machine_does_not_meet_the_policy() -> None:
    """Ehrlicher Ist-Stand (nur gelesen): diese Maschine erfuellt die Politik NICHT - ausgehend ist erlaubt."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-NetFirewallProfile | Select-Object Name,@{n='Enabled';e={[bool]$_.Enabled}},"
                          "@{n='Out';e={[string]$_.DefaultOutboundAction}} | ConvertTo-Json"],
                         capture_output=True, text=True, timeout=60)
    profiles = json.loads(out.stdout)
    export = {"profiles": [{"name": p["Name"], "enabled": p["Enabled"] is True, "default_outbound": p["Out"]}
                           for p in profiles], "rules": []}
    assert {p["default_outbound"] for p in export["profiles"]} == {"NotConfigured"}  # gelesen: nicht blockiert
    assert any("nicht standardmaessig blockiert" in e for e in check_rules(export, POLICY))


# --- 3. Abnahme-Proben in getrennten Prozessen ----------------------------------------------------------------------------


def _acceptance_environment() -> dict | None:
    if os.environ.get("LEXONO_ISOLATION_ACCEPTANCE") != "1" or not os.environ.get("LEXONO_ISOLATION_MANIFEST"):
        return None
    return json.loads(Path(os.environ["LEXONO_ISOLATION_MANIFEST"]).read_text(encoding="utf-8"))


def _guard_target(host: str) -> None:
    """VOR jedem Prozessstart: ausserhalb der freigegebenen Zielumgebung nur Loopback-Ziele."""
    if _acceptance_environment() is not None:
        return
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if not loopback:
        raise PermissionError(f"Abnahme-Probe zu {host!r} verweigert - nur in der freigegebenen Zielumgebung erlaubt")


_CHILD = {
    "tcp": "import socket,sys; s=socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=float(sys.argv[3])); s.close()",
    "udp": textwrap.dedent("""
        import socket, sys
        s = socket.socket(socket.AF_INET6 if ':' in sys.argv[1] else socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(float(sys.argv[3])); s.sendto(b'lexono-abnahme', (sys.argv[1], int(sys.argv[2])))
        data = s.recv(64); sys.exit(0 if data else 1)"""),
    "dns": "import socket,sys; socket.getaddrinfo(sys.argv[1], int(sys.argv[2]))",
    "native": textwrap.dedent("""
        import ctypes, socket, sys
        class SA(ctypes.Structure):
            _fields_ = [('f', ctypes.c_ushort), ('p', ctypes.c_ushort), ('a', ctypes.c_ubyte * 4), ('z', ctypes.c_ubyte * 8)]
        s = socket.socket(); s.settimeout(None)
        ip = socket.inet_aton(sys.argv[1]); sa = SA(socket.AF_INET, socket.htons(int(sys.argv[2])), (ctypes.c_ubyte * 4)(*ip))
        r = ctypes.WinDLL('ws2_32').connect(ctypes.c_size_t(s.fileno()), ctypes.byref(sa), ctypes.sizeof(sa))
        sys.exit(0 if r == 0 else 1)"""),
}


def probe(kind: str, host: str, port: int = 0, timeout: float = 3.0) -> str:
    """Ein Zugriffsversuch in einem GETRENNTEN Prozess. Ergebnis: 'erreicht' oder 'blockiert' (aus Sicht des Ziels)."""
    _guard_target(host)
    env = {k: v for k, v in os.environ.items() if not k.upper().endswith("_PROXY")}
    env["NO_PROXY"] = "*"
    if kind in _CHILD:
        cmd = [sys.executable, "-I", "-c", _CHILD[kind], host, str(port), str(timeout)]
    elif kind == "curl":
        cmd = [str(SYSTEM32 / "curl.exe"), "--noproxy", "*", "-s", "-o", "NUL", "--max-time", str(timeout),
               f"http://{host}:{port}/"]
    elif kind == "nslookup":  # DNS ueber ein eigenes Programm; Server ausdruecklich angegeben
        cmd = [str(SYSTEM32 / "nslookup.exe"), "-timeout=1", "-retry=1", "lexono-abnahme.invalid", host]
    else:
        raise ValueError(kind)
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout + 15, env=env)
    except subprocess.TimeoutExpired:
        return "blockiert"
    if kind == "nslookup":
        return "erreicht" if b"lexono-abnahme.invalid" in result.stdout and b"Server:" in result.stdout and \
            b"timed out" not in result.stdout and b"Zeit" not in result.stdout else "blockiert"
    return "erreicht" if result.returncode == 0 else "blockiert"


def test_probe_guard_refuses_external_targets_before_any_process_starts(monkeypatch) -> None:
    monkeypatch.delenv("LEXONO_ISOLATION_ACCEPTANCE", raising=False)
    started: list = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: started.append(a))
    for kind, host in (("tcp", "192.0.2.1"), ("dns", "lexono-abnahme.invalid"), ("curl", "198.51.100.1"),
                       ("native", "203.0.113.1"), ("udp", "2001:db8::1"), ("nslookup", "192.0.2.53")):
        with pytest.raises(PermissionError):
            probe(kind, host, 443)
    assert started == []


class _Http(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: ANN002
        pass

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # danach lauscht dort niemand
    return port


def test_probes_work_in_separate_processes_against_local_targets() -> None:
    """Selbsttest der Proben (KEIN Isolationsnachweis): erreichbar = lokaler Dienst, 'blockiert' = lokaler Port ohne Dienst."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Http)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))

    def echo() -> None:
        try:
            data, addr = udp.recvfrom(64)
            udp.sendto(data, addr)
        except OSError:
            pass

    threading.Thread(target=echo, daemon=True).start()
    try:
        port, closed = server.server_address[1], _closed_port()
        assert probe("tcp", "127.0.0.1", port) == "erreicht"
        assert probe("tcp", "127.0.0.1", closed, timeout=2) == "blockiert"
        assert probe("curl", "127.0.0.1", port) == "erreicht"
        assert probe("curl", "127.0.0.1", closed, timeout=2) == "blockiert"
        assert probe("udp", "127.0.0.1", udp.getsockname()[1]) == "erreicht"
        assert probe("dns", "localhost", 80) == "erreicht"
        if sys.platform == "win32":
            assert probe("native", "127.0.0.1", port) == "erreicht"
            assert probe("native", "127.0.0.1", closed) == "blockiert"
            assert probe("nslookup", "127.0.0.1") == "blockiert"  # kein DNS-Dienst auf 127.0.0.1:53
    finally:
        server.shutdown()
        server.server_close()
        udp.close()


# --- 4. Abnahme in der Zielumgebung (nur dort; hier NICHT VERIFIZIERT) ------------------------------------------------------

ACCEPTANCE = _acceptance_environment()
target_env = pytest.mark.skipif(ACCEPTANCE is None, reason="nur in der freigegebenen, isolierten Zielumgebung (Manifest der IT)")


@target_env
def test_acceptance_approved_test_endpoint_is_reachable() -> None:
    endpoint = ACCEPTANCE["freigegebener_testendpunkt"]  # von IT bereitgestellter Testdienst, KEIN Modell-Endpunkt
    assert probe("tcp", endpoint["ip"], endpoint["port"]) == "erreicht"


@target_env
@pytest.mark.parametrize("kind", ["tcp", "curl", "native", "udp"])
def test_acceptance_canary_addresses_are_blocked(kind) -> None:
    """Kanarienziele: von IT kontrollierte Senken ausserhalb der Isolation; die IT bestaetigt anhand ihrer Protokolle,
    dass dort NICHTS ankam (zweiter, unabhaengiger Nachweis)."""
    for target in ACCEPTANCE["kanarienziele"]:  # IPv4 und IPv6, verschiedene Ports
        assert probe(kind, target["ip"], target["port"]) == "blockiert", target


@target_env
def test_acceptance_dns_is_blocked() -> None:
    for resolver in ACCEPTANCE["dns_resolver"]:
        assert probe("nslookup", resolver) == "blockiert", resolver
    assert probe("dns", ACCEPTANCE["kanarienname"], 443) == "blockiert"


@target_env
def test_acceptance_firewall_export_from_outside_matches_the_policy() -> None:
    export = json.loads(Path(ACCEPTANCE["firewall_export"]).read_text(encoding="utf-8"))  # vom Host/IT erzeugt
    assert check_rules(export, json.loads(Path(ACCEPTANCE["politik"]).read_text(encoding="utf-8"))) == []
