"""§90 (I12 v2): Belegbuendel der VM-Abnahme V1-V13 und automatisches Urteil.

Konzept: AUDIT_S89.md §2-§4. Das Urteil wird AUS DEN BELEGEN berechnet, nie aus einer Markierung: eine Bestaetigung,
die "PASS" behauptet, ohne dass die Pflichtbelege das tragen, wird abgelehnt.

Belegbuendel = Verzeichnis mit `manifest.json` und den Protokolldateien. Das Manifest bindet das Buendel an Lauf,
Maschine, Netzwerkpolitik und Pruefwerkzeug und fuehrt je Datei SHA-256, Test, Art, Erzeuger, Nachweisklasse und
Zeitpunkt. Die Signatur der Bestaetigung deckt den SHA-256 des Manifests ab -> jede Datei ist mittelbar signiert.

Nachweisklassen (aus dem Erzeuger abgeleitet, eine abweichende Angabe ist ein Widerspruch):
- E0 Selbsttest innerhalb der geprueften Grenze (VM), E1 Beobachtung vom Host ausserhalb der VM, E2 fremdes System.
Ehrliche Grenze: Der Erzeuger steht im Manifest; ob ein E1-Beleg wirklich vom Host stammt, belegt erst die Signatur des
Owners auf dem Host (owner_sign.py) - Software kann die Herkunft einer Datei nicht beweisen.

Ergebnisse je Test (objektiv):
- NICHT DURCHGEFUEHRT: kein einziger Beleg fuer den Test;
- BLOCKIERT: Pflichtbeleg oder geforderte Nachweisklasse fehlt, Beleg unlesbar/widerspruechlich, Buendel ungueltig;
- FAIL: ein Kriterium verletzt ODER uneindeutig (z. B. uebersprungener Test, fehlendes Kriterium, Kanarienlog ohne
  Abdeckung des Pruefzeitraums);
- PASS: alle Pflichtbelege vorhanden, gueltig, in der geforderten Klasse und alle Kriterien erfuellt."""

from __future__ import annotations

import hashlib
import ipaddress
import json
from datetime import datetime, timedelta
from pathlib import Path

PASS, FAIL, BLOCKIERT, NICHT_DURCHGEFUEHRT = "PASS", "FAIL", "BLOCKIERT", "NICHT DURCHGEFUEHRT"
STATUSES = (PASS, FAIL, BLOCKIERT, NICHT_DURCHGEFUEHRT)
MANIFEST_SCHEMA = "lexono-isolation-evidence/1"
MANIFEST_NAME = "manifest.json"
MAX_EVIDENCE_AGE = timedelta(hours=24)
#: §91: Hoechstalter je Belegart zum NUTZUNGSZEITPUNKT (Gate vor Start und vor jedem Aufruf). Alle Arten beschreiben
#: veraenderlichen Zustand (Port-ACL, Listener, Gastkonfiguration, Image) bzw. sind daran gebunden -> nicht laenger
#: als 24 h ab Erzeugung; damit endet eine Bestaetigung spaetestens 24 h nach ihrem aeltesten Beleg (vorher bis 48 h).
MAX_EVIDENCE_AGE_AT_USE = {"firewall_export": timedelta(hours=24), "kanarien": timedelta(hours=24),
                           "gegenprobe": timedelta(hours=24),
                           "pytest": timedelta(hours=24), "kriterien": timedelta(hours=24)}
CLOCK_SKEW = timedelta(minutes=5)
#: §98 (N5): zulaessige Abweichung der VM-Uhr von der Host-Uhr (aus den Phasen-Fenstern des Hosts geschaetzt)
MAX_UHRENABWEICHUNG = timedelta(minutes=5)
#: §96 (H1): Mindest-Ruhezeit (monoton gemessen) ohne neue Treffer, bevor eine Listener-Phase endet
MIN_NACHLAUF_S = 2.0
#: §96 (N2): exakte Ablehnungsmeldung des Gates fuer eine abgelaufene Bestaetigung (kein Teilstring-Vergleich)
MSG_ATTESTATION_EXPIRED = "Bestaetigung abgelaufen oder noch nicht gueltig"
#: §96 (H2): Ergebnisse einer Probe. NUR "zeitueberschreitung" ist mit einer verwerfenden Sperre vereinbar; "abgelehnt"
#: heisst, dass das Paket das Ziel erreicht hat (Port zu, Pfad offen).
PROBE_RESULTS = ("erreicht", "abgelehnt", "zeitueberschreitung", "fehler")
REQUIRED_TESTS = tuple(f"V{i}" for i in range(1, 14))
KLASSEN = {"E0": 0, "E1": 1, "E2": 2}
#: Erzeuger -> Nachweisklasse (fest; nicht frei waehlbar)
ERZEUGER = {"vm-abnahme": "E0", "host-abnahme": "E1", "endpunkt-protokoll": "E2"}
ARTEN = ("pytest", "kanarien", "firewall_export", "kriterien", "gegenprobe")
DNS_PORTS = {53, 853, 5353, 5355, 137}

S86 = "tests/test_s86_isolation_design.py::"
_CANARY = S86 + "test_acceptance_canary_addresses_are_blocked"


def _pytest(*prefixes: str) -> dict:
    return {"art": "pytest", "klasse": "E0", "knoten": prefixes}


def _kriterien(klasse: str, *ids: str) -> dict:
    return {"art": "kriterien", "klasse": klasse, "kriterien": ids}


def _kanarien(*merkmale: str) -> dict:
    return {"art": "kanarien", "klasse": "E1", "merkmale": merkmale}


#: Pflichtbelege je Abnahmetest (isolation/vm_abnahme.json). Kriterien-IDs sind die objektiven Pruefpunkte des Solls.
ANFORDERUNGEN: dict[str, list[dict]] = {
    "V1": [{"art": "firewall_export", "klasse": "E1"}],
    "V2": [_kriterien("E0", "gast_firewall_alle_profile_aktiv", "gast_firewall_ausgehend_block",
                      "aenderung_durch_benchmark_konto_gescheitert", "benchmark_konto_ohne_administratorrechte",
                      # §96 (M4, N6, N7)
                      "zustand_nach_aenderungsversuch_unveraendert", "arbeitsbaum_sauber",
                      "repository_schreibgeschuetzt", "laeuft_als_benchmark_konto"),
           _kriterien("E1", "gast_richtlinie_vom_host_exportiert")],
    "V3": [_pytest(S86 + "test_acceptance_approved_test_endpoint_is_reachable"),
           _kriterien("E2", "eingehende_verbindung_protokolliert", "keine_weiterleitung_an_modell_endpunkt")],
    "V4": [_pytest(_CANARY + "[tcp]", _CANARY + "[curl]"), _kanarien("ipv4", "tcp")],
    "V5": [_pytest(_CANARY + "[tcp]"), _kanarien("ipv6")],
    "V6": [_pytest(_CANARY + "[udp]"), _kanarien("udp")],
    "V7": [_pytest(S86 + "test_acceptance_dns_is_blocked"), _kanarien("dns")],
    "V8": [_pytest(_CANARY + "[native]", _CANARY + "[curl]"), _kanarien("ipv4", "tcp")],
    "V9": [_kriterien("E1", "kein_zweiter_netzadapter", "keine_gemeinsamen_ordner", "keine_host_zwischenablage",
                      "kein_vpn", "kein_ausgehendes_rdp", "keine_updates_telemetrie_ntp_nach_aussen",
                      "adapter_am_abnahme_switch", "host_dienste_in_vorher_phase_geschuetzt",  # §96 (M2, M8)
                      # §98 (M8/M9): Adapter-Schutz und Hyper-V-Kanaele neben dem Netzwerk
                      "mac_spoofing_aus", "dhcp_guard_an", "router_guard_an", "integrationsdienste_minimal"),
           _kriterien("E0", "genau_ein_netzadapter_in_der_vm", "standardgateway_wie_soll", "kein_dns_server_in_der_vm",
                      "keine_routen_ueber_fremde_gateways")],
    "V10": [_pytest(S86 + "test_acceptance_firewall_export_from_outside_matches_the_policy"),
            _kriterien("E1", "export_ausserhalb_der_vm_erzeugt", "aenderung_aus_der_vm_an_hostregeln_gescheitert")],
    "V11": [_kriterien("E1", "golden_image_zurueckgesetzt", "image_hash_vorher_gleich_nachher",
                       "vm_nutzt_kinddatentraeger", "kind_haengt_am_golden_image", "golden_image_schreibgeschuetzt"),
            _kriterien("E0", "kurzpruefung_v1_bis_v10_bestanden")],
    "V12": [_kanarien("ipv4", "ipv6", "udp", "dns"), _kriterien("E1", "auswertung_ausserhalb_des_benchmarks")],
    "V13": [_pytest("tests/test_s87_isolation_gate.py::", "tests/test_s90_owner_gate.py::"),
            _kriterien("E1", "produktiv_gate_lehnt_veraltete_bestaetigung_ab")],
}
assert tuple(ANFORDERUNGEN) == REQUIRED_TESTS

