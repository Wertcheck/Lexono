"""§94/§96: Echter Abnahmemodus - Konfiguration, Manifest, Beobachter und Kommandozeilen fuer VM, Host und Endpunkt.

Ablauf einer realen Abnahme (Stufe A ohne Endpunkt, AUDIT_S93 §4; nichts davon laeuft ohne Freigabe):
  Host  : isolation_host_control startcheck                     -> keine Reste (ACL, Firewall, VM aus, Protokolle)
  Host  : isolation_host_control image-zuruecksetzen / fw-setzen -> Protokoll V11, eigene befristete Firewall-Regeln
  Host  : host-lauf                                              -> Listener + Funktionskontrolle, Phase "vorher",
                                                                    gibt den Start-Token der Vorher-Phase aus
  VM    : vm-teil --phase vorher --start-token <T>               -> Gegenprobe (E0), gibt den VM-Abschluss-Token aus
  Host  : Token eingeben -> Ruhezeit, Phase "aktivierung", Gegenprobe (E1); acl-setzen -> Aktivierungsprotokoll
  Host  : Enter -> V1-Export, Phase "nachher", Abnahme-Manifest (mit Start-Token der Nachher-Phase)
  VM    : vm-teil --phase nachher --manifest ...                 -> §86/§87/§90-Tests, V2/V9/V11 (E0), Abschluss-Token
  Host  : Token eingeben -> Ruhezeit, Listener leeren/beenden; Kanarien, V9, V10, V11, V12, V13, V2 (E1)
  Host  : acl-entfernen, fw-entfernen (Rueckbau; bei Abbruch steht der Auftrag in lauf_status.json)
  Endp. : endpunkt-teil (nach D3)                                -> V3 (E2)
  Host  : buendeln, danach `owner_sign urteil` (Signatur erst nach D4)

§96 (H1/M6): Reihenfolgen zwischen VM und Host werden ueber Phasen-Tokens belegt (der Host erzeugt den Start-Token,
die VM ihren Abschluss-Token NACH allen Proben; der Host schliesst die Phase erst danach und nach einer Ruhezeit) - nie
ueber einen Vergleich der beiden Uhren.

Sperren: jeder Schritt mit Systemzugriff verlangt `LEXONO_ISOLATION_ACCEPTANCE=1` UND `--real`; Belege entstehen dann
im Modus "real" (Trockenlauf-Belege werden produktiv abgelehnt, gemischte Buendel ebenfalls). VM-Schritte laufen nur
auf der Maschine der Konfiguration, Host-Schritte nie dort. Nichts wird geschaetzt oder ergaenzt: was nicht beobachtet
wird, fehlt als Beleg (BLOCKIERT/NICHT DURCHGEFUEHRT); Fehler werden als Beobachtung festgehalten (FAIL)."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.legal_bench import answer_specs, isolation_acceptance as acc, isolation_evidence as ev, isolation_gate

KONFIG_SCHEMA = "lexono-isolation-abnahme-konfig/1"
MANIFEST_SCHEMA = "lexono-isolation-abnahme-manifest/1"
STATUS_PREFIX = "lauf_status_"  # §98 (M6): je Lauf und Zeitpunkt eine eigene Datei - nie ueberschrieben
#: §98 (H1): Abstand der Zwischenkontrollen (Firewall + Listener) waehrend der Nachher-Phase
KONTROLL_INTERVALL_S = 15.0
KANARIENNAME = "lexono-abnahme.invalid"
_SID = re.compile(r"S-1-\d+(?:-\d+)+")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
_TOKEN = re.compile(r"^[0-9a-f]{32}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
KONFIG_KEYS = {"schema", "run_id", "vm_name", "vm_identitaet", "netzwerkpolitik", "host_ips", "vm_ips", "kanarien",
               "golden_image", "protokolle", "gast_richtlinie", "endpunkt", "v13", "switch", "erlaubte_gateways",
               "benchmark_konto"}
S86 = "tests/test_s86_isolation_design.py"
GATE_TESTS = ("tests/test_s87_isolation_gate.py", "tests/test_s90_owner_gate.py")


def _refuse(*reasons: str) -> None:
    raise acc.AcceptanceRefused(list(reasons))


def require_real(real: bool) -> None:
    if not real or os.environ.get(acc.ENV_REAL) != "1":
        _refuse(f"echter Abnahmeschritt verweigert - nur mit {acc.ENV_REAL}=1 und --real")


def check_token(value: str | None, what: str) -> str:
    token = (value or "").strip().lower()
    if not _TOKEN.match(token):
        _refuse(f"{what}: ungueltiger Token (32 Hex-Zeichen erwartet)")
    return token


# --- Konfiguration -------------------------------------------------------------------------------------------------------


def load_konfig(path: Path) -> dict:  # noqa: PLR0912
    """Strenge Pruefung: fehlende, unbekannte oder widerspruechliche Angaben -> Ablehnung (nichts wird ergaenzt)."""
    try:
        konfig = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        _refuse(f"Konfiguration unlesbar ({type(exc).__name__})")
    reasons: list[str] = []
    if not isinstance(konfig, dict) or konfig.get("schema") != KONFIG_SCHEMA:
        _refuse("Konfiguration hat ein unbekanntes Schema")
    if set(konfig) != KONFIG_KEYS:
        reasons.append(f"Konfiguration: fehlend {sorted(KONFIG_KEYS - set(konfig))}, unbekannt {sorted(set(konfig) - KONFIG_KEYS)}")
        _refuse(*reasons)
    if not isinstance(konfig["run_id"], str) or not _RUN_ID.match(konfig["run_id"]):
        reasons.append("run_id ungueltig")
    try:
        acc._safe_vm_name(konfig["vm_name"])
    except acc.AcceptanceRefused as exc:
        reasons += exc.reasons
    ident = konfig["vm_identitaet"]
    if not isinstance(ident, dict) or set(ident) != {"hostname", "machine_guid"} or not all(
            isinstance(v, str) and v for v in ident.values()):
        reasons.append("vm_identitaet braucht hostname und machine_guid")
    if not Path(str(konfig["netzwerkpolitik"])).is_file():
        reasons.append("Netzwerkpolitik nicht gefunden")
    try:
        host_ips = {ipaddress.ip_address(konfig["host_ips"]["ipv4"]), ipaddress.ip_address(konfig["host_ips"]["ipv6"])}
        vm_ips = [ipaddress.ip_address(ip) for ip in konfig["vm_ips"]]
        if not vm_ips:
            reasons.append("vm_ips fehlen")
    except (KeyError, TypeError, ValueError):
        reasons.append("host_ips (ipv4, ipv6) bzw. vm_ips ungueltig")
        host_ips = set()
    seen = set()
    for target in konfig["kanarien"] if isinstance(konfig["kanarien"], list) else []:
        try:
            ip, port, proto = ipaddress.ip_address(target["ip"]), int(target["port"]), target["protokoll"]
        except (KeyError, TypeError, ValueError):
            reasons.append("Kanarienziel unvollstaendig")
            continue
        if set(target) != {"ip", "port", "protokoll"} or proto not in ("tcp", "udp", "dns") or not 0 < port < 65536:
            reasons.append(f"Kanarienziel {target} ungueltig")
        if ip not in host_ips:
            reasons.append(f"Kanarienziel {ip} ist keine Host-Adresse am Abnahme-Switch")
        if (str(ip), port, "tcp" if proto == "tcp" else "udp") in seen:
            reasons.append(f"Kanarienziel {ip}:{port} doppelt")
        seen.add((str(ip), port, "tcp" if proto == "tcp" else "udp"))
    try:
        covered = {c for t in konfig["kanarien"] for c in ev.channels(t)}
        missing = sorted(set(ev.KANAELE["V12"]) - covered)
        if missing:
            reasons.append(f"Kanarienziele decken Kanaele nicht ab: {', '.join(missing)}")
    except Exception:  # noqa: BLE001
        reasons.append("Kanarienziele nicht auswertbar")
    image = konfig["golden_image"]
    if not isinstance(image, dict) or set(image) != {"eltern_vhdx", "kind_vhdx", "soll_sha256"} \
            or not ev._hex64(image.get("soll_sha256")):
        reasons.append("golden_image braucht eltern_vhdx, kind_vhdx, soll_sha256 (64 Hex)")
    if not isinstance(konfig["protokolle"], dict) or set(konfig["protokolle"]) != {"acl_aktivierung", "image_ruecksetzung"}:
        reasons.append("protokolle braucht acl_aktivierung und image_ruecksetzung")
    for key, fields in (("gast_richtlinie", {"export", "soll"}), ("endpunkt", {"vm_ip", "log"}),
                        ("v13", {"schluesselliste", "bestaetigung"}), ("switch", {"name"})):
        if not isinstance(konfig[key], dict) or set(konfig[key]) != fields:
            reasons.append(f"{key} braucht {sorted(fields)} (Wert null = nicht vorhanden -> Beleg fehlt)")
    if isinstance(konfig["switch"], dict) and not _NAME.match(str(konfig["switch"].get("name") or "")):
        reasons.append("switch.name ungueltig")
    try:
        if not isinstance(konfig["erlaubte_gateways"], list):
            raise TypeError
        [ipaddress.ip_address(g) for g in konfig["erlaubte_gateways"]]  # noqa: B018
    except (TypeError, ValueError):
        reasons.append("erlaubte_gateways muss eine Liste von IP-Adressen sein (Stufe A: leer)")
    if not isinstance(konfig["benchmark_konto"], str) or not _NAME.match(konfig["benchmark_konto"]):
        reasons.append("benchmark_konto ungueltig")
    if reasons:
        _refuse(*reasons)
    return konfig


def parameter_aus_konfig(konfig: dict) -> dict:
    """Soll-Parameter der Kriterien (§96): Konto, Gateways, Switch, Datentraeger."""
    return {"benchmark_konto": konfig["benchmark_konto"], "erlaubte_gateways": list(konfig["erlaubte_gateways"]),
            "switch": konfig["switch"]["name"], "kind_vhdx": konfig["golden_image"]["kind_vhdx"],
            "eltern_vhdx": konfig["golden_image"]["eltern_vhdx"]}


def lauf_aus_konfig(konfig: dict, *, tool=isolation_gate.pruefwerkzeug_stand) -> tuple[dict, dict]:  # noqa: ANN001
    policy_path = Path(konfig["netzwerkpolitik"])
    stand = tool()
    if not stand.get("commit") or not stand.get("sha256"):
        _refuse("Pruefwerkzeug nicht bestimmbar (Commit/Dateien) - fail-closed")
    lauf = {"run_id": konfig["run_id"], "maschine": dict(konfig["vm_identitaet"]),
            "netzwerkpolitik_sha256": isolation_gate.sha256_file(policy_path), "pruefwerkzeug": stand}
    return lauf, json.loads(policy_path.read_text(encoding="utf-8"))


# --- Abnahme-Manifest fuer die §86-Proben ------------------------------------------------------------------------------


def abnahme_manifest(konfig: dict, lauf: dict, policy: dict, *, acl: list | None, start_token: str | None = None) -> dict:
    endpoints = sorted(isolation_gate._approved_endpoints(policy))
    return {"schema": MANIFEST_SCHEMA, "lauf": lauf, "lauf_sha256": ev.lauf_sha256(lauf),
            "phasen_start_token": start_token,
            "freigegebener_testendpunkt": {"ip": endpoints[0][0], "port": endpoints[0][1]} if endpoints else None,
            "kanarienziele": [{"ip": t["ip"], "port": int(t["port"])} for t in konfig["kanarien"]],
            "dns_resolver": [konfig["host_ips"]["ipv4"]], "kanarienname": KANARIENNAME,
            "firewall_export_inhalt": ev.acl_to_export(acl) if acl is not None else None, "politik_inhalt": policy}


def check_manifest(manifest: dict, lauf: dict) -> None:
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        _refuse("Abnahme-Manifest hat ein unbekanntes Schema")
    if manifest.get("lauf") != lauf or manifest.get("lauf_sha256") != ev.lauf_sha256(lauf):
        _refuse("Abnahme-Manifest gehoert zu anderem Lauf/Maschine/Politik/Pruefwerkzeug")
    if not manifest.get("firewall_export_inhalt"):
        _refuse("Abnahme-Manifest ohne ACL-Export - erst nach der Aktivierung erzeugen")
    check_token(manifest.get("phasen_start_token"), "Abnahme-Manifest (Start-Token der Nachher-Phase)")


# --- Beobachter (echt; jeder liefert Rohdaten, keine Bewertung) ---------------------------------------------------------


def _ps(key: str, *, vm: str | None = None, runner=subprocess.run, **values) -> list[dict]:  # noqa: ANN001, ANN003
    result = acc.run_real(key, real=True, vm=vm, runner=runner, **values)
    if result.returncode != 0:
        raise RuntimeError(f"{key}: Exit {result.returncode}: {(result.stderr or '')[:200]}")
    return acc.normalize_ps_json(result.stdout)


def _as_list(value) -> list:  # noqa: ANN001
    """PowerShell serialisiert eine Ein-Element-Liste in Objekten teils als Objekt: deterministisch als Liste."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def observe_vm_v2(runner=subprocess.run, *, worktree=None, write_test=None) -> dict:  # noqa: ANN001
    """V2 (VM, E0). §96 (N6): Aenderungsversuch ZULETZT und mit Ruecknahme, falls er gelingt; Zustand vorher/nachher."""
    profiles = [{"name": p.get("Name"), "enabled": p.get("enabled"), "default_outbound": p.get("default_outbound")}
                for p in _ps("vm.gast_firewall", runner=runner)]
    sids = acc.run_real("vm.konto_sids", real=True, runner=runner)
    if sids.returncode != 0:
        raise RuntimeError("whoami /groups fehlgeschlagen")
    name = _ps("vm.konto_name", runner=runner)
    observation = {"firewall_profile": profiles, "konto_gruppen_sids": sorted(set(_SID.findall(sids.stdout or ""))),
                   "konto_name": name[0].get("name") if len(name) == 1 else None,
                   "arbeitsbaum": (worktree or observe_worktree)(runner),
                   "schreibtest": (write_test or observe_write_access)()}
    before = _ps("vm.firewall_public_lokal", runner=runner)
    before = before[0].get("wert") if len(before) == 1 else None
    attempt = acc.run_real("vm.aenderungsversuch", real=True, runner=runner)  # MUSS scheitern
    rollback = None
    if attempt.returncode == 0 and before in ("Block", "Allow", "NotConfigured"):
        rollback = acc.run_real("vm.aenderungsversuch_rueckbau", real=True, runner=runner, wert=before).returncode
    elif attempt.returncode == 0:
        rollback = -1  # Ausgangswert unbekannt - Ruecknahme unmoeglich (verhindert PASS)
    after = _ps("vm.firewall_public_lokal", runner=runner)
    observation["aenderungsversuch"] = {"exit_code": attempt.returncode, "fehler": (attempt.stderr or "")[:200],
                                        "vorher": before, "nachher": after[0].get("wert") if len(after) == 1 else None,
                                        "rueckbau_exit": rollback}
    return observation


