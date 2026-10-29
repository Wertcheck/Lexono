"""§102 (AUDIT_S100 A1/A4): Welcher Code wird tatsaechlich geladen, und darf das laufende Konto ihn veraendern?

A1 - Codequellen (fail-closed, jede Abweichung ist ein Problem -> Gate geschlossen bzw. V2 BLOCKIERT):
- Bytecode-Isolation: Python laeuft mit `PYTHONDONTWRITEBYTECODE=1` und einem NICHT existierenden `PYTHONPYCACHEPREFIX`
  in einem fuer das Konto nicht beschreibbaren Ordner. Dann liest weder Python noch pytest (Assertion-Rewriting) Bytecode
  aus `__pycache__` - untergeschobene, veraltete oder verwaiste `.pyc` sind wirkungslos, jede Quelle wird frisch
  uebersetzt (AUDIT_S100 R2a). Ohne diese Isolation wird NICHT versucht, Bytecode zu "pruefen": BLOCKIERT.
- Quellenloser Bytecode (`*.pyc`/`*.pyo` ausserhalb `__pycache__`) und Erweiterungsmodule (`*.pyd`/`*.so`) im
  Repository werden auch mit Isolation geladen -> Problem (R2b).
- Ueberschattung: Paket- oder Modulnamen in der Repository-Wurzel, die nicht im Fingerabdruck stehen (R3).
- Suchpfad: jeder Eintrag in `sys.path` muss Repository-Wurzel, venv oder Basis-Installation sein.
- Geladene Module: jedes Modul mit Datei muss aus dem Fingerabdruck (Repository), aus der venv oder der
  Basis-Installation stammen und aus einer Quelle (`.py`) bzw. einem Erweiterungsmodul ausserhalb des Repositorys.

Grenzen (ehrlich, AUDIT_S102): Der INHALT von Dateien in venv/site-packages und der Standardbibliothek wird nicht
gehasht (nur Distributionen, `.pth`, Eintraege - `isolation_gate.laufzeit_stand`); gegen Veraenderung schuetzt dort nur
der Schreibschutz (A4). Module, die erst NACH einer Pruefung importiert werden, prueft erst die naechste Pruefung.

A4 - Schreibrechte (`write_access`): Repository (inkl. `__pycache__`), venv (inkl. `Scripts`, `pyvenv.cfg`,
site-packages rekursiv) und die tatsaechliche Basis-Installation (`sys.base_prefix`, Standardbibliothek) samt aller
weiteren `sys.path`-Ordner. Dateien: Oeffnen zum Anhaengen (schreibt nichts). Ordner: exklusives Anlegen einer
Probedatei, die sofort entfernt wird. Nicht eindeutig pruefbar (z. B. Freigabeverletzung bei einer laufenden Datei) ->
`nicht_pruefbar` (nie "geschuetzt")."""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Ordnernamen, die in jeder Tiefe nicht zum importierbaren Code des Repositorys gehoeren (Build-Ausgaben, Caches; .gitignore)
EXCLUDED_ANY = frozenset({".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", "build", "dist", "build_tools"})
#: nur in der Wurzel ausgeschlossen (.gitignore: /data/, /gateway_data/)
EXCLUDED_ROOT = frozenset({"data", "gateway_data"})
BYTECODE_SUFFIXES = (".pyc", ".pyo")
EXTENSION_SUFFIXES = (".pyd", ".so")
#: Dateien, die in venv/Basis-Installation Code ausfuehren oder den Start steuern (A4)
RUNTIME_CODE_SUFFIXES = (".py", ".pyc", ".pyo", ".pyd", ".dll", ".exe", ".pth", ".cfg", ".zip", ".so", ".bat", ".ps1")
#: Dateien im Repository, deren Veraenderung zaehlt (Code und Konfiguration)
REPO_CODE_SUFFIXES = (*RUNTIME_CODE_SUFFIXES, ".json", ".toml", ".yaml", ".yml", ".txt")
MAX_LISTE = 200  # Ausgabe gekuerzt; die Anzahl steht vollstaendig im Ergebnis
#: §104 (A12): Windows-Fehlercodes. Nur ACCESS_DENIED belegt fehlenden Schreibzugriff; alles andere ist "nicht_pruefbar".
ERROR_FILE_NOT_FOUND = 2
ERROR_PATH_NOT_FOUND = 3
ERROR_ACCESS_DENIED = 5
ERROR_SHARING_VIOLATION = 32
_GENERIC_WRITE = 0x40000000
#: §110 (A16): Rechte, den SCHUTZ selbst zu veraendern - wer sie hat, kann einen Schreibschutz aufheben und gilt daher
#: als "beschreibbar" (unsicher), auch wenn der reine Inhalts-Schreibzugriff verweigert waere. WRITE_DAC = DACL aendern,
#: WRITE_OWNER = Eigentum uebernehmen. (Der Eigentuemer hat WRITE_DAC implizit - ein eigenes Verzeichnis ist nie sicher.)
_WRITE_DAC = 0x00040000
_WRITE_OWNER = 0x00080000
_FILE_SHARE_ALL = 0x1 | 0x2 | 0x4  # READ | WRITE | DELETE - fremde offene Handles stoeren die Pruefung nicht
_OPEN_EXISTING = 3
_CREATE_NEW = 1
_FLAG_BACKUP_SEMANTICS = 0x02000000  # noetig, um ein Verzeichnis-Handle zu oeffnen
_FLAG_DELETE_ON_CLOSE = 0x04000000  # Probedatei wird beim Schliessen automatisch entfernt
#: ungueltige CreateFileW-Rueckgaben (c_void_p): None, 0 und INVALID_HANDLE_VALUE (-1 bzw. als unsigned 2**64-1/2**32-1)
_INVALID_HANDLES = frozenset({None, 0, -1, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFF})


