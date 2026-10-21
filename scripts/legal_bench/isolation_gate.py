"""§87/§90 (I12): Externes Isolations-Gate des Antwort-Benchmarks - Owner-Freigabe mit Belegbindung (Schema v2).

Vor jedem Lauf und vor jedem Aufruf muss eine signierte Bestaetigung des Owners vorliegen, dass genau diese Zielumgebung
die freigegebene Netzwerkpolitik erfuellt UND dass die VM-Abnahme V1-V13 laut Belegen bestanden ist. Fehlt etwas, ist
etwas manipuliert, veraltet, fremd, widerrufen oder widerspruechlich: kein Start bzw. Abbruch vor dem Provider-Aufruf
(fail-closed).

Vertrauensmodell (AUDIT_S89.md, umgesetzt in §90, AUDIT_S90.md):
- Wurzelschluessel offline; sein Fingerabdruck steht im geprueften Code (owner_keys.PINNED_ROOT_FINGERPRINTS). Heute
  LEER -> Gate geschlossen.
- Schluesselliste (vom Wurzelschluessel signiert, Version, Ablauf <= 90 Tage, Widerrufe) in einer Datei, die fuer das
  Benchmark-Konto SCHREIBGESCHUETZT ist (Pfad: LEXONO_ISOLATION_TRUST_ANCHORS). Nur dort aktive, nicht widerrufene
  Freigabeschluessel mit Rolle "Owner" zaehlen.
- Bestaetigung v2 (LEXONO_ISOLATION_ATTESTATION) = signiertes kanonisches JSON, gebunden an: Lauf-ID, Maschine
  (Hostname + MachineGuid), SHA-256 der Netzwerkpolitik, SHA-256 des Firewall-Exports (= Beleg V1), Endpunkt, DNS-Modus,
  Pruefer (= Inhaber des Schluessels), Version und SHA-256 der Schluesselliste, Pruefwerkzeug (Commit + Fingerabdruck),
  SHA-256 des Belegbuendel-Manifests und das Urteil je Test; Gueltigkeit max. 24 h. Schema v1 (nur "bestanden"-Liste,
  ohne Belege) wird abgelehnt.
- Belegbuendel (LEXONO_ISOLATION_EVIDENCE): das Gate rechnet das Urteil SELBST aus den Belegen nach
  (isolation_evidence.evaluate). Eine Bestaetigung, deren Urteil nicht zu den Belegen passt, wird abgelehnt; eine
  manuelle PASS-Markierung ersetzt keine Pflichtpruefung.
- Test-Gates (`IsolationGate.for_tests`): Wurzel nur im Speicher, test-markiert, NIE ein Nicht-Loopback-Endpunkt; ein
  Produktiv-Gate lehnt test-markierte Listen und Bestaetigungen ab. Die Endpunktsperre (`answer_driver.RealCloudDisabled`,
  C-14) bleibt davon unberuehrt.

Grenzen: Die Signatur belegt die Willenserklaerung des Owners, nicht die Herkunft jeder Datei; Diebstahl von Schluessel
und Passphrase oder ein schreibbarer Anker bzw. veraenderter Code brechen das Modell (AUDIT_S90.md, Restrisiken)."""

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

from scripts.legal_bench import answer_specs, isolation_codepfad, isolation_evidence, owner_keys