#: §92 (A4): Kanaele (Adressfamilie/Protokoll), deren Sperre ein Test belegt. Fuer JEDEN Kanal muss die Gegenprobe zeigen,
#: dass das Kanarienziel VOR Aktivierung der Port-ACL ueber den VM-Pfad erreichbar war - erst dann ist ein spaeteres
#: "kein Treffer" der ACL zurechenbar. Ein kalibrierter Kanal belegt nichts fuer einen anderen Kanal oder ein anderes Ziel.
KANAELE: dict[str, tuple[str, ...]] = {
    "V4": ("ipv4/tcp",), "V5": ("ipv6/tcp", "ipv6/udp"), "V6": ("ipv4/udp",), "V7": ("ipv4/dns",),
    "V8": ("ipv4/tcp",), "V12": ("ipv4/tcp", "ipv6/tcp", "ipv4/udp", "ipv6/udp", "ipv4/dns"),
}
for _test, _kanaele in KANAELE.items():
    if _test != "V12":  # V12 ist die Host-Auswertung - ohne eigene VM-Proben
        ANFORDERUNGEN[_test].append({"art": "gegenprobe", "klasse": "E0", "kanaele": _kanaele, "genau": True})
    ANFORDERUNGEN[_test].append({"art": "gegenprobe", "klasse": "E1", "kanaele": _kanaele, "genau": True})


def channels(target: dict) -> set[str]:
    """Kanaele eines Kanarienziels, z. B. {"ipv4/udp", "ipv4/dns"} fuer UDP auf Port 53."""
    family = "ipv6" if ipaddress.ip_address(target["ip"]).version == 6 else "ipv4"
    protocol = str(target.get("protokoll", "")).lower()
    result = {f"{family}/{protocol}"}
    if protocol == "dns" or int(target.get("port", 0)) in DNS_PORTS:
        result.add(f"{family}/dns")
    return result


def _key(target: dict) -> str:
    return f"{target['ip']}:{target['port']}"


# --- Regelpruefer (aus §86 hierher verlagert; tests/test_s86_isolation_design.py nutzt ihn weiter) --------------------------


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
            try:
                allowed.setdefault(str(ipaddress.ip_address(ip)), set()).add(int(freigabe["port"]))
            except ValueError:
                errors.append(f"Freigabe {ip!r} ist keine IP-Adresse - kein Start")
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
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                errors.append(f"Regel {name}: Zieladresse {address!r} ist keine IP-Adresse")
                continue
            if ip.is_loopback and loopback_ok:
                continue
            if str(ip) not in allowed or not {int(p) for p in ports} <= allowed[str(ip)]:
                errors.append(f"Regel {name}: {address}:{sorted(ports)} ist nicht freigegeben")
    return errors


# --- §91: Ableitung aus Beobachtungen --------------------------------------------------------------------------------------
# Ein Beleg enthaelt getrennt `soll` und `beobachtung` (Rohdaten des Erzeugers). Kriterien, pytest-Ergebnisse und der
# Firewall-Export werden HIER aus der Beobachtung abgeleitet - eine im Beleg mitgeschriebene Bewertung (z. B. ein von Hand
# gesetztes `true`/"passed"/PASS) zaehlt nicht; widerspricht sie der Ableitung, ist der Beleg widerspruechlich (BLOCKIERT).

ADMIN_SID = "S-1-5-32-544"
_ANY = {"", "*", "any"}


def _hex64(value) -> bool:  # noqa: ANN001
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def junit_results(xml_text: str) -> dict[str, str]:
    """pytest-JUnit-XML -> {nodeid: passed|failed|error|skipped}. DTD/Entitaeten werden abgelehnt (untrusted input)."""
    import xml.etree.ElementTree as ET  # noqa: N817, S405 - ohne DTD/Entitaeten (unten abgelehnt)

    if not isinstance(xml_text, str) or "<!DOCTYPE" in xml_text.upper() or "<!ENTITY" in xml_text.upper():
        raise ValueError("JUnit-XML fehlt oder enthaelt DTD/Entitaeten")
    root = ET.fromstring(xml_text)  # noqa: S314
    results: dict[str, str] = {}
    for case in root.iter("testcase"):
        nodeid = case.get("classname", "").replace(".", "/") + ".py::" + case.get("name", "")
        if case.find("failure") is not None:
            outcome = "failed"
        elif case.find("error") is not None:
            outcome = "error"
        elif case.find("skipped") is not None:
            outcome = "skipped"
        else:
            outcome = "passed"
        if nodeid in results and results[nodeid] != outcome:
            raise ValueError(f"widerspruechliche Ergebnisse fuer {nodeid}")
        results[nodeid] = outcome
    if not results:
        raise ValueError("JUnit-XML ohne Testfaelle")
    return results


def _acl_address(value) -> str:  # noqa: ANN001
    text = str(value or "").strip()
    if text.lower() in _ANY:
        return "any"
    if "/" in text:
        address, prefix = text.split("/", 1)
        if prefix == ("128" if ":" in address else "32"):
            return address  # Einzeladresse
    return text


def acl_to_export(acl: list) -> dict:
    """Hyper-V Port-ACLs (Get-VMNetworkAdapterExtendedAcl, als JSON) -> Exportformat von `check_rules`.

    Standard "Block" nur, wenn je eine ausgehende Deny-Regel fuer ALLE IPv4- und ALLE IPv6-Ziele (alle Protokolle, alle
    Ports) existiert. Jede ausgehende Allow-Regel wird als Regel uebernommen und von `check_rules` streng geprueft.
    Eingehende Regeln sind fuer den Abfluss nicht massgeblich (zustandsbehaftete Antworten). Ob Hyper-V das so durchsetzt,
    belegen erst die Kanarien (V4-V8, V12) - der Export allein ist kein Wirksamkeitsnachweis."""
    if not isinstance(acl, list):
        raise TypeError("ACL-Export ist keine Liste")
    outbound = [a for a in acl if str(a.get("Direction")).lower() == "outbound"]
    allows = [a for a in outbound if str(a.get("Action")).lower() == "allow"]
    deny_all = [a for a in outbound if str(a.get("Action")).lower() == "deny"
                and str(a.get("Protocol") or "").strip().lower() in _ANY
                and str(a.get("RemotePort") or "").strip().lower() in _ANY]

    def covers(full: str) -> bool:
        return any(str(d.get("RemoteIPAddress") or "").strip().lower() in _ANY | {full} for d in deny_all)

    blocked = covers("0.0.0.0/0") and covers("::/0")
    rules = [{"name": f"port-acl-{i}", "enabled": True, "direction": "Outbound", "action": "Allow",
              "protocol": str(a.get("Protocol") or "any"), "remote_addresses": [_acl_address(a.get("RemoteIPAddress"))],
              "remote_ports": [str(a.get("RemotePort") or "any")]} for i, a in enumerate(allows)]
    return {"profiles": [{"name": "hyperv-port-acl", "enabled": bool(acl), "default_outbound": "Block" if blocked else "Allow"}],
            "rules": rules}


def _junit_all_passed(b: dict) -> bool | None:
    try:
        results = junit_results(b.get("junit_xml"))
    except Exception:  # noqa: BLE001 - nicht auswertbar = unklar
        return None
    if any(v in ("failed", "error") for v in results.values()):
        return False
    return True if all(v == "passed" for v in results.values()) else None


