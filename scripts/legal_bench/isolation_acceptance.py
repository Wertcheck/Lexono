"""§91 (A3): Host- und VM-Abnahmeskripte fuer V1-V13 - Beobachten, Belegteile schreiben, Buendeln, Trockenlauf.

Ablauf der spaeteren realen Abnahme (AUDIT_S89 §3, AUDIT_S91.md):
1. Host startet Kanarien-Listener (`CanaryListener`) und exportiert Port-ACLs/VM-Konfiguration (Rolle "host" = E1).
2. VM fuehrt die Abnahme-Proben/-Tests aus und liest ihre eigene Konfiguration (Rolle "vm" = E0).
3. Endpunkt-Log wird importiert (Rolle "endpunkt" = E2).
4. Host buendelt die Teile (`assemble`), `owner_sign urteil` zeigt das Urteil, der Owner signiert nur bei PASS.

Grundsaetze:
- Jeder Beleg enthaelt getrennt `soll` (aus der Abnahmematrix/den Anforderungen) und `beobachtung` (Rohdaten). Die
  Bewertung leitet `isolation_evidence.derive` aus der Beobachtung ab - auch beim Urteil und im Gate erneut.
- Jeder Beleg ist mit Test-ID, Zeit, Umgebung (Erzeuger, Modus) und `lauf_sha256` (Lauf, Maschine, Politik,
  Pruefwerkzeug) verknuepft; die Klasse folgt allein aus der Rolle des Teils (vm=E0, host=E1, endpunkt=E2). Eine Rolle
  kann nur die Belege schreiben, die die Anforderungen fuer ihre Klasse vorsehen (die VM kann keinen E1-Kanarienbeleg
  erzeugen).
- Fehler eines Beobachters werden als Beobachtung `{"fehler": ...}` festgehalten -> Ableitung unklar -> FAIL; ein nicht
  vorhandener Beobachter erzeugt keinen Beleg -> BLOCKIERT/NICHT DURCHGEFUEHRT. Nichts wird geraten.
- Echte Beobachter (PowerShell, nur lesend - Ausnahme V2-Aenderungsversuch in der VM, der scheitern MUSS) laufen nur
  mit `LEXONO_ISOLATION_ACCEPTANCE=1` UND ausdruecklichem `real=True`. Ohne beides: Ablehnung vor jedem Prozessstart.
  Belege im Modus "trockenlauf" lehnt ein Produktiv-Gate ab.
- Kein Provider-Aufruf, keine Verbindung nach aussen: der Trockenlauf nutzt nur Loopback (Listener und Proben)."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import quoteattr

from scripts.legal_bench import isolation_evidence as ev

ROLLEN = {"vm": "vm-abnahme", "host": "host-abnahme", "endpunkt": "endpunkt-protokoll"}
PART_SCHEMA = "lexono-isolation-evidence-part/1"
PART_MANIFEST = "teil.json"
PART_STATE = "teil.offen.json"  # §94: Zwischenstand eines Teils ueber mehrere Phasen (CLI-Aufrufe)
ENV_REAL = "LEXONO_ISOLATION_ACCEPTANCE"
MODES = ("trockenlauf", "real")
MATRIX_PATH = Path(__file__).resolve().parents[2] / "benchmarks" / "legal_answers_v1" / "isolation" / "vm_abnahme.json"


class AcceptanceRefused(RuntimeError):
    """Abnahmeschritt verweigert (falsche Rolle, widerspruechlicher Teil, echter Modus ohne Freigabe ...)."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons[:5]))
        self.reasons = list(reasons)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(value) -> bytes:  # noqa: ANN001
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def requirement(test: str, art: str, klasse: str) -> dict | None:
    for req in ev.ANFORDERUNGEN.get(test, []):
        if req["art"] == art and req["klasse"] == klasse:
            return req
    return None


def soll(test: str, req: dict) -> dict:
    """Sollzustand eines Belegs: Text der Abnahmematrix + maschinenpruefbare Erwartung (aus den Anforderungen)."""
    matrix = {t["id"]: t for t in json.loads(MATRIX_PATH.read_text(encoding="utf-8"))["tests"]}
    expected = {"pytest": {"knoten": list(req.get("knoten", ())), "ergebnis": "passed"},
                "kriterien": {"kriterien": dict.fromkeys(req.get("kriterien", ()), True)},
                "kanarien": {"treffer": [], "merkmale": list(req.get("merkmale", ())), "listener_ueber_pruefzeitraum": True},
                "firewall_export": {"standard_ausgehend": "Block", "erlaubt": "nur Endpunkte der Netzwerkpolitik (TCP)"},
                "gegenprobe": {"kanaele": list(req.get("kanaele", ())), "vorher": "Kanarienziel je Kanal aus der VM erreichbar "
                               "(Treffer am Host), Listener mit Funktionskontrolle, ACL waehrend der Gegenprobe != finale ACL",
                               "danach": "ACL-Export (V1), dann Nachher-Proben ohne Treffer auf denselben Zielen"}}
    expected["kanarien"]["abschluss"] = {"geleert": True, "threads_beendet": True, "nachlauf_s_mindestens": ev.MIN_NACHLAUF_S}
    return {"matrix": matrix[test]["soll"], "erwartet": expected[req["art"]], "klasse_mindestens": req["klasse"]}


def evidence_content(test: str, art: str, klasse: str, *, beobachtung: dict, lauf: dict, policy: dict,  # noqa: PLR0913
                     umgebung: dict, erzeugt: str, konfiguration=None, strict: bool = True,  # noqa: ANN001
                     parameter: dict | None = None) -> dict:
    """Inhalt eines Belegs. `strict=False` nur fuer Negativtests (Beleg ausserhalb der Anforderungen). `parameter` =
    Soll-Werte aus der Abnahme-Konfiguration (z. B. Switch, Benchmark-Konto, Datentraeger) - Teil des Solls, nie der
    Beobachtung (§96)."""
    req = requirement(test, art, klasse)
    if req is None and strict:
        raise AcceptanceRefused([f"{test}: kein Pflichtbeleg {art} in Klasse {klasse} vorgesehen"])
    target = soll(test, req) if req else {"matrix": None, "erwartet": None, "klasse_mindestens": None}
    needed = ev.SOLL_PARAMETER.get((test, klasse), ()) if art == "kriterien" else ()
    if needed:
        target["parameter"] = {k: (parameter or {}).get(k) for k in needed}
    return {"art": art, "test": test, "erzeugt": erzeugt, "umgebung": umgebung, "lauf_sha256": ev.lauf_sha256(lauf),
            "konfiguration_sha256": hashlib.sha256(_canonical(konfiguration)).hexdigest() if konfiguration is not None
            else None, "soll": target, "beobachtung": beobachtung,
            # nur zur Lesbarkeit - Urteil und Gate leiten erneut aus der Beobachtung ab
            **ev.derive(test, art, klasse, beobachtung, policy, target)}


def environment(rolle: str, mode: str) -> dict:
    if mode not in MODES:
        raise AcceptanceRefused([f"unbekannter Modus {mode!r}"])
    return {"erzeuger": ROLLEN[rolle], "modus": mode, "hostname": socket.gethostname().lower(),
            "python": platform.python_version(), "plattform": platform.platform(terse=True)}


# --- Belegteil einer Rolle -------------------------------------------------------------------------------------------------


