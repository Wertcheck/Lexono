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

Technik: `sys.addaudithook` - die Pruefung laeuft in der C-Laufzeit VOR dem Systemaufruf und laesst sich danach nicht mehr
entfernen (ausserhalb des Zauns wirkungslos). Grenzen siehe benchmarks/legal_answers_v1/AUDIT_S84.md: C-Erweiterungen mit
eigener Netzwerkbibliothek, zugelassene Programme und absichtliche Manipulation dieses Moduls."""

from __future__ import annotations

import ipaddress
import os
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager


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
_STATE = {"depth": 0, "allowed": [], "installed": False}
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


def _check_host(host) -> None:  # noqa: ANN001
    if host is None or (isinstance(host, (str, bytes)) and host in ("", b"")):
        return  # Bindung an eine lokale Adresse, keine Aufloesung
    if not _is_loopback(host):
        raise NetworkFenceViolation(f"Netzzaun: Namensaufloesung fuer {host!r} verweigert (keine externe DNS-Abfrage)")


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _resolve(program) -> str | None:  # noqa: ANN001
    if not program:
        return None
    program = os.fsdecode(program)
    found = shutil.which(program) or (program if os.path.isfile(program) else None)
    return _norm(found) if found else None


def _program_of(executable, args) -> tuple[str | None, list[str]]:  # noqa: ANN001
    if isinstance(args, (str, bytes)):
        import shlex

        parts = shlex.split(os.fsdecode(args), posix=False) if args else []
        parts = [p.strip('"') for p in parts]
    else:
        parts = [os.fsdecode(a) for a in (args or [])]
    return _resolve(executable or (parts[0] if parts else None)), parts[1:]


def _check_spawn(executable, args) -> None:  # noqa: ANN001
    program, rest = _program_of(executable, args)
    for allowed_program, allowed_args in _STATE["allowed"]:
        if program == allowed_program and (allowed_args is None or tuple(rest) == tuple(allowed_args)):
            _SPAWN.approved = True
            return
    raise ProcessFenceViolation(f"Prozesszaun: Start von {program or executable or args!r} verweigert - waehrend eines "
                                "Benchmark-Laufs sind nur zugelassene lokale Programme erlaubt (keine OS-Isolation)")


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
        _check_spawn(args[0], args[1])
    elif event == "_winapi.CreateProcess":
        if not getattr(_SPAWN, "approved", False):
            raise ProcessFenceViolation("Prozesszaun: direkter CreateProcess-Aufruf ohne zugelassenes Programm verweigert")
        _SPAWN.approved = False
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
def loopback_only(allowed_programs: list[tuple[str, tuple[str, ...] | None]] | None = None, *,
                  check_existing_connections: bool = True):
    """`allowed_programs`: [(Programm, exakte Argumente oder None = beliebig)] - nur lokale Programme ohne Netzwerkbedarf."""
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
            resolved = []
            for program, args in allowed_programs or []:
                path = _resolve(program)
                if path is None:
                    raise ProcessFenceViolation(f"zugelassenes Programm {program!r} nicht gefunden - fail-closed")
                resolved.append((path, tuple(args) if args is not None else None))
            _STATE["allowed"] = resolved
            _wrap_windows_proactor()
        _STATE["depth"] += 1
    try:
        yield
    finally:
        with _LOCK:
            _STATE["depth"] -= 1
            if _STATE["depth"] == 0:
                _STATE["allowed"] = []
                _restore_windows_proactor()