SCHEMA = "lexono-isolation-attestation/2"
LEGACY_SCHEMAS = ("lexono-isolation-attestation/1",)
POLICY_PATH = answer_specs.ROOT / "isolation" / "netzwerkpolitik.json"
MAX_VALIDITY = timedelta(hours=24)
#: VM-Abnahmetests (AUDIT_S87.md), die laut Belegen PASS sein muessen
REQUIRED_TESTS = isolation_evidence.REQUIRED_TESTS
ENV_ANCHORS = "LEXONO_ISOLATION_TRUST_ANCHORS"
ENV_ATTESTATION = "LEXONO_ISOLATION_ATTESTATION"
ENV_EVIDENCE = "LEXONO_ISOLATION_EVIDENCE"
#: Dateien des Pruefwerkzeugs; ihr Fingerabdruck ist an Bestaetigung und Belegbuendel gebunden.
#: §96 (M4): ALLE ausfuehrungsrelevanten Dateien des Benchmarks (Treiber, Zaun, Ablage, Ausfuehrung, Isolation) und die
#: Provider-/Datenschutzpfade - nicht nur die Isolationsdateien. Die Signatur bindet den Inhalt des COMMITS (owner_sign).
#: §98 (H2): der GESAMTE ausfuehrbare Python-Code des Repositorys, der beim Benchmark bzw. bei den Abnahmetests
#: importiert werden kann (app/** inkl. der von e2e.py genutzten Module, scripts/** inkl. scripts/__init__.py, Module
#: im Wurzelverzeichnis - sie liegen beim Start im Suchpfad und koennten Bibliotheken ueberdecken -, tests/conftest.py)
#: sowie die Projekt-/Abhaengigkeitskonfiguration.
#: §102 (AUDIT_S100 A1): `*.py` in JEDEM importierbaren Ordner des Repositorys (isolation_codepfad.python_files - z. B.
#: auch gateway/, tests/, Wurzelmodule), nur Build-Ausgaben/Caches/Daten sind ausgenommen; dazu die Konfiguration.
TOOL_GLOBS = ("pyproject.toml", "setup.cfg", "requirements*.txt", "app/**/*.json", "app/**/*.yaml", "app/**/*.yml",
              "app/**/*.toml")
TOOL_FILES_EXTRA = (
    "tests/test_s86_isolation_design.py", "tests/test_s87_isolation_gate.py", "tests/test_s90_owner_gate.py",
    "tests/test_s91_acceptance_dry_run.py", "tests/test_s92_acl_counter_probe.py", "tests/test_s94_real_acceptance_path.py",
    "tests/test_s96_review_fixes.py", "tests/test_s98_review_fixes.py", "tests/isolation_test_support.py",
    "benchmarks/legal_answers_v1/isolation/vm_abnahme.json", "benchmarks/legal_answers_v1/isolation/netzwerkpolitik.json",
)


def im_fingerprint(name: str) -> bool:
    """§105 (A10): Gehoert ein repo-relativer POSIX-Pfad zum Fingerabdruck? Spiegelt tool_files() - dient dem Abgleich
    der im COMMIT verfolgten Dateien gegen den Arbeitsbaum (geloeschte/hinzugefuegte Dateien erkennen)."""
    from pathlib import PurePosixPath

    if name in TOOL_FILES_EXTRA:
        return True
    pure = PurePosixPath(name)
    if any(pure.full_match(pattern) for pattern in TOOL_GLOBS):
        return True
    if name.endswith(".py"):
        segments = name.split("/")[:-1]
        for index, segment in enumerate(segments):
            if segment in isolation_codepfad.EXCLUDED_ANY or segment.startswith(".") or segment.endswith(".egg-info") \
                    or (index == 0 and segment in isolation_codepfad.EXCLUDED_ROOT):
                return False
        return True
    return False


def tool_files() -> tuple[str, ...]:
    files = {path.relative_to(answer_specs.REPO).as_posix() for pattern in TOOL_GLOBS
             for path in answer_specs.REPO.glob(pattern)
             if (path.is_file() or path.is_symlink()) and "__pycache__" not in path.parts}
    files |= set(isolation_codepfad.python_files(answer_specs.REPO))
    return tuple(sorted(files | set(TOOL_FILES_EXTRA)))


def codepfad_probleme() -> list[str]:
    """§102 (A1): Codequellen dieses Prozesses - leer nur bei Bytecode-Isolation und ausschliesslich erfasstem Code."""
    try:
        return isolation_codepfad.code_source_problems(answer_specs.REPO, set(tool_files()))
    except Exception as exc:  # noqa: BLE001 - nicht pruefbar = geschlossen
        return [f"Codequellen nicht pruefbar ({type(exc).__name__})"]