def _norm(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(path)))


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("\\/") + os.sep)


def _excluded(rel_dir: Path, name: str) -> bool:
    return (name in EXCLUDED_ANY or name.startswith(".") or name.endswith(".egg-info")
            or (rel_dir == Path(".") and name in EXCLUDED_ROOT))


def walk_repo(repo: Path, *, keep_pycache: bool = False, onerror=None):  # noqa: ANN001, ANN201
    """os.walk ueber die importierbaren Teile des Repositorys (ausgeschlossene Ordner werden nicht betreten).
    `onerror` erhaelt nicht lesbare Ordner (sonst uebergeht os.walk sie stillschweigend)."""
    repo = Path(repo)
    for dirpath, dirnames, filenames in os.walk(repo, onerror=onerror):
        rel = Path(dirpath).relative_to(repo)
        dirnames[:] = sorted(d for d in dirnames if not _excluded(rel, d) or (keep_pycache and d == "__pycache__"))
        yield rel, dirnames, sorted(filenames)


def python_files(repo: Path) -> list[str]:
    """Alle `*.py` des Repositorys ausserhalb ausgeschlossener Ordner (repo-relativ, posix)."""
    return [(rel / name).as_posix() for rel, _, files in walk_repo(repo) for name in files if name.endswith(".py")]


# --- A1: Codequellen ------------------------------------------------------------------------------------------------------


def _is_package(folder: Path) -> bool:
    return any((folder / f"__init__{s}").exists() for s in (".py", ".pyc", ".pyo", ".pyd"))


def _importable_excluded(repo: Path) -> list[str]:
    """§104 (A14): ausgeschlossene Verzeichnisse (build/dist/… sowie data/… in der Wurzel), die trotzdem importierbar
    sind, weil ihr Elternverzeichnis ein regulaeres Paket ist (oder sie direkt unter der Wurzel liegen). Solche Pfade
    sind weder im Fingerabdruck noch (wenn gitignoriert) in `git status` sichtbar."""
    repo = Path(repo)
    problems: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        rel = Path(dirpath).relative_to(repo)
        for name in list(dirnames):
            if not _excluded(rel, name) or name in (".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"):
                continue  # .git/.venv/node_modules sind keine Python-Pakete; __pycache__ wird separat behandelt
            folder = Path(dirpath) / name
            parent_is_pkg = (rel == Path(".")) or _is_package(Path(dirpath))
            has_code = _is_package(folder) or any(c.lower().endswith((".py", *BYTECODE_SUFFIXES, *EXTENSION_SUFFIXES))
                                                  for c in os.listdir(folder) if (folder / c).is_file())
            if parent_is_pkg and has_code:
                problems.append(f"importierbares, ausgeschlossenes Verzeichnis (nicht im Fingerabdruck/ggf. gitignoriert): "
                                f"{(rel / name).as_posix()}")
        # ausgeschlossene Verzeichnisse auf dieser Ebene bereits bewertet -> nicht tiefer absteigen (schnell, shallow)
        dirnames[:] = [d for d in dirnames if not _excluded(rel, d)]
    return problems