def observe_worktree(runner=subprocess.run) -> dict:  # noqa: ANN001
    """§96 (M4): Arbeitsbaum in der VM unveraendert (inkl. unverfolgter Dateien)?"""
    result = runner(["git", "-C", str(answer_specs.REPO), "status", "--porcelain", "--untracked-files=all"],
                    capture_output=True, text=True, timeout=120, check=False)
    return {"exit_code": result.returncode, "porcelain": (result.stdout or "").strip() if result.returncode == 0 else None}


def observe_write_access(repo: Path | None = None, *, site_packages: list[Path] | None = None) -> dict:
    """§96/§98 (M4/M1): Darf das laufende (Benchmark-)Konto ausfuehrbaren Code veraendern?

    Geprueft werden ALLE Dateien des Fingerabdrucks, ALLE Ordner, in denen sie liegen (app/**, scripts/**, tests/,
    Wurzel), `.venv`, alle site-packages-Ordner sowie deren .pth-/Editable-Dateien.
    Dateien: Oeffnen zum Anhaengen (ohne zu schreiben). Ordner: exklusives Anlegen einer Probedatei, die sofort wieder
    entfernt wird (nur in der VM; gelingt das, ist der Ordner beschreibbar -> FAIL)."""
    import site

    repo = Path(repo or answer_specs.REPO)
    if repo.resolve() == answer_specs.REPO.resolve():
        names = [n for n in isolation_gate.tool_files() if (repo / n).is_file()]
    else:
        names = sorted({q.relative_to(repo).as_posix() for pattern in isolation_gate.TOOL_GLOBS for q in repo.glob(pattern)
                        if q.is_file() and "__pycache__" not in q.parts})
    packages = site_packages if site_packages is not None else [
        Path(x) for x in site.getsitepackages() if Path(x).name.lower() == "site-packages"]
    files = [repo / n for n in names]
    for folder in packages:
        if folder.is_dir():
            files += [f for f in folder.iterdir() if f.is_file() and (f.suffix.lower() == ".pth"
                                                                       or f.name.startswith("__editable__"))]
    folders = sorted({repo, repo / ".venv", *[f.parent for f in files], *packages}, key=str)
    writable, checked = [], 0

    def label(path: Path) -> str:
        try:
            return path.relative_to(repo).as_posix()
        except ValueError:
            return str(path)

    for path in files:
        checked += 1
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_APPEND)
        except OSError:
            continue
        os.close(fd)
        writable.append(label(path))
    for folder in folders:
        if not folder.is_dir():
            continue
        checked += 1
        probe = folder / f".lexono-schreibtest-{os.getpid()}"
        try:
            fd = os.open(str(probe), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except OSError:
            continue
        os.close(fd)
        try:
            os.remove(probe)
        except OSError:
            pass
        writable.append(label(folder).rstrip("/") + "/" if label(folder) != "." else "./")
    return {"geprueft": checked, "beschreibbar": writable, "ordner": len(folders), "dateien": len(files)}


def observe_vm_network(runner=subprocess.run) -> dict:  # noqa: ANN001
    config = [{"InterfaceAlias": c.get("InterfaceAlias"), **{k: _as_list(c.get(k)) for k in
                                                             ("ipv4", "ipv6", "gateway4", "gateway6", "dns")}}
              for c in _ps("vm.ip_konfiguration", runner=runner)]
    return {"netzadapter": _ps("vm.netzadapter", runner=runner), "ip_konfiguration": config,
            "routen": _ps("vm.routen", runner=runner)}


def run_pytest(nodes: list[str], junit: Path, *, manifest_path: Path, real: bool, runner=subprocess.run) -> str:  # noqa: ANN001
    require_real(real)
    env = {**os.environ, acc.ENV_REAL: "1", "LEXONO_ISOLATION_MANIFEST": str(manifest_path)}
    runner([sys.executable, "-m", "pytest", *nodes, "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"],
           cwd=answer_specs.REPO, env=env, capture_output=True, text=True, timeout=1800, check=False)
    if not Path(junit).is_file():
        raise RuntimeError("pytest lieferte kein JUnit-Protokoll")
    return Path(junit).read_text(encoding="utf-8")


def gate_gegenprobe(konfig: dict, policy_path: Path, *, pinned_roots=None) -> dict:  # noqa: ANN001
    """V13 (Host): Produktiv-Gate mit einer absichtlich veralteten Bestaetigung pruefen - 1 h nach deren Ablauf.
    §96 (N1/N2): Schluesselstatus semantisch (aktiv/widerrufen/abgelaufen/unbekannt), keine Teilstrings."""
    paths = konfig["v13"]
    attestation = json.loads(Path(paths["bestaetigung"]).read_text(encoding="utf-8"))
    expired = datetime.fromisoformat(attestation["payload"]["gueltig_bis"]) + timedelta(hours=1)
    options = {"pinned_roots": pinned_roots} if pinned_roots is not None else {}
    gate = isolation_gate.IsolationGate(keylist_path=Path(paths["schluesselliste"]), attestation_path=Path(paths["bestaetigung"]),
                                        evidence_path=None, policy_path=policy_path,
                                        identity=lambda: dict(konfig["vm_identitaet"]), now=lambda: expired, **options)
    keylist, _ = gate.load_keylist(expired)
    key_id = attestation.get("key_id")
    if keylist is None:
        status = "unbekannt"
    elif key_id in keylist.revoked:
        status = "widerrufen"
    elif key_id not in keylist.anchors:
        status = "unbekannt"
    else:
        anchor = keylist.anchors[key_id]
        status = "aktiv" if anchor["gueltig_von"] <= expired <= anchor["gueltig_bis"] else "abgelaufen"
    return {"diagnose": gate.problems(run_id=konfig["run_id"], provider_endpoints=set(), check_firewall=False),
            "schluesselliste_gueltig": keylist is not None, "schluessel_status": status,
            "pruefzeitpunkt": expired.isoformat()}


def file_hashes(export: str | None, target: str | None) -> dict:
    if not export or not target:
        raise RuntimeError("Gastrichtlinie: Export oder Soll nicht angegeben")
    return {"gast_richtlinie_sha256": hashlib.sha256(Path(export).read_bytes()).hexdigest(),
            "golden_image_richtlinie_sha256": hashlib.sha256(Path(target).read_bytes()).hexdigest()}


def _outside_repo(path: Path) -> None:
    if Path(path).resolve().is_relative_to(answer_specs.REPO.resolve()):
        _refuse(f"{path}: Belege nie im Repository ablegen (Arbeitsbaum muss unveraendert bleiben)")


# --- VM-Teil ------------------------------------------------------------------------------------------------------------


def vm_teil(konfig_path: Path, ordner: Path, phase: str, *, real: bool, manifest_path: Path | None = None,  # noqa: PLR0913
            start_token: str | None = None, runner=subprocess.run, identity=isolation_gate.machine_identity,  # noqa: ANN001
            tool=isolation_gate.pruefwerkzeug_stand, pytest_runner=None, worktree=None, write_test=None) -> dict:  # noqa: ANN001
    """Rueckgabe: {"teil", "vm_abschluss_token"} - der Token wird dem Host gemeldet (er schliesst die Phase danach)."""
    require_real(real)
    konfig = load_konfig(konfig_path)
    if identity() != konfig["vm_identitaet"]:
        _refuse("vm-teil laeuft nicht auf der VM der Konfiguration (Hostname/MachineGuid)")
    _outside_repo(ordner)
    lauf, policy = lauf_aus_konfig(konfig, tool=tool)
    parameter = parameter_aus_konfig(konfig)
    tests = [t for t, reqs in ev.ANFORDERUNGEN.items() for r in reqs if r["art"] == "gegenprobe" and r["klasse"] == "E0"]
    if phase == "vorher":
        token = check_token(start_token, "Start-Token der Vorher-Phase")
        part = acc.EvidencePart(ordner, "vm", lauf, policy, mode="real", parameter=parameter)
        observation = acc.calibration_probe(konfig["kanarien"], vm_ips=konfig["vm_ips"], start_token=token, mode="real")
        for test in tests:
            part.record(test, "gegenprobe", observation)
        return {"teil": str(part.folder), "vm_abschluss_token": observation["phasen_token"]["vm_abschluss"]}
    if phase != "nachher":
        _refuse(f"unbekannte Phase {phase!r}")
    part = acc.EvidencePart.resume(ordner, "vm", lauf, policy, mode="real", parameter=parameter)
    if not all(part.has(t, "gegenprobe") for t in tests):
        _refuse("Reihenfolge verletzt: Gegenprobe (Phase vorher) fehlt")
    if manifest_path is None:
        _refuse("Phase nachher braucht das Abnahme-Manifest des Hosts")
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    check_manifest(manifest, lauf)
    pytest_runner = pytest_runner or (lambda nodes, junit: run_pytest(nodes, junit, manifest_path=manifest_path,
                                                                      real=real, runner=runner))
    work = Path(ordner).parent / f"{Path(ordner).name}_junit"
    _outside_repo(work)
    work.mkdir(exist_ok=True)
    # 1. alle Proben/Tests ZUERST (eager), 2. Konfiguration, 3. Aenderungsversuch zuletzt, 4. Abschluss-Token, 5. Belege
    results = {}
    for name, nodes in (("s86", [S86]), ("gate", list(GATE_TESTS))):
        try:
            results[name] = {"junit_xml": pytest_runner(nodes, work / f"{name}.xml")}
        except Exception as exc:  # noqa: BLE001 - Fehler wird Beobachtung (nie PASS)
            results[name] = {"fehler": f"{type(exc).__name__}: {str(exc)[:200]}"}
    observed = {}
    for key, observer in (("V9", lambda: observe_vm_network(runner)),
                          ("V2", lambda: observe_vm_v2(runner, worktree=worktree, write_test=write_test))):
        try:
            observed[key] = observer()
        except Exception as exc:  # noqa: BLE001
            observed[key] = {"fehler": f"{type(exc).__name__}: {str(exc)[:200]}"}
    phase_token = {"start": manifest["phasen_start_token"], "vm_abschluss": acc.new_token()}
    for test, reqs in ev.ANFORDERUNGEN.items():
        for req in reqs:
            if req["klasse"] == "E0" and req["art"] == "pytest":
                part.record(test, "pytest", {**results["gate" if test == "V13" else "s86"], "phasen_token": phase_token})
    part.record("V11", "kriterien", results["s86"])  # Kurzpruefung nach der Ruecksetzung = derselbe Abnahmelauf
    part.record("V9", "kriterien", observed["V9"])
    part.record("V2", "kriterien", observed["V2"])
    part.close()
    return {"teil": str(part.folder), "vm_abschluss_token": phase_token["vm_abschluss"]}


# --- Host-Teil ------------------------------------------------------------------------------------------------------------


def _vm_config(konfig: dict, control, runner) -> dict:  # noqa: ANN001
    items = _ps("host.vm_konfiguration", vm=konfig["vm_name"], runner=runner)
    if len(items) != 1:
        raise RuntimeError("VM-Konfiguration nicht eindeutig")
    config = items[0]
    switches = _ps("host.vm_switch", vm=konfig["vm_name"], runner=runner)
    guard = _ps("host.vethernet_schutz", runner=runner, iface=f"vEthernet ({konfig['switch']['name']})")
    guard = guard[0] if len(guard) == 1 else {}
    return {"vm_netzadapter": _as_list(config.get("vm_netzadapter")), "gastdienste_aktiv": config.get("gastdienste_aktiv"),
            "erweiterte_sitzung": config.get("erweiterte_sitzung"), "acl": control.read_acl(),
            "integrationsdienste": _as_list(config.get("integrationsdienste")),
            "switch": switches[0] if len(switches) == 1 else None,
            "vethernet_schutz": {"kategorie": guard.get("kategorie"), "standard_eingehend": guard.get("standard_eingehend"),
                                 "regeln": [{**r, "LocalPort": _as_list(r.get("LocalPort")),
                                             "InterfaceAlias": _as_list(r.get("InterfaceAlias"))}
                                            for r in _as_list(guard.get("regeln"))]}}


def _v11_observation(konfig: dict, reset: Path, runner) -> dict:  # noqa: ANN001
    record = json.loads(reset.read_text(encoding="utf-8"))
    image = konfig["golden_image"]
    disks = [d.get("Path") for d in _ps("host.vm_datentraeger", vm=konfig["vm_name"], runner=runner)]
    vhd = _ps("host.vhd", runner=runner, pfad=image["kind_vhdx"])
    attrs = os.stat(image["eltern_vhdx"]).st_file_attributes if hasattr(os.stat_result, "st_file_attributes") else None
    return {**{k: v for k, v in record.items() if k in ("zuruecksetzen", "image_sha256_soll", "image_sha256_vorher",
                                                         "image_sha256_nachher")},
            "vm_datentraeger": disks, "kind_vhd": vhd[0] if len(vhd) == 1 else None,
            "eltern_schreibgeschuetzt": bool(attrs & stat.FILE_ATTRIBUTE_READONLY) if attrs is not None else None}


def _write_status(ordner: Path, status: dict) -> Path:
    """Exklusiv, mit Lauf und Zeitstempel im Namen - aeltere Rueckbauauftraege bleiben erhalten."""
    Path(ordner).mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = Path(ordner) / f"{STATUS_PREFIX}{status['run_id']}_{stamp}.json"
    with open(path, "x", encoding="utf-8") as handle:
        json.dump(status, handle, ensure_ascii=False, indent=1)
    return path


def _rueckbau_aus_ist(konfig: dict, control) -> list[str]:  # noqa: ANN001
    """§98 (M6): Rueckbauauftrag aus dem TATSAECHLICHEN Zustand (nicht aus dem erreichten Programmschritt)."""
    run = konfig["run_id"]
    todo = []
    try:
        if control.read_acl():
            todo.append(f"acl-entfernen --lauf {run} (Port-ACL nicht leer)")
    except Exception as exc:  # noqa: BLE001
        todo.append(f"ACL-Zustand unbekannt ({type(exc).__name__}) - startcheck ausfuehren")
    try:
        own = [r for r in control.read_firewall() if f"run_id={run};" in r["Description"] + ";"]
        if own:
            todo.append(f"fw-entfernen --lauf {run} ({len(own)} eigene Regel(n))")
    except Exception as exc:  # noqa: BLE001
        todo.append(f"Firewall-Zustand unbekannt ({type(exc).__name__}) - startcheck ausfuehren")
    return todo


def _fw_kontrolle(konfig: dict, control, zeitpunkt: str) -> dict:  # noqa: ANN001
    """§98 (H1): eigene Firewall-Regeln Ist == Soll? Fehler/Unlesbarkeit -> gleich False (nie PASS)."""
    try:
        result = control.fw_pruefen(konfig)
        return {"zeitpunkt": zeitpunkt, "gleich": result["gleich"], "ist_sha256": result["ist_sha256"],
                "probleme": result["probleme"]}
    except Exception as exc:  # noqa: BLE001
        return {"zeitpunkt": zeitpunkt, "gleich": False, "ist_sha256": None, "probleme": [f"{type(exc).__name__}"]}


def host_lauf(konfig_path: Path, ordner: Path, *, real: bool, wait=input, runner=subprocess.run,  # noqa: ANN001, PLR0913, PLR0915
              identity=isolation_gate.machine_identity, tool=isolation_gate.pruefwerkzeug_stand) -> dict:  # noqa: ANN001
    """Ein durchgehender Host-Prozess ueber alle Phasen (der Listener darf nicht neu starten). §96 (M7): Jeder Abbruch
    hinterlaesst `lauf_status.json` mit dem Rueckbauauftrag; Rueckbau selbst nur mit isolation_host_control."""
    from scripts.legal_bench import isolation_host_control as hc

    require_real(real)
    konfig = load_konfig(konfig_path)
    if identity() == konfig["vm_identitaet"]:
        _refuse("host-lauf darf nicht auf der geprueften VM laufen")
    _outside_repo(ordner)
    lauf, policy = lauf_aus_konfig(konfig, tool=tool)
    record_path = Path(konfig["protokolle"]["acl_aktivierung"])
    if record_path.exists():
        _refuse("Aktivierungsprotokoll existiert schon - neuer Lauf braucht ein neues Protokoll")
    control = hc.HostControl(konfig["vm_name"], ausfuehren=False, runner=runner, run_id=konfig["run_id"])
    status = {"run_id": konfig["run_id"], "phase": "start", "abgebrochen": True, "grund": None,
              "rueckbau_noetig": [], "zeit": datetime.now(timezone.utc).isoformat()}
    try:
        firewall = control.fw_pruefen(konfig)
        if not firewall["gleich"]:
            _refuse("eigene Host-Firewall-Regeln fehlen, weichen ab oder sind abgelaufen - fw-setzen/fw-pruefen",
                    *firewall["probleme"])
        part = acc.EvidencePart(Path(ordner) / "teil_host", "host", lauf, policy, mode="real",
                                parameter=parameter_aus_konfig(konfig))
        result = _host_phases(konfig, ordner, part, control, record_path, status, wait, runner, lauf, policy)
        status.update(phase="abgeschlossen", abgebrochen=False)
        return result
    except BaseException as exc:
        status["grund"] = "; ".join(exc.reasons) if isinstance(exc, acc.AcceptanceRefused) else \
            f"{type(exc).__name__}: {str(exc)[:300]}"
        raise
    finally:
        status["zeit"] = datetime.now(timezone.utc).isoformat()
        status["rueckbau_noetig"] = _rueckbau_aus_ist(konfig, control)
        path = _write_status(ordner, status)
        print(f"RUECKBAU: {', '.join(status['rueckbau_noetig']) or 'nichts'} (isolation_host_control ... --ausfuehren); "
              f"Status: {path}", file=sys.stderr)


def _host_phases(konfig, ordner, part, control, record_path, status, wait, runner, lauf, policy) -> dict:  # noqa: ANN001, PLR0913
    from scripts.legal_bench import isolation_host_control as hc

    calibration_tests = [t for t, reqs in ev.ANFORDERUNGEN.items() for r in reqs
                         if r["art"] == "gegenprobe" and r["klasse"] == "E1"]
    canary_tests = [t for t, reqs in ev.ANFORDERUNGEN.items() for r in reqs if r["art"] == "kanarien"]
    with acc.CanaryListener(konfig["kanarien"], mode="real") as listener:
        if len(set(listener.controls)) != len(konfig["kanarien"]):
            _refuse("Funktionskontrolle der Kanarien-Listener unvollstaendig - Abbruch vor der Gegenprobe")
        acl_before = control.read_acl()
        if acl_before:
            _refuse("Port-ACL ist vor der Gegenprobe nicht leer - Abbruch")
        vorher_start = acc.new_token()
        listener.mark("vorher")
        status["phase"] = "vorher"
        vm_end = check_token(wait(f"Phase VORHER: in der VM `vm-teil --phase vorher --start-token {vorher_start}` "
                                  "ausfuehren und den ausgegebenen Abschluss-Token hier eingeben: "), "VM-Abschluss vorher")
        listener.settle()  # §98 (H1): Ruhe, dann Kontrollen VOR dem Phasenende
        listener.control_check("vorher_ende")
        fw_vorher = [_fw_kontrolle(konfig, control, "vorher_ende")]
        listener.mark("aktivierung")  # endet erst nach der Ruhezeit (H1)
        status["phase"] = "aktivierung"
        calibration = {**listener.observation("vorher"), "vm_quelle_ips": list(konfig["vm_ips"]),
                       "acl_sha256": hashlib.sha256(acc._canonical(acl_before)).hexdigest(),
                       "phasen_token": {"start": vorher_start, "vm_abschluss": vm_end}, "firewall_kontrollen": fw_vorher}
        for test in calibration_tests:
            part.record(test, "gegenprobe", calibration)
        wait("Phase AKTIVIERUNG: jetzt `isolation_host_control acl-setzen --ausfuehren` ausfuehren, danach Enter. ")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("schema") != hc.RECORD_SCHEMA or record.get("vm") != konfig["vm_name"] \
                or datetime.fromisoformat(record["zeit_gesetzt"]) < datetime.fromisoformat(calibration["aktiv_bis"]):
            _refuse("Aktivierungsprotokoll fehlt, gehoert zu anderer VM oder liegt vor dem Ende der Gegenprobe")
        part.observe("V1", "firewall_export", lambda: {"acl": control.read_acl()})
        nachher_start = acc.new_token()
        listener.mark("nachher")
        status["phase"] = "nachher"
        listener.control_check("nachher_start")
        fw_nachher = [_fw_kontrolle(konfig, control, "nachher_start")]
        manifest = abnahme_manifest(konfig, lauf, policy, acl=control.read_acl(), start_token=nachher_start)
        manifest_path = Path(ordner) / "abnahme_manifest.json"
        with open(manifest_path, "x", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=1)

        def periodic() -> dict:  # §98 (H1): waehrend der Nachher-Phase Firewall UND Listener wiederholt pruefen
            listener.control_check("nachher_waehrend")
            return _fw_kontrolle(konfig, control, "nachher_waehrend")

        with acc.PeriodicCheck(periodic, interval_s=KONTROLL_INTERVALL_S) as monitor:
            nachher_end = check_token(wait(f"Phase NACHHER: Manifest {manifest_path} in die VM bringen, `vm-teil --phase "
                                           "nachher` ausfuehren und den ausgegebenen Abschluss-Token eingeben: "),
                                      "VM-Abschluss nachher")
        listener.settle()
        listener.control_check("nachher_ende")
        fw_nachher += [*monitor.results, _fw_kontrolle(konfig, control, "nachher_ende")]
    status["phase"] = "auswertung"
    after = {**listener.observation("nachher"), "phasen_token": {"start": nachher_start, "vm_abschluss": nachher_end},
             "firewall_kontrollen": fw_nachher}
    for test in canary_tests:
        part.record(test, "kanarien", after)
    part.observe("V9", "kriterien", lambda: _vm_config(konfig, control, runner))
    part.observe("V10", "kriterien", lambda: {
        "acl_vorher_sha256": record["acl_nachher_sha256"], "export_erzeuger": part.erzeuger,
        "acl_nachher_sha256": hashlib.sha256(acc._canonical(control.read_acl())).hexdigest()})
    reset = Path(konfig["protokolle"]["image_ruecksetzung"])
    if reset.is_file():
        part.observe("V11", "kriterien", lambda: _v11_observation(konfig, reset, runner))
    part.observe("V12", "kriterien", lambda: {"auswertung_durch": part.erzeuger,
                                              "kanarien_logs_sha256": [hashlib.sha256(acc._canonical(after)).hexdigest()]})
    if konfig["v13"]["bestaetigung"] and konfig["v13"]["schluesselliste"]:
        part.observe("V13", "kriterien", lambda: gate_gegenprobe(konfig, Path(konfig["netzwerkpolitik"])))
    if konfig["gast_richtlinie"]["export"] and konfig["gast_richtlinie"]["soll"]:
        part.observe("V2", "kriterien", lambda: file_hashes(konfig["gast_richtlinie"]["export"],
                                                            konfig["gast_richtlinie"]["soll"]))
    part.close()
    return {"teil": str(part.folder), "manifest": str(manifest_path), "nachher_treffer": len(after["treffer"]),
            "ausserhalb_der_phasen": len(after["ausserhalb_der_phasen"]), "abschluss": after["abschluss"]}


# --- Endpunkt-Teil (nach D3) ---------------------------------------------------------------------------------------------


def endpunkt_teil(konfig_path: Path, log_path: Path, ordner: Path, *, von: str, bis: str, real: bool,  # noqa: PLR0913
                  tool=isolation_gate.pruefwerkzeug_stand) -> Path:  # noqa: ANN001
    require_real(real)
    konfig = load_konfig(konfig_path)
    lauf, policy = lauf_aus_konfig(konfig, tool=tool)
    part = acc.EvidencePart(ordner, "endpunkt", lauf, policy, mode="real")

    def observe() -> dict:
        entries = []
        for number, line in enumerate(Path(log_path).read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            entry = json.loads(line)
            if not isinstance(entry, dict) or not {"zeit", "quelle_ip", "pfad", "status", "weitergeleitet"} <= set(entry):
                raise ValueError(f"Log-Zeile {number} unvollstaendig")
            entries.append(entry)
        if not konfig["endpunkt"]["vm_ip"]:
            raise ValueError("vm_ip des Endpunkts nicht konfiguriert")
        return {"eintraege": entries, "vm_ip": konfig["endpunkt"]["vm_ip"], "fenster": [von, bis]}

    part.observe("V3", "kriterien", observe)
    part.close()
    return part.folder


def buendeln(konfig_path: Path, teile: dict[str, Path], ausgabe: Path, *, tool=isolation_gate.pruefwerkzeug_stand) -> Path:  # noqa: ANN001
    konfig = load_konfig(konfig_path)
    lauf, _ = lauf_aus_konfig(konfig, tool=tool)
    return acc.assemble(ausgabe, teile, lauf=lauf)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="isolation_real", description="§94/§96 echter Abnahmemodus (nur mit Freigabe)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("konfig-pruefen")
    p.add_argument("--konfig", type=Path, required=True)
    p = sub.add_parser("vm-teil")
    p.add_argument("--konfig", type=Path, required=True)
    p.add_argument("--ordner", type=Path, required=True)
    p.add_argument("--phase", choices=("vorher", "nachher"), required=True)
    p.add_argument("--manifest", type=Path)
    p.add_argument("--start-token")
    p.add_argument("--real", action="store_true")
    p = sub.add_parser("host-lauf")
    p.add_argument("--konfig", type=Path, required=True)
    p.add_argument("--ordner", type=Path, required=True)
    p.add_argument("--real", action="store_true")
    p = sub.add_parser("endpunkt-teil")
    p.add_argument("--konfig", type=Path, required=True)
    p.add_argument("--log", type=Path, required=True)
    p.add_argument("--ordner", type=Path, required=True)
    p.add_argument("--von", required=True)
    p.add_argument("--bis", required=True)
    p.add_argument("--real", action="store_true")
    p = sub.add_parser("buendeln")
    p.add_argument("--konfig", type=Path, required=True)
    p.add_argument("--vm", type=Path, required=True)
    p.add_argument("--host", type=Path, required=True)
    p.add_argument("--endpunkt", type=Path)
    p.add_argument("--ausgabe", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "konfig-pruefen":
            konfig = load_konfig(args.konfig)
            print(json.dumps({"lauf": lauf_aus_konfig(konfig)[0]}, ensure_ascii=False, indent=1))
        elif args.cmd == "vm-teil":
            result = vm_teil(args.konfig, args.ordner, args.phase, real=args.real, manifest_path=args.manifest,
                             start_token=args.start_token)
            print(f"Teil: {result['teil']}\nABSCHLUSS-TOKEN (am Host eingeben): {result['vm_abschluss_token']}")
        elif args.cmd == "host-lauf":
            print(json.dumps(host_lauf(args.konfig, args.ordner, real=args.real), ensure_ascii=False, indent=1))
        elif args.cmd == "endpunkt-teil":
            print(endpunkt_teil(args.konfig, args.log, args.ordner, von=args.von, bis=args.bis, real=args.real))
        else:
            teile = {"vm": args.vm, "host": args.host, **({"endpunkt": args.endpunkt} if args.endpunkt else {})}
            print(buendeln(args.konfig, teile, args.ausgabe))
    except acc.AcceptanceRefused as exc:
        print("ABGELEHNT: " + "; ".join(exc.reasons), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