@dataclass
class EvidencePart:
    folder: Path
    rolle: str
    lauf: dict
    policy: dict
    mode: str = "trockenlauf"
    clock: object = utcnow
    entries: list = field(default_factory=list)
    parameter: dict = field(default_factory=dict)  # §96: Soll-Parameter (aus der Konfiguration)

    def __post_init__(self) -> None:
        if self.rolle not in ROLLEN:
            raise AcceptanceRefused([f"unbekannte Rolle {self.rolle!r}"])
        if self.mode == "real" and os.environ.get(ENV_REAL) != "1":
            raise AcceptanceRefused([f"Belege im Modus 'real' nur in der freigegebenen Abnahmeumgebung ({ENV_REAL}=1)"])
        self.folder = Path(self.folder)
        if self.folder.exists() and any(self.folder.iterdir()) and not getattr(self, "_resuming", False):
            raise AcceptanceRefused([f"{self.folder} ist nicht leer - Belege werden nie ueberschrieben"])
        self.folder.mkdir(parents=True, exist_ok=True)
        self.erzeuger = ROLLEN[self.rolle]
        self.klasse = ev.ERZEUGER[self.erzeuger]
        self.umgebung = environment(self.rolle, self.mode)

    def record(self, test: str, art: str, beobachtung: dict, *, konfiguration=None) -> Path:  # noqa: ANN001
        if not isinstance(beobachtung, dict):
            raise AcceptanceRefused([f"{test}: Beobachtung ist kein Objekt"])
        created = self.clock().isoformat()
        content = evidence_content(test, art, self.klasse, beobachtung=beobachtung, lauf=self.lauf, policy=self.policy,
                                   umgebung=self.umgebung, erzeugt=created, konfiguration=konfiguration,
                                   parameter=self.parameter)
        name = f"{test}/{art}_{self.klasse}.json"
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(content, ensure_ascii=False, indent=1).encode("utf-8")
        with open(path, "xb") as handle:  # exklusiv
            handle.write(raw)
        self.entries.append({"datei": name, "test": test, "art": art, "erzeuger": self.erzeuger, "klasse": self.klasse,
                             "erzeugt": created, "sha256": hashlib.sha256(raw).hexdigest()})
        self._save_state()
        return path

    def has(self, test: str, art: str) -> bool:
        return any(e["test"] == test and e["art"] == art for e in self.entries)

    def _state(self) -> dict:
        return {"schema": PART_SCHEMA, "rolle": self.rolle, "erzeuger": self.erzeuger, "lauf": self.lauf,
                "modus": self.mode, "belege": self.entries}

    def _save_state(self) -> None:
        (self.folder / PART_STATE).write_text(json.dumps(self._state(), ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def resume(cls, folder: Path, rolle: str, lauf: dict, policy: dict, *, mode: str,
               parameter: dict | None = None) -> EvidencePart:
        """Offenen Teil fortsetzen (naechste Phase). Abgeschlossene Teile, fremde Laeufe/Rollen/Modi und veraenderte
        Belege werden abgelehnt."""
        folder = Path(folder)
        if (folder / PART_MANIFEST).exists():
            raise AcceptanceRefused([f"Teil {folder} ist bereits abgeschlossen"])
        try:
            state = json.loads((folder / PART_STATE).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise AcceptanceRefused([f"kein offener Teil in {folder} ({type(exc).__name__})"]) from None
        if state.get("rolle") != rolle or state.get("lauf") != lauf or state.get("modus") != mode:
            raise AcceptanceRefused(["offener Teil gehoert zu anderer Rolle, anderem Lauf oder Modus"])
        for entry in state.get("belege", []):
            if hashlib.sha256((folder / entry["datei"]).read_bytes()).hexdigest() != entry["sha256"]:
                raise AcceptanceRefused([f"Beleg {entry['datei']!r} seit der letzten Phase veraendert"])
        part = cls.__new__(cls)
        part._resuming = True
        cls.__init__(part, folder, rolle, lauf, policy, mode=mode, entries=list(state["belege"]),
                     parameter=dict(parameter or {}))
        return part

    def observe(self, test: str, art: str, observer, *args, **kwargs) -> Path | None:  # noqa: ANN001, ANN002, ANN003
        """Beobachter ausfuehren; fehlt er -> kein Beleg; Fehler -> Beobachtung {"fehler"} (nie PASS)."""
        if observer is None:
            return None
        try:
            beobachtung = observer(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - jeder Fehler wird festgehalten, nicht verschluckt
            beobachtung = {"fehler": f"{type(exc).__name__}: {str(exc)[:200]}"}
        if not isinstance(beobachtung, dict):
            beobachtung = {"fehler": f"Beobachter lieferte {type(beobachtung).__name__}"}
        return self.record(test, art, beobachtung)

    def close(self) -> Path:
        manifest = {"schema": PART_SCHEMA, "rolle": self.rolle, "erzeuger": self.erzeuger, "lauf": self.lauf,
                    "modus": self.mode, "belege": self.entries}
        path = self.folder / PART_MANIFEST
        with open(path, "x", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=1)
        return path


def assemble(target: Path, parts: dict[str, Path], *, lauf: dict) -> Path:
    """Teile (Rolle -> Ordner) zu einem Belegbuendel. Jeder Widerspruch -> AcceptanceRefused, kein Buendel."""
    target = Path(target)
    if target.exists():
        raise AcceptanceRefused([f"{target} existiert bereits - Buendel werden nie ueberschrieben"])
    reasons: list[str] = []
    entries: list[dict] = []
    files: list[tuple[Path, str]] = []
    modes: set = set()
    for rolle, folder in parts.items():
        try:
            manifest = json.loads((Path(folder) / PART_MANIFEST).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"Teil {rolle}: Manifest unlesbar ({type(exc).__name__})")
            continue
        if rolle not in ROLLEN or manifest.get("schema") != PART_SCHEMA or manifest.get("rolle") != rolle \
                or manifest.get("erzeuger") != ROLLEN[rolle]:
            reasons.append(f"Teil {rolle}: Rolle/Erzeuger widerspricht dem Teil (angegeben {manifest.get('rolle')!r})")
            continue
        if manifest.get("lauf") != lauf:
            reasons.append(f"Teil {rolle}: gehoert zu einem anderen Lauf")
            continue
        modes.add(manifest.get("modus"))
        for entry in manifest.get("belege", []):
            if entry.get("erzeuger") != ROLLEN[rolle] or entry.get("klasse") != ev.ERZEUGER[ROLLEN[rolle]]:
                reasons.append(f"Teil {rolle}: Beleg {entry.get('datei')!r} beansprucht fremde Klasse/Erzeuger")
                continue
            source = (Path(folder) / entry["datei"]).resolve()
            if not source.is_relative_to(Path(folder).resolve()) or not source.is_file():
                reasons.append(f"Teil {rolle}: Beleg {entry.get('datei')!r} fehlt oder liegt ausserhalb")
                continue
            if hashlib.sha256(source.read_bytes()).hexdigest() != entry.get("sha256"):
                reasons.append(f"Teil {rolle}: Beleg {entry['datei']!r} nach dem Schreiben veraendert")
                continue
            if any(e["datei"] == entry["datei"] for e in entries):
                reasons.append(f"Beleg {entry['datei']!r} kommt in mehreren Teilen vor")
                continue
            entries.append(entry)
            files.append((source, entry["datei"]))
    if len(modes) > 1:
        reasons.append(f"Teile mischen Modi ({', '.join(sorted(map(str, modes)))}) - Trockenlauf und echte Belege nie zusammen")
    if reasons:
        raise AcceptanceRefused(reasons)
    target.mkdir(parents=True)
    for source, name in files:
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target / name)
    (target / ev.MANIFEST_NAME).write_text(json.dumps({"schema": ev.MANIFEST_SCHEMA, "lauf": lauf, "belege": entries},
                                                      ensure_ascii=False, indent=1), encoding="utf-8")
    return target


# --- Kanarien-Listener (Host, E1) ------------------------------------------------------------------------------------------


def _loopback(ip: str) -> bool:
    import ipaddress

    return ipaddress.ip_address(ip).is_loopback


def new_token() -> str:
    """Phasen-Token (128 bit). Belegt Reihenfolgen ohne Uhrenvergleich zwischen VM und Host (§96, M6)."""
    return secrets.token_hex(16)


class CanaryListener:
    """Kanarienziele auf dem Host: jede eingehende Verbindung/jedes Datagramm ist ein Treffer (= Abfluss aus der VM).

    §96:
    - H1: Eine Phase endet erst nach einer Ruhezeit ohne Treffer (`settle`, monoton gemessen); beim Beenden werden alle
      Sockets bis zur Leere gelesen und alle Threads beendet (`abschluss`). Ein Thread stirbt nie still an einem Fehler.
    - M1: JEDER Treffer wird mit seiner Phase gespeichert; Treffer ausserhalb von "vorher"/"nachher" (z. B. waehrend der
      ACL-Aktivierung) erscheinen im Nachher-Log als `ausserhalb_der_phasen`.
    - N3/H2: UDP-/DNS-Ziele antworten mit einem Echo an den Absender - so kann die VM "erreicht" von
      "zeitueberschreitung" unterscheiden. Das Echo geht nur an die Quelle des Datagramms.

    Im Trockenlauf NUR Loopback. Nicht-Loopback (z. B. Host-Adresse am internen vSwitch) nur im Modus "real" mit
    `LEXONO_ISOLATION_ACCEPTANCE=1`."""

    POLL_S = 0.1

    def __init__(self, targets: list[dict], *, mode: str = "trockenlauf", clock=utcnow, echo: bool = True,  # noqa: ANN001
                 nachlauf_s: float = ev.MIN_NACHLAUF_S, max_wait_s: float = 60.0) -> None:
        for target in targets:
            if target.get("protokoll") not in ("tcp", "udp", "dns"):
                raise AcceptanceRefused([f"unbekanntes Protokoll {target.get('protokoll')!r}"])
            if not _loopback(target["ip"]) and not (mode == "real" and os.environ.get(ENV_REAL) == "1"):
                raise AcceptanceRefused([f"Kanarienziel {target['ip']} nur im freigegebenen echten Modus"])
        self.targets, self.clock, self.echo = [dict(t) for t in targets], clock, echo
        self.nachlauf_s, self.max_wait_s = nachlauf_s, max_wait_s
        self.hits: list[dict] = []
        self.controls: list[str] = []
        self.errors = 0
        self.kontrollen: list[dict] = []  # §98 (H1): Positivkontrollen zu festen Zeitpunkten
        self._nonce: bytes | None = None  # §98 (N4): Kennung der laufenden Kontrolle (im Paket, nicht nur am Ziel)
        self._controls_now: set[str] = set()
        self._sockets: list[socket.socket] = []
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_hit = time.monotonic()
        self.active_from = self.active_to = None
        self.phase: str | None = None
        self.phases: dict[str, dict] = {}
        self.abschluss: dict | None = None

    def settle(self) -> float:
        """Warten, bis `nachlauf_s` lang kein Treffer kam (monoton, hoechstens `max_wait_s`). Rueckgabe: Ruhezeit."""
        start = time.monotonic()
        while True:
            with self._lock:
                quiet = time.monotonic() - max(self._last_hit, start)
            if quiet >= self.nachlauf_s or time.monotonic() - start >= self.max_wait_s:
                return round(quiet, 3)
            time.sleep(self.POLL_S)

    def mark(self, phase: str) -> None:
        """Phasenwechsel (z. B. "vorher" -> "aktivierung" -> "nachher"). Die laufende Phase endet erst nach der Ruhezeit."""
        quiet = self.settle() if self.phase else None
        with self._lock:
            if phase in self.phases:
                raise AcceptanceRefused([f"Phase {phase!r} bereits beendet - Phasen sind einmalig"])
            now, mono = self.clock(), time.monotonic()
            if self.phase:
                current = self.phases[self.phase]
                current.update(bis=now, nachlauf_s=quiet, dauer_s=round(mono - current["_mono"], 3))
            self.phase = phase
            self.phases[phase] = {"von": now, "bis": None, "nachlauf_s": None, "dauer_s": None, "_mono": mono}

    CONTROL_PREFIX = b"lexono-kontrolle:"

    def _hit(self, target: dict, source, data: bytes = b"") -> None:  # noqa: ANN001
        with self._lock:
            key = f"{target['ip']}:{target['port']}"
            if self._nonce is not None and data == self.CONTROL_PREFIX + self._nonce:
                self.controls.append(key)  # eigene Kontrolle - am Inhalt erkannt, kein Treffer aus der VM
                self._controls_now.add(key)
                return
            self.hits.append({"zeit": self.clock().isoformat(), "ziel": key, "protokoll": target["protokoll"],
                              "quelle": str(source[0]), "phase": self.phase})
            self._last_hit = time.monotonic()

    def _serve(self, sock: socket.socket, target: dict) -> None:
        """Bis zum Stopp UND bis die Warteschlange leer ist (erst ein Timeout nach dem Stopp beendet den Thread)."""
        sock.settimeout(self.POLL_S)
        while True:
            data = b""
            try:
                if target["protokoll"] == "tcp":
                    conn, source = sock.accept()
                    try:  # Kontrollen senden eine Kennung; VM-Proben verbinden nur (leer/Timeout = Treffer)
                        conn.settimeout(0.3)
                        data = conn.recv(64)
                    except OSError:
                        data = b""
                    finally:
                        conn.close()
                else:
                    data, source = sock.recvfrom(512)
                    if self.echo:
                        try:
                            sock.sendto(data[:64], source)
                        except OSError:
                            pass
            except TimeoutError:
                if self._stop.is_set():
                    return
                continue
            except OSError:  # z. B. WSAECONNRESET nach Echo an einen geschlossenen Port - Listener laeuft weiter
                with self._lock:
                    self.errors += 1
                if self._stop.is_set():
                    return
                continue
            self._hit(target, source, data)

    def __enter__(self) -> CanaryListener:
        for target in self.targets:
            family = socket.AF_INET6 if ":" in target["ip"] else socket.AF_INET
            kind = socket.SOCK_STREAM if target["protokoll"] == "tcp" else socket.SOCK_DGRAM
            sock = socket.socket(family, kind)
            sock.bind((target["ip"], int(target.get("port", 0))))
            if kind == socket.SOCK_STREAM:
                sock.listen(64)
            target["port"] = sock.getsockname()[1]
            self._sockets.append(sock)
            thread = threading.Thread(target=self._serve, args=(sock, target), daemon=True)
            self._threads.append(thread)
            thread.start()
        self.active_from = self.clock()
        self.self_check()
        return self

    def self_check(self) -> None:
        """Positivkontrolle beim Start (siehe `control_check`)."""
        self.control_check("start")

    def control_check(self, zeitpunkt: str) -> dict:
        """§98 (H1/N4): Positivkontrolle zu einem Zeitpunkt (Start, Ende "vorher", Beginn/waehrend/Ende "nachher").
        Jedes Ziel muss eine Probe des Hosts mit zufaelliger Kennung empfangen; ein gleichzeitiger VM-Treffer bleibt ein
        Treffer (Erkennung am Inhalt). Fehlt ein Ziel, ist "kein Treffer" fuer diesen Zeitraum nicht belegbar."""
        nonce = secrets.token_hex(8).encode()
        payload = self.CONTROL_PREFIX + nonce
        with self._lock:
            self._nonce, self._controls_now = nonce, set()
        for target in self.targets:
            key = f"{target['ip']}:{target['port']}"
            family = socket.AF_INET6 if ":" in target["ip"] else socket.AF_INET
            try:
                if target["protokoll"] == "tcp":
                    with socket.create_connection((target["ip"], target["port"]), timeout=2) as conn:
                        conn.sendall(payload)
                else:
                    with socket.socket(family, socket.SOCK_DGRAM) as sock:
                        sock.settimeout(2)
                        sock.sendto(payload, (target["ip"], target["port"]))
                        if self.echo:
                            sock.recvfrom(64)
            except OSError:
                pass
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with self._lock:
                    if key in self._controls_now:
                        break
                time.sleep(0.01)
        with self._lock:
            ok = sorted(self._controls_now)
            self._nonce = None
            result = {"zeitpunkt": zeitpunkt, "zeit": self.clock().isoformat(), "phase": self.phase, "ok": ok,
                      "fehlend": sorted({f"{t['ip']}:{t['port']}" for t in self.targets} - set(ok))}
            self.kontrollen.append(result)
        return result

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        quiet = self.settle() if self.phase else 0.0
        with self._lock:
            self.active_to = self.clock()
            if self.phase:
                current = self.phases[self.phase]
                current.update(bis=self.active_to, nachlauf_s=quiet,
                               dauer_s=round(time.monotonic() - current["_mono"], 3))
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=10)
        finished = all(not t.is_alive() for t in self._threads)
        for sock in self._sockets:
            sock.close()
        self.abschluss = {"geleert": finished, "threads_beendet": finished, "nachlauf_s": quiet, "fehler": self.errors}

    def observation(self, phase: str | None = None) -> dict:
        """Log einer (beendeten) Phase bzw. der ganzen Laufzeit. Treffer anderer Phasen gehoeren nicht in `treffer`,
        werden aber im Nachher-Log als `ausserhalb_der_phasen` mitgefuehrt (nie unbeobachtet)."""
        with self._lock:
            if phase is not None:
                window = self.phases.get(phase)
                if not window or window["bis"] is None:
                    return {"fehler": f"Phase {phase!r} nicht gelaufen bzw. nicht beendet"}
                low, high = window["von"], window["bis"]
                hits = [h for h in self.hits if h["phase"] == phase]
            elif self.active_from is None or self.active_to is None:
                return {"fehler": "Listener nicht gestartet bzw. nicht beendet"}
            else:
                low, high, hits, window = self.active_from, self.active_to, list(self.hits), {}
            observation = {"ziele": [dict(t) for t in self.targets], "treffer": [dict(h) for h in hits],
                           "kontrolle": list(self.controls), "aktiv_von": low.isoformat(), "aktiv_bis": high.isoformat(),
                           "nachlauf_s": window.get("nachlauf_s"), "dauer_s": window.get("dauer_s"),
                           "kontrollen": [dict(k) for k in self.kontrollen
                                          if phase is None or k["zeitpunkt"].startswith(phase)]}
            if phase == "nachher":
                observation["ausserhalb_der_phasen"] = [dict(h) for h in self.hits
                                                        if h["phase"] not in ("vorher", "nachher")]
                observation["abschluss"] = dict(self.abschluss) if self.abschluss else None
            return observation


class PeriodicCheck:
    """§98 (H1): wiederholte Pruefung (z. B. eigene Firewall-Regeln) waehrend einer Phase in einem Hintergrund-Thread.
    `check()` liefert ein Ergebnis-Objekt; Fehler werden als Ergebnis festgehalten, nie verschluckt."""

    def __init__(self, check, *, interval_s: float = 15.0, zeitpunkt: str = "nachher_waehrend") -> None:  # noqa: ANN001
        self.check, self.interval_s, self.zeitpunkt = check, interval_s, zeitpunkt
        self.results: list[dict] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                result = dict(self.check())
            except Exception as exc:  # noqa: BLE001
                result = {"gleich": False, "fehler": f"{type(exc).__name__}: {str(exc)[:200]}"}
            self.results.append({**result, "zeitpunkt": self.zeitpunkt})

    def __enter__(self) -> PeriodicCheck:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        self._stop.set()
        self._thread.join(timeout=30)


def calibration_probe(targets: list[dict], *, vm_ips: list[str], start_token: str, mode: str = "trockenlauf",  # noqa: PLR0913
                      clock=utcnow, timeout: float = 5.0, skip_channels: tuple[str, ...] = ()) -> dict:  # noqa: ANN001
    """§92/§96 (VM, E0): Vorher-Probe - jedes Kanarienziel einmal ansprechen, BEVOR die Port-ACL aktiv ist.

    Ergebnis je Ziel (§96, H2): "erreicht" (TCP verbunden bzw. UDP-Echo empfangen), "abgelehnt" (RST/ICMP - Paket kam an,
    Port zu), "zeitueberschreitung" (keine Antwort), "fehler". Nur "erreicht" belegt den Pfad. Ohne Freigabe nur
    Loopback-Ziele. `start_token` stammt vom Host (Beginn der Vorher-Phase); `vm_abschluss` wird NACH allen Versuchen
    erzeugt und dem Host uebergeben."""
    attempts = []
    for target in targets:
        if not _loopback(target["ip"]) and not (mode == "real" and os.environ.get(ENV_REAL) == "1"):
            raise AcceptanceRefused([f"Gegenprobe zu {target['ip']} nur im freigegebenen echten Modus"])
        kanaele = sorted(ev.channels(target))
        if any(k in skip_channels for k in kanaele):
            continue
        attempts.append({"ziel": f"{target['ip']}:{target['port']}", "protokoll": target["protokoll"],
                         "kanaele": kanaele, "ergebnis": probe_once(target, timeout=timeout),
                         "zeit_vm": clock().isoformat()})  # nur informativ - wird nie mit der Host-Uhr verglichen
    return {"vm_quelle_ips": list(vm_ips), "versuche": attempts,
            "phasen_token": {"start": start_token, "vm_abschluss": new_token()}}


def probe_once(target: dict, *, timeout: float = 5.0) -> str:
    """Eine Probe mit eindeutigem Ergebnis (§96, H2). Zeitlimit > Windows-Wiederholung nach RST (~2 s)."""
    family = socket.AF_INET6 if ":" in target["ip"] else socket.AF_INET
    try:
        if target["protokoll"] == "tcp":
            with socket.create_connection((target["ip"], target["port"]), timeout=timeout):
                return "erreicht"
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(b"lexono-gegenprobe", (target["ip"], target["port"]))
            sock.recvfrom(64)
            return "erreicht"
    except ConnectionRefusedError:
        return "abgelehnt"
    except ConnectionResetError:  # UDP: ICMP "Port unreachable" (Windows)
        return "abgelehnt"
    except TimeoutError:
        return "zeitueberschreitung"
    except OSError:
        return "fehler"


# --- Echte Beobachter (nur lesend, nur im freigegebenen echten Modus) -------------------------------------------------------

#: PowerShell-Befehle der realen Abnahme. Lesend, ausser V2-Aenderungsversuch (muss in der VM SCHEITERN). Platzhalter
#: {vm} werden nur mit dem geprueften VM-Namen ersetzt. Ausgefuehrt NUR ueber `run_real`.
REAL_COMMANDS = {
    # §94: Enum-Werte ausdruecklich als Text - Windows PowerShell 5.1 serialisiert Enums sonst als Zahl (nicht eindeutig)
    "host.port_acl": "@(Get-VMNetworkAdapterExtendedAcl -VMName {vm} | Select-Object "
                     "@{n='Direction';e={[string]$_.Direction}},@{n='Action';e={[string]$_.Action}},"
                     "@{n='RemoteIPAddress';e={[string]$_.RemoteIPAddress}},@{n='RemotePort';e={[string]$_.RemotePort}},"
                     "@{n='Protocol';e={[string]$_.Protocol}},@{n='Weight';e={[int]$_.Weight}},"
                     "@{n='Stateful';e={[bool]$_.Stateful}}) | ConvertTo-Json -Depth 3",
    "host.vm_zustand": "@{state=[string](Get-VM -Name {vm}).State} | ConvertTo-Json",
    # §96 (M7): eigene Firewall-Gruppe lesen (Regeln mit Filtern)
    "host.firewall_gruppe": "@(Get-NetFirewallRule -Group 'Lexono-Abnahme' -ErrorAction SilentlyContinue | ForEach-Object { "
                            "$p=$_|Get-NetFirewallPortFilter; $a=$_|Get-NetFirewallAddressFilter; "
                            "$i=$_|Get-NetFirewallInterfaceFilter; [pscustomobject]@{DisplayName=[string]$_.DisplayName;"
                            "Enabled=[string]$_.Enabled;Direction=[string]$_.Direction;Action=[string]$_.Action;"
                            "Protocol=[string]$p.Protocol;LocalPort=(@($p.LocalPort)|ForEach-Object{[string]$_}) -join ',';"
                            "LocalAddress=(@($a.LocalAddress)|ForEach-Object{[string]$_}) -join ',';"
                            "RemoteAddress=(@($a.RemoteAddress)|ForEach-Object{[string]$_}) -join ',';"
                            "InterfaceAlias=(@($i.InterfaceAlias)|ForEach-Object{[string]$_}) -join ',';"
                            "Description=[string]$_.Description} }) | ConvertTo-Json",
    # §96 (M2) Switch des VM-Adapters; (M3) tatsaechlicher Datentraeger der VM und seine Elternkette
    "host.vm_switch": "@(Get-VMNetworkAdapter -VMName {vm} | ForEach-Object { Get-VMSwitch -Name $_.SwitchName } | "
                      "Select-Object Name,@{n='SwitchType';e={[string]$_.SwitchType}}) | ConvertTo-Json",
    "host.vm_datentraeger": "@(Get-VMHardDiskDrive -VMName {vm} | Select-Object @{n='Path';e={[string]$_.Path}}) | "
                            "ConvertTo-Json",
    "host.vhd": "Get-VHD -Path {pfad} | Select-Object @{n='Path';e={[string]$_.Path}},"
                "@{n='ParentPath';e={[string]$_.ParentPath}},@{n='VhdType';e={[string]$_.VhdType}} | ConvertTo-Json",
    # §96 (M8) Schutz der Host-Dienste auf vEthernet in der Vorher-Phase (lesend; Auswertung siehe isolation_real)
    "host.vethernet_schutz": "$c=Get-NetConnectionProfile -InterfaceAlias {iface}; "
                             "$n=@{Public='Public';Private='Private';DomainAuthenticated='Domain'}[[string]$c.NetworkCategory]; "
                             "$p=Get-NetFirewallProfile -PolicyStore ActiveStore -Name $n; "
                             "$r=@(Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True -Direction Inbound -Action Allow | "
                             "Where-Object { $_.Group -ne 'Lexono-Abnahme' } | ForEach-Object { $f=$_ | Get-NetFirewallPortFilter; "
                             "$i=$_ | Get-NetFirewallInterfaceFilter; [pscustomobject]@{Name=[string]$_.Name;"
                             "Profile=[string]$_.Profile;LocalPort=@($f.LocalPort|ForEach-Object{[string]$_});"
                             "Protocol=[string]$f.Protocol;InterfaceAlias=@($i.InterfaceAlias|ForEach-Object{[string]$_})} }); "
                             "@{kategorie=$n; standard_eingehend=[string]$p.DefaultInboundAction; regeln=$r} | ConvertTo-Json -Depth 5",
    "host.vm_konfiguration": "$a=@(Get-VMNetworkAdapter -VMName {vm} | Select-Object Name,SwitchName,MacAddress,"
                             "@{n='MacAddressSpoofing';e={[string]$_.MacAddressSpoofing}},"
                             "@{n='DhcpGuard';e={[string]$_.DhcpGuard}},@{n='RouterGuard';e={[string]$_.RouterGuard}}); "
                             "$g=(Get-VMIntegrationService -VMName {vm} -Name 'Guest Service Interface').Enabled; "
                             "$i=@(Get-VMIntegrationService -VMName {vm} | Select-Object Name,@{n='Enabled';e={[bool]$_.Enabled}}); "
                             "$e=(Get-VMHost).EnableEnhancedSessionMode; "
                             "@{vm_netzadapter=$a; gastdienste_aktiv=[bool]$g; erweiterte_sitzung=[bool]$e; "
                             "integrationsdienste=$i} | ConvertTo-Json -Depth 4",
    # §96 (M5): wirksame Einstellung (ActiveStore) - NICHT VERIFIZIERT gegen eine reale VM
    "vm.gast_firewall": "Get-NetFirewallProfile -PolicyStore ActiveStore | Select-Object Name,"
                        "@{n='enabled';e={[bool]$_.Enabled}},"
                        "@{n='default_outbound';e={[string]$_.DefaultOutboundAction}} | ConvertTo-Json",
    "vm.netzadapter": "@(Get-NetAdapter | Select-Object Name,@{n='Status';e={[string]$_.Status}},MacAddress) | ConvertTo-Json",
    "vm.konto_sids": "whoami /groups /fo csv",
    "vm.aenderungsversuch": "Set-NetFirewallProfile -Profile Public -DefaultOutboundAction Allow -ErrorAction Stop",
    # §96 (N6): Zustand vor/nach dem Versuch und Ruecknahme, falls er gelang
    "vm.firewall_public_lokal": "@{wert=[string](Get-NetFirewallProfile -Profile Public -PolicyStore PersistentStore)"
                                ".DefaultOutboundAction} | ConvertTo-Json",
    "vm.aenderungsversuch_rueckbau": "Set-NetFirewallProfile -Profile Public -DefaultOutboundAction {wert} -ErrorAction Stop",
    "vm.konto_name": "@{name=[string][Security.Principal.WindowsIdentity]::GetCurrent().Name} | ConvertTo-Json",
    "vm.routen": "@(Get-NetRoute | Select-Object DestinationPrefix,@{n='NextHop';e={[string]$_.NextHop}},"
                 "InterfaceAlias) | ConvertTo-Json",
    "vm.ip_konfiguration": "@(Get-NetIPConfiguration | Select-Object InterfaceAlias,"
                           "@{n='ipv4';e={@($_.IPv4Address.IPAddress)}},@{n='ipv6';e={@($_.IPv6Address.IPAddress)}},"
                           "@{n='gateway4';e={@($_.IPv4DefaultGateway.NextHop)}},"
                           "@{n='gateway6';e={@($_.IPv6DefaultGateway.NextHop)}},"
                           "@{n='dns';e={@($_.DNSServer.ServerAddresses)}}) | ConvertTo-Json -Depth 4",
}


def normalize_ps_json(text: str | None) -> list[dict]:
    """§94: Ausgabe von `ConvertTo-Json` deterministisch als Liste von Objekten.

    Windows PowerShell liefert fuer kein Ergebnis eine leere Ausgabe, fuer genau ein Ergebnis ein OBJEKT und erst ab zwei
    eine Liste. Hier: leer/`null` -> [], Objekt -> [Objekt], Liste -> Liste; BOM und Leerraum entfernt; Schluessel
    sortiert. Alles andere (kein JSON, Skalare, verschachtelte Listen) -> ValueError (Beobachtung wird Fehler -> nie
    PASS). Die Reihenfolge der Liste bleibt erhalten; wer sie nicht braucht, sortiert selbst."""
    if text is None:
        return []
    cleaned = text.lstrip("﻿").strip()
    if not cleaned or cleaned == "null":
        return []
    data = json.loads(cleaned)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError("PowerShell-Ausgabe ist keine Objektliste")
    return [json.loads(json.dumps(item, sort_keys=True, ensure_ascii=False)) for item in data]


def _safe_vm_name(name: str) -> str:
    if not name or not all(c.isalnum() or c in "-_" for c in name):
        raise AcceptanceRefused(["VM-Name nur aus Buchstaben, Ziffern, - und _"])
    return f"'{name}'"


_FIREWALL_VALUES = ("Block", "Allow", "NotConfigured")


def _safe_path(value: str) -> str:
    if not value or not all(c.isalnum() or c in "-_.:\\/ ()" for c in value):
        raise AcceptanceRefused([f"Pfad {value!r} enthaelt unzulaessige Zeichen"])
    return f"'{value}'"


def run_real(key: str, *, real: bool, vm: str | None = None, runner=subprocess.run, pfad: str | None = None,  # noqa: ANN001, PLR0913
             wert: str | None = None, iface: str | None = None) -> subprocess.CompletedProcess:
    """Einziger Weg zu echten Systembefehlen. Verweigert VOR dem Prozessstart, wenn nicht ausdruecklich freigegeben.
    Platzhalter werden nur mit geprueften Werten ersetzt (in einfachen Anfuehrungszeichen, ohne Sonderzeichen)."""
    if not real or os.environ.get(ENV_REAL) != "1":
        raise AcceptanceRefused([f"echter Befehl {key!r} verweigert - nur in der freigegebenen Abnahmeumgebung "
                                 f"({ENV_REAL}=1 und real=True)"])
    command = REAL_COMMANDS[key]
    if "{vm}" in command:
        command = command.replace("{vm}", _safe_vm_name(vm))
    if "{pfad}" in command:
        command = command.replace("{pfad}", _safe_path(pfad))
    if "{iface}" in command:
        command = command.replace("{iface}", _safe_path(iface))
    if "{wert}" in command:
        if wert not in _FIREWALL_VALUES:
            raise AcceptanceRefused([f"unzulaessiger Wert {wert!r}"])
        command = command.replace("{wert}", wert)
    return runner(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True,
                  timeout=120, check=False)


# --- Synthetische Beobachtungen (Trockenlauf und Tests) ---------------------------------------------------------------------

DRY_RUN_VM = {"hostname": "lexono-bench-vm-trockenlauf", "machine_guid": "00000000-0000-4000-8000-0000000000b1"}


def junit_xml(results: dict[str, str]) -> str:
    """Synthetisches pytest-JUnit-XML {nodeid: outcome}."""
    cases = []
    for nodeid, outcome in results.items():
        module, name = nodeid.split("::", 1)
        classname = module.removesuffix(".py").replace("/", ".")
        inner = {"failed": "<failure message='x'/>", "error": "<error message='x'/>",
                 "skipped": "<skipped message='x'/>"}.get(outcome, "")
        cases.append(f"<testcase classname={quoteattr(classname)} name={quoteattr(name)}>{inner}</testcase>")
    return f"<testsuites><testsuite name='pytest'>{''.join(cases)}</testsuite></testsuites>"


def required_nodeids(test: str) -> dict[str, str]:
    req = requirement(test, "pytest", "E0")
    return {(p + "test_beispiel" if p.endswith("::") else p): "passed" for p in req["knoten"]} if req else {}


def synthetic_acl(endpoints: set[tuple[str, int]]) -> list[dict]:
    acl = [{"Direction": "Outbound", "Action": "Deny", "RemoteIPAddress": "0.0.0.0/0", "RemotePort": "", "Protocol": "",
            "Weight": 1, "Stateful": False},
           {"Direction": "Outbound", "Action": "Deny", "RemoteIPAddress": "::/0", "RemotePort": "", "Protocol": "",
            "Weight": 1, "Stateful": False}]
    acl += [{"Direction": "Outbound", "Action": "Allow", "RemoteIPAddress": f"{ip}/32", "RemotePort": str(port),
             "Protocol": "TCP", "Weight": 10, "Stateful": True} for ip, port in sorted(endpoints)]
    return acl


#: synthetische Phasen-Tokens (nur Trockenlauf/Tests)
SYNTHETIC_TOKENS = {"vorher": {"start": "1" * 32, "vm_abschluss": "2" * 32},
                    "nachher": {"start": "3" * 32, "vm_abschluss": "4" * 32}}
#: synthetische Soll-Parameter je Test (Abnahme-Konfiguration des Trockenlaufs)
SYNTHETIC_PARAMETER = {"benchmark_konto": "lexono-bench", "erlaubte_gateways": [], "switch": "lexono-abnahme",
                       "kind_vhdx": "C:\\Hyper-V\\lexono\\kind.vhdx", "eltern_vhdx": "C:\\Hyper-V\\lexono\\golden.vhdx"}


#: synthetischer Fingerabdruck der eigenen Firewall-Regeln (Trockenlauf/Tests - es gibt keine Firewall)
SYNTHETIC_FW_SHA = hashlib.sha256(b"lexono-firewall-trockenlauf").hexdigest()


def synthetic_controls(targets: list[dict], points: tuple[str, ...], now: datetime) -> tuple[list, list]:
    keys = [f"{t['ip']}:{t['port']}" for t in targets]
    listener = [{"zeitpunkt": p, "zeit": now.isoformat(), "ok": keys, "fehlend": []} for p in points]
    firewall = [{"zeitpunkt": p, "gleich": True, "ist_sha256": SYNTHETIC_FW_SHA, "simuliert": True} for p in points]
    return listener, firewall


def default_canary(now: datetime, tokens: dict | None = None, targets: list[dict] | None = None) -> dict:
    tokens = tokens or SYNTHETIC_TOKENS
    targets = targets or [{"ip": "127.0.0.1", "port": 40001, "protokoll": "tcp"}, {"ip": "::1", "port": 40002, "protokoll": "tcp"},
                          {"ip": "127.0.0.1", "port": 40003, "protokoll": "udp"},
                          {"ip": "127.0.0.1", "port": 40004, "protokoll": "dns"}, {"ip": "::1", "port": 40005, "protokoll": "udp"}]
    listener, firewall = synthetic_controls(targets, ev.KONTROLLPUNKTE["nachher"], now)
    return {"ziele": targets, "kontrolle": [f"{t['ip']}:{t['port']}" for t in targets], "treffer": [],
            "kontrollen": listener, "firewall_kontrollen": firewall,
            "aktiv_von": (now - timedelta(minutes=35)).isoformat(), "aktiv_bis": now.isoformat(),
            "nachlauf_s": ev.MIN_NACHLAUF_S, "dauer_s": 2100.0, "ausserhalb_der_phasen": [],
            "abschluss": {"geleert": True, "threads_beendet": True, "nachlauf_s": ev.MIN_NACHLAUF_S, "fehler": 0},
            "phasen_token": dict(tokens["nachher"])}


def synthetic_beobachtung(test: str, art: str, klasse: str, *, endpoints: set[tuple[str, int]], now: datetime,  # noqa: PLR0913
                          canary: dict | None = None, tokens: dict | None = None) -> dict:
    """Beobachtungen eines einwandfreien Zielsystems (nur Daten, keine Systemabfrage)."""
    tokens = tokens or SYNTHETIC_TOKENS
    acl = synthetic_acl(endpoints)
    canary = json.loads(json.dumps(canary)) if canary else default_canary(now, tokens)
    if art == "pytest":
        return {"junit_xml": junit_xml(required_nodeids(test)), "phasen_token": dict(tokens["nachher"])}
    if art == "firewall_export":
        return {"acl": acl}
    if art == "kanarien":
        return canary
    if art == "gegenprobe":
        return synthetic_gegenprobe(klasse, canary=canary, now=now, tokens=tokens)
    h = hashlib.sha256(b"lexono-golden-image-trockenlauf").hexdigest()
    return {
        ("V2", "E0"): lambda: {"firewall_profile": [{"name": n, "enabled": True, "default_outbound": "Block"}
                                                    for n in ("Domain", "Private", "Public")],
                               "aenderungsversuch": {"exit_code": 1, "fehler": "Zugriff verweigert", "vorher": "Block",
                                                     "nachher": "Block", "rueckbau_exit": None},
                               "konto_gruppen_sids": ["S-1-5-32-545", "S-1-5-11"], "konto_name": "LEXONO-VM\\lexono-bench",
                               "arbeitsbaum": {"exit_code": 0, "porcelain": ""},
                               "schreibtest": {"geprueft": 12, "beschreibbar": []}},
        ("V2", "E1"): lambda: {"gast_richtlinie_sha256": h, "golden_image_richtlinie_sha256": h},
        ("V3", "E2"): lambda: {"vm_ip": "10.88.0.2", "fenster": [(now - timedelta(hours=1)).isoformat(), now.isoformat()],
                               "eintraege": [{"zeit": (now - timedelta(minutes=20)).isoformat(), "quelle_ip": "10.88.0.2",
                                              "pfad": "/abnahme", "status": 401, "weitergeleitet": False}]},
        ("V9", "E1"): lambda: {"vm_netzadapter": [{"Name": "LAN", "SwitchName": "lexono-abnahme", "MacAddressSpoofing": "Off",
                                                   "DhcpGuard": "On", "RouterGuard": "On"}],
                               "integrationsdienste": [{"Name": "Heartbeat", "Enabled": True},
                                                       {"Name": "Guest Service Interface", "Enabled": False},
                                                       {"Name": "Key-Value Pair Exchange", "Enabled": False}],
                               "switch": {"Name": "lexono-abnahme", "SwitchType": "Internal"},
                               "gastdienste_aktiv": False, "erweiterte_sitzung": False, "acl": acl,
                               "vethernet_schutz": {"kategorie": "Public", "standard_eingehend": "Block",
                                                    "regeln": []}},
        ("V9", "E0"): lambda: {"netzadapter": [{"Name": "Ethernet", "Status": "Up"}],
                               "ip_konfiguration": [{"InterfaceAlias": "Ethernet", "ipv4": ["10.88.0.2"],
                                                     "ipv6": ["fd00:88::2"], "gateway4": [], "gateway6": [], "dns": []}],
                               "routen": [{"DestinationPrefix": "10.88.0.0/24", "NextHop": "0.0.0.0"},
                                          {"DestinationPrefix": "fd00:88::/64", "NextHop": "::"}]},
        ("V10", "E1"): lambda: {"acl_vorher_sha256": hashlib.sha256(_canonical(acl)).hexdigest(),
                                "acl_nachher_sha256": hashlib.sha256(_canonical(acl)).hexdigest(),
                                "export_erzeuger": "host-abnahme"},
        ("V11", "E1"): lambda: {"zuruecksetzen": {"exit_code": 0, "zeit": (now - timedelta(minutes=70)).isoformat()},
                                "image_sha256_soll": h, "image_sha256_vorher": h, "image_sha256_nachher": h,
                                "vm_datentraeger": [SYNTHETIC_PARAMETER["kind_vhdx"]],
                                "kind_vhd": {"Path": SYNTHETIC_PARAMETER["kind_vhdx"],
                                             "ParentPath": SYNTHETIC_PARAMETER["eltern_vhdx"], "VhdType": "Differencing"},
                                "eltern_schreibgeschuetzt": True},
        ("V11", "E0"): lambda: {"junit_xml": junit_xml({"tests/test_s86_isolation_design.py::test_kurz": "passed"})},
        ("V12", "E1"): lambda: {"auswertung_durch": "host-abnahme",
                                "kanarien_logs_sha256": [hashlib.sha256(_canonical(canary)).hexdigest()]},
        ("V13", "E1"): lambda: {"diagnose": [ev.MSG_ATTESTATION_EXPIRED], "schluesselliste_gueltig": True,
                                "schluessel_status": "aktiv"},
    }[(test, klasse)]()


#: ACL waehrend der Gegenprobe (Trockenlauf: noch keine Regeln) - muss sich von der finalen ACL unterscheiden
CALIBRATION_ACL: list = []
SYNTHETIC_VM_IPS = ["10.88.0.2", "fd00:88::2"]


def synthetic_gegenprobe(klasse: str, *, canary: dict | None, now: datetime, tokens: dict | None = None) -> dict:
    """Erfolgreiche Gegenprobe auf denselben Zielen wie das Kanarienlog: Vorher-Fenster 60-50 min vor `now`."""
    tokens = tokens or SYNTHETIC_TOKENS
    targets = (canary or default_canary(now, tokens))["ziele"]
    low, high = now - timedelta(minutes=60), now - timedelta(minutes=50)
    when = (now - timedelta(minutes=55)).isoformat()
    if klasse == "E0":
        return {"vm_quelle_ips": list(SYNTHETIC_VM_IPS), "phasen_token": dict(tokens["vorher"]), "versuche": [
            {"ziel": f"{t['ip']}:{t['port']}", "protokoll": t["protokoll"], "kanaele": sorted(ev.channels(t)),
             "ergebnis": "erreicht", "zeit_vm": when} for t in targets]}
    source = {4: SYNTHETIC_VM_IPS[0], 6: SYNTHETIC_VM_IPS[1]}
    listener, firewall = synthetic_controls(targets, ev.KONTROLLPUNKTE["vorher"], now)
    return {"ziele": [dict(t) for t in targets], "kontrolle": [f"{t['ip']}:{t['port']}" for t in targets],
            "kontrollen": listener, "firewall_kontrollen": firewall,
            "treffer": [{"zeit": when, "ziel": f"{t['ip']}:{t['port']}", "protokoll": t["protokoll"], "phase": "vorher",
                         "quelle": source[6 if ":" in t["ip"] else 4]} for t in targets],
            "aktiv_von": low.isoformat(), "aktiv_bis": high.isoformat(), "vm_quelle_ips": list(SYNTHETIC_VM_IPS),
            "acl_sha256": hashlib.sha256(_canonical(CALIBRATION_ACL)).hexdigest(), "nachlauf_s": ev.MIN_NACHLAUF_S,
            "phasen_token": dict(tokens["vorher"])}


# --- Sammler je Rolle ---------------------------------------------------------------------------------------------------


def collect(part: EvidencePart, observers: dict, select=None) -> list[Path]:  # noqa: ANN001
    """Alle Pflichtbelege der Rolle des Teils. `observers[(test, art)]` liefert die Beobachtung; fehlt er -> kein Beleg.
    `select(test, art)` = nur diese Belege jetzt (Phasen: Gegenprobe -> ACL -> Nachher)."""
    written = []
    for test, reqs in ev.ANFORDERUNGEN.items():
        for req in reqs:
            if req["klasse"] != part.klasse or (select and not select(test, req["art"])):
                continue
            path = part.observe(test, req["art"], observers.get((test, req["art"])))
            if path:
                written.append(path)
    return written


def synthetic_observers(rolle: str, *, endpoints: set[tuple[str, int]], now: datetime,
                        canary: dict | None = None, tokens: dict | None = None) -> dict:
    klasse = ev.ERZEUGER[ROLLEN[rolle]]
    return {(t, r["art"]): (lambda t=t, r=r: synthetic_beobachtung(t, r["art"], klasse, endpoints=endpoints, now=now,
                                                                   canary=canary, tokens=tokens))
            for t, reqs in ev.ANFORDERUNGEN.items() for r in reqs if r["klasse"] == klasse}


# --- Trockenlauf --------------------------------------------------------------------------------------------------------


@dataclass
class DryRun:
    folder: Path
    policy_path: Path
    lauf: dict
    bundle: Path
    verdict: dict
    canary: dict


def dry_run(folder: Path, *, leak: str | None = None, change=None, endpoints=frozenset({("127.0.0.1", 9)}),  # noqa: ANN001, PLR0913
            tool: dict | None = None, now: datetime | None = None) -> DryRun:
    """Lokaler Trockenlauf: synthetische Zielumgebung, ECHTER Kanarien-Listener auf Loopback, Buendel, Urteil (§90).

    `leak` = optional lokaler Zugriff auf ein Kanarienziel ("tcp" | "udp" | "tcp6") - simuliert einen Abfluss;
    `change(rolle, observers)` = Beobachter vor dem Sammeln veraendern (Negativfaelle)."""
    from scripts.legal_bench import isolation_gate

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    policy = json.loads(isolation_gate.POLICY_PATH.read_text(encoding="utf-8"))
    policy.update(status="FREIGEGEBEN")  # nur diese Trockenlauf-Kopie
    by_port: dict[int, list[str]] = {}
    for ip, port in sorted(endpoints):
        by_port.setdefault(port, []).append(ip)
    policy["freigaben"] = [{**policy["freigaben"][0], "hostname": "trockenlauf.test", "ip_adressen": ips, "port": port,
                            "freigegeben_durch": "Trockenlauf"} for port, ips in by_port.items()]
    policy["dns"]["entscheidung"] = "kein_dns_in_der_vm"
    policy_path = folder / "netzwerkpolitik.trockenlauf.json"
    policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=1), encoding="utf-8")
    now = now or utcnow()
    lauf = {"run_id": f"trockenlauf-{now.strftime('%Y%m%dT%H%M%S')}", "maschine": DRY_RUN_VM,
            "netzwerkpolitik_sha256": isolation_gate.sha256_file(policy_path),
            "pruefwerkzeug": tool or isolation_gate.pruefwerkzeug_stand()}
    targets = [{"ip": "127.0.0.1", "port": 0, "protokoll": "tcp"}, {"ip": "::1", "port": 0, "protokoll": "tcp"},
               {"ip": "127.0.0.1", "port": 0, "protokoll": "udp"}, {"ip": "::1", "port": 0, "protokoll": "udp"},
               {"ip": "127.0.0.1", "port": 0, "protokoll": "dns"}]
    vm_ips = ["127.0.0.1", "::1"]  # Trockenlauf: "VM-Pfad" = Loopback dieses Prozesses
    parts = {r: EvidencePart(folder / f"teil_{r}", r, lauf, policy, parameter=dict(SYNTHETIC_PARAMETER)) for r in ROLLEN}
    vorher_start, nachher_start = new_token(), new_token()  # vom Host erzeugt (§96, M6)
    vm_end: dict = {}
    is_acl = lambda t, a: (t, a) == ("V1", "firewall_export")  # noqa: E731
    with CanaryListener(targets) as listener:  # Funktionskontrolle beim Start (vor der Gegenprobe)
        cal_cache: dict = {}

        def vm_calibration():  # noqa: ANN202 - einmal proben, fuer alle Tests verwenden
            if "obs" not in cal_cache:
                cal_cache["obs"] = calibration_probe(listener.targets, vm_ips=vm_ips, start_token=vorher_start)
            return cal_cache["obs"]

        def vm_token(phase: str) -> dict:  # noqa: ANN202 - die VM meldet ihren Abschluss-Token an den Host
            if phase == "vorher":
                return dict(vm_calibration()["phasen_token"])
            return {"start": nachher_start, "vm_abschluss": vm_end.setdefault("nachher", new_token())}

        tokens = {"nachher": {"start": nachher_start, "vm_abschluss": None}}
        fw_checks: list = []

        def fw_check(point: str) -> None:  # Trockenlauf: keine Host-Firewall - ausdruecklich simuliert
            fw_checks.append({"zeitpunkt": point, "gleich": True, "ist_sha256": SYNTHETIC_FW_SHA, "simuliert": True})
        observers = {r: synthetic_observers(r, endpoints=set(endpoints), now=now) for r in ROLLEN}
        for (test, art) in list(observers["vm"]):
            if art == "pytest":
                original = observers["vm"][(test, art)]
                observers["vm"][(test, art)] = lambda original=original: {**original(), "phasen_token": vm_token("nachher")}
        acl_during = hashlib.sha256(_canonical(CALIBRATION_ACL)).hexdigest()
        for (test, art) in list(observers["vm"]):
            if art == "gegenprobe":
                observers["vm"][(test, art)] = vm_calibration
        for (test, art) in list(observers["host"]):
            if art == "gegenprobe":
                observers["host"][(test, art)] = lambda: {
                    **listener.observation("vorher"), "vm_quelle_ips": vm_ips, "acl_sha256": acl_during,
                    "phasen_token": vm_token("vorher"),
                    "firewall_kontrollen": [c for c in fw_checks if c["zeitpunkt"].startswith("vorher")]}
            elif art == "kanarien":
                observers["host"][(test, art)] = lambda: canary_log()

        def canary_log() -> dict:  # noqa: ANN202
            return {**listener.observation("nachher"), "phasen_token": vm_token("nachher"),
                    "firewall_kontrollen": [c for c in fw_checks if c["zeitpunkt"].startswith("nachher")]}

        observers["host"][("V12", "kriterien")] = lambda: {
            "auswertung_durch": "host-abnahme", "kanarien_logs_sha256": [hashlib.sha256(_canonical(canary_log())).hexdigest()]}
        del tokens
        if change:
            for rolle in ROLLEN:
                change(rolle, observers[rolle])
        # Phase 1 "vorher": ACL noch nicht aktiv - Gegenprobe aus der VM, Host protokolliert die Treffer
        listener.mark("vorher")
        collect(parts["vm"], observers["vm"], select=lambda t, a: a == "gegenprobe")
        listener.settle()
        listener.control_check("vorher_ende")
        fw_check("vorher_ende")
        listener.mark("aktivierung")
        collect(parts["host"], observers["host"], select=lambda t, a: a == "gegenprobe")
        # Phase 2 "aktivierung": Port-ACL setzen (Trockenlauf: nur synthetisch) und vom Host exportieren (V1)
        collect(parts["host"], observers["host"], select=is_acl)
        # Phase 3 "nachher": Abnahme-Proben aus der VM; jeder Treffer = Abfluss trotz ACL
        listener.mark("nachher")
        listener.control_check("nachher_start")
        fw_check("nachher_start")
        collect(parts["vm"], observers["vm"], select=lambda t, a: a != "gegenprobe")
        if leak:
            _leak(listener.targets, leak)
        listener.settle()
        listener.control_check("nachher_ende")
        fw_check("nachher_ende")
    canary = canary_log()
    collect(parts["host"], observers["host"], select=lambda t, a: a != "gegenprobe" and not is_acl(t, a))
    collect(parts["endpunkt"], observers["endpunkt"])
    for part in parts.values():
        part.close()
    parts = {r: part.folder for r, part in parts.items()}
    bundle = assemble(folder / "buendel", parts, lauf=lauf)
    verdict = ev.evaluate(bundle, policy=policy, lauf=lauf, reference=utcnow(), now=utcnow(), allow_dry_run=True)
    return DryRun(folder=folder, policy_path=policy_path, lauf=lauf, bundle=bundle, verdict=verdict, canary=canary)


