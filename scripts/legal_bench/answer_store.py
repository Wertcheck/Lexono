"""§80 (E2): Ablage der Laeufe des Antwort-Benchmarks - AUSSERHALB des Git-Repositorys und ausserhalb der Lexono-
Datenverzeichnisse, mit restriktiven Zugriffsrechten, stabilen IDs, Hashes, Zeitstempeln und Status.

Aufbau je Lauf:  <basis>/<run_id>/
    .lexono_benchmark_run   Markierung (nur so gekennzeichnete Verzeichnisse werden je geloescht)
    state.json              Zustand: Start-Fingerabdruecke, Status je Aufruf, Kostenbuch, Ereignisse, state_sha256
    calls/<call_id>.json    Antwortdatensatz je abgeschlossenem Aufruf (record_sha256; Hash auch im Zustand)

Gespeichert werden: pseudonymisierte Payload (Pflicht `privacy.payload_capture`), Antworttext (fuer die blinde
Bewertung), Usage/Kosten, Hashes. NICHT gespeichert: Schluessel, Request-Header, Konfigurationsinhalt (nur dessen Hash;
die Freigabe-Personen stehen in der Konfiguration selbst), Klartext-Mappings.

Zugriff: Windows - Vererbung entfernt, Vollzugriff nur fuer das ausfuehrende Konto (icacls); sonst 0700/0600.
Integritaet: jede Datei mit eigenem SHA-256 ueber den kanonischen Inhalt; der Zustand fuehrt die Datensatz-Hashes;
unbekannte, fehlende oder veraenderte Dateien sind ein Fehler (`verify`). Schreiben atomar (temporaere Datei + replace)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts.legal_bench import answer_specs

MARKER = ".lexono_benchmark_run"
_ID_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")


class StoreError(RuntimeError):
    """Ablage unzulaessig, beschaedigt oder nicht schreibbar - der Lauf darf nicht weiterlaufen."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical(data: dict, drop: str) -> bytes:
    return json.dumps({k: v for k, v in data.items() if k != drop}, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _digest(data: dict, drop: str) -> str:
    return hashlib.sha256(_canonical(data, drop)).hexdigest()


def _forbidden_roots() -> list[Path]:
    roots = [answer_specs.REPO.resolve()]
    for env in ("PROGRAMDATA", "LOCALAPPDATA"):
        if os.environ.get(env):
            roots.append((Path(os.environ[env]) / "Lexono").resolve())
    if os.environ.get("LEXONO_DATA_DIR"):
        roots.append(Path(os.environ["LEXONO_DATA_DIR"]).resolve())
    return roots


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _current_principal() -> str:
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    domain = os.environ.get("USERDOMAIN")
    return f"{domain}\\{user}" if domain else user


def restrict(path: Path) -> None:
    """Nur das ausfuehrende Konto darf lesen/schreiben (Verzeichnisse vererben das an neue Dateien)."""
    if sys.platform == "win32":
        grant = f"{_current_principal()}:(OI)(CI)F" if path.is_dir() else f"{_current_principal()}:F"
        out = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", grant], capture_output=True, text=True)
        if out.returncode != 0:
            raise StoreError(f"Zugriffsrechte konnten nicht gesetzt werden (icacls {out.returncode})")
    else:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)