def _acl_egress_only_endpoint(b: dict, policy: dict) -> bool | None:
    try:
        return not check_rules(acl_to_export(b["acl"]), policy)
    except Exception:  # noqa: BLE001
        return None


def _param(soll: dict | None, key: str):  # noqa: ANN202
    parameter = (soll or {}).get("parameter")
    return parameter.get(key) if isinstance(parameter, dict) else None


def _norm_path(value) -> str | None:  # noqa: ANN001
    import ntpath

    return ntpath.normcase(ntpath.normpath(value)) if isinstance(value, str) and value else None


def _v2_e0(b: dict, _policy: dict, soll: dict | None = None) -> dict:
    profiles, attempt, sids = b.get("firewall_profile"), b.get("aenderungsversuch"), b.get("konto_gruppen_sids")
    out: dict = dict.fromkeys(("gast_firewall_alle_profile_aktiv", "gast_firewall_ausgehend_block"))
    if isinstance(profiles, list) and profiles and all(isinstance(p, dict) for p in profiles):
        out["gast_firewall_alle_profile_aktiv"] = {"Domain", "Private", "Public"} <= {p.get("name") for p in profiles} \
            and all(p.get("enabled") is True for p in profiles)
        out["gast_firewall_ausgehend_block"] = all(str(p.get("default_outbound")).lower() == "block" for p in profiles)
    attempt = attempt if isinstance(attempt, dict) else {}
    code = attempt.get("exit_code")
    out["aenderung_durch_benchmark_konto_gescheitert"] = (code != 0) if type(code) is int else None
    before, after, rollback = attempt.get("vorher"), attempt.get("nachher"), attempt.get("rueckbau_exit")
    if not isinstance(before, str) or not isinstance(after, str) or type(code) is not int:
        out["zustand_nach_aenderungsversuch_unveraendert"] = None
    else:  # gelungener Versuch muss zurueckgenommen sein - ein Rueckbaufehler verhindert PASS
        out["zustand_nach_aenderungsversuch_unveraendert"] = before == after and (code != 0 or rollback == 0)
    out["benchmark_konto_ohne_administratorrechte"] = (ADMIN_SID not in sids) if isinstance(sids, list) and sids else None
    git = b.get("arbeitsbaum")
    git = git if isinstance(git, dict) else {}
    out["arbeitsbaum_sauber"] = (git.get("porcelain") == "") if git.get("exit_code") == 0 and \
        isinstance(git.get("porcelain"), str) else None
    write = b.get("schreibtest")
    write = write if isinstance(write, dict) else {}
    if isinstance(write.get("geprueft"), int) and write["geprueft"] > 0 and \
            isinstance(write.get("beschreibbar"), list):
        out["repository_schreibgeschuetzt"] = not write["beschreibbar"]
    else:
        out["repository_schreibgeschuetzt"] = None
    expected, actual = _param(soll, "benchmark_konto"), b.get("konto_name")
    out["laeuft_als_benchmark_konto"] = (str(actual).split("\\")[-1].lower() == str(expected).lower()) \
        if isinstance(expected, str) and expected and isinstance(actual, str) and actual else None
    return out


def _v2_e1(b: dict, _policy: dict, _soll: dict | None = None) -> dict:
    actual, target = b.get("gast_richtlinie_sha256"), b.get("golden_image_richtlinie_sha256")
    return {"gast_richtlinie_vom_host_exportiert": (actual == target) if _hex64(actual) and _hex64(target) else None}


def _v3_e2(b: dict, _policy: dict, _soll: dict | None = None) -> dict:
    entries, vm_ip, window = b.get("eintraege"), b.get("vm_ip"), b.get("fenster")
    try:
        low, high = _time(window[0]), _time(window[1])
        own = [e for e in entries if e.get("quelle_ip") == vm_ip and low <= _time(e["zeit"]) <= high]
    except Exception:  # noqa: BLE001
        return dict.fromkeys(("eingehende_verbindung_protokolliert", "keine_weiterleitung_an_modell_endpunkt"))
    return {"eingehende_verbindung_protokolliert": bool(own),
            "keine_weiterleitung_an_modell_endpunkt": all(e.get("weitergeleitet") is False for e in own) if own else None}


#: §98 (M8): eingeschaltet erlaubte Hyper-V-Integrationsdienste (englische und deutsche Anzeigenamen). Jeder andere
#: eingeschaltete Dienst (z. B. Gastdienstschnittstelle, Austausch von Schluesselwertpaaren, VSS) -> FAIL. Namen anderer
#: Sprachversionen sind NICHT VERIFIZIERT und fuehren zu FAIL, nicht zu PASS.
ERLAUBTE_INTEGRATIONSDIENSTE = {"Heartbeat", "Takt", "Shutdown", "Herunterfahren", "Time Synchronization",
                                "Zeitsynchronisierung"}
#: §98: Pfade, die diese Abnahme NICHT prueft - werden im Urteil ausdruecklich als "nicht abgedeckt" ausgewiesen
NICHT_ABGEDECKT = (
    "ICMP und andere IP-Protokolle als TCP/UDP",
    "Ziele ausser den Host-Kanarien (Internet, andere Hosts/Ports) - nur per Schluss aus der Deny-all-Regel",
    "Nicht-IP-Frames, Broadcast/Multicast (DHCP, LLMNR, mDNS, NetBIOS) und IPv6-Link-Local",
    "DNS ueber TCP, DoT/DoH, IPv6-DNS",
    "Hyper-V-Sockets (hvsocket/VMBus) und weitere Integrationskanaele - nur Konfiguration geprueft, keine Probe",
    "Verhalten nach Ende des Pruefzeitraums (Belege gelten hoechstens 24 h)",
)

#: §96 (M8): Host-Dienste, die in der Vorher-Phase (ohne ACL) ueber vEthernet erreichbar waeren
RISKANTE_HOST_PORTS = {"135", "139", "445", "623", "16992", "16993", "5040", "7680", "5985", "5986", "3389"}


def _risky_rules(rules: list, category: str, iface: str) -> list | None:
    """Eingehende Allow-Regeln, die in der Vorher-Phase RPC/SMB/AMT/WinRM/RDP u. a. auf vEthernet oeffnen wuerden.
    Unklare Regelangaben -> None (nie "geschuetzt")."""
    risky = []
    for rule in rules:
        if not isinstance(rule, dict):
            return None
        ports = [str(p) for p in (rule.get("LocalPort") or [])] if isinstance(rule.get("LocalPort"), list) else None
        aliases = [str(a) for a in (rule.get("InterfaceAlias") or [])] if isinstance(rule.get("InterfaceAlias"), list) else None
        profile = str(rule.get("Profile", ""))
        if ports is None or aliases is None or not ports:  # §98 (M7): leere/unlesbare Portliste = unklar
            return None
        applies = (profile in ("Any", "") or category in profile) and (not aliases or "Any" in aliases or iface in aliases)
        dangerous = any(p in RISKANTE_HOST_PORTS or p in ("Any", "RPC", "RPCEPMap") or "-" in p for p in ports)
        if applies and dangerous:
            risky.append(rule.get("Name"))
    return risky