def known_roots() -> dict[str, list[str]]:
    """Tatsaechliche Installationspfade dieses Prozesses (venv und Basis-Installation)."""
    import site

    venv = sorted({_norm(sys.prefix), _norm(sys.exec_prefix)})
    base = sorted({_norm(sys.base_prefix), _norm(sys.base_exec_prefix)})
    packages = sorted({_norm(p) for p in site.getsitepackages()})
    return {"venv": venv, "basis": base, "site_packages": packages}


def _kernel32():  # noqa: ANN202
    """kernel32 mit use_last_error=True, damit ctypes.get_last_error() den echten GetLastError-Wert liefert."""
    import ctypes

    return ctypes.WinDLL("kernel32", use_last_error=True)


def _win_create(path: str, access: int, share: int, disposition: int, flags: int) -> tuple[int, int]:
    """CreateFileW -> (handle, GetLastError). §104 (A12): os.open bildet den Windows-Fehler auf errno ab und verliert
    dabei die Unterscheidung ACCESS_DENIED (5) / SHARING_VIOLATION (32) - die WinAPI gibt sie direkt zurueck."""
    import ctypes
    from ctypes import wintypes

    k32 = _kernel32()
    create = k32.CreateFileW
    create.restype = ctypes.c_void_p
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                       wintypes.DWORD, ctypes.c_void_p]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.CloseHandle.restype = wintypes.BOOL
    handle = create(path, access, share, None, disposition, flags, None)
    valid = handle not in _INVALID_HANDLES
    error = 0 if valid else ctypes.get_last_error()
    if valid:
        k32.CloseHandle(handle)
    return handle, error


def _classify(error: int) -> str:
    """Windows-Fehlercode -> Ergebnis. NUR ACCESS_DENIED gilt als 'geschuetzt' (echter fehlender Schreibzugriff)."""
    if error == ERROR_ACCESS_DENIED:
        return "geschuetzt"
    return "nicht_pruefbar"  # SHARING_VIOLATION, NOT_FOUND, Unbekanntes: keine Schutzbehauptung