def access_principals(path: Path) -> list[str]:
    """Konten mit Zugriff (Windows: icacls-Eintraege; sonst Rechtebits) - fuer Pruefung und Tests."""
    if sys.platform != "win32":
        mode = path.stat().st_mode & 0o777
        return ["owner"] if mode & 0o077 == 0 else [f"mode {oct(mode)}"]
    out = subprocess.run(["icacls", str(path)], capture_output=True, text=True).stdout
    principals = []
    for line in out.splitlines():
        line = line.replace(str(path), "", 1).strip()
        if ":" in line and "(" in line:
            principals.append(line.split(":", 1)[0].strip())
    return principals


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class RunStore:
    def __init__(self, base: Path) -> None:
        base = Path(base).resolve()
        for root in _forbidden_roots():
            if _is_within(base, root) or _is_within(root, base):
                raise StoreError(f"Ablage {base} liegt im/ueber dem Repository oder einem Lexono-Datenverzeichnis - "
                                 "Benchmark-Antworten gehoeren ausserhalb davon")
        base.mkdir(parents=True, exist_ok=True)
        restrict(base)
        self.base = base

    def run_dir(self, run_id: str) -> Path:
        if not run_id or not set(run_id) <= _ID_CHARS:
            raise StoreError(f"ungueltige Lauf-ID {run_id!r}")
        return self.base / run_id

    def exists(self, run_id: str) -> bool:
        return (self.run_dir(run_id) / "state.json").exists()

    def create(self, run_id: str, state: dict) -> dict:
        directory = self.run_dir(run_id)
        if directory.exists():
            raise StoreError(f"Lauf {run_id} existiert bereits - Wiederanlauf statt Neuanlage")
        directory.mkdir()
        restrict(directory)
        (directory / MARKER).write_text(f"Lexono Antwort-Benchmark, Lauf {run_id}\n", encoding="utf-8")
        (directory / "calls").mkdir()
        self.save_state(run_id, state)
        return self.load_state(run_id)

    def save_state(self, run_id: str, state: dict) -> None:
        state = {**state, "run_id": run_id, "updated_at": now()}
        state["state_sha256"] = _digest(state, "state_sha256")
        _atomic_write(self.run_dir(run_id) / "state.json", state)

    def load_state(self, run_id: str) -> dict:
        errors = self.verify(run_id)
        if errors:
            raise StoreError("Ablage beschaedigt: " + "; ".join(errors[:5]))
        return json.loads((self.run_dir(run_id) / "state.json").read_text(encoding="utf-8"))

    def write_record(self, run_id: str, cid: str, record: dict) -> str:
        if not cid or not set(cid) <= _ID_CHARS:
            raise StoreError(f"ungueltige Aufruf-ID {cid!r}")
        path = self.run_dir(run_id) / "calls" / f"{cid}.json"
        if path.exists():
            raise StoreError(f"Datensatz {cid} existiert bereits - kein Ueberschreiben (Doppelaufruf?)")
        record = {**record, "call_id": cid, "stored_at": now()}
        record["record_sha256"] = _digest(record, "record_sha256")
        _atomic_write(path, record)
        return record["record_sha256"]

    def read_record(self, run_id: str, cid: str) -> dict:
        return json.loads((self.run_dir(run_id) / "calls" / f"{cid}.json").read_text(encoding="utf-8"))

    def verify(self, run_id: str) -> list[str]:
        directory = self.run_dir(run_id)
        errors: list[str] = []
        if not (directory / MARKER).exists():
            return ["Markierung fehlt - kein Benchmark-Laufverzeichnis"]
        try:
            state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return [f"state.json nicht lesbar ({type(exc).__name__})"]
        if not isinstance(state, dict) or state.get("state_sha256") != _digest(state, "state_sha256"):
            return ["state.json veraendert (Hash passt nicht)"]
        expected = {cid: c.get("record_sha256") for cid, c in state.get("calls", {}).items() if c.get("record_sha256")}
        present = {p.stem for p in (directory / "calls").glob("*.json")}
        for extra in sorted(present - set(expected)):
            errors.append(f"unbekannter Datensatz calls/{extra}.json (nicht im Zustand)")
        for leftover in sorted((directory / "calls").glob("*.tmp")):
            errors.append(f"unvollstaendig geschriebene Datei {leftover.name}")
        for cid, digest in sorted(expected.items()):
            path = directory / "calls" / f"{cid}.json"
            if not path.exists():
                errors.append(f"Datensatz {cid} fehlt")
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                errors.append(f"Datensatz {cid} nicht lesbar")
                continue
            if record.get("record_sha256") != _digest(record, "record_sha256") or record.get("record_sha256") != digest:
                errors.append(f"Datensatz {cid} veraendert (Hash passt nicht)")
        return errors

    def delete(self, run_id: str, *, confirm: str) -> None:
        """Kontrollierte Loeschung: nur mit Bestaetigung = Lauf-ID, nur markierte Laufverzeichnisse innerhalb der Basis,
        nicht waehrend ein Aufruf in Bearbeitung ist."""
        if confirm != run_id:
            raise StoreError("Loeschen nur mit ausdruecklicher Bestaetigung der Lauf-ID")
        directory = self.run_dir(run_id)
        if not _is_within(directory.resolve(), self.base) or not (directory / MARKER).exists():
            raise StoreError("kein markiertes Laufverzeichnis dieser Ablage - nichts geloescht")
        try:
            state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        if any(c.get("status") == "in_bearbeitung" for c in state.get("calls", {}).values()):
            raise StoreError("Aufruf in Bearbeitung - erst klaeren, dann loeschen")
        shutil.rmtree(directory)
