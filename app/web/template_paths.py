"""Absolute Pfade zu Templates/statischen Assets (Prompt 36, Windows-Installer).

Bewusst ABSOLUT und am Ort dieser Datei verankert - nicht relativ zum
Arbeitsverzeichnis des Prozesses, wie es vorher an allen Verwendungsstellen
der Fall war. Grund: der Windows-Entry-Point (`run.py`) wechselt das
Arbeitsverzeichnis beim Start bewusst in das persistente Datenverzeichnis
(`%PROGRAMDATA%\\Lexono`, siehe app/setup/paths.py), damit relative Pfade
in den Settings (`DATABASE_URL`, `INTAKE_STORAGE_DIR`, ...) dorthin zeigen,
statt versehentlich in den schreibgeschützten Installationsordner. Templates
und statische Assets sind aber Teil der INSTALLATION, nicht der Daten, und
müssen unabhängig vom aktuellen Arbeitsverzeichnis auffindbar bleiben - mit
einem relativen `"app/web/templates"`-String (wie vor diesem Prompt an allen
neun Verwendungsstellen) wäre das Dashboard nach dem Verzeichniswechsel
funktionslos gewesen (404 auf jede Seite/jedes statische Asset). Funktioniert
unverändert sowohl im normalen Entwicklungsbetrieb als auch gebündelt
(PyInstaller extrahiert `app/web/templates`/`app/web/static` unter demselben
relativen Pfad, siehe windows/lexono.spec).
"""

from pathlib import Path

_WEB_DIR = Path(__file__).resolve().parent

TEMPLATES_DIR = str(_WEB_DIR / "templates")
STATIC_DIR = str(_WEB_DIR / "static")