def _v9_e1(b: dict, policy: dict, soll: dict | None = None) -> dict:
    adapters, egress = b.get("vm_netzadapter"), _acl_egress_only_endpoint(b, policy)
    flag = b.get("gastdienste_aktiv"), b.get("erweiterte_sitzung")
    switch, expected = b.get("switch"), _param(soll, "switch")
    on_switch = None
    if isinstance(adapters, list) and isinstance(switch, dict) and isinstance(expected, str) and expected:
        on_switch = (len(adapters) == 1 and isinstance(adapters[0], dict) and adapters[0].get("SwitchName") == expected
                     and switch.get("Name") == expected and switch.get("SwitchType") == "Internal")
    guard = b.get("vethernet_schutz")
    protected = None
    if isinstance(guard, dict) and isinstance(guard.get("regeln"), list) and isinstance(expected, str) and expected:
        risky = _risky_rules(guard["regeln"], str(guard.get("kategorie")), f"vEthernet ({expected})")
        if risky is None:
            protected = None
        elif risky or guard.get("standard_eingehend") == "Allow":
            protected = False
        elif guard.get("standard_eingehend") == "Block":
            protected = True  # "NotConfigured" bleibt unklar: Wirkung nicht eindeutig
    adapter = adapters[0] if isinstance(adapters, list) and len(adapters) == 1 and isinstance(adapters[0], dict) else None

    def setting(key: str, wanted: str) -> bool | None:
        value = adapter.get(key) if adapter else None
        return (value == wanted) if value in ("On", "Off") else None

    services = b.get("integrationsdienste")
    minimal = None
    if isinstance(services, list) and services and all(isinstance(x, dict) and isinstance(x.get("Enabled"), bool)
                                                       for x in services):
        minimal = all(x.get("Name") in ERLAUBTE_INTEGRATIONSDIENSTE for x in services if x["Enabled"])
    return {"kein_zweiter_netzadapter": (len(adapters) == 1) if isinstance(adapters, list) else None,
            "mac_spoofing_aus": setting("MacAddressSpoofing", "Off"), "dhcp_guard_an": setting("DhcpGuard", "On"),
            "router_guard_an": setting("RouterGuard", "On"), "integrationsdienste_minimal": minimal,
            "keine_gemeinsamen_ordner": (flag[0] is False) if isinstance(flag[0], bool) else None,
            "keine_host_zwischenablage": (flag[1] is False) if isinstance(flag[1], bool) else None,
            # VPN, RDP, Updates/Telemetrie/NTP brauchen Abfluss: nur der freigegebene Endpunkt darf erreichbar sein
            "kein_vpn": egress, "kein_ausgehendes_rdp": egress, "keine_updates_telemetrie_ntp_nach_aussen": egress,
            "adapter_am_abnahme_switch": on_switch, "host_dienste_in_vorher_phase_geschuetzt": protected}


#: Platzhalter-DNS von Windows ohne konfigurierten Server (veraltete Site-Local-Adressen) - zaehlen nicht als Server.
#: NICHT VERIFIZIERT, ob Get-NetIPConfiguration sie in der Abnahme-VM ausgibt.
WINDOWS_DNS_PLATZHALTER = {"fec0:0:0:ffff::1", "fec0:0:0:ffff::2", "fec0:0:0:ffff::3"}


def _v9_e0(b: dict, _policy: dict, soll: dict | None = None) -> dict:
    adapters, config, allowed = b.get("netzadapter"), b.get("ip_konfiguration"), _param(soll, "erlaubte_gateways")
    gateway = dns = None
    if isinstance(config, list) and all(isinstance(c, dict) for c in config):
        gateways = {g for c in config for g in (c.get("gateway4") or []) + (c.get("gateway6") or []) if g}
        servers = {d for c in config for d in (c.get("dns") or []) if d} - WINDOWS_DNS_PLATZHALTER
        gateway = gateways <= set(allowed) if isinstance(allowed, list) else None
        dns = not servers
    routes, foreign = b.get("routen"), None
    if isinstance(routes, list) and isinstance(allowed, list) and all(isinstance(r, dict) for r in routes):
        hops = {str(r.get("NextHop")) for r in routes} - {"0.0.0.0", "::", "", "None"}
        foreign = hops <= set(allowed)
    return {"genau_ein_netzadapter_in_der_vm": (len(adapters) == 1) if isinstance(adapters, list) else None,
            "standardgateway_wie_soll": gateway, "kein_dns_server_in_der_vm": dns,
            "keine_routen_ueber_fremde_gateways": foreign}


def _v10_e1(b: dict, _policy: dict, _soll: dict | None = None) -> dict:
    before, after = b.get("acl_vorher_sha256"), b.get("acl_nachher_sha256")
    return {"export_ausserhalb_der_vm_erzeugt": (b.get("export_erzeuger") == "host-abnahme") if _hex64(before) else None,
            "aenderung_aus_der_vm_an_hostregeln_gescheitert": (before == after) if _hex64(before) and _hex64(after) else None}


def _v11_e1(b: dict, _policy: dict, soll: dict | None = None) -> dict:
    reset = b.get("zuruecksetzen")
    code = reset.get("exit_code") if isinstance(reset, dict) else None
    hashes = [b.get(k) for k in ("image_sha256_soll", "image_sha256_vorher", "image_sha256_nachher")]
    child, parent = _norm_path(_param(soll, "kind_vhdx")), _norm_path(_param(soll, "eltern_vhdx"))
    disks, vhd = b.get("vm_datentraeger"), b.get("kind_vhd")
    uses_child = chained = None
    if child and isinstance(disks, list):
        uses_child = len(disks) == 1 and _norm_path(disks[0]) == child
    if child and parent and isinstance(vhd, dict):
        chained = (_norm_path(vhd.get("Path")) == child and _norm_path(vhd.get("ParentPath")) == parent
                   and vhd.get("VhdType") == "Differencing")
    protected = b.get("eltern_schreibgeschuetzt")
    return {"golden_image_zurueckgesetzt": (code == 0) if type(code) is int else None,
            "image_hash_vorher_gleich_nachher": (len(set(hashes)) == 1) if all(_hex64(h) for h in hashes) else None,
            "vm_nutzt_kinddatentraeger": uses_child, "kind_haengt_am_golden_image": chained,
            "golden_image_schreibgeschuetzt": protected if isinstance(protected, bool) else None}


def _v11_e0(b: dict, _policy: dict, _soll: dict | None = None) -> dict:
    return {"kurzpruefung_v1_bis_v10_bestanden": _junit_all_passed(b)}


def _v12_e1(b: dict, _policy: dict, _soll: dict | None = None) -> dict:
    logs = b.get("kanarien_logs_sha256")
    valid = isinstance(logs, list) and logs and all(_hex64(h) for h in logs)
    return {"auswertung_ausserhalb_des_benchmarks": (b.get("auswertung_durch") == "host-abnahme") if valid else None}


def _v13_e1(b: dict, _policy: dict, _soll: dict | None = None) -> dict:
    """§96 (N1, N2): Ohne gueltige, gepinnte Vertrauenskette oder ohne aktiven Schluessel ist ueber die Alterspruefung
    nichts belegt -> BLOCKIERT (Voraussetzung fehlt). Sonst muss genau die Ablaufmeldung des Gates vorliegen."""
    diagnosis = b.get("diagnose")
    if not isinstance(diagnosis, list):
        return {"produktiv_gate_lehnt_veraltete_bestaetigung_ab": None}
    if b.get("schluesselliste_gueltig") is not True or b.get("schluessel_status") != "aktiv":
        return {"produktiv_gate_lehnt_veraltete_bestaetigung_ab": "blockiert"}
    return {"produktiv_gate_lehnt_veraltete_bestaetigung_ab": MSG_ATTESTATION_EXPIRED in diagnosis}


#: (Test, Klasse) -> Ableitung der Kriterien aus der Beobachtung (fuer jede Kriterien-Anforderung genau eine)
KRITERIEN_ABLEITUNG = {("V2", "E0"): _v2_e0, ("V2", "E1"): _v2_e1, ("V3", "E2"): _v3_e2, ("V9", "E1"): _v9_e1,
                       ("V9", "E0"): _v9_e0, ("V10", "E1"): _v10_e1, ("V11", "E1"): _v11_e1, ("V11", "E0"): _v11_e0,
                       ("V12", "E1"): _v12_e1, ("V13", "E1"): _v13_e1}
assert set(KRITERIEN_ABLEITUNG) == {(t, r["klasse"]) for t, reqs in ANFORDERUNGEN.items() for r in reqs
                                    if r["art"] == "kriterien"}
#: Soll-Parameter, die eine Ableitung braucht (aus der Abnahme-Konfiguration; fehlen sie -> unklar)
SOLL_PARAMETER = {("V2", "E0"): ("benchmark_konto",), ("V9", "E0"): ("erlaubte_gateways",), ("V9", "E1"): ("switch",),
                  ("V11", "E1"): ("kind_vhdx", "eltern_vhdx")}
KANARIEN_FELDER = ("ziele", "treffer", "kontrolle", "aktiv_von", "aktiv_bis", "ausserhalb_der_phasen", "abschluss",
                   "phasen_token", "kontrollen", "firewall_kontrollen")
