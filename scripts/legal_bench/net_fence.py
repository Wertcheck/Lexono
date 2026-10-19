"""§83/§84: Netz- und Prozesszaun fuer den Benchmark-Treiber.

Waehrend `loopback_only()` aktiv ist, gilt PROZESSWEIT (alle Threads):
- Netzwerk: Verbindungen (`socket.connect`/`connect_ex`, auch roher `_socket`, asyncio), UDP-Versand (`sendto`,
  `sendmsg`) und Namensaufloesung (`getaddrinfo`, `gethostbyname`, `gethostbyaddr`, `getnameinfo`) nur fuer Loopback
  (127.0.0.0/8, ::1, "localhost"). Ein externer Hostname wird VOR jeder DNS-Abfrage verweigert.
- Prozesse: Start nur fuer ausdruecklich zugelassene Programme (`allowed_programs`, z. B. Tesseract); `os.system`,
  `os.exec*`, `os.spawn*`, `os.startfile` und direkte `_winapi.CreateProcess`-Aufrufe sind gesperrt. Ein zugelassenes
  Programm kann selbst Netzwerk nutzen - das ist eine ANNAHME je Programm, keine Durchsetzung (keine OS-Isolation).
- Native Netz-APIs ueber ctypes: Laden typischer Netz-DLLs (`ws2_32`, `winhttp`, `wininet`, `dnsapi` ...) und Nachschlagen
  typischer Netz-Funktionen gesperrt.
- Beim Aktivieren (Windows): Pruefung, dass der Prozess KEINE bestehende Verbindung zu einem Nicht-Loopback-Ziel hat
  (vorab geoeffnete Verbindungen wuerden sonst ohne neues `connect` weiter senden); ohne pruefbares Ergebnis fail-closed.

§85: Programme werden ueber `ProgramRule` zugelassen - absoluter, vertrauenswuerdig aufgeloester Pfad (nur absolute PATH-
Eintraege, nie das aktuelle Verzeichnis), SHA-256 der Datei und Fingerabdruck ihres Verzeichnisses (DLL-Austausch),
Aufruf NUR mit diesem absoluten Pfad (sonst wuerde CreateProcess eigenstaendig suchen), Abgleich der CreateProcess-
Befehlszeile, Argument- und Umgebungspruefung je Programm. Loopback optional nur zu ausdruecklich vorgesehenen Ports.

Technik: `sys.addaudithook` - die Pruefung laeuft in der C-Laufzeit VOR dem Systemaufruf und laesst sich danach nicht mehr
entfernen (ausserhalb des Zauns wirkungslos). KEINE Sicherheitsgrenze gegenueber nativem Code: C-Erweiterungen, direkte
Overlapped-Aufrufe und vorab beschaffte Funktionszeiger laufen am Hook vorbei; Kindprozesse zugelassener Programme sind
nicht erfasst. Siehe benchmarks/legal_answers_v1/AUDIT_S84.md und AUDIT_S85.md."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass


class NetworkFenceViolation(ConnectionError):
    """Netzwerkzugriff zu einem Nicht-Loopback-Ziel waehrend eines Benchmark-Laufs - verweigert, nichts gesendet."""


class ProcessFenceViolation(PermissionError):
    """Start eines nicht zugelassenen Prozesses waehrend eines Benchmark-Laufs - verweigert (keine OS-Isolation)."""


_NETWORK_DLLS = ("ws2_32", "wsock32", "mswsock", "winhttp", "wininet", "dnsapi", "iphlpapi", "urlmon", "websocket",
                 "libcurl", "libssl", "libcrypto")
_NETWORK_SYMBOLS = {"connect", "wsaconnect", "sendto", "wsasendto", "getaddrinfo", "getaddrinfow", "getaddrinfoexw",
                    "gethostbyname", "dnsquery_a", "dnsquery_w", "dnsquery_utf8", "winhttpopen", "winhttpconnect",
                    "internetopena", "internetopenw", "internetconnecta", "internetconnectw", "urldownloadtofilea",
                    "urldownloadtofilew", "curl_easy_perform"}
_LOCK = threading.Lock()
_STATE = {"depth": 0, "allowed": [], "installed": False, "ports": None}
_SPAWN = threading.local()
_AUDIT_INSTALLED = False  # fuer Diagnose/Tests


def active() -> bool:
    return _STATE["depth"] > 0


def _is_loopback(host) -> bool:  # noqa: ANN001
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    if host.lower() in ("localhost", "localhost."):
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _check_address(address) -> None:  # noqa: ANN001
    if isinstance(address, (str, bytes)) and not isinstance(address, tuple):
        if isinstance(address, str) and (address.startswith(("\\\\", "/")) or ":" not in address):
            return  # AF_UNIX-Pfad (lokal)
    host = address[0] if isinstance(address, tuple) and address else address
    if not _is_loopback(host):
        raise NetworkFenceViolation(f"Netzzaun: Verbindung/Versand zu {host!r} verweigert - waehrend eines Benchmark-Laufs "
                                    "sind nur lokale Pruefendpunkte erlaubt (C-14 gesperrt)")
    ports = _STATE.get("ports")
    port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
    if ports is not None and port not in ports:
        raise NetworkFenceViolation(f"Netzzaun: lokaler Port {port!r} ist nicht als Pruefendpunkt vorgesehen "
                                    f"(zugelassen: {sorted(ports)})")


def _check_host(host) -> None:  # noqa: ANN001
    if host is None or (isinstance(host, (str, bytes)) and host in ("", b"")):
        return  # Bindung an eine lokale Adresse, keine Aufloesung
    if not _is_loopback(host):
        raise NetworkFenceViolation(f"Netzzaun: Namensaufloesung fuer {host!r} verweigert (keine externe DNS-Abfrage)")


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _is_local_path(value: str) -> bool:
    """Absoluter lokaler Pfad mit Laufwerksbuchstaben - kein UNC/SMB (\\\\host\\share), keine URL, kein Geraetepfad."""
    if not isinstance(value, str) or "://" in value or value.startswith(("\\\\", "//", "\\??\\")):
        return False
    return bool(re.match(r"^[A-Za-z]:[\\/]", value)) if sys.platform == "win32" else value.startswith("/")


def trusted_executable(name: str) -> str | None:
    """Programm ueber NUR absolute PATH-Eintraege aufloesen - nie ueber das aktuelle Verzeichnis oder relative Eintraege
    (Windows wuerde dort sonst zuerst suchen). Ein absoluter Pfad wird nur akzeptiert, wenn er lokal ist und existiert."""
    if os.path.isabs(name):
        return _norm(name) if _is_local_path(name) and os.path.isfile(name) else None
    cwd = _norm(os.getcwd())
    exts = [""] + [e.lower() for e in os.environ.get("PATHEXT", ".EXE").split(os.pathsep) if e] if sys.platform == "win32" else [""]
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or not os.path.isabs(entry) or not _is_local_path(entry) or _norm(entry) == cwd:
            continue
        for ext in exts:
            candidate = os.path.join(entry, name + ext if not name.lower().endswith(ext) else name)
            if os.path.isfile(candidate):
                return _norm(candidate)
    return None


@dataclass(frozen=True)
class ProgramRule:
    """Zulassung eines Programms. `args`: exakte Argumente; sonst `validate_args(argv_ohne_programm) -> Fehler|None`.
    `validate_env(umgebung) -> Fehler|None` prueft die Umgebung, mit der das Programm startet."""

    name: str
    program: str
    args: tuple[str, ...] | None = None
    validate_args: Callable[[list[str]], str | None] | None = None
    validate_env: Callable[[dict], str | None] | None = None


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dir_fingerprint(path: str) -> tuple:
    folder = os.path.dirname(path)
    return tuple(sorted((e.name.lower(), e.stat().st_size, e.stat().st_mtime_ns) for e in os.scandir(folder) if e.is_file()))


def _pin(rule: ProgramRule) -> dict:
    path = trusted_executable(rule.program)
    if path is None:
        raise ProcessFenceViolation(f"zugelassenes Programm {rule.name!r} ({rule.program!r}) nicht vertrauenswuerdig "
                                    "auffindbar - fail-closed")
    return {"rule": rule, "path": path, "sha256": _sha256(path), "dir": _dir_fingerprint(path)}


def _command_parts(executable, args) -> list[str]:  # noqa: ANN001
    if isinstance(args, (str, bytes)):
        import shlex

        parts = [p.strip('"') for p in shlex.split(os.fsdecode(args), posix=False)] if args else []
    else:
        parts = [os.fsdecode(a) for a in (args or [])]
    if executable:
        parts = [os.fsdecode(executable), *parts[1:]] if parts else [os.fsdecode(executable)]
    return parts


def _check_spawn(executable, args, env=None) -> None:  # noqa: ANN001
    parts = _command_parts(executable, args)
    program = parts[0] if parts else None
    if not program or not os.path.isabs(program):
        raise ProcessFenceViolation(f"Prozesszaun: Start von {program!r} verweigert - nur zugelassene Programme mit absolutem, "
                                    "festgeschriebenem Pfad (sonst sucht Windows eigenstaendig, z. B. im aktuellen Verzeichnis)")
    path = _norm(program)
    for pinned in _STATE["allowed"]:
        if pinned["path"] != path:
            continue
        rule: ProgramRule = pinned["rule"]
        if _sha256(path) != pinned["sha256"] or _dir_fingerprint(path) != pinned["dir"]:
            raise ProcessFenceViolation(f"Prozesszaun: {rule.name} oder sein Verzeichnis wurde seit dem Aktivieren veraendert")
        rest = parts[1:]
        if rule.args is not None and tuple(rest) != rule.args:
            raise ProcessFenceViolation(f"Prozesszaun: {rule.name} nur mit {list(rule.args)} zugelassen, nicht {rest}")
        if rule.args is None:
            problem = rule.validate_args(rest) if rule.validate_args else "keine Argumentpruefung hinterlegt"
            if problem:
                raise ProcessFenceViolation(f"Prozesszaun: {rule.name} mit unzulaessigen Argumenten verweigert ({problem})")
        if rule.validate_env:
            problem = rule.validate_env(dict(env) if env is not None else dict(os.environ))
            if problem:
                raise ProcessFenceViolation(f"Prozesszaun: {rule.name} mit unzulaessiger Umgebung verweigert ({problem})")
        _SPAWN.approved = path
        return
    raise ProcessFenceViolation(f"Prozesszaun: Start von {program!r} verweigert - waehrend eines Benchmark-Laufs sind nur "
                                "zugelassene lokale Programme erlaubt (keine OS-Isolation)")


def _check_create_process(application_name, command_line) -> None:  # noqa: ANN001, ARG001
    """Nur nach einer Zulassung durch das `subprocess.Popen`-Ereignis (dessen Argument ist bereits die fertige Befehlszeile,
    die CreateProcess erhaelt). §85: das Argument `command_line` dieses Ereignisses ist in diesem Python-Build NICHT
    verwertbar (beobachtet: '\x02') und wird deshalb nicht ausgewertet; ein gesetzter `application_name` muss passen."""
    approved = getattr(_SPAWN, "approved", None)
    _SPAWN.approved = None
    if not approved:
        raise ProcessFenceViolation("Prozesszaun: direkter CreateProcess-Aufruf ohne zugelassenes Programm verweigert")
    if isinstance(application_name, str) and application_name and (
            not os.path.isabs(application_name) or _norm(application_name) != approved):
        raise ProcessFenceViolation("Prozesszaun: CreateProcess-Programm passt nicht zum geprueften Programm")


def _audit(event: str, args: tuple) -> None:
    if _STATE["depth"] <= 0:
        return
    if event in ("socket.connect", "socket.sendto", "socket.sendmsg"):
        _check_address(args[1])
    elif event == "socket.getaddrinfo":
        _check_host(args[0])
    elif event in ("socket.gethostbyname", "socket.gethostbyaddr"):
        _check_host(args[0])
    elif event == "socket.getnameinfo":
        _check_address(args[0])
    elif event == "subprocess.Popen":
        _check_spawn(args[0], args[1], args[3] if len(args) > 3 else None)
    elif event == "_winapi.CreateProcess":
        _check_create_process(args[0], args[1])
    elif event in ("os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.startfile", "pty.spawn"):
        raise ProcessFenceViolation(f"Prozesszaun: {event} waehrend eines Benchmark-Laufs verweigert")
    elif event == "ctypes.dlopen":
        name = os.path.basename(os.fsdecode(args[0] or "")).lower()
        if name.startswith(_NETWORK_DLLS):
            raise NetworkFenceViolation(f"Netzzaun: Laden der Netzwerkbibliothek {name!r} ueber ctypes verweigert")
    elif event == "ctypes.dlsym":
        symbol = args[1]
        symbol = (symbol.decode("ascii", "replace") if isinstance(symbol, bytes) else str(symbol)).lower()
        if symbol in _NETWORK_SYMBOLS:
            raise NetworkFenceViolation(f"Netzzaun: Netzwerkfunktion {symbol!r} ueber ctypes verweigert")


def _wrap_windows_proactor() -> None:
    """§84 (Befund P2): asyncio baut unter Windows Verbindungen ueber den Proactor auf (Overlapped `ConnectEx`/`WSASendTo`) -
    dieser Weg loest KEIN Audit-Ereignis aus. Waehrend des Zauns werden deshalb `IocpProactor.connect`/`sendto` mit derselben
    Adresspruefung ummantelt (Wiederherstellung beim Beenden)."""
    if sys.platform != "win32":
        return
    import asyncio.windows_events as windows_events

    proactor = windows_events.IocpProactor
    originals = {"connect": proactor.connect, "sendto": proactor.sendto}

    def connect(self, conn, address):  # noqa: ANN001, ANN202
        _check_address(address)
        return originals["connect"](self, conn, address)

    def sendto(self, conn, buf, flags=0, addr=None):  # noqa: ANN001, ANN202
        if addr is not None:
            _check_address(addr)
        return originals["sendto"](self, conn, buf, flags, addr)

    proactor.connect, proactor.sendto = connect, sendto
    _STATE["proactor"] = (proactor, originals)


def _restore_windows_proactor() -> None:
    saved = _STATE.pop("proactor", None)
    if saved:
        proactor, originals = saved
        proactor.connect, proactor.sendto = originals["connect"], originals["sendto"]


def _install() -> None:
    global _AUDIT_INSTALLED
    if not _STATE["installed"]:
        sys.addaudithook(_audit)
        _STATE["installed"] = True
        _AUDIT_INSTALLED = True


def existing_external_connections() -> list[str]:
    """Bestehende TCP-Verbindungen DIESES Prozesses zu Nicht-Loopback-Zielen. Windows: `netstat -ano`. Fehler -> Ausnahme
    (der Aufrufer bricht fail-closed ab)."""
    if sys.platform != "win32":
        raise NetworkFenceViolation("Startpruefung offener Verbindungen ist nur unter Windows umgesetzt - fail-closed")
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=60, check=True).stdout
    out += subprocess.run(["netstat", "-ano", "-p", "TCPv6"], capture_output=True, text=True, timeout=60, check=True).stdout
    pid, found = str(os.getpid()), []
    for line in out.splitlines():
        parts = line.split()
        # §84: sprachunabhaengig (netstat lokalisiert den Status, z. B. "HERGESTELLT") - jede Zeile dieses Prozesses mit einem
        # echten Gegenueber zaehlt; lauschende Sockets haben als Gegenueber 0.0.0.0:0 bzw. [::]:0
        if len(parts) == 5 and parts[0].upper() == "TCP" and parts[4] == pid:
            remote = parts[2].rsplit(":", 1)[0].strip("[]")
            if remote not in ("0.0.0.0", "::", "*") and not _is_loopback(remote):
                found.append(parts[2])
    return found


@contextmanager
def loopback_only(allowed_programs: list[ProgramRule | tuple] | None = None, *, check_existing_connections: bool = True,
                  allowed_loopback_ports: set[int] | None = None):
    """`allowed_programs`: `ProgramRule`s (oder Kurzform (Programm, exakte Argumente)) - nur lokale Programme ohne
    Netzwerkbedarf. `allowed_loopback_ports`: None = jeder Loopback-Port, sonst nur diese (z. B. der lokale Recorder)."""
    with _LOCK:
        if _STATE["depth"] == 0:
            if check_existing_connections:
                try:
                    existing = existing_external_connections()
                except NetworkFenceViolation:
                    raise
                except Exception as exc:  # noqa: BLE001 - ohne pruefbares Ergebnis kein Start (fail-closed)
                    raise NetworkFenceViolation(f"Startpruefung offener Verbindungen fehlgeschlagen ({type(exc).__name__})") from None
                if existing:
                    raise NetworkFenceViolation(f"Bestehende externe Verbindung(en) {existing[:3]} - Zaun nicht aktivierbar")
            _install()
            rules = [r if isinstance(r, ProgramRule) else ProgramRule(str(r[0]), str(r[0]),
                                                                       tuple(r[1]) if r[1] is not None else None)
                     for r in allowed_programs or []]
            _STATE["allowed"] = [_pin(rule) for rule in rules]  # wirft bei nicht auffindbarem Programm (fail-closed)
            _STATE["ports"] = set(allowed_loopback_ports) if allowed_loopback_ports is not None else None
            _wrap_windows_proactor()
        _STATE["depth"] += 1
    try:
        yield
    finally:
        with _LOCK:
            _STATE["depth"] -= 1
            if _STATE["depth"] == 0:
                _STATE["allowed"] = []
                _STATE["ports"] = None
                _restore_windows_proactor()
