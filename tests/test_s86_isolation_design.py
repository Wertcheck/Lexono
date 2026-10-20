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

from scripts.legal_bench import isolation_evidence

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


# §90: der Regelpruefer liegt jetzt in scripts/legal_bench/isolation_evidence.py (vom Urteil fuer V1 genutzt);
# hier unveraendert weiter getestet.
check_rules = isolation_evidence.check_rules


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


# §96 (H2): Exit-Codes der Kindprozesse - 0 erreicht, 3 abgelehnt (RST/ICMP: Paket kam an, Pfad offen!),
# 4 zeitueberschreitung (keine Antwort - nur das ist mit einer verwerfenden ACL vereinbar), 5 fehler (Netzwerkfehler),
# 6 keine_aufloesung (Namensaufloesung ohne Netzwerkbeleg). §98 (M2): Prozess-Timeout und unerwartete Exit-Codes
# (Absturz) sind KEIN Sperrnachweis: "prozess_timeout" bzw. "prozessfehler".
RESULTS = {0: "erreicht", 3: "abgelehnt", 4: "zeitueberschreitung", 5: "fehler", 6: "keine_aufloesung"}
_CHILD = {
    "tcp": textwrap.dedent("""
        import socket, sys
        try:
            socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=float(sys.argv[3])).close()
        except ConnectionRefusedError:
            sys.exit(3)
        except TimeoutError:
            sys.exit(4)
        except OSError:
            sys.exit(5)"""),
    "udp": textwrap.dedent("""
        import socket, sys
        s = socket.socket(socket.AF_INET6 if ':' in sys.argv[1] else socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(float(sys.argv[3]))
        try:
            s.sendto(b'lexono-abnahme', (sys.argv[1], int(sys.argv[2])))
            s.recv(64)
        except ConnectionResetError:
            sys.exit(3)
        except TimeoutError:
            sys.exit(4)
        except OSError:
            sys.exit(5)"""),
    # §98 (M3): Namensaufloesung ueber das System - ohne Resolver scheitert sie SOFORT und ohne Paket. Das ist kein
    # Sperrnachweis ("keine_aufloesung"), nur ein Plausibilitaetswert.
    "dns": textwrap.dedent("""
        import socket, sys
        try:
            socket.getaddrinfo(sys.argv[1], int(sys.argv[2]))
        except socket.gaierror:
            sys.exit(6)
        except OSError:
            sys.exit(5)"""),
    # §98 (M3): echte DNS-Abfrage (UDP/53, ohne System-Resolver) an einen ausdruecklich genannten Server. JEDE Antwort
    # (auch unvollstaendig/fehlerhaft) heisst: Pfad offen -> "erreicht". Nur ausbleibende Antwort = "zeitueberschreitung".
    "dnsquery": textwrap.dedent("""
        import os, socket, struct, sys
        server, timeout = sys.argv[1], float(sys.argv[3])
        qid = int.from_bytes(os.urandom(2), 'big')
        name = b''.join(bytes([len(p)]) + p for p in b'lexono-abnahme.invalid'.split(b'.')) + b'\\0'
        packet = struct.pack('>HHHHHH', qid, 0x0100, 1, 0, 0, 0) + name + struct.pack('>HH', 1, 1)
        s = socket.socket(socket.AF_INET6 if ':' in server else socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            s.sendto(packet, (server, int(sys.argv[2]) or 53))
            s.recvfrom(512)
        except ConnectionResetError:
            sys.exit(3)
        except TimeoutError:
            sys.exit(4)
        except OSError:
            sys.exit(5)"""),
    # §96 (N4): nativ fuer IPv4 UND IPv6 (sockaddr_in6); WSA-Fehler 10061 = abgelehnt, 10060 = Zeitueberschreitung
    "native": textwrap.dedent("""
        import ctypes, socket, sys
        host, port = sys.argv[1], int(sys.argv[2])
        ws2 = ctypes.WinDLL('ws2_32')
        if ':' in host:
            class SA(ctypes.Structure):
                _fields_ = [('f', ctypes.c_ushort), ('p', ctypes.c_ushort), ('flow', ctypes.c_ulong),
                            ('a', ctypes.c_ubyte * 16), ('scope', ctypes.c_ulong)]
            s = socket.socket(socket.AF_INET6)
            sa = SA(socket.AF_INET6, socket.htons(port), 0, (ctypes.c_ubyte * 16)(*socket.inet_pton(socket.AF_INET6, host)), 0)
        else:
            class SA(ctypes.Structure):
                _fields_ = [('f', ctypes.c_ushort), ('p', ctypes.c_ushort), ('a', ctypes.c_ubyte * 4), ('z', ctypes.c_ubyte * 8)]
            s = socket.socket()
            sa = SA(socket.AF_INET, socket.htons(port), (ctypes.c_ubyte * 4)(*socket.inet_aton(host)))
        s.settimeout(None)
        r = ws2.connect(ctypes.c_size_t(s.fileno()), ctypes.byref(sa), ctypes.sizeof(sa))
        if r == 0:
            sys.exit(0)
        error = ws2.WSAGetLastError()
        sys.exit(3 if error == 10061 else 4 if error == 10060 else 5)"""),
}


