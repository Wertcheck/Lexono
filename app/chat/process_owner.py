"""Besitzer eines laufenden Upload-Vorgangs (§60): Rechner, Prozess-ID, Prozess-Startzeitpunkt und Ausfuehrungsversuch.

Ein Vorgang darf NUR dann von einem anderen Request uebernommen werden, wenn sein Besitzer NACHWEISLICH nicht mehr
laeuft - eine abgelaufene Zeit allein gibt nie frei (ein langsamer OCR-Lauf ist kein Absturz). Die Startzeit schuetzt
gegen wiederverwendete Prozess-IDs. Jeder Ausfuehrungsversuch traegt eine eigene Kennung (Nonce): die atomare
Uebernahme (Compare-and-Set) gewinnt dadurch genau EIN Request - auch wenn der beendete Besitzer der eigene Prozess war.
Auf einem fremden Rechner (gemeinsame Datenbank) ist das nicht pruefbar: dann "unbekannt" - der Vorgang wird nicht
uebernommen (fail-closed), ein bewusst neuer Upload bleibt moeglich.

Der Rechnername wird nur als Hash gespeichert (er enthaelt oft Personennamen, z. B. "LAPTOP-MUELLER").
Bewusst ohne Zusatzpaket (psutil ist nicht installiert): Windows ueber kernel32, sonst /proc.
"""

from __future__ import annotations

import hashlib
import os
import socket
import sys
import uuid

ALIVE = "alive"
DEAD = "dead"
UNKNOWN = "unknown"

_STILL_ACTIVE = 259
_ERROR_INVALID_PARAMETER = 87  # OpenProcess: keine Prozess-ID dieses Werts (Prozess existiert nicht)


def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    return ctypes, wintypes, kernel32


def _probe(pid: int) -> tuple[str, int | None]:
    """(Zustand, Startzeit-Kennung) des Prozesses `pid` auf DIESEM Rechner. Im Zweifel UNKNOWN, nie DEAD."""
    if pid <= 0:
        return UNKNOWN, None
    if sys.platform == "win32":
        ctypes, wintypes, kernel32 = _kernel32()
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            # NUR "keine solche Prozess-ID" belegt das Ende; Zugriff verweigert u. a. -> nicht pruefbar
            return (DEAD, None) if ctypes.get_last_error() == _ERROR_INVALID_PARAMETER else (UNKNOWN, None)
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return UNKNOWN, None
            if code.value != _STILL_ACTIVE:
                return DEAD, None  # beendet (Handle haelt nur noch den Prozesseintrag)
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                            ctypes.byref(kernel), ctypes.byref(user)):
                return UNKNOWN, None
            return ALIVE, (created.dwHighDateTime << 32) | created.dwLowDateTime
        finally:
            kernel32.CloseHandle(handle)
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as handle:
            fields = handle.read().rsplit(")", 1)[1].split()
        return ALIVE, int(fields[19])  # starttime (Feld 22)
    except FileNotFoundError:
        return DEAD, None
    except (OSError, ValueError, IndexError):
        return UNKNOWN, None


_HOST = hashlib.sha256(("lexono-upload-owner|" + socket.gethostname()).encode("utf-8")).hexdigest()[:16]
_SELF_STATE, _SELF_TICKS = _probe(os.getpid())
#: Prozesskennung DIESES Prozesses (keine Inhalte - Rechner-Hash, Prozess-ID, Startzeit)
PROCESS = f"{_HOST}|{os.getpid()}|{_SELF_TICKS if _SELF_TICKS is not None else 'x'}"


def new_attempt() -> str:
    """Besitzerkennung fuer EINEN Ausfuehrungsversuch dieses Prozesses (Prozesskennung + Nonce)."""
    return f"{PROCESS}|{uuid.uuid4().hex[:16]}"


def is_own(owner: str | None) -> bool:
    return bool(owner) and owner.rsplit("|", 1)[0] == PROCESS


def state_of(owner: str | None) -> str:
    """ALIVE / DEAD / UNKNOWN fuer den Prozess eines FREMDEN Besitzers (den eigenen beurteilt der Aufrufer selbst)."""
    if not owner:
        return UNKNOWN  # Altbestand ohne Besitzer: nicht pruefbar
    parts = owner.split("|")
    if len(parts) != 4 or not parts[1].isdigit():
        return UNKNOWN
    host, pid, ticks = parts[0], int(parts[1]), parts[2]
    if host != _HOST:
        return UNKNOWN  # anderer Rechner: Lebenszeichen nicht pruefbar
    state, current_ticks = _probe(pid)
    if state != ALIVE:
        return state
    if not ticks.isdigit() or current_ticks is None:
        return UNKNOWN
    # gleiche Prozess-ID, aber anderer Startzeitpunkt: die ID wurde wiederverwendet - der Besitzer lebt nicht mehr
    return ALIVE if int(ticks) == current_ticks else DEAD
