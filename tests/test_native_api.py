"""Tests für `run.py::_NativeApi` (Masterprompt V2, Task #61 – eigene
Titelleiste im frameless-Fenster statt nativer OS-Chrome).

Reine Unit-Tests gegen ein Fake-Window-Objekt (kein echtes pywebview-
Fenster, kein Display nötig) - prüfen, dass die vier neuen JS-aufrufbaren
Methoden die erwarteten `webview.Window`-Methoden mit den erwarteten
Argumenten aufrufen, inklusive der Untergrenzen beim Resize. Das
tatsächliche visuelle Ergebnis (Drag/Resize/Minimieren/Schließen im echten
Fenster) ist NUR manuell/per Installer-Build überprüfbar (siehe
.agentic/VISUAL_QA.md) - hier wird ausschließlich die Python-seitige Logik
abgesichert."""

from __future__ import annotations

import run


class _FakeWindow:
    def __init__(self, x: int = 100, y: int = 80, width: int = 1400, height: int = 900) -> None:
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.minimize_calls = 0
        self.destroy_calls = 0
        self.move_calls: list[tuple[int, int]] = []
        self.resize_calls: list[tuple[int, int]] = []

    def minimize(self) -> None:
        self.minimize_calls += 1

    def destroy(self) -> None:
        self.destroy_calls += 1

    def move(self, x: int, y: int) -> None:
        self.move_calls.append((x, y))

    def resize(self, width: int, height: int) -> None:
        self.resize_calls.append((width, height))


def test_minimize_window_calls_native_minimize() -> None:
    api = run._NativeApi()
    api._window = _FakeWindow()
    api.minimize_window()
    assert api._window.minimize_calls == 1


def test_minimize_window_is_noop_without_window() -> None:
    api = run._NativeApi()
    api.minimize_window()  # darf nicht werfen


def test_close_window_calls_native_destroy() -> None:
    api = run._NativeApi()
    api._window = _FakeWindow()
    api.close_window()
    assert api._window.destroy_calls == 1


def test_close_window_is_noop_without_window() -> None:
    api = run._NativeApi()
    api.close_window()  # darf nicht werfen - "X muss zuverlaessig funktionieren"


def test_move_window_by_adds_delta_to_current_position() -> None:
    api = run._NativeApi()
    api._window = _FakeWindow(x=100, y=80)
    api.move_window_by(15, -5)
    assert api._window.move_calls == [(115, 75)]


def test_move_window_by_is_noop_without_window() -> None:
    api = run._NativeApi()
    api.move_window_by(10, 10)  # darf nicht werfen


def test_resize_window_by_adds_delta_to_current_size() -> None:
    api = run._NativeApi()
    api._window = _FakeWindow(width=1400, height=900)
    api.resize_window_by(50, -20)
    assert api._window.resize_calls == [(1450, 880)]


def test_resize_window_by_enforces_minimum_width_and_height() -> None:
    api = run._NativeApi()
    api._window = _FakeWindow(width=950, height=650)
    api.resize_window_by(-500, -500)
    width, height = api._window.resize_calls[0]
    assert width == run._MIN_WINDOW_WIDTH
    assert height == run._MIN_WINDOW_HEIGHT


def test_resize_window_by_is_noop_without_window() -> None:
    api = run._NativeApi()
    api.resize_window_by(10, 10)  # darf nicht werfen