def probe(kind: str, host: str, port: int = 0, timeout: float = 5.0) -> str:
    """Ein Zugriffsversuch in einem GETRENNTEN Prozess. Ergebnis (§96): 'erreicht', 'abgelehnt', 'zeitueberschreitung'
    oder 'fehler'. Nur 'zeitueberschreitung' darf als Sperre gelten. Das Zeitlimit muss laenger sein als die
    Windows-Wiederholung nach einem RST (~2 s gemessen) - sonst sieht eine Ablehnung wie eine Zeitueberschreitung aus."""
    _guard_target(host)
    env = {k: v for k, v in os.environ.items() if not k.upper().endswith("_PROXY")}
    env["NO_PROXY"] = "*"
    if kind in _CHILD:
        cmd = [sys.executable, "-I", "-c", _CHILD[kind], host, str(port), str(timeout)]
    elif kind == "curl":
        target = f"[{host}]" if ":" in host else host
        cmd = [str(SYSTEM32 / "curl.exe"), "--noproxy", "*", "-s", "-o", "NUL", "--max-time", str(timeout),
               f"http://{target}:{port}/"]
    elif kind == "nslookup":  # DNS ueber ein eigenes Programm; Server ausdruecklich angegeben
        cmd = [str(SYSTEM32 / "nslookup.exe"), "-timeout=1", "-retry=1", "lexono-abnahme.invalid", host]
    else:
        raise ValueError(kind)
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout + 15, env=env)
    except subprocess.TimeoutExpired:
        return "prozess_timeout"  # §98 (M2): ein haengender Prozess ist KEIN Sperrnachweis
    if kind == "nslookup":
        return _nslookup_result(result.stdout + result.stderr)
    if kind == "curl":  # 7 = Verbindung fehlgeschlagen (abgelehnt), 28 = Zeitlimit
        return {0: "erreicht", 7: "abgelehnt", 28: "zeitueberschreitung"}.get(result.returncode, "prozessfehler")
    return RESULTS.get(result.returncode, "prozessfehler")


#: §98 (N3): exakte Zeitlimit-Meldungen von nslookup (englisch/deutsch, verschiedene Konsolen-Kodierungen)
_NSLOOKUP_TIMEOUT = [b"DNS request timed out", b"timed-out", b"Zeitlimit", *["Zeitüberschreitung".encode(c)
                                                                            for c in ("cp850", "cp1252", "utf-8")]]