def _leak(targets: list[dict], kind: str) -> None:
    """Simulierter Abfluss - NUR zu den eigenen Loopback-Kanarien."""
    for target in targets:
        if not _loopback(target["ip"]):
            raise AcceptanceRefused(["Leck-Simulation nur gegen Loopback"])
    import time

    if kind == "udp6":
        target = next(t for t in targets if t["protokoll"] == "udp" and ":" in t["ip"])
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
            sock.sendto(b"lexono-trockenlauf", (target["ip"], target["port"]))
    elif kind in ("tcp", "tcp6"):
        target = next(t for t in targets if t["protokoll"] == "tcp" and (":" in t["ip"]) == (kind == "tcp6"))
        family = socket.AF_INET6 if kind == "tcp6" else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(3)
            sock.connect((target["ip"], target["port"]))
    else:
        target = next(t for t in targets if t["protokoll"] == kind and ":" not in t["ip"])
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(b"lexono-trockenlauf", (target["ip"], target["port"]))
    time.sleep(0.3)  # Listener-Thread Zeit zum Protokollieren geben


def main(argv: list[str] | None = None) -> int:
    import argparse
    import tempfile

    parser = argparse.ArgumentParser(prog="isolation_acceptance", description="§91 A3 - nur Trockenlauf ohne Freigabe")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("trockenlauf")
    p.add_argument("--ordner", type=Path, default=None)
    p.add_argument("--leck", choices=("tcp", "tcp6", "udp", "udp6", "dns"), default=None)
    args = parser.parse_args(argv)
    folder = args.ordner or Path(tempfile.mkdtemp(prefix="lexono-trockenlauf-")) / "lauf"
    result = dry_run(folder, leak=args.leck)
    print(f"Trockenlauf (synthetisch, Loopback) - Buendel: {result.bundle}")
    print(f"Gesamturteil: {result.verdict['gesamt']}")
    for test, outcome in result.verdict["tests"].items():
        print(f"  {test}: {outcome['status']}" + (f" - {outcome['gruende'][0]}" if outcome["gruende"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