def _probe_dir(folder: Path) -> str:
    """'beschreibbar' | 'geschuetzt' | 'nicht_pruefbar'. §104 (A12/A16): Es werden BEIDE Schreibrechte geprueft -
    eine neue Datei anlegen UND ein Unterverzeichnis anlegen; beide werden sofort wieder entfernt. Gelingt eines, ist
    der Ordner beschreibbar. Nur wenn beide ACCESS_DENIED ergeben, gilt der Ordner als geschuetzt."""
    folder = Path(folder)
    if sys.platform != "win32":
        probe = folder / f".lexono-schreibtest-{os.getpid()}-{id(folder)}"
        try:
            fd = os.open(str(probe), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except PermissionError:
            return "geschuetzt"
        except OSError:
            return "nicht_pruefbar"
        os.close(fd)
        try:
            os.remove(probe)
        except OSError:
            pass
        return "beschreibbar"
    import ctypes

    errors = []
    probe = str(folder / f".lexono-schreibtest-{os.getpid()}-{id(folder)}")
    handle, error = _win_create(probe, _GENERIC_WRITE, _FILE_SHARE_ALL, _CREATE_NEW, _FLAG_DELETE_ON_CLOSE)
    if handle not in _INVALID_HANDLES:
        return "beschreibbar"  # Datei-Anlage moeglich (Handle mit DELETE_ON_CLOSE bereits geschlossen)
    errors.append(error)
    k32 = _kernel32()
    sub = str(folder / f".lexono-schreibtest-dir-{os.getpid()}-{id(folder)}")
    if k32.CreateDirectoryW(sub, None):
        k32.RemoveDirectoryW(sub)
        return "beschreibbar"  # Unterverzeichnis-Anlage moeglich
    errors.append(ctypes.get_last_error())
    # §110 (A16): kann das Konto die Berechtigungen/den Eigentuemer des Verzeichnisses aendern? Dann ist der Schutz
    # aufhebbar -> beschreibbar. (Verzeichnis-Handle braucht FILE_FLAG_BACKUP_SEMANTICS.)
    for maske in (_WRITE_DAC, _WRITE_OWNER):
        h, e = _win_create(str(folder), maske, _FILE_SHARE_ALL, _OPEN_EXISTING, _FLAG_BACKUP_SEMANTICS)
        if h not in _INVALID_HANDLES:
            return "beschreibbar"
        errors.append(e)
    if all(e == ERROR_ACCESS_DENIED for e in errors):
        return "geschuetzt"
    return "nicht_pruefbar"


def _probe_file(path: Path) -> str:
    """'beschreibbar' | 'geschuetzt' | 'nicht_pruefbar'. §104 (A12): Datei zum Schreiben OEFFNEN (nichts schreiben).
    Eine Freigabeverletzung (laufende Datei) und jeder andere Fehler sind KEIN Schutznachweis -> 'nicht_pruefbar'."""
    path = Path(path)
    if sys.platform != "win32":
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_APPEND)
        except PermissionError:
            return "geschuetzt"
        except OSError:
            return "nicht_pruefbar"
        os.close(fd)
        return "beschreibbar"
    errors = []
    handle, error = _win_create(str(path), _GENERIC_WRITE, _FILE_SHARE_ALL, _OPEN_EXISTING, 0)
    if handle not in _INVALID_HANDLES:
        return "beschreibbar"  # Inhalt schreibbar
    errors.append(error)
    # §110 (A16): Recht, DACL/Eigentuemer zu aendern (Schutz aufhebbar) -> ebenfalls "beschreibbar" (unsicher)
    for maske in (_WRITE_DAC, _WRITE_OWNER):
        h, e = _win_create(str(path), maske, _FILE_SHARE_ALL, _OPEN_EXISTING, 0)
        if h not in _INVALID_HANDLES:
            return "beschreibbar"
        errors.append(e)
    if all(e == ERROR_ACCESS_DENIED for e in errors):  # nur wenn WEDER Inhalt NOCH Rechte aenderbar sind
        return "geschuetzt"
    return "nicht_pruefbar"


def bytecode_isolation(*, flags=None, prefix: str | None = None, probe=_probe_dir) -> list[str]:  # noqa: ANN001
    """Bytecode wird weder gelesen noch geschrieben (siehe Moduldoku)."""
    flags = flags if flags is not None else sys.flags
    prefix = sys.pycache_prefix if prefix is None else prefix
    problems = []
    if not getattr(flags, "dont_write_bytecode", False):
        problems.append("Bytecode-Isolation fehlt: PYTHONDONTWRITEBYTECODE=1 (bzw. -B) nicht gesetzt")
    if not prefix:
        problems.append("Bytecode-Isolation fehlt: PYTHONPYCACHEPREFIX nicht gesetzt - Bytecode aus __pycache__ wuerde "
                        "geladen (untergeschobene .pyc moeglich)")
        return problems
    path = Path(prefix)
    if not path.is_absolute():
        problems.append("PYTHONPYCACHEPREFIX ist kein absoluter Pfad")
        return problems
    if path.exists():
        problems.append(f"PYTHONPYCACHEPREFIX {prefix} existiert - darin liegender Bytecode wuerde geladen")
        return problems
    ancestor = path.parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    result = probe(ancestor)
    if result != "geschuetzt":
        problems.append(f"Elternordner {ancestor} des PYTHONPYCACHEPREFIX ist {result.replace('_', ' ')} - "
                        "Bytecode koennte dort untergeschoben werden")
    return problems


def _module_files(modules) -> list[tuple[str, str]]:  # noqa: ANN001
    result = []
    for name, module in list(modules.items()):
        spec = getattr(module, "__spec__", None)
        origin = getattr(spec, "origin", None) if spec is not None else None
        file = getattr(module, "__file__", None) or origin
        if not isinstance(file, str) or file in ("built-in", "frozen") or not os.path.isabs(file):
            continue  # eingebaute, eingefrorene und Namensraum-Module haben keine eigene Datei
        result.append((name, file))
    return result