def _nslookup_result(out: bytes) -> str:
    """nslookup gibt "Server:/Address:" IMMER aus (auch ohne Antwort) - das ist kein Erreichbarkeitsbeleg. Erreicht ist
    der Server nur, wenn er geantwortet hat (Name/NXDOMAIN/SERVFAIL/REFUSED). Formulierungen anderer Sprachversionen
    sind NICHT VERIFIZIERT; Unbekanntes ergibt "fehler" (nie Sperre)."""
    if any(marker in out for marker in _NSLOOKUP_TIMEOUT):
        return "zeitueberschreitung"
    answered = (b"Non-existent domain", b"NXDOMAIN", b"Name:", b"Server failed", b"SERVFAIL", b"Query refused",
                b"REFUSED")
    if any(marker in out for marker in answered):
        return "erreicht"  # der Server hat geantwortet (Inhalt egal) - Pfad offen
    if b"No response from server" in out:
        return "abgelehnt"  # ohne Zeitlimit-Meldung: sofortige Ablehnung (z. B. ICMP) - Paket kam an
    return "fehler"


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
        for _ in range(2):  # udp- und dnsquery-Probe
            try:
                data, addr = udp.recvfrom(512)
                udp.sendto(data, addr)
            except OSError:
                pass

    threading.Thread(target=echo, daemon=True).start()
    try:
        port, closed = server.server_address[1], _closed_port()
        assert probe("tcp", "127.0.0.1", port) == "erreicht"
        # §96 (H2): ein geschlossener Port ist ABGELEHNT (Pfad offen) - niemals als Sperre zu werten
        assert probe("tcp", "127.0.0.1", closed) == "abgelehnt"
        assert probe("curl", "127.0.0.1", port) == "erreicht"
        assert probe("curl", "127.0.0.1", closed) == "abgelehnt"
        assert probe("udp", "127.0.0.1", udp.getsockname()[1]) == "erreicht"
        assert probe("dns", "localhost", 80) == "erreicht"
        if sys.platform == "win32":
            assert probe("native", "127.0.0.1", port) == "erreicht"
            assert probe("native", "127.0.0.1", closed) == "abgelehnt"
            assert probe("nslookup", "127.0.0.1") != "erreicht"  # kein DNS-Dienst auf 127.0.0.1:53
        assert probe("dnsquery", "127.0.0.1", udp.getsockname()[1]) == "erreicht"  # Echo-Server antwortet
    finally:
        server.shutdown()
        server.server_close()
        udp.close()


# --- 4. Abnahme in der Zielumgebung (nur dort; hier NICHT VERIFIZIERT) ------------------------------------------------------

ACCEPTANCE = _acceptance_environment()
target_env = pytest.mark.skipif(ACCEPTANCE is None, reason="nur in der freigegebenen, isolierten Zielumgebung (Manifest der IT)")


@target_env
def test_acceptance_approved_test_endpoint_is_reachable() -> None:
    endpoint = ACCEPTANCE["freigegebener_testendpunkt"]  # freigegebener Endpunkt, Probe ohne Modellaufruf
    assert probe("tcp", endpoint["ip"], endpoint["port"]) == "erreicht"


@target_env
@pytest.mark.parametrize("kind", ["tcp", "curl", "native", "udp"])
def test_acceptance_canary_addresses_are_blocked(kind) -> None:
    """Kanarienziele: von IT kontrollierte Senken ausserhalb der Isolation; die IT bestaetigt anhand ihrer Protokolle,
    dass dort NICHTS ankam (zweiter, unabhaengiger Nachweis)."""
    for target in ACCEPTANCE["kanarienziele"]:  # IPv4 und IPv6, verschiedene Ports
        # §96 (H2): nur ein verworfenes Paket (Zeitueberschreitung) ist eine Sperre - "abgelehnt" = Pfad offen
        assert probe(kind, target["ip"], target["port"]) == "zeitueberschreitung", target


@target_env
def test_acceptance_dns_is_blocked() -> None:
    assert ACCEPTANCE["dns_resolver"], "kein DNS-Kanarienresolver im Manifest - V7 nicht pruefbar"
    for resolver in ACCEPTANCE["dns_resolver"]:
        # §98 (M3): aussagekraeftig sind nur Abfragen, die tatsaechlich ein DNS-Paket senden
        assert probe("dnsquery", resolver, 53) == "zeitueberschreitung", resolver
        assert probe("nslookup", resolver) == "zeitueberschreitung", resolver
    # System-Aufloesung nur als Plausibilitaet (ohne Resolver scheitert sie ohne Paket); sie darf nie "erreicht" sein
    assert probe("dns", ACCEPTANCE["kanarienname"], 443) != "erreicht"


@target_env
def test_acceptance_firewall_export_from_outside_matches_the_policy() -> None:
    # §94: Manifest bettet Export und Politik ein (vom Host erzeugt, an Lauf/Politik/Pruefwerkzeug gebunden)
    export = ACCEPTANCE.get("firewall_export_inhalt") or json.loads(Path(ACCEPTANCE["firewall_export"]).read_text(encoding="utf-8"))
    policy = ACCEPTANCE.get("politik_inhalt") or json.loads(Path(ACCEPTANCE["politik"]).read_text(encoding="utf-8"))
    assert check_rules(export, policy) == []
