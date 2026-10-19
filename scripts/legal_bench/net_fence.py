"""§83: Netzzaun fuer den Benchmark-Treiber - waehrend eines Laufs sind ausgehende Socket-Verbindungen NUR zu Loopback-
Adressen (127.0.0.0/8, ::1, "localhost") erlaubt. Zweite Verteidigungslinie neben `answer_driver.assert_local_only`:
faengt auch Cloud-Clients ab, die in Closures, globalen Variablen oder Unterbibliotheken versteckt sind.

Oeffnet selbst keine Verbindung. Wirkt prozessweit, solange der Kontext offen ist (der Treiber laeuft als Skript bzw. im
Test). Grenze: Unterprozesse (z. B. ein gestartetes Programm mit eigener Netzwerkverbindung) werden NICHT erfasst."""

from __future__ import annotations

import ipaddress
import socket
import threading
from contextlib import contextmanager


class NetworkFenceViolation(ConnectionError):
    """Verbindung zu einem Nicht-Loopback-Ziel waehrend eines Benchmark-Laufs - verweigert, nichts gesendet."""


_LOCK = threading.Lock()
_DEPTH = 0
_ORIGINAL: dict = {}


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
        return False  # Hostname (z. B. api.anthropic.com) - nie erlaubt


def _check(address) -> None:  # noqa: ANN001
    host = address[0] if isinstance(address, tuple) and address else address
    if not _is_loopback(host):
        raise NetworkFenceViolation(f"Netzzaun: Verbindung zu {host!r} verweigert - waehrend eines Benchmark-Laufs sind nur "
                                    "lokale Pruefendpunkte erlaubt (C-14 gesperrt)")


@contextmanager
def loopback_only():
    global _DEPTH
    with _LOCK:
        if _DEPTH == 0:
            _ORIGINAL.update(connect=socket.socket.connect, connect_ex=socket.socket.connect_ex,
                             create_connection=socket.create_connection)

            def connect(self, address):  # noqa: ANN001, ANN202
                _check(address)
                return _ORIGINAL["connect"](self, address)

            def connect_ex(self, address):  # noqa: ANN001, ANN202
                _check(address)
                return _ORIGINAL["connect_ex"](self, address)

            def create_connection(address, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
                _check(address)
                return _ORIGINAL["create_connection"](address, *args, **kwargs)

            socket.socket.connect = connect
            socket.socket.connect_ex = connect_ex
            socket.create_connection = create_connection
        _DEPTH += 1
    try:
        yield
    finally:
        with _LOCK:
            _DEPTH -= 1
            if _DEPTH == 0:
                socket.socket.connect = _ORIGINAL["connect"]
                socket.socket.connect_ex = _ORIGINAL["connect_ex"]
                socket.create_connection = _ORIGINAL["create_connection"]