def code_source_problems(repo: Path, fingerprint: set[str], *, modules=None, path: list | None = None,  # noqa: ANN001, PLR0912
                         roots: dict | None = None, isolation=bytecode_isolation) -> list[str]:  # noqa: ANN001
    """A1: alle Probleme mit Codequellen dieses Prozesses (leer = eindeutig)."""
    repo_n = _norm(repo)
    roots = roots or known_roots()
    allowed = [*roots["venv"], *roots["basis"], *roots["site_packages"]]
    problems = list(isolation())
    # quellenloser Bytecode und Erweiterungsmodule im Repository; symbolische Ordner-Links werden nicht durchlaufen.
    # §104 (A15): Endungen case-insensitiv (unter Windows wird auch `.PYC`/`.PYD` geladen).
    for rel, dirnames, files in walk_repo(repo):
        for name in dirnames:
            if (Path(repo) / rel / name).is_symlink():
                problems.append(f"symbolischer Ordner-Link im Repository (Inhalt nicht erfasst): {(rel / name).as_posix()}")
        for name in files:
            low = name.lower()
            if low.endswith(BYTECODE_SUFFIXES):
                problems.append(f"quellenloser Bytecode im Repository: {(rel / name).as_posix()}")
            elif low.endswith(EXTENSION_SUFFIXES):
                problems.append(f"Erweiterungsmodul im Repository (nicht im Fingerabdruck): {(rel / name).as_posix()}")
    # §104 (A14): importierbare, aber ausgeschlossene (meist gitignorierte) Unterverzeichnisse - sie umgehen Fingerabdruck
    # UND `git status`. Erkannt wird ein ausgeschlossenes Verzeichnis, das als (Teil-)Paket importierbar ist.
    problems += _importable_excluded(repo)
    # Ueberschattung durch Wurzel-Pakete/-Module ausserhalb des Fingerabdrucks
    import importlib.machinery

    suffixes = tuple(s.lower() for s in importlib.machinery.all_suffixes())
    for child in sorted(Path(repo).iterdir()):
        if child.is_file() and child.name.lower().endswith(suffixes) and child.name not in fingerprint:
            problems.append(f"Modul in der Repository-Wurzel nicht im Fingerabdruck: {child.name}")
        elif child.is_dir() and any((child / f"__init__{s}").exists() for s in suffixes) \
                and f"{child.name}/__init__.py" not in fingerprint:
            problems.append(f"Paket {child.name}/ in der Repository-Wurzel nicht im Fingerabdruck (kann eine "
                            "Bibliothek ueberdecken)")
    # Suchpfad
    for entry in (sys.path if path is None else path):
        if isinstance(entry, str) and entry.startswith("__editable__.") and entry.endswith(".__path_hook__"):
            continue  # Editable-Finder (Inhalt im Laufzeit-Fingerabdruck)
        resolved = _norm(entry or os.getcwd())
        if resolved == repo_n or any(_inside(resolved, r) for r in allowed):
            continue
        problems.append(f"unbekannter Eintrag im Suchpfad: {entry or os.getcwd()}")
    # tatsaechlich geladene Module
    for name, file in _module_files(sys.modules if modules is None else modules):
        resolved = _norm(file)
        if file.endswith(BYTECODE_SUFFIXES):
            problems.append(f"Modul {name} aus Bytecode ohne Quelle geladen ({file})")
        elif any(_inside(resolved, r) for r in allowed):
            continue  # venv/Basis-Installation (die venv darf im Repository liegen)
        elif _inside(resolved, repo_n):
            rel = Path(os.path.relpath(resolved, repo_n)).as_posix()
            if rel not in {os.path.normcase(f).replace("\\", "/") for f in fingerprint}:
                problems.append(f"Modul {name} aus nicht erfasster Datei des Repositorys geladen ({rel})")
        else:
            problems.append(f"Modul {name} ausserhalb von Repository/venv/Basis-Installation geladen ({file})")
    return problems


# --- A4: Schreibrechte ----------------------------------------------------------------------------------------------------