#: §98 (H1): Pflicht-Zeitpunkte der Listener- und Firewall-Kontrollen
KONTROLLPUNKTE = {"vorher": ("vorher_ende",), "nachher": ("nachher_start", "nachher_ende")}


def derive(test: str, art: str, klasse: str, beobachtung: dict, policy: dict, soll: dict | None = None) -> dict:
    """Bewertbare Felder aus der Beobachtung (und Soll-Parametern). Fehler -> leere bzw. unklare Werte (nie PASS)."""
    if art == "kriterien":
        deriver = KRITERIEN_ABLEITUNG.get((test, klasse))
        return {"kriterien": deriver(beobachtung, policy, soll) if deriver else {}}
    if art == "pytest":
        try:
            return {"ergebnisse": junit_results(beobachtung.get("junit_xml"))}
        except Exception:  # noqa: BLE001
            return {"ergebnisse": {}}
    if art == "firewall_export":
        try:
            return {"export": acl_to_export(beobachtung["acl"]) if "acl" in beobachtung else beobachtung["export"]}
        except Exception:  # noqa: BLE001
            return {"export": None}
    if art == "gegenprobe":
        keys = ("vm_quelle_ips", "versuche", "phasen_token") if klasse == "E0" else (
            "ziele", "kontrolle", "treffer", "aktiv_von", "aktiv_bis", "vm_quelle_ips", "acl_sha256", "nachlauf_s",
            "phasen_token", "kontrollen", "firewall_kontrollen")
        return {k: beobachtung.get(k) for k in keys}
    return {k: beobachtung.get(k) for k in KANARIEN_FELDER}  # kanarien