#: §102 (A3): Zeitanker. Die Gueltigkeit beim GEBRAUCH haengt nicht allein an der VM-Uhr: Das Ende der Nachher-Phase
#: (Host-Uhr, im signierten Buendel) plus die seither vergangene Zeit nach dem monotonen Systemzaehler der VM
#: (GetTickCount64 - zaehlt seit dem Start, auch im Ruhezustand, von keinem Konto stellbar) muss vor dem Ablauf liegen.
#: Ein Neustart (andere Boot-ID) oder ein fehlender Anker -> geschlossen.
ZEITANKER_DRIFT = 0.001  # 0,1 % zusaetzliche Laufzeit als Sicherheitsabschlag (Zaehler-Drift)
ZEITANKER_MARGE = timedelta(minutes=5)
BOOT_ID_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters"


def vm_zeitbasis() -> dict:
    """Monotoner Zaehler (ms seit Systemstart) und Boot-ID dieser Maschine; nicht lesbar -> None (fail-closed)."""
    tick = boot = None
    if sys.platform == "win32":
        try:
            import ctypes
            import winreg

            getter = ctypes.windll.kernel32.GetTickCount64
            getter.restype = ctypes.c_ulonglong
            tick = int(getter())
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, BOOT_ID_KEY) as key:
                boot = int(winreg.QueryValueEx(key, "BootId")[0])
        except (OSError, AttributeError, ValueError):
            tick = boot = None
    return {"tick_ms": tick, "boot_id": boot}


def zeitanker_probleme(anchor: dict | None, current: dict, valid_to: datetime) -> list[str]:
    """§102 (A3): Ablauf beim Gebrauch ueber Host-Zeitanker + monotone VM-Dauer (siehe oben). Leer = noch gueltig."""
    if not isinstance(anchor, dict):
        return ["kein Zeitanker im Belegbuendel - Gueltigkeit beim Gebrauch nicht unabhaengig von der VM-Uhr pruefbar"]
    tick, boot = current.get("tick_ms"), current.get("boot_id")
    if type(tick) is not int or type(boot) is not int:
        return ["monotoner Zaehler bzw. Boot-ID der VM nicht lesbar - Gueltigkeit nicht pruefbar"]
    if boot != anchor["boot_id"]:
        return ["VM seit der Abnahme neu gestartet (Boot-ID) - Zeitanker ungueltig, neue Abnahme noetig"]
    elapsed = tick - anchor["tick_ms"]
    if elapsed < 0:
        return ["monotoner Zaehler der VM liegt vor dem Zeitanker (widerspruechlich)"]
    latest_now = datetime.fromisoformat(anchor["host_spaetestens"]) + timedelta(milliseconds=elapsed * (1 + ZEITANKER_DRIFT)) + ZEITANKER_MARGE
    if latest_now > valid_to:
        return ["Bestaetigung laut Host-Zeitanker und VM-Laufzeit abgelaufen (VM-Uhr nicht massgeblich)"]
    return []


#: Umgebungsvariablen, die beeinflussen, welcher Code geladen wird. Gesetzt = Teil des Fingerabdrucks (Wert normalisiert).
IMPORT_ENV = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONSAFEPATH", "PYTHONNOUSERSITE",
              "PYTHONINSPECT", "PYTHONEXECUTABLE")