def write_access(repo: Path, *, repo_files: list[str] | None = None, venv: list[Path] = (), base: list[Path] = (),  # noqa: PLR0913
                 extra: list[Path] = (), probe_dir=_probe_dir, probe_file=_probe_file) -> dict:  # noqa: ANN001
    """Schreibrechte in allen Code- und Ausfuehrungspfaden (siehe Moduldoku). Ergebnis ist Beobachtung, keine Bewertung."""
    repo = Path(repo)
    writable, unclear, checked = [], [], 0
    roots = {"repository": [str(repo)], "venv": [str(p) for p in venv], "basis": [str(p) for p in base],
             "sys_path": [str(p) for p in extra]}

    def label(path: Path, folder: bool) -> str:
        try:
            text = path.relative_to(repo).as_posix()
        except ValueError:
            text = str(path)
        return (text.rstrip("/") + "/" if text != "." else "./") if folder else text

    def check(path: Path, folder: bool) -> None:
        nonlocal checked
        checked += 1
        result = (probe_dir if folder else probe_file)(path)
        if result == "beschreibbar":
            writable.append(label(path, folder))
        elif result != "geschuetzt":
            unclear.append(label(path, folder))

    done: set[str] = set()
    listed = set(repo_files or [])

    def unreadable(error: OSError) -> None:  # nicht lesbarer Ordner: Inhalt unbekannt -> nie "geschuetzt"
        unclear.append(f"{getattr(error, 'filename', '?')} (nicht lesbar)")

    for rel, _, files in walk_repo(repo, keep_pycache=True, onerror=unreadable):
        folder = repo / rel
        done.add(_norm(folder))
        check(folder, True)
        for name in files:
            if (rel / name).as_posix() in listed or name.endswith(REPO_CODE_SUFFIXES):
                check(folder / name, False)
    for top in [*venv, *base, *extra]:
        top = Path(top)
        if not top.exists():
            unclear.append(f"{top} (nicht vorhanden)")
            continue
        if top.is_file():
            check(top, False)
            continue
        for dirpath, dirnames, filenames in os.walk(top, onerror=unreadable):
            key = _norm(dirpath)
            if key in done:
                dirnames[:] = []
                continue
            done.add(key)
            dirnames.sort()
            check(Path(dirpath), True)
            # §110 (A16): in *.dist-info/*.egg-info auch die Metadaten (entry_points.txt, RECORD, top_level.txt usw.) -
            # ueber Entry Points/Plugin-Registrierung kann sonst Code nachgeladen werden, ohne im Fingerabdruck zu stehen
            in_metadata = _norm(dirpath).rstrip("\\/").endswith((".dist-info", ".egg-info"))
            for name in sorted(filenames):
                if name.lower().endswith(RUNTIME_CODE_SUFFIXES) or (in_metadata and name.lower().endswith(".txt")) \
                        or name in ("RECORD", "entry_points.txt", "top_level.txt"):
                    check(Path(dirpath) / name, False)
    return {"geprueft": checked, "beschreibbar": writable[:MAX_LISTE], "beschreibbar_anzahl": len(writable),
            "nicht_pruefbar": unclear[:MAX_LISTE], "nicht_pruefbar_anzahl": len(unclear), "wurzeln": roots}


def runtime_roots() -> dict[str, list[Path]]:
    """Tatsaechlicher Installationspfad: venv (inkl. Scripts, pyvenv.cfg, site-packages), Basis-Installation
    (Standardbibliothek) und alle weiteren existierenden Ordner im Suchpfad."""
    from scripts.legal_bench import answer_specs

    roots = known_roots()
    venv = [Path(p) for p in roots["venv"]]
    base = [Path(p) for p in roots["basis"] if all(not _inside(p, v) for v in roots["venv"])]
    covered = [*roots["venv"], *roots["basis"], _norm(answer_specs.REPO)]  # Repository wird eigens geprueft
    extra = []
    for entry in sys.path:
        if not entry or entry.startswith("__editable__."):
            continue
        resolved = _norm(entry)
        if os.path.exists(resolved) and not any(_inside(resolved, c) for c in covered):
            extra.append(Path(resolved))
    return {"venv": venv, "basis": base, "extra": sorted(set(extra))}