def lauf_sha256(lauf: dict) -> str:
    return hashlib.sha256(json.dumps(lauf, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                          ).hexdigest()


# --- Auswertung einzelner Belege ------------------------------------------------------------------------------------------
# Rueckgabe: ("erfuellt" | "verletzt" | "unklar", Grund)


def _eval_pytest(content: dict, req: dict, _ctx: dict) -> tuple[str, str]:
    results = content.get("ergebnisse")
    if not isinstance(results, dict) or not results:
        return "unklar", "pytest-Protokoll ohne Ergebnisse"
    for prefix in req["knoten"]:
        matches = {k: v for k, v in results.items() if k.startswith(prefix)}
        if not matches:
            return "unklar", f"Pflichttest {prefix} nicht im Protokoll"
        if any(v in ("failed", "error") for v in matches.values()):
            return "verletzt", f"Pflichttest {prefix} fehlgeschlagen"
        if any(v != "passed" for v in matches.values()):
            return "unklar", f"Pflichttest {prefix} nicht bestanden (uebersprungen/unbekannt)"
    return "erfuellt", ""


def _eval_kriterien(content: dict, req: dict, _ctx: dict) -> tuple[str, str]:
    criteria = content.get("kriterien")
    if not isinstance(criteria, dict):
        return "unklar", "Kriterienprotokoll ohne Kriterien"
    for cid in req["kriterien"]:
        value = criteria.get(cid)
        if value is False:
            return "verletzt", f"Kriterium {cid} verletzt"
        if value == "blockiert":
            return "blockiert", f"Kriterium {cid}: Voraussetzung fehlt (z. B. keine gueltige Vertrauenskette)"
        if value is not True:
            return "unklar", f"Kriterium {cid} nicht eindeutig erfuellt ({value!r})"
    return "erfuellt", ""


def _canary_features(targets: list) -> set[str]:
    features: set[str] = set()
    for target in targets:
        ip = ipaddress.ip_address(target["ip"])
        features.add("ipv6" if ip.version == 6 else "ipv4")
        protocol = str(target.get("protokoll", "")).lower()
        features.add(protocol)
        if int(target.get("port", 0)) in DNS_PORTS or protocol == "dns":
            features.add("dns")
    return features


def _token_ok(token) -> bool:  # noqa: ANN001
    return isinstance(token, dict) and all(isinstance(token.get(k), str) and len(token[k]) == 32
                                           and all(c in "0123456789abcdef" for c in token[k])
                                           for k in ("start", "vm_abschluss")) and token["start"] != token["vm_abschluss"]


def _abschluss_ok(content: dict) -> str | None:
    """§96 (H1): Listener-Phase vollstaendig abgeschlossen? Rueckgabe = Grund, wenn nicht."""
    done = content.get("abschluss")
    if not isinstance(done, dict) or done.get("geleert") is not True or done.get("threads_beendet") is not True:
        return "Listener nicht vollstaendig abgeschlossen (Sockets geleert, Threads beendet)"
    if not isinstance(done.get("nachlauf_s"), (int, float)) or done["nachlauf_s"] < MIN_NACHLAUF_S:
        return f"Nachlauf ohne Treffer kuerzer als {MIN_NACHLAUF_S} s - spaete Treffer nicht ausgeschlossen"
    return None


def _kontroll_problem(content: dict, phase: str) -> str | None:
    """§98 (H1): Listener und eigene Firewall-Regeln muessen zu jedem Pflicht-Zeitpunkt (und bei jeder Zwischenkontrolle)
    nachweislich funktioniert bzw. unveraendert bestanden haben. Sonst ist "kein Treffer" nicht der ACL zurechenbar."""
    try:
        targets = {_key(t) for t in content["ziele"]}
        listener, firewall = content.get("kontrollen"), content.get("firewall_kontrollen")
        if not isinstance(listener, list) or not isinstance(firewall, list):
            return "Listener- bzw. Firewall-Kontrollen fehlen"
        for point in KONTROLLPUNKTE[phase]:
            if not any(c.get("zeitpunkt") == point for c in listener):
                return f"Listener-Kontrolle {point} fehlt"
            if not any(c.get("zeitpunkt") == point for c in firewall):
                return f"Firewall-Kontrolle {point} fehlt"
        for check in listener:
            missing = targets - set(check.get("ok") or [])
            if missing:
                return f"Listener reagierte bei Kontrolle {check.get('zeitpunkt')} nicht ({', '.join(sorted(missing)[:3])})"
        for check in firewall:
            if check.get("gleich") is not True:
                return f"eigene Firewall-Regeln bei Kontrolle {check.get('zeitpunkt')} fehlend/veraendert/unlesbar"
        hashes = {c.get("ist_sha256") for c in firewall}
        if len(hashes) != 1 or not _hex64(next(iter(hashes))):
            return "eigene Firewall-Regeln waehrend der Phase veraendert - Ergebnis ungueltig"
    except (KeyError, TypeError, AttributeError):
        return "Kontrollen nicht auswertbar"
    return None


def _eval_kanarien(content: dict, req: dict, _ctx: dict) -> tuple[str, str]:
    hits, outside = content.get("treffer"), content.get("ausserhalb_der_phasen")
    if not isinstance(hits, list) or not isinstance(outside, list):
        return "unklar", "Kanarienlog ohne Trefferliste bzw. ohne Treffer ausserhalb der Phasen"
    if hits:
        return "verletzt", f"Kanarientreffer aus der VM ({len(hits)})"
    if outside:  # §96 (M1): Treffer waehrend ACL-Aktivierung/Phasenwechsel - nie unbeobachtet
        return "verletzt", f"Kanarientreffer ausserhalb der Phasen vorher/nachher ({len(outside)}, z. B. ACL-Aktivierung)"
    try:
        features = _canary_features(content["ziele"])
        _time(content["aktiv_von"]), _time(content["aktiv_bis"])
    except (KeyError, ValueError, TypeError):
        return "unklar", "Kanarienlog ohne gueltige Ziele bzw. Aktivzeitraum"
    controls = content.get("kontrolle")
    unchecked = [f"{t['ip']}:{t['port']}" for t in content["ziele"]
                 if not isinstance(controls, list) or f"{t['ip']}:{t['port']}" not in controls]
    if unchecked:
        return "unklar", f"Funktionskontrolle der Kanarien-Listener fehlt ({', '.join(unchecked[:3])})"
    missing = [m for m in req["merkmale"] if m not in features]
    if missing:
        return "unklar", f"Kanarienziele decken {', '.join(missing)} nicht ab"
    reason = _abschluss_ok(content)
    if reason:
        return "unklar", reason
    reason = _kontroll_problem(content, "nachher")
    if reason:
        return "blockiert", reason
    if not _token_ok(content.get("phasen_token")):
        return "unklar", "Kanarienlog ohne gueltige Phasen-Tokens (Start/VM-Abschluss)"
    return "erfuellt", ""


def _eval_firewall_export(content: dict, _req: dict, ctx: dict) -> tuple[str, str]:
    errors = check_rules(content.get("export"), ctx["policy"])
    return ("verletzt", "; ".join(errors[:3])) if errors else ("erfuellt", "")


def _reached_before(content: dict) -> dict[str, set[str]]:
    """Kanal -> Ziele mit Treffer aus der VM in der Vorher-Phase (Host-Beobachtung, Host-Uhr)."""
    low, high = _time(content["aktiv_von"]), _time(content["aktiv_bis"])
    sources = set(content["vm_quelle_ips"])
    hit_keys = {h["ziel"] for h in content["treffer"] if h.get("quelle") in sources and low <= _time(h["zeit"]) <= high}
    reached: dict[str, set[str]] = {}
    for target in content["ziele"]:
        if _key(target) in hit_keys:
            for channel in channels(target):
                reached.setdefault(channel, set()).add(_key(target))
    return reached


def _eval_gegenprobe(content: dict, req: dict, _ctx: dict) -> tuple[str, str]:
    """Einzelbeleg der Gegenprobe. "blockiert" = Voraussetzung fuer die Zurechnung der Sperre fehlt (-> BLOCKIERT)."""
    try:
        if not isinstance(content.get("vm_quelle_ips"), list) or not content["vm_quelle_ips"]:
            return "unklar", "Gegenprobe ohne VM-Quelladressen"
        if not _token_ok(content.get("phasen_token")):
            return "unklar", "Gegenprobe ohne gueltige Phasen-Tokens"
        if req["klasse"] == "E0":
            attempts = content["versuche"]
            for channel in req["kanaele"]:
                own = [a for a in attempts if channel in a["kanaele"]]
                if not own:
                    return "blockiert", f"Gegenprobe: Kanal {channel} wurde aus der VM nicht versucht"
                if not any(a["ergebnis"] == "erreicht" for a in own):  # §96 (H2): nur "erreicht" belegt den Pfad
                    results = ", ".join(sorted({str(a["ergebnis"]) for a in own}))
                    return "blockiert", (f"Gegenprobe: Kanal {channel} vor der ACL nicht erreicht ({results}) - "
                                         "spaetere Sperre nicht der ACL zurechenbar")
            return "erfuellt", ""
        if not isinstance(content["treffer"], list) or not isinstance(content["kontrolle"], list):
            return "unklar", "Gegenprobe-Log unvollstaendig (Treffer/Kontrolle)"
        if not _hex64(content.get("acl_sha256")):
            return "unklar", "Gegenprobe ohne Hash der ACL waehrend der Gegenprobe"
        if not isinstance(content.get("nachlauf_s"), (int, float)) or content["nachlauf_s"] < MIN_NACHLAUF_S:
            return "unklar", f"Vorher-Phase ohne Nachlauf von {MIN_NACHLAUF_S} s beendet - spaete Treffer moeglich"
        reason = _kontroll_problem(content, "vorher")
        if reason:
            return "blockiert", f"Gegenprobe: {reason}"
        reached = _reached_before(content)
        for channel in req["kanaele"]:
            targets = [t for t in content["ziele"] if channel in channels(t)]
            if not targets:
                return "blockiert", f"Gegenprobe: kein Kanarienziel fuer Kanal {channel}"
            dead = [_key(t) for t in targets if _key(t) not in content["kontrolle"]]
            if dead:
                return "blockiert", f"Gegenprobe: Listener ohne Funktionskontrolle ({', '.join(dead[:3])})"
            if channel not in reached:
                return "blockiert", (f"Gegenprobe: Kanal {channel} vor der ACL aus der VM nicht erreichbar (kein "
                                     "Treffer) - spaetere Sperre nicht der ACL zurechenbar")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return "unklar", f"Gegenprobe nicht auswertbar ({type(exc).__name__})"
    return "erfuellt", ""


def vm_clock_problem(contents: list) -> str | None:
    """§98 (N5): Die VM schreibt ihre Belege nachweislich (Phasen-Tokens) INNERHALB der vom Host beobachteten Phase.
    Daraus folgt fuer jeden VM-Beleg: Uhrenabweichung = VM-Zeit - Host-Zeit liegt in [v - Ende, v - Beginn]. Alle Intervalle
    zusammen muessen sich schneiden (sonst widerspruechlich) und duerfen hoechstens MAX_UHRENABWEICHUNG von 0 entfernt
    sein - sonst ist die VM-Uhr (Belegalter, Gate-Gueltigkeit in der VM) nicht vertrauenswuerdig -> BLOCKIERT."""
    try:
        calibration = {e["test"]: c for e, c in contents if e["art"] == "gegenprobe" and e["klasse"] == "E1"}
        after = [c for e, c in contents if e["art"] == "kanarien"]
        bounds = []
        for entry, _ in contents:
            if entry["klasse"] != "E0":
                continue
            if entry["art"] == "gegenprobe" and entry["test"] in calibration:
                window = calibration[entry["test"]]
            elif entry["art"] == "pytest" and after:
                window = after[0]
            else:
                continue
            value = entry["_erzeugt"]
            bounds.append((value - _time(window["aktiv_bis"]), value - _time(window["aktiv_von"])))
    except (KeyError, TypeError, ValueError):
        return "Zeitangaben der Phasen nicht auswertbar - Uhrenabgleich VM/Host unmoeglich"
    if not bounds:
        return None
    low, high = max(b[0] for b in bounds), min(b[1] for b in bounds)
    if low > high:
        return "Zeitangaben der VM-Belege widersprechen den Host-Phasen (Uhrensprung oder Manipulation)"
    if low > MAX_UHRENABWEICHUNG or high < -MAX_UHRENABWEICHUNG:
        return (f"VM-Uhr weicht mehr als {int(MAX_UHRENABWEICHUNG.total_seconds() // 60)} min von der Host-Uhr ab "
                "- Belegalter und Gueltigkeit in der VM nicht belastbar")
    return None


def _v11_cross(own: list, calibration_start) -> list[str]:  # noqa: ANN001
    """§94/§96: Die Ruecksetzung muss VOR dem Beginn der Gegenprobe liegen (beides Host-Uhr). Dass die VM-Belege danach
    entstanden, belegen die Phasen-Tokens (vom Host nach der Ruecksetzung erzeugt) - kein Vergleich mit der VM-Uhr."""
    resets = [c for e, c in own if e["art"] == "kriterien" and e["klasse"] == "E1"]
    if not resets:
        return []
    try:
        when = _time(resets[0]["beobachtung"]["zuruecksetzen"]["zeit"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return ["Zeitpunkt der Ruecksetzung fehlt oder ist ungueltig"]
    if calibration_start is None:
        return ["keine Gegenprobe nach der Ruecksetzung - Lauf nicht dem zurueckgesetzten System zuzuordnen"]
    if when >= calibration_start:
        return ["Ruecksetzung nach Beginn der Gegenprobe - Lauf nicht vom zurueckgesetzten System"]
    return []


def _v12_cross(own: list) -> list[str]:
    """§96 (N5): Die Host-Auswertung muss genau das Kanarienlog dieses Buendels betreffen."""
    logs = [c for e, c in own if e["art"] == "kanarien"]
    reports = [c for e, c in own if e["art"] == "kriterien"]
    if not logs or not reports:
        return []
    digest = hashlib.sha256(json.dumps(logs[0]["beobachtung"], ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()
    if digest not in (reports[0]["beobachtung"].get("kanarien_logs_sha256") or []):
        return ["V12-Auswertung bezieht sich nicht auf das Kanarienlog dieses Buendels"]
    return []


def _gegenprobe_cross(test: str, own: list, acl: dict | None, tokens: dict) -> list[str]:
    """Kette Vorher (Gegenprobe) -> ACL-Aktivierung (V1-Export) -> Nachher (Kanarien, Proben).

    §96 (M6): Reihenfolgen werden nur mit derselben Uhr (Host) oder ueber Phasen-Tokens belegt - nie Host- gegen VM-Uhr.
    Jede Luecke oder jeder Widerspruch -> Gruende fuer BLOCKIERT; leer = Kette schluessig."""
    e1 = [c for e, c in own if e["art"] == "gegenprobe" and e["klasse"] == "E1"]
    if not e1:
        return []  # fehlender Pflichtbeleg wird bereits gemeldet
    cal = e1[0]
    e0 = [c for e, c in own if e["art"] == "gegenprobe" and e["klasse"] == "E0"]
    post = [c for e, c in own if e["art"] == "kanarien"]
    reasons: list[str] = []
    try:
        cal_to = _time(cal["aktiv_bis"])
        reached = _reached_before(cal)
        if acl is None:
            reasons.append("ACL-Konfiguration (V1) fehlt - Nachher-Sperre keiner ACL zurechenbar")
        else:
            if acl["erzeugt"] < cal_to:
                reasons.append("ACL-Export vor Ende der Gegenprobe - Reihenfolge Vorher/ACL/Nachher verletzt")
            if acl["sha256"] == cal.get("acl_sha256"):
                reasons.append("ACL waehrend der Gegenprobe gleich der finalen ACL - Wirkung nicht der ACL zurechenbar")
        cal_fw = {c.get("ist_sha256") for c in cal.get("firewall_kontrollen") or []}
        for log in post:
            if {c.get("ist_sha256") for c in log.get("firewall_kontrollen") or []} != cal_fw or len(cal_fw) != 1:
                reasons.append("eigene Firewall-Regeln zwischen Vorher- und Nachher-Phase veraendert - Ergebnis ungueltig")
            if _time(log["aktiv_von"]) < cal_to:
                reasons.append("Vorher- und Nachher-Phase der Kanarien ueberlappen")
            if acl is not None and _time(log["aktiv_von"]) < acl["erzeugt"]:
                reasons.append("Nachher-Phase begann vor dem ACL-Export (Host-Uhr) - Reihenfolge verletzt")
            if log.get("phasen_token") == cal.get("phasen_token"):
                reasons.append("Vorher- und Nachher-Phase mit denselben Tokens - Phasen nicht getrennt")
            if tokens["nachher"] != [log.get("phasen_token")] * len(tokens["nachher"]) or not tokens["nachher"]:
                reasons.append("Nachher-Proben der VM tragen nicht die Phasen-Tokens des Kanarienlogs - "
                               "Proben nicht nachweislich innerhalb der beobachteten Nachher-Phase")
            for channel in KANAELE[test]:
                targets = [t for t in log["ziele"] if channel in channels(t)]
                if not targets:
                    reasons.append(f"Kanal {channel} im Nachher-Log nicht geprueft")
                unchecked = [_key(t) for t in targets if _key(t) not in reached.get(channel, set())]
                if unchecked:
                    reasons.append(f"Nachher-Ziel ohne erfolgreiche Gegenprobe (anderer Pfad/Port): {', '.join(unchecked[:3])}")
        for attempts in e0:
            if set(attempts["vm_quelle_ips"]) != set(cal["vm_quelle_ips"]):
                reasons.append("widerspruechlich: VM- und Host-Gegenprobe nennen verschiedene VM-Adressen")
            if attempts.get("phasen_token") != cal.get("phasen_token"):
                reasons.append("Gegenprobe der VM traegt nicht die Phasen-Tokens der Host-Vorher-Phase")
            hits = {k for keys in reached.values() for k in keys}
            # Widersprueche je Kanal des Tests: ein Kanal belegt nichts fuer einen anderen
            relevant = [a for a in attempts["versuche"] if set(a["kanaele"]) & set(KANAELE[test])]
            for attempt in relevant:
                result = attempt["ergebnis"]
                if result not in PROBE_RESULTS:
                    reasons.append(f"Gegenprobe-Versuch {attempt['ziel']} mit unklarem Ergebnis {result!r}")
                elif result == "erreicht" and attempt["ziel"] not in hits:
                    reasons.append(f"widerspruechlich: VM meldet {attempt['ziel']} erreicht, Host sah keinen Treffer")
                elif result != "erreicht" and attempt["ziel"] in hits:
                    reasons.append(f"widerspruechlich: VM meldet {attempt['ziel']} {result}, Host sah einen Treffer")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        reasons.append(f"Gegenprobe-Kette nicht auswertbar ({type(exc).__name__})")
    return reasons


_EVALUATORS = {"gegenprobe": _eval_gegenprobe, "pytest": _eval_pytest, "kriterien": _eval_kriterien, "kanarien": _eval_kanarien,
               "firewall_export": _eval_firewall_export}


# --- Buendel und Urteil ---------------------------------------------------------------------------------------------------


def _time(value) -> datetime:  # noqa: ANN001
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Zeitangabe ohne Zeitzone")
    return parsed


def _blocked_all(reasons: list[str], manifest_sha: str | None = None) -> dict:
    return {"gesamt": BLOCKIERT, "probleme": reasons, "manifest_sha256": manifest_sha, "firewall_export_sha256": None,
            "tests": {t: {"status": BLOCKIERT, "gruende": ["Belegbuendel ungueltig"]} for t in REQUIRED_TESTS}}


def overall(statuses: list[str]) -> str:
    """Gesamturteil: PASS nur, wenn ALLE PASS; sonst der schwerste Befund (FAIL > BLOCKIERT > NICHT DURCHGEFUEHRT)."""
    for status in (FAIL, BLOCKIERT, NICHT_DURCHGEFUEHRT):
        if status in statuses:
            return status
    return PASS if statuses and all(s == PASS for s in statuses) else BLOCKIERT


def evaluate(bundle: Path | None, *, policy: dict, lauf: dict, reference: datetime, now: datetime | None = None,
             allow_dry_run: bool = False) -> dict:
    """Urteil ueber ein Belegbuendel. `lauf` = erwartete Bindung (run_id, maschine, netzwerkpolitik_sha256,
    pruefwerkzeug); `reference` = Ausstellungszeitpunkt der Bestaetigung (Belege hoechstens 24 h davor); `now` =
    Nutzungszeitpunkt (§91: Belege auch dann hoechstens `MAX_EVIDENCE_AGE_AT_USE` alt - sonst bis zu 48 h);
    `allow_dry_run` = Belege im Modus "trockenlauf" zulassen (nur Test-Gates/Trockenlauf, nie produktiv)."""
    if bundle is None or not Path(bundle).is_dir():
        return {**_blocked_all(["kein Belegbuendel vorhanden"]),
                "tests": {t: {"status": NICHT_DURCHGEFUEHRT, "gruende": ["kein Beleg"]} for t in REQUIRED_TESTS},
                "gesamt": NICHT_DURCHGEFUEHRT}
    bundle = Path(bundle).resolve()
    manifest_path = bundle / MANIFEST_NAME
    try:
        manifest_raw = manifest_path.read_bytes()
        manifest = json.loads(manifest_raw.decode("utf-8"))
        entries = manifest["belege"]
        if not isinstance(entries, list):
            raise TypeError("belege")
    except Exception as exc:  # noqa: BLE001 - unlesbar = blockiert
        return _blocked_all([f"Manifest des Belegbuendels unlesbar ({type(exc).__name__})"])
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    problems: list[str] = []
    if manifest.get("schema") != MANIFEST_SCHEMA:
        problems.append("Manifest hat ein unbekanntes Schema")
    if manifest.get("lauf") != lauf:
        differing = sorted(k for k in set(lauf) | set(manifest.get("lauf") or {})
                           if (manifest.get("lauf") or {}).get(k) != lauf.get(k))
        problems.append(f"Belegbuendel gehoert zu anderem Lauf/Maschine/Politik/Pruefwerkzeug ({', '.join(differing)})")
    listed: set[Path] = set()
    contents: list[tuple[dict, dict]] = []
    for entry in entries:
        try:
            name, digest, test, art = entry["datei"], entry["sha256"], entry["test"], entry["art"]
            producer, klasse, created = entry["erzeuger"], entry["klasse"], _time(entry["erzeugt"])
        except (KeyError, TypeError, ValueError) as exc:
            problems.append(f"Manifesteintrag unvollstaendig ({type(exc).__name__})")
            continue
        if not all(isinstance(v, str) and v for v in (name, digest, test, art, producer, klasse)):
            problems.append("Manifesteintrag mit ungueltigen Feldtypen")
            continue
        path = (bundle / name).resolve()
        if not path.is_relative_to(bundle) or path == manifest_path:
            problems.append(f"Beleg {name!r} liegt ausserhalb des Buendels")
            continue
        if path in listed:
            problems.append(f"Beleg {name!r} doppelt im Manifest")
            continue
        listed.add(path)
        if test not in REQUIRED_TESTS or art not in ARTEN:
            problems.append(f"Beleg {name!r}: unbekannter Test bzw. unbekannte Art")
            continue
        if ERZEUGER.get(producer) != klasse:
            problems.append(f"Beleg {name!r}: Klasse {klasse!r} passt nicht zum Erzeuger {producer!r} (widerspruechlich)")
            continue
        if not reference - MAX_EVIDENCE_AGE <= created <= reference + CLOCK_SKEW:
            problems.append(f"Beleg {name!r} veraltet oder aus der Zukunft (erzeugt {entry['erzeugt']})")
            continue
        if now is not None and now - created > MAX_EVIDENCE_AGE_AT_USE[art]:
            problems.append(f"Beleg {name!r} zum Nutzungszeitpunkt veraltet (aelter als "
                            f"{int(MAX_EVIDENCE_AGE_AT_USE[art].total_seconds() // 3600)} h) - neue Abnahme noetig")
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            problems.append(f"Beleg {name!r} fehlt")
            continue
        if hashlib.sha256(raw).hexdigest() != digest:
            problems.append(f"Beleg {name!r} manipuliert (SHA-256 weicht vom Manifest ab)")
            continue
        try:
            content = json.loads(raw.decode("utf-8"))
        except ValueError:
            problems.append(f"Beleg {name!r} unlesbar")
            continue
        if not isinstance(content, dict) or content.get("art") != art or content.get("test") != test:
            problems.append(f"Beleg {name!r}: Inhalt widerspricht dem Manifest (Art/Test)")
            continue
        umgebung = content.get("umgebung")
        if content.get("erzeugt") != entry["erzeugt"] or not isinstance(umgebung, dict) \
                or umgebung.get("erzeuger") != producer:
            problems.append(f"Beleg {name!r}: Zeit bzw. Erzeuger im Beleg widerspricht dem Manifest")
            continue
        if umgebung.get("modus") not in (("real", "trockenlauf") if allow_dry_run else ("real",)):
            problems.append(f"Beleg {name!r}: Modus {umgebung.get('modus')!r} - Trockenlauf-Belege zaehlen nicht produktiv")
            continue
        if content.get("lauf_sha256") != lauf_sha256(lauf):
            problems.append(f"Beleg {name!r}: an anderen Lauf/Konfiguration gebunden (lauf_sha256)")
            continue
        if not isinstance(content.get("soll"), dict) or not isinstance(content.get("beobachtung"), dict):
            problems.append(f"Beleg {name!r}: Soll und Beobachtung nicht getrennt erfasst")
            continue
        derived = derive(test, art, klasse, content["beobachtung"], policy, content["soll"])
        stated = {k: content[k] for k in derived if k in content}
        if any(stated[k] != derived[k] for k in stated):
            problems.append(f"Beleg {name!r}: mitgeschriebene Bewertung widerspricht der Beobachtung "
                            f"({', '.join(sorted(k for k in stated if stated[k] != derived[k]))}) - nicht verwertbar")
            continue
        contents.append(({**entry, "_erzeugt": created}, {**content, **derived}))
    modes = {c["umgebung"].get("modus") for _, c in contents}
    if len(modes) > 1:
        problems.append(f"Belegbuendel mischt Modi ({', '.join(sorted(map(str, modes)))}) - nicht verwertbar")
    unlisted = sorted(str(p.relative_to(bundle)) for p in bundle.rglob("*")
                      if p.is_file() and p.resolve() != manifest_path and p.resolve() not in listed)
    if unlisted:
        problems.append(f"nicht im Manifest gefuehrte Dateien im Buendel ({', '.join(unlisted[:3])})")
    if problems:
        return _blocked_all(problems, manifest_sha)

    tests: dict[str, dict] = {}
    firewall_sha = None
    acl = None  # §92: ACL-Konfiguration (V1) als Zeitpunkt der Aktivierung und Endzustand der Gegenprobe-Kette
    for entry, content in contents:
        if entry["test"] == "V1" and entry["art"] == "firewall_export":
            final = content["beobachtung"].get("acl", content["beobachtung"].get("export"))
            acl = {"erzeugt": entry["_erzeugt"], "sha256": hashlib.sha256(json.dumps(
                final, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()}
    # §96 (H1/M6): Phasen-Tokens aller Nachher-Proben der VM und Beginn der Gegenprobe (Host-Uhr)
    tokens = {"nachher": [c["beobachtung"].get("phasen_token") for e, c in contents
                          if e["art"] == "pytest" and e["klasse"] == "E0"]}
    calibrations = [c for e, c in contents if e["art"] == "gegenprobe" and e["klasse"] == "E1"]
    try:
        calibration_start = min(_time(c["aktiv_von"]) for c in calibrations) if calibrations else None
    except (KeyError, TypeError, ValueError):
        calibration_start = None
    clock = vm_clock_problem(contents)
    for test in REQUIRED_TESTS:
        own = [(e, c) for e, c in contents if e["test"] == test]
        if not own:
            tests[test] = {"status": NICHT_DURCHGEFUEHRT, "gruende": ["kein Beleg"]}
            continue
        ctx = {"policy": policy}
        blocked, failed = [], []
        for req in ANFORDERUNGEN[test]:
            candidates = [(e, c) for e, c in own if e["art"] == req["art"] and KLASSEN[e["klasse"]] >= KLASSEN[req["klasse"]]
                          and (not req.get("genau") or e["klasse"] == req["klasse"])]
            if not candidates:
                weaker = any(e["art"] == req["art"] for e, _ in own)
                blocked.append(f"Pflichtbeleg {req['art']} in Klasse {req['klasse']} fehlt"
                               + (" (nur schwaechere Klasse vorhanden)" if weaker else ""))
                continue
            if req["art"] == "kriterien":
                # Kriterien koennen auf mehrere Protokolle (je Klasse) verteilt sein: ein "false" irgendwo = verletzt,
                # sonst muss jedes Pflichtkriterium in mindestens einem Protokoll ausdruecklich "true" sein
                merged: dict = {}
                for _, content in candidates:
                    criteria = content.get("kriterien")
                    for cid, value in (criteria.items() if isinstance(criteria, dict) else ()):
                        seen = [merged.get(cid), value]
                        merged[cid] = False if False in seen else "blockiert" if "blockiert" in seen \
                            else True if True in seen else None
                names = "+".join(e["datei"] for e, _ in candidates)
                candidates = [({"datei": names}, {"kriterien": merged})]
            for entry, content in candidates:
                verdict, reason = _EVALUATORS[req["art"]](content, req, ctx)
                if verdict == "blockiert":
                    blocked.append(f"{entry['datei']}: {reason}")
                elif verdict != "erfuellt":
                    failed.append(f"{entry['datei']}: {reason}" + (" (uneindeutig = FAIL)" if verdict == "unklar" else ""))
                if req["art"] == "firewall_export":
                    firewall_sha = entry["sha256"]
        if test in KANAELE:
            blocked += _gegenprobe_cross(test, own, acl, tokens)
        if test == "V11":
            blocked += _v11_cross(own, calibration_start)
        if test == "V12":
            blocked += _v12_cross(own)
        if clock and any(e["klasse"] == "E0" for e, _ in own):
            blocked.append(clock)
        if failed:
            tests[test] = {"status": FAIL, "gruende": failed + blocked}
        elif blocked:
            tests[test] = {"status": BLOCKIERT, "gruende": blocked}
        else:
            tests[test] = {"status": PASS, "gruende": []}
    return {"gesamt": overall([t["status"] for t in tests.values()]), "probleme": [], "manifest_sha256": manifest_sha,
            "firewall_export_sha256": firewall_sha, "tests": tests, "nicht_abgedeckt": list(NICHT_ABGEDECKT)}