def _normalize_paths(data: bytes) -> bytes:
    """Absolute Pfade (Repository, venv) gleich behandeln, damit Host und VM bei gleicher Installation gleich sind."""
    import sys as _sys

    replacements = {str(answer_specs.REPO): "<REPO>", str(answer_specs.REPO).replace("\\", "/"): "<REPO>",
                    _sys.prefix: "<PREFIX>", _sys.prefix.replace("\\", "/"): "<PREFIX>"}
    text = data.replace(b"\r\n", b"\n").decode("utf-8", "surrogateescape")
    for old, new in sorted(replacements.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(old, new).replace(old.lower(), new)
    return text.encode("utf-8", "surrogateescape")


def laufzeit_stand() -> dict:
    """§98 (H2): Laufzeitumgebung, die Code ausfuehren kann, ohne im Repository zu liegen.

    - Python-Version/Implementierung;
    - installierte Distributionen (Name==Version);
    - Inhalt ALLER .pth-Dateien und der Editable-Finder sowie sitecustomize/usercustomize (sie laufen beim Start);
    - Liste der Eintraege in site-packages (unbekannte Module/Ordner aendern den Fingerabdruck);
    - gesetzte Import-Umgebungsvariablen.
    Nicht lesbar -> sha256 None (fail-closed)."""
    import importlib.metadata
    import platform
    import site
    import sys as _sys

    try:
        dists = sorted({f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions()
                        if d.metadata and d.metadata["Name"]})
        packages = [Path(p) for p in site.getsitepackages() if Path(p).name.lower() == "site-packages"]
        startup: dict[str, str] = {}
        entries: list[str] = []
        for folder in packages:
            for path in sorted(folder.iterdir()):
                entries.append(path.name)
                name = path.name.lower()
                if path.is_file() and (name.endswith(".pth") or name.startswith("__editable__")
                                       or name in ("sitecustomize.py", "usercustomize.py")):
                    startup[path.name] = hashlib.sha256(_normalize_paths(path.read_bytes())).hexdigest()
        env = {k: _normalize_paths(os.environ[k].encode()).decode() for k in IMPORT_ENV if k in os.environ}
        state = {"python": f"{platform.python_implementation()} {platform.python_version()}", "distributionen": dists,
                 "startcode": startup, "site_packages": sorted(entries), "umgebung": env}
    except Exception:  # noqa: BLE001 - nicht erfassbar = geschlossen
        return {"python": None, "sha256": None}
    return {"python": state["python"], "sha256": hashlib.sha256(canonical(state)).hexdigest()}


TOOL_FILES = tool_files()


class IsolationNotConfirmed(RuntimeError):
    """Keine gueltige Bestaetigung der Isolation - kein Start, kein Aufruf."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("Isolation nicht bestaetigt: " + "; ".join(reasons[:3]))
        self.reasons = list(reasons)


def canonical(payload: dict) -> bytes:
    return owner_keys.canonical(payload)


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


def pruefwerkzeug_stand() -> dict:
    """Commit (ohne Prozessstart gelesen) und Fingerabdruck der Pruefwerkzeug-Dateien (Zeilenenden normalisiert, damit
    Host- und VM-Checkout desselben Commits gleich sind). Fehlende Datei oder unbestimmbarer Commit -> None (fail-closed)."""
    from scripts.legal_bench import answer_run

    digests = {}
    laufzeit = laufzeit_stand()
    for name in tool_files():
        path = answer_specs.REPO / name
        try:
            if path.is_symlink():  # §98: Symlinks sind keine geprueften Dateien (Ziel ausserhalb moeglich)
                raise OSError("Symlink")
            data = path.read_bytes().replace(b"\r\n", b"\n")
        except OSError:
            return {"commit": answer_run._git_commit(), "sha256": None, "laufzeit_sha256": laufzeit["sha256"]}
        digests[name] = hashlib.sha256(data).hexdigest()
    return {"commit": answer_run._git_commit(), "sha256": hashlib.sha256(canonical(digests)).hexdigest(),
            "laufzeit_sha256": laufzeit["sha256"]}


def guest_firewall() -> dict:
    """Gast-Firewall lesen (zweite Linie; nur Plausibilitaet - die Grenze liegt am Host/Perimeter)."""
    if sys.platform != "win32":
        return {}
    # §96 (M5): wirksame Einstellung (ActiveStore) statt nur lokal gespeicherter (PersistentStore) - NICHT VERIFIZIERT
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-NetFirewallProfile -PolicyStore ActiveStore | Select-Object Name,"
                          "@{n='Enabled';e={[bool]$_.Enabled}},"
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


def _approved_endpoints(policy: dict) -> set[tuple[str, int]]:
    endpoints = [f for f in policy.get("freigaben", []) if f.get("ip_adressen") and f.get("port")]
    return {(ip, int(f["port"])) for f in endpoints for ip in f["ip_adressen"]}


@dataclass
class IsolationGate:
    keylist_path: Path | None
    attestation_path: Path | None
    evidence_path: Path | None = None
    policy_path: Path = POLICY_PATH
    test_only: bool = False
    pinned_roots: tuple[str, ...] = owner_keys.PINNED_ROOT_FINGERPRINTS
    min_keylist_version: int = owner_keys.MIN_KEYLIST_VERSION
    setup_problems: list[str] = field(default_factory=list)
    identity: object = machine_identity
    firewall: object = guest_firewall
    tool: object = pruefwerkzeug_stand
    codepfad: object = codepfad_probleme
    zeitbasis: object = vm_zeitbasis
    now: object = lambda: datetime.now(timezone.utc)  # noqa: E731

    @classmethod
    def from_environment(cls) -> IsolationGate:
        """Produktiv: Schluesselliste, Bestaetigung und Belegbuendel NUR aus vom Owner bereitgestellten Pfaden."""
        problems: list[str] = []
        anchor_path = os.environ.get(ENV_ANCHORS)
        if not anchor_path:
            problems.append("kein Vertrauensanker (Schluesselliste des Owners) hinterlegt - Gate geschlossen")
        attestation = os.environ.get(ENV_ATTESTATION)
        evidence = os.environ.get(ENV_EVIDENCE)
        return cls(keylist_path=Path(anchor_path) if anchor_path else None,
                   attestation_path=Path(attestation) if attestation else None,
                   evidence_path=Path(evidence) if evidence else None, setup_problems=problems)

    @classmethod
    def for_tests(cls, keylist_path: Path, attestation_path: Path, evidence_path: Path, policy_path: Path, *,
                  pinned_roots: tuple[str, ...], **kwargs) -> IsolationGate:  # noqa: ANN003
        """NUR fuer Tests: Wurzel im Speicher, Liste/Bestaetigung muessen test-markiert sein, nur Loopback-Endpunkte."""
        return cls(keylist_path=keylist_path, attestation_path=attestation_path, evidence_path=evidence_path,
                   policy_path=policy_path, test_only=True, pinned_roots=pinned_roots, **kwargs)

    def load_keylist(self, now: datetime) -> tuple[owner_keys.KeyList | None, list[str]]:
        if self.keylist_path is None:
            return None, []
        path = Path(self.keylist_path)
        if not path.is_file():
            return None, ["Vertrauensanker-Datei (Schluesselliste) nicht vorhanden"]
        if not self.test_only and _writable_by_this_process(path):
            return None, ["Vertrauensanker ist fuer den Benchmark-Prozess schreibbar - kein Vertrauensnachweis"]
        try:
            raw = path.read_bytes()
        except OSError as exc:
            return None, [f"Vertrauensanker unlesbar ({type(exc).__name__})"]
        return owner_keys.verify_keylist(raw, pinned_roots=self.pinned_roots, min_version=self.min_keylist_version,
                                         now=now, test_only=self.test_only)

    def problems(self, *, run_id: str | None, provider_endpoints: set[tuple[str, int]],
                 check_firewall: bool = True) -> list[str]:
        """Alle Gruende gegen einen Start/Aufruf; leer = bestaetigt. Jeder unerwartete Fehler = geschlossen."""
        try:
            return self._problems(run_id=run_id, provider_endpoints=provider_endpoints, check_firewall=check_firewall)
        except Exception as exc:  # noqa: BLE001 - z. B. unerwartete Typen in Bestaetigung/Belegen: nie fail-open
            return [f"Isolationspruefung fehlgeschlagen ({type(exc).__name__}) - fail-closed"]

    def _problems(self, *, run_id: str | None, provider_endpoints: set[tuple[str, int]],
                  check_firewall: bool) -> list[str]:
        problems = list(self.setup_problems)
        now = self.now()  # ein Zeitpunkt fuer alle Pruefungen dieses Aufrufs
        if not self.pinned_roots:
            problems.append(owner_keys.ROOT_MISSING)
        keylist, keylist_problems = self.load_keylist(now)
        problems += [p for p in keylist_problems if p not in problems]
        if keylist is None and not problems:
            problems.append("kein Vertrauensanker - Gate geschlossen")
        # --- Politik ------------------------------------------------------------------------------------------------
        try:
            policy = json.loads(Path(self.policy_path).read_text(encoding="utf-8"))
            policy_sha = sha256_file(self.policy_path)
        except (OSError, ValueError) as exc:
            return problems + [f"Netzwerkpolitik nicht lesbar ({type(exc).__name__})"]
        if policy.get("status") != "FREIGEGEBEN":
            problems.append(f"Netzwerkpolitik nicht freigegeben (Status {policy.get('status')!r})")
        approved = _approved_endpoints(policy)
        if not approved:
            problems.append("kein freigegebener Endpunkt in der Netzwerkpolitik")
        if not policy.get("dns", {}).get("entscheidung"):
            problems.append("DNS-Entscheidung der Netzwerkpolitik OFFEN")
        # --- Bestaetigung -------------------------------------------------------------------------------------------
        if self.attestation_path is None or not Path(self.attestation_path).is_file():
            return problems + ["keine Bestaetigung des Owners vorhanden"]
        try:
            document = json.loads(Path(self.attestation_path).read_text(encoding="utf-8"))
            payload, signature, key_id = document["payload"], bytes.fromhex(document["signature_hex"]), document["key_id"]
            if not isinstance(payload, dict):
                raise TypeError("payload")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return problems + [f"Bestaetigung unlesbar ({type(exc).__name__})"]
        if payload.get("schema") in LEGACY_SCHEMAS:
            return problems + ["Bestaetigung im veralteten Schema v1 (ohne Belegbindung) - abgelehnt"]
        if keylist is None:
            return problems + ["Bestaetigung nicht pruefbar: keine gueltige Schluesselliste"]
        if key_id in keylist.revoked:
            return problems + [f"Schluessel {key_id!r} ist widerrufen (seit {keylist.revoked[key_id]['seit']})"]
        anchor = keylist.anchors.get(key_id)
        if anchor is None:
            return problems + [f"Bestaetigung mit unbekanntem Schluessel {key_id!r} (nicht in der Schluesselliste)"]
        try:
            anchor["key"].verify(signature, canonical(payload))
        except Exception:  # noqa: BLE001 - jede Abweichung = ungueltig
            return problems + ["Signatur der Bestaetigung ungueltig (manipuliert oder fremd)"]
        try:
            valid_from = datetime.fromisoformat(payload["gueltig_von"])
            valid_to = datetime.fromisoformat(payload["gueltig_bis"])
            if valid_from.tzinfo is None or valid_to.tzinfo is None:
                raise ValueError("ohne Zeitzone")
        except (KeyError, ValueError, TypeError):
            return problems + ["Gueltigkeitsangaben fehlen oder sind ungueltig"]
        if payload.get("schema") != SCHEMA:
            problems.append("Bestaetigung hat ein unbekanntes Schema")
        if bool(payload.get("test_only")) != self.test_only:
            problems.append("Test-Bestaetigung im Produktivbetrieb bzw. umgekehrt - abgelehnt")
        if not valid_from <= now <= valid_to:
            problems.append(isolation_evidence.MSG_ATTESTATION_EXPIRED)
        if valid_to - valid_from > MAX_VALIDITY:
            problems.append("Gueltigkeitsfenster der Bestaetigung laenger als 24 Stunden")
        if now > anchor["gueltig_bis"]:
            problems.append("Schluessel des Owners abgelaufen")
        if not anchor["gueltig_von"] <= valid_from <= anchor["gueltig_bis"]:
            problems.append("Bestaetigung ausserhalb der Gueltigkeit des Schluessels ausgestellt")
        if payload.get("run_id") != run_id:
            problems.append(f"Bestaetigung gilt fuer einen anderen Lauf ({payload.get('run_id')!r})")
        identity = self.identity()
        if not identity.get("machine_guid") or payload.get("maschine") != identity:
            problems.append("Bestaetigung gilt fuer eine andere Maschine/VM (Hostname oder MachineGuid)")
        if payload.get("netzwerkpolitik_sha256") != policy_sha:
            problems.append("Netzwerkpolitik seit der Bestaetigung geaendert")
        attested = {(e.get("ip"), e.get("port")) for e in payload.get("endpunkte") or [] if isinstance(e, dict)}
        if attested != approved:
            problems.append("Endpunkt der Bestaetigung weicht von der Netzwerkpolitik ab")
        if not set(provider_endpoints) <= attested:
            problems.append(f"Provider-Endpunkt {sorted(provider_endpoints)} nicht bestaetigt")
        if self.test_only and any(not str(ip).startswith("127.") and ip != "::1"
                                  for ip, _ in attested | set(provider_endpoints)):
            problems.append("Test-Gate darf nur Loopback-Endpunkte zulassen")
        if payload.get("dns_modus") != policy.get("dns", {}).get("entscheidung"):
            problems.append("DNS-Modus der Bestaetigung weicht von der Netzwerkpolitik ab")
        pruefer = payload.get("pruefer") or {}
        if not pruefer.get("name") or pruefer.get("name") != anchor["inhaber"] \
                or pruefer.get("rolle") != owner_keys.OWNER_ROLE:
            problems.append("Pruefer der Bestaetigung ist nicht der Inhaber des Signaturschluessels (Owner)")
        if payload.get("schluesselliste") != {"version": keylist.version, "sha256": keylist.sha256}:
            problems.append("Bestaetigung gilt fuer eine andere Schluesselliste (Rotation/Widerruf seither)")
        tool = self.tool()
        if not tool.get("commit") or not tool.get("sha256") or not tool.get("laufzeit_sha256"):
            problems.append("Pruefwerkzeug nicht bestimmbar (Commit/Dateien/Laufzeitumgebung) - fail-closed")
        elif payload.get("pruefwerkzeug") != tool:
            problems.append("Pruefwerkzeug seit der Bestaetigung geaendert (Commit oder Dateien)")
        problems += [f"Codequelle: {p}" for p in self.codepfad()]  # §102 (A1): tatsaechlich geladener Code
        # --- Belege: Urteil wird nachgerechnet ----------------------------------------------------------------------
        lauf = {"run_id": run_id, "maschine": identity, "netzwerkpolitik_sha256": policy_sha, "pruefwerkzeug": tool}
        verdict = isolation_evidence.evaluate(self.evidence_path, policy=policy, reference=valid_from, now=now,
                                              allow_dry_run=self.test_only, lauf=lauf)
        basis = self.zeitbasis()
        problems += zeitanker_probleme(verdict.get("zeitanker"), basis, valid_to)  # §102 (A3)
        # §104 (A13): Das Belegalter beim Gebrauch (24 h) nicht allein an der VM-Uhr messen. Liegt der aus dem Host-
        # Zeitanker und dem monotonen VM-Zaehler abgeleitete Zeitpunkt SPAETER als die VM-Uhr (zurueckgestellte Uhr),
        # dann mit diesem strengeren Zeitpunkt neu bewerten.
        anker = verdict.get("zeitanker")
        if anker and type(basis.get("tick_ms")) is int and basis.get("boot_id") == anker.get("boot_id"):
            elapsed = basis["tick_ms"] - anker["tick_ms"]
            if elapsed >= 0:
                anker_now = datetime.fromisoformat(anker["host_spaetestens"]) + timedelta(
                    milliseconds=elapsed * (1 + ZEITANKER_DRIFT))
                if anker_now > now:
                    verdict = isolation_evidence.evaluate(self.evidence_path, policy=policy, reference=valid_from,
                                                          now=anker_now, allow_dry_run=self.test_only, lauf=lauf)
        problems += verdict["probleme"]
        if verdict["manifest_sha256"] != payload.get("buendel_sha256"):
            problems.append("Belegbuendel weicht von der Bestaetigung ab (fehlend, ausgetauscht oder manipuliert)")
        not_passed = [f"{t}: {r['status']}" for t, r in verdict["tests"].items() if r["status"] != isolation_evidence.PASS]
        if not_passed:
            problems.append(f"VM-Abnahme laut Belegen nicht vollstaendig PASS ({', '.join(not_passed[:5])})")
        claimed = (payload.get("urteil") or {}) if isinstance(payload.get("urteil"), dict) else {}
        if claimed.get("tests") != {t: r["status"] for t, r in verdict["tests"].items()} \
                or claimed.get("gesamt") != verdict["gesamt"]:
            problems.append("Urteil der Bestaetigung widerspricht den Belegen")
        firewall_sha = payload.get("firewall_export_sha256")
        if not firewall_sha or firewall_sha != verdict["firewall_export_sha256"]:
            problems.append("Firewall-Export der Bestaetigung passt nicht zum Beleg V1 (Host/Perimeter)")
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
