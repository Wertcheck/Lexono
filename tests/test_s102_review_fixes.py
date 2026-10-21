"""§102: Regressionstests zu AUDIT_S100 A1-A4 (Codequellen, Wiederherstellung, Zeitanker, Schreibrechte).

Nur lokal: Kopie-Verzeichnisse in tmp_path, Kindprozesse auf Loopback-freiem Python-Code, simuliertes Hyper-V
(`FakeSystem`, §94) und synthetische Belege. Keine Systemaenderung, keine externe Verbindung, keine echten Schluessel.
Die Ergebnisse belegen nur die Werkzeugkette - KEINE reale Isolation."""

from __future__ import annotations

import json
import os
import py_compile
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.legal_bench import answer_specs, isolation_acceptance as acc, isolation_codepfad as cp
from scripts.legal_bench import isolation_evidence as ev, isolation_gate, isolation_host_control as hc
from scripts.legal_bench import isolation_real as real
from scripts.legal_bench.isolation_evidence import BLOCKIERT, FAIL, PASS
from tests.isolation_test_support import bundle_items, make_test_gate, synthetic_policy, write_bundle
from tests.test_s94_real_acceptance_path import CLEAN_TREE, READ_ONLY, FakeSystem, _full_run, setup  # noqa: F401

LOOP = {("127.0.0.1", 9)}


def _has(items, text) -> bool:  # noqa: ANN001
    return any(text in i for i in items)


def _problems(gate) -> list[str]:  # noqa: ANN001
    return gate.problems(run_id="qa80-lauf", provider_endpoints=LOOP)


# === A1: Codequellen =====================================================================================================


def test_fingerprint_covers_every_importable_python_file() -> None:
    files = set(isolation_gate.tool_files())
    assert set(cp.python_files(answer_specs.REPO)) <= files
    for name in ("gateway/__init__.py", "tests/conftest.py", "tests/test_s102_review_fixes.py",
                 "scripts/legal_bench/isolation_codepfad.py", "run.py"):
        assert name in files, name
    assert not any(f.startswith((".venv/", "build/", "dist/", "data/")) or "__pycache__" in f for f in files)


@pytest.mark.parametrize("flags, prefix, probe, expected", [
    (False, None, "geschuetzt", "PYTHONDONTWRITEBYTECODE"),
    (True, None, "geschuetzt", "PYTHONPYCACHEPREFIX nicht gesetzt"),
    (True, "relativ/pfad", "geschuetzt", "kein absoluter Pfad"),
    (True, "EXISTIERT", "geschuetzt", "existiert"),
    (True, "NEU", "beschreibbar", "Elternordner"),
    (True, "NEU", "nicht_pruefbar", "Elternordner"),
], ids=["ohne_B", "ohne_prefix", "relativ", "prefix_existiert", "eltern_beschreibbar", "eltern_unklar"])
def test_bytecode_isolation_is_required(tmp_path, flags, prefix, probe, expected) -> None:
    prefix = {"EXISTIERT": str(tmp_path), "NEU": str(tmp_path / "kein-bytecode")}.get(prefix, prefix)
    problems = cp.bytecode_isolation(flags=SimpleNamespace(dont_write_bytecode=flags), prefix=prefix or "",
                                     probe=lambda _p: probe)
    assert _has(problems, expected), problems


def test_bytecode_isolation_ok_only_with_protected_missing_prefix(tmp_path) -> None:
    assert cp.bytecode_isolation(flags=SimpleNamespace(dont_write_bytecode=True), prefix=str(tmp_path / "x" / "y"),
                                 probe=lambda _p: "geschuetzt") == []


def _pkg(base: Path) -> Path:
    (base / "pkg").mkdir(parents=True)
    (base / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    source = base / "pkg" / "mod.py"
    source.write_text("WER = 'quelle'\n", encoding="utf-8")
    return source


def _run(base: Path, isolated: bool) -> str:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE")}
    if isolated:
        env.update(PYTHONPYCACHEPREFIX=str(base / "kein-bytecode"), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-c", "import pkg.mod; print(pkg.mod.WER)"], cwd=base, env=env,
                            capture_output=True, text=True, timeout=60, check=False)
    return result.stdout.strip() or result.stderr.strip()[-200:]


def test_planted_bytecode_is_loaded_without_isolation_and_ignored_with_it(tmp_path) -> None:
    """Regression AUDIT_S100 R2a: ungepruefter Hash-Bytecode einer fremden Quelle im __pycache__."""
    _pkg(tmp_path)
    foreign = tmp_path / "fremd.py"
    foreign.write_text("WER = 'FREMD'\n", encoding="utf-8")
    py_compile.compile(str(foreign), cfile=str(tmp_path / "pkg" / "__pycache__" / f"mod.{sys.implementation.cache_tag}.pyc"),
                       invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    assert _run(tmp_path, isolated=False) == "FREMD"  # der Angriff wirkt ohne Isolation
    assert _run(tmp_path, isolated=True) == "quelle"
    assert not (tmp_path / "kein-bytecode").exists()  # nichts geschrieben


def test_stale_bytecode_is_ignored_with_isolation(tmp_path) -> None:
    """Veraltete .pyc: Quelle nach dem Uebersetzen bei gleicher Groesse und gleichem Zeitstempel geaendert."""
    source = _pkg(tmp_path)
    py_compile.compile(str(source), cfile=str(tmp_path / "pkg" / "__pycache__" / f"mod.{sys.implementation.cache_tag}.pyc"))
    stat = source.stat()
    source.write_text("WER = 'andere'\n", encoding="utf-8")  # gleiche Laenge wie "quelle"
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert _run(tmp_path, isolated=False) == "quelle"  # veralteter Bytecode wird geladen
    assert _run(tmp_path, isolated=True) == "andere"


def test_isolated_child_process_passes_the_isolation_check(tmp_path) -> None:
    env = {**os.environ, "PYTHONPYCACHEPREFIX": str(tmp_path / "kein-bytecode"), "PYTHONDONTWRITEBYTECODE": "1"}
    code = ("from scripts.legal_bench import isolation_codepfad as c; "
            "print(c.bytecode_isolation(probe=lambda p: 'geschuetzt'))")
    result = subprocess.run([sys.executable, "-c", code], cwd=answer_specs.REPO, env=env, capture_output=True, text=True,
                            timeout=120, check=False)
    assert result.stdout.strip() == "[]", result.stderr[-300:]


def _repo(tmp_path: Path) -> tuple[Path, set[str]]:
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "run.py").write_text("", encoding="utf-8")
    return repo, {"app/__init__.py", "run.py"}


def _problems_of(repo: Path, fingerprint: set[str], **kwargs) -> list[str]:  # noqa: ANN003
    roots = {"venv": [], "basis": [], "site_packages": []}
    return cp.code_source_problems(repo, fingerprint, modules=kwargs.pop("modules", {}), path=kwargs.pop("path", [str(repo)]),
                                   roots=kwargs.pop("roots", roots), isolation=lambda: [])


def test_clean_copy_has_no_code_source_problems(tmp_path) -> None:
    assert _problems_of(*_repo(tmp_path)) == []


@pytest.mark.parametrize("name, expected", [
    ("app/nur.pyc", "quellenloser Bytecode"), ("app/alt.pyo", "quellenloser Bytecode"),
    ("app/schnell.pyd", "Erweiterungsmodul"), ("app/lib.so", "Erweiterungsmodul"),
], ids=["pyc", "pyo", "pyd", "so"])
def test_sourceless_bytecode_and_extensions_are_problems(tmp_path, name, expected) -> None:
    """Regression AUDIT_S100 R2b (quellenlose .pyc werden auch mit Isolation geladen)."""
    repo, fingerprint = _repo(tmp_path)
    (repo / name).write_bytes(b"\x00")
    assert _has(_problems_of(repo, fingerprint), expected)


@pytest.mark.parametrize("make, expected", [
    (lambda r: (r / "cryptography").mkdir() or (r / "cryptography" / "__init__.py").write_text(""), "Paket cryptography/"),
    (lambda r: (r / "build").mkdir() or (r / "build" / "__init__.py").write_text(""), "Paket build/"),
    (lambda r: (r / "yaml.pyc").write_bytes(b"\x00"), "Modul in der Repository-Wurzel"),
], ids=["ueberdeckt_bibliothek", "ausgeschlossener_ordner", "wurzel_bytecode"])
def test_shadowing_in_the_repository_root_is_a_problem(tmp_path, make, expected) -> None:
    """Regression AUDIT_S100 R3: Wurzel-Paket ausserhalb des Fingerabdrucks ueberdeckt eine installierte Bibliothek."""
    repo, fingerprint = _repo(tmp_path)
    make(repo)
    assert _has(_problems_of(repo, fingerprint), expected)


def test_fingerprinted_root_package_is_not_shadowing(tmp_path) -> None:
    repo, fingerprint = _repo(tmp_path)
    (repo / "gateway").mkdir()
    (repo / "gateway" / "__init__.py").write_text("", encoding="utf-8")
    assert _problems_of(repo, fingerprint | {"gateway/__init__.py"}) == []


def test_unknown_search_path_entries_are_problems(tmp_path) -> None:
    repo, fingerprint = _repo(tmp_path)
    venv = tmp_path / "venv"
    venv.mkdir()
    roots = {"venv": [cp._norm(venv)], "basis": [], "site_packages": []}
    path = [str(repo), str(venv / "Lib"), "__editable__.lexono-0.1.0.finder.__path_hook__"]
    assert _problems_of(repo, fingerprint, path=path, roots=roots) == []
    assert _has(_problems_of(repo, fingerprint, path=[*path, str(tmp_path / "fremd")], roots=roots),
                "unbekannter Eintrag im Suchpfad")
    assert _has(_problems_of(repo, fingerprint, path=[str(repo / "app")], roots=roots), "unbekannter Eintrag")


def test_loaded_modules_must_come_from_the_fingerprint(tmp_path) -> None:
    repo, fingerprint = _repo(tmp_path)
    venv = tmp_path / "venv"
    roots = {"venv": [cp._norm(venv)], "basis": [], "site_packages": []}
    (repo / "app" / "neu.py").write_text("", encoding="utf-8")
    modules = {"ok": SimpleNamespace(__file__=str(repo / "app" / "__init__.py")),
               "lib": SimpleNamespace(__file__=str(venv / "Lib" / "site-packages" / "lib.py")),
               "builtin": SimpleNamespace(__spec__=SimpleNamespace(origin="built-in"))}
    assert _problems_of(repo, fingerprint, modules=modules, roots=roots) == []
    bad = {"neu": SimpleNamespace(__file__=str(repo / "app" / "neu.py")),
           "byte": SimpleNamespace(__file__=str(venv / "Lib" / "x.pyc")),
           "fremd": SimpleNamespace(__file__=str(tmp_path / "anderswo" / "x.py"))}
    problems = _problems_of(repo, fingerprint, modules=bad, roots=roots)
    assert _has(problems, "Modul neu aus nicht erfasster Datei")  # Datei nach dem Fingerabdruck hinzugekommen
    assert _has(problems, "Modul byte aus Bytecode ohne Quelle") and _has(problems, "Modul fremd ausserhalb")


def test_symlinked_directory_in_the_repository_is_a_problem(tmp_path) -> None:
    repo, fingerprint = _repo(tmp_path)
    target = tmp_path / "aussen"
    target.mkdir()
    try:
        (repo / "app" / "plugin").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks ohne Entwicklermodus/Rechte nicht anlegbar")
    assert _has(_problems_of(repo, fingerprint), "symbolischer Ordner-Link")


def test_changes_after_the_fingerprint_are_detected(tmp_path, monkeypatch) -> None:
    repo, _ = _repo(tmp_path)
    monkeypatch.setattr(answer_specs, "REPO", repo)
    monkeypatch.setattr(isolation_gate, "TOOL_FILES_EXTRA", ())
    first = isolation_gate.pruefwerkzeug_stand()["sha256"]
    (repo / "app" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    changed = isolation_gate.pruefwerkzeug_stand()["sha256"]
    (repo / "app" / "neu.py").write_text("", encoding="utf-8")  # neues Modul - auch in jedem Unterordner erfasst
    added = isolation_gate.pruefwerkzeug_stand()["sha256"]
    assert len({first, changed, added}) == 3 and "app/neu.py" in isolation_gate.tool_files()


def test_code_source_problems_close_the_gate(tmp_path) -> None:
    gate = make_test_gate(tmp_path)
    assert _problems(gate) == []
    gate.codepfad = lambda: ["quellenloser Bytecode im Repository: app/x.pyc"]
    assert _has(_problems(gate), "Codequelle: quellenloser Bytecode")
    gate.codepfad = isolation_gate.codepfad_probleme  # echte Pruefung: dieser pytest-Prozess ist nicht isoliert
    assert _has(_problems(gate), "Codequelle: Bytecode-Isolation fehlt")


def test_code_check_failure_closes_the_gate(monkeypatch) -> None:
    monkeypatch.setattr(isolation_gate.isolation_codepfad, "code_source_problems",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("kaputt")))
    assert isolation_gate.codepfad_probleme() == ["Codequellen nicht pruefbar (OSError)"]


def test_manipulated_pth_closes_the_gate(tmp_path, monkeypatch) -> None:
    """Die Bestaetigung bindet die Laufzeit; eine danach veraenderte .pth (Startcode) schliesst das Gate."""
    import site

    folder = tmp_path / "site-packages"
    folder.mkdir()
    (folder / "start.pth").write_text("import harmlos", encoding="utf-8")
    monkeypatch.setattr(site, "getsitepackages", lambda: [str(folder)])
    (tmp_path / "g").mkdir()
    gate = make_test_gate(tmp_path / "g")  # signiert mit genau dieser Laufzeit
    assert _problems(gate) == []
    (folder / "start.pth").write_text("import os; os.system('fremd')", encoding="utf-8")
    assert _has(_problems(gate), "Pruefwerkzeug seit der Bestaetigung geaendert")


def _judge(tmp_path: Path, *hooks) -> dict:  # noqa: ANN002
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    policy_path = synthetic_policy(tmp_path, LOOP)
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    lauf = {"run_id": "lauf-102", "maschine": {"hostname": "vm", "machine_guid": "g"},
            "netzwerkpolitik_sha256": isolation_gate.sha256_file(policy_path),
            "pruefwerkzeug": {"commit": "c" * 40, "sha256": "d" * 64}}
    items = bundle_items(LOOP, now)
    for hook in hooks:
        hook(items)
    folder = write_bundle(tmp_path / f"b{len(list(tmp_path.iterdir()))}", items, lauf=lauf, policy=policy)
    return ev.evaluate(folder, policy=policy, lauf=lauf, reference=now, now=now, allow_dry_run=True)


@pytest.mark.parametrize("value, status, reason", [
    ({"probleme": []}, PASS, None),
    ({"probleme": ["Bytecode-Isolation fehlt"]}, BLOCKIERT, "codequellen_eindeutig: Voraussetzung fehlt"),
    (None, FAIL, "codequellen_eindeutig nicht eindeutig"),
], ids=["eindeutig", "probleme", "fehlt"])
def test_v2_requires_unambiguous_code_sources(tmp_path, value, status, reason) -> None:
    def hook(items):  # noqa: ANN001, ANN202
        if value is None:
            items["V2/kriterien_E0.json"]["beobachtung"].pop("codepfad")
        else:
            items["V2/kriterien_E0.json"]["beobachtung"]["codepfad"] = value
    verdict = _judge(tmp_path, hook)
    assert verdict["tests"]["V2"]["status"] == status
    if reason:
        assert _has(verdict["tests"]["V2"]["gruende"], reason), verdict["tests"]["V2"]["gruende"]


def test_vm_part_records_code_sources(setup) -> None:
    observation = real.observe_vm_v2(setup["system"], worktree=CLEAN_TREE, write_test=READ_ONLY,
                                     code_check=lambda: ["Bytecode-Isolation fehlt: ..."])
    assert observation["codepfad"] == {"probleme": ["Bytecode-Isolation fehlt: ..."]}
    assert ev.derive("V2", "kriterien", "E0", observation, {})["kriterien"]["codequellen_eindeutig"] == "blockiert"


def test_pytest_children_of_the_vm_part_get_the_same_isolation(monkeypatch, tmp_path) -> None:
    seen = {}

    def runner(argv, **kw):  # noqa: ANN001, ANN202
        seen.update(argv=argv, env=kw["env"])
        Path(kw["env"]["LEXONO_ISOLATION_MANIFEST"]).parent.mkdir(exist_ok=True)
        (tmp_path / "j.xml").write_text(acc.junit_xml({"tests/x.py::t": "passed"}), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setenv(acc.ENV_REAL, "1")
    monkeypatch.setattr(sys, "pycache_prefix", str(tmp_path / "kein-bytecode"))
    real.run_pytest(["tests/x.py"], tmp_path / "j.xml", manifest_path=tmp_path / "m.json", real=True, runner=runner)
    assert "-B" in seen["argv"] and seen["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert seen["env"]["PYTHONPYCACHEPREFIX"] == str(tmp_path / "kein-bytecode")


# === A4: Schreibrechte ===================================================================================================


def _installation(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo, _ = _repo(tmp_path)
    (repo / "app" / "__pycache__").mkdir()
    venv, base = tmp_path / "venv", tmp_path / "Python313"
    for folder in (venv / "Scripts", venv / "Lib" / "site-packages" / "paket", base / "Lib" / "json"):
        folder.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = x", encoding="utf-8")
    (venv / "Scripts" / "python.exe").write_bytes(b"MZ")
    (venv / "Lib" / "site-packages" / "start.pth").write_text("import x", encoding="utf-8")
    (venv / "Lib" / "site-packages" / "paket" / "mod.py").write_text("", encoding="utf-8")
    (base / "Lib" / "json" / "__init__.py").write_text("", encoding="utf-8")
    (base / "python313.zip").write_bytes(b"PK")
    return repo, venv, base


def test_write_access_covers_all_code_and_runtime_paths(tmp_path) -> None:
    repo, venv, base = _installation(tmp_path)
    result = cp.write_access(repo, venv=[venv], base=[base])
    writable = set(result["beschreibbar"])
    for item in ("app/__pycache__/", "app/__init__.py", str(venv / "Scripts") + "/", str(venv / "Scripts" / "python.exe"),
                 str(venv / "pyvenv.cfg"), str(venv / "Lib" / "site-packages" / "start.pth"),
                 str(venv / "Lib" / "site-packages" / "paket") + "/", str(venv / "Lib" / "site-packages" / "paket" / "mod.py"),
                 str(base / "Lib" / "json") + "/", str(base / "Lib" / "json" / "__init__.py"), str(base / "python313.zip")):
        assert item in writable, item
    assert result["nicht_pruefbar"] == [] and not list(tmp_path.rglob(".lexono-schreibtest-*"))
    assert ev._write_protected(result) is False  # beschreibbar -> FAIL


def _protected(_path) -> str:  # noqa: ANN001
    return "geschuetzt"


def test_protected_installation_passes_only_with_all_path_groups(tmp_path) -> None:
    repo, venv, base = _installation(tmp_path)
    result = cp.write_access(repo, venv=[venv], base=[base], probe_dir=_protected, probe_file=_protected)
    assert result["beschreibbar"] == [] and ev._write_protected(result) is True
    without_base = cp.write_access(repo, venv=[venv], base=[], probe_dir=_protected, probe_file=_protected)
    assert ev._write_protected(without_base) is None  # Standardbibliothek nicht geprueft -> nie "geschuetzt"


def test_unclear_paths_block_and_never_count_as_protected(tmp_path) -> None:
    repo, venv, base = _installation(tmp_path)
    exe = venv / "Scripts" / "python.exe"
    result = cp.write_access(repo, venv=[venv], base=[base], probe_dir=_protected,
                             probe_file=lambda p: "nicht_pruefbar" if Path(p) == exe else "geschuetzt")
    assert result["nicht_pruefbar"] == [str(exe)] and ev._write_protected(result) == "blockiert"
    missing = cp.write_access(repo, venv=[tmp_path / "gibt-es-nicht"], base=[base], probe_dir=_protected,
                              probe_file=_protected)
    assert _has(missing["nicht_pruefbar"], "nicht vorhanden") and ev._write_protected(missing) == "blockiert"


def test_unreadable_directories_are_reported_not_skipped(tmp_path, monkeypatch) -> None:
    """os.walk uebergeht nicht lesbare Ordner sonst stillschweigend - sie duerfen nie als geprueft gelten."""
    repo, venv, base = _installation(tmp_path)
    real_scandir = os.scandir

    def scandir(path):  # noqa: ANN001, ANN202
        if Path(path) == venv / "Lib" / "site-packages" / "paket":
            raise PermissionError(13, "kein Lesezugriff", str(path))
        return real_scandir(path)

    monkeypatch.setattr(cp.os, "scandir", scandir)
    result = cp.write_access(repo, venv=[venv], base=[base], probe_dir=_protected, probe_file=_protected)
    assert _has(result["nicht_pruefbar"], "nicht lesbar") and ev._write_protected(result) == "blockiert"


def test_probe_classify_maps_only_access_denied_to_protected() -> None:
    """§104 (A12): Nur ACCESS_DENIED (5) belegt Schutz; Freigabeverletzung (32) und alles andere sind nicht_pruefbar."""
    assert cp._classify(cp.ERROR_ACCESS_DENIED) == "geschuetzt"
    assert cp._classify(cp.ERROR_SHARING_VIOLATION) == "nicht_pruefbar"
    assert cp._classify(cp.ERROR_FILE_NOT_FOUND) == "nicht_pruefbar" and cp._classify(0) == "nicht_pruefbar"


def test_runtime_roots_follow_the_actual_installation() -> None:
    roots = cp.runtime_roots()
    assert cp._norm(sys.prefix) in {cp._norm(p) for p in roots["venv"]}
    if cp._norm(sys.base_prefix) != cp._norm(sys.prefix):
        assert cp._norm(sys.base_prefix) in {cp._norm(p) for p in roots["basis"]}  # Standardbibliothek
    assert not any(cp._inside(cp._norm(e), cp._norm(answer_specs.REPO)) for e in roots["extra"])


@pytest.mark.parametrize("schreibtest, status", [
    (lambda w: w.update(nicht_pruefbar=["C:\\Python313\\python.exe"], nicht_pruefbar_anzahl=1), BLOCKIERT),
    (lambda w: w["wurzeln"].update(basis=[]), FAIL),
    (lambda w: w.update(beschreibbar_anzahl=3), FAIL),
], ids=["unklar", "ohne_standardbibliothek", "beschreibbar"])
def test_v2_write_protection_criterion(tmp_path, schreibtest, status) -> None:
    verdict = _judge(tmp_path, lambda items: schreibtest(items["V2/kriterien_E0.json"]["beobachtung"]["schreibtest"]))
    assert verdict["tests"]["V2"]["status"] == status, verdict["tests"]["V2"]["gruende"]


def test_real_repository_write_test_only_touches_given_paths_in_tests(tmp_path) -> None:
    """Mit fremdem Repository (Tests) wird nie die echte Installation geprueft (keine Probedateien in .venv)."""
    repo, _ = _repo(tmp_path)
    result = real.observe_write_access(repo, site_packages=[])
    assert result["wurzeln"]["venv"] == [] and result["wurzeln"]["basis"] == []


# === A3: Zeitanker =======================================================================================================


def test_vm_clock_alone_does_not_extend_validity(tmp_path) -> None:
    """VM-Uhr zurueckgestellt (Gate sieht `now` innerhalb der Gueltigkeit), aber laut monotonem Zaehler sind 3 h vergangen."""
    gate = make_test_gate(tmp_path)
    anchor = acc.SYNTHETIC_ZEITANKER
    gate.now = lambda: gate._now + timedelta(minutes=5)
    gate.zeitbasis = lambda: {"tick_ms": anchor["tick_ms"] + 3 * 3_600_000, "boot_id": anchor["boot_id"]}
    problems = _problems(gate)
    assert _has(problems, "laut Host-Zeitanker und VM-Laufzeit abgelaufen")
    assert ev.MSG_ATTESTATION_EXPIRED not in problems  # die VM-Uhr allein haette nichts bemerkt


@pytest.mark.parametrize("basis, expected", [
    ({"tick_ms": acc.SYNTHETIC_ZEITANKER["tick_ms"] + 60_000, "boot_id": 99}, "neu gestartet (Boot-ID)"),
    ({"tick_ms": acc.SYNTHETIC_ZEITANKER["tick_ms"] - 1, "boot_id": acc.SYNTHETIC_ZEITANKER["boot_id"]}, "vor dem Zeitanker"),
    ({"tick_ms": None, "boot_id": None}, "nicht lesbar"),
], ids=["neustart", "zaehler_rueckwaerts", "unlesbar"])
def test_untrustworthy_time_basis_closes_the_gate(tmp_path, basis, expected) -> None:
    gate = make_test_gate(tmp_path)
    gate.zeitbasis = lambda: dict(basis)
    assert _has(_problems(gate), expected)


def test_valid_attestation_passes_with_anchor(tmp_path) -> None:
    gate = make_test_gate(tmp_path)
    assert _problems(gate) == []
    assert isolation_gate.zeitanker_probleme(
        {"tick_ms": 0, "boot_id": 1, "host_spaetestens": gate._now.isoformat()},
        {"tick_ms": 3_600_000, "boot_id": 1}, gate._now + timedelta(hours=2)) == []
    assert isolation_gate.zeitanker_probleme(  # Sicherheitsabschlag: knapp vor Ablauf gilt bereits als abgelaufen
        {"tick_ms": 0, "boot_id": 1, "host_spaetestens": gate._now.isoformat()},
        {"tick_ms": 7_190_000, "boot_id": 1}, gate._now + timedelta(hours=2))


def _strip_anchor(items: dict) -> None:
    for item in items.values():
        if item["entry"]["art"] == "pytest":
            item["beobachtung"].pop("zeitanker", None)


def test_missing_anchor_blocks_v13_and_the_gate(tmp_path) -> None:
    verdict = _judge(tmp_path, _strip_anchor)
    assert verdict["tests"]["V13"]["status"] == BLOCKIERT and verdict["zeitanker"] is None
    assert _has(verdict["tests"]["V13"]["gruende"], "kein Zeitanker") or _has(verdict["tests"]["V13"]["gruende"],
                                                                             "Zeitanker der VM fehlt")
    gate = make_test_gate(tmp_path / "g", bundle_hook=_strip_anchor, via_tool=False) if (tmp_path / "g").mkdir() is None \
        else None
    assert _has(_problems(gate), "kein Zeitanker im Belegbuendel")


def test_contradictory_anchors_block_v13(tmp_path) -> None:
    def hook(items):  # noqa: ANN001, ANN202
        items["V4/pytest_E0.json"]["beobachtung"]["zeitanker"] = {"tick_ms": 5, "boot_id": 7}
    verdict = _judge(tmp_path, hook)
    assert verdict["tests"]["V13"]["status"] == BLOCKIERT
    assert _has(verdict["tests"]["V13"]["gruende"], "widerspruechlich")


def test_this_machine_provides_a_time_basis() -> None:
    """Echt (nur lesend): GetTickCount64 und Boot-ID sind ohne Administratorrechte lesbar."""
    basis = isolation_gate.vm_zeitbasis()
    if sys.platform != "win32":
        pytest.skip("nur Windows")
    assert type(basis["tick_ms"]) is int and basis["tick_ms"] > 0 and type(basis["boot_id"]) is int


def test_vm_part_records_the_anchor_after_all_probes(setup) -> None:
    _, _, verdict, _ = _full_run(setup)
    anchor = verdict["zeitanker"]
    assert anchor and anchor["boot_id"] == isolation_gate.vm_zeitbasis()["boot_id"]
    assert verdict["tests"]["V13"]["status"] != PASS or anchor  # Anker aus dem echten Pfad (simuliertes System)


# === A2: Wiederherstellung nach Absturz ==================================================================================


class PowerLoss(BaseException):
    """Simulierter Stromausfall: kein except/finally des Werkzeugs greift mehr (Rueckbau wird nicht ausgefuehrt)."""


class Cut(FakeSystem):
    def __init__(self, base: FakeSystem, prefix: str, step: int, after: bool) -> None:
        super().__init__()
        self.__dict__.update({k: v for k, v in base.__dict__.items() if k != "calls"})
        self.base, self.prefix, self.step, self.after, self.count = base, prefix, step, after, 0

    def __call__(self, argv, **kw):  # noqa: ANN001, ANN204
        if argv[-1].startswith(self.prefix):
            self.count += 1
            if self.count == self.step and not self.after:
                raise PowerLoss
        result = self.base(argv, **kw)
        if argv[-1].startswith(self.prefix) and self.count == self.step and self.after:
            raise PowerLoss
        return result


def _crash(setup, journal: Path, run_id: str, kind: str, step: int, after: bool) -> None:  # noqa: ANN001
    system = setup["system"]
    prefix = "New-NetFirewallRule" if kind == "fw" else "Add-VMNetworkAdapterExtendedAcl"
    control = hc.HostControl("lexono-bench", ausfuehren=True, runner=Cut(system, prefix, step, after), journal=journal,
                             run_id=run_id, startcheck_pflicht=False)  # §106 (A5): Absturz-Szenario-Aufbau
    control._fw_rollback = control._rollback = lambda *_a: (_ for _ in ()).throw(PowerLoss())
    konfig = dict(setup["konfig"], run_id=run_id)
    with pytest.raises(PowerLoss):
        if kind == "fw":
            control.fw_setzen(konfig, setup["tmp"] / f"fw_{run_id}_{step}.json")
        else:
            control.acl_setzen(setup["policy"], setup["tmp"] / f"acl_{run_id}_{step}.json")


def _startcheck(setup, journal: Path) -> dict:  # noqa: ANN001
    Path(setup["konfig"]["golden_image"]["kind_vhdx"]).write_bytes(b"kind")
    return hc.HostControl("lexono-bench", ausfuehren=False, runner=setup["system"], journal=journal).startcheck(setup["konfig"])


def _recover(setup, journal: Path, run_id: str, kind: str) -> dict:  # noqa: ANN001
    control = hc.HostControl("lexono-bench", ausfuehren=True, runner=setup["system"], journal=journal, run_id="neuer-lauf")
    if kind == "fw":
        return control.fw_entfernen(setup["konfig"], lauf=run_id)
    return control.acl_entfernen(setup["policy"], lauf=run_id)


@pytest.mark.parametrize("kind, step", [("fw", s) for s in range(1, 6)] + [("acl", s) for s in range(1, 4)])
@pytest.mark.parametrize("after", [False, True], ids=["vor_dem_befehl", "nach_dem_befehl"])
def test_power_loss_at_every_step_is_recoverable_and_idempotent(setup, tmp_path, kind, step, after) -> None:
    """Regression AUDIT_S100 R4: frueher blieb `startcheck` nach dem dokumentierten Rueckbau dauerhaft "nicht ok"."""
    journal = tmp_path / "aenderungsjournal.jsonl"
    _crash(setup, journal, "alter-lauf", kind, step, after)
    left = len(setup["system"].firewall if kind == "fw" else setup["system"].acl)
    assert left == step - (0 if after else 1)
    first = _startcheck(setup, journal)
    assert first["ok"] is False and _has(first["befunde"], "offener Vorgang")
    result = _recover(setup, journal, "alter-lauf", kind)
    assert len(result["entfernt"]) == left and len(result["abgeschlossene_vorgaenge"]) == 1
    assert setup["system"].firewall == [] and setup["system"].acl == []
    check = _startcheck(setup, journal)
    assert check["ok"] is True, check["befunde"]
    again = _recover(setup, journal, "alter-lauf", kind)  # idempotent: nichts mehr zu tun, nichts kaputt
    assert again["entfernt"] == [] and again["abgeschlossene_vorgaenge"] == []
    assert _startcheck(setup, journal)["ok"] is True
    statuses = [e["status"] for e in hc.read_journal(journal)]
    assert statuses[0] == "geplant" and "zurueckgebaut" in statuses  # Journal nur ergaenzt


def test_recovery_never_touches_foreign_rules(setup, tmp_path) -> None:
    journal = tmp_path / "j.jsonl"
    _crash(setup, journal, "alter-lauf", "fw", 3, True)
    foreign = {**setup["system"].firewall[0], "DisplayName": "Lexono-Abnahme alter-lauf TCP-1 127.0.0.1",
               "Description": "run_id=alter-lauf;ablauf=x"}  # gleiches Namensschema, nicht im Journal
    setup["system"].firewall.append(foreign)
    result = _recover(setup, journal, "alter-lauf", "fw")
    assert setup["system"].firewall == [foreign] and result["unberuehrt"] == [foreign]
    check = _startcheck(setup, journal)
    assert check["ok"] is False and _has(check["befunde"], "Firewall-Gruppe")  # fremde Regel: Mensch entscheidet
    assert not _has(check["befunde"], "offener Vorgang")


def test_several_crashed_runs_are_recovered_separately(setup, tmp_path) -> None:
    journal = tmp_path / "j.jsonl"
    _crash(setup, journal, "lauf-a", "acl", 2, True)
    _crash(setup, journal, "lauf-b", "fw", 2, True)
    _recover(setup, journal, "lauf-a", "acl")
    check = _startcheck(setup, journal)
    assert check["ok"] is False and _has(check["befunde"], "lauf-b") and not _has(check["befunde"], "'lauf-a'")
    _recover(setup, journal, "lauf-b", "fw")
    assert _startcheck(setup, journal)["ok"] is True


def test_torn_journal_line_is_kept_and_acknowledged_only_when_clean(setup, tmp_path) -> None:
    journal = tmp_path / "j.jsonl"
    _crash(setup, journal, "lauf-a", "fw", 2, True)
    with open(journal, "ab") as handle:
        handle.write(b'{"zeit": "2026-10-20T10:00:00+00:00", "status": "begon')  # halb geschriebene Zeile
    torn = journal.read_bytes()
    control = hc.HostControl("lexono-bench", ausfuehren=True, runner=setup["system"], journal=journal, run_id="x")
    with pytest.raises(hc.ControlRefused, match="nicht leer"):
        control.journal_quittieren(setup["konfig"])  # Reste vorhanden -> nichts quittieren
    _recover(setup, journal, "lauf-a", "fw")
    assert journal.read_bytes().startswith(torn)  # nie umgeschrieben, nur angehaengt
    check = _startcheck(setup, journal)
    assert check["ok"] is False and _has(check["befunde"], "unlesbar")
    assert all(json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()[-3:])  # neue Zeilen heil
    result = control.journal_quittieren(setup["konfig"])
    assert len(result["quittiert"]) == 1 and _startcheck(setup, journal)["ok"] is True


def test_interrupted_image_reset_is_closed_by_a_later_successful_reset(setup, tmp_path) -> None:
    journal = tmp_path / "j.jsonl"
    image = setup["konfig"]["golden_image"]
    child = Path(image["kind_vhdx"])
    child.write_bytes(b"alt")

    class Abort(FakeSystem):
        def __call__(self, argv, **kw):  # noqa: ANN001, ANN204
            if argv[-1].startswith("New-VHD"):
                raise KeyboardInterrupt
            return super().__call__(argv, **kw)

    first = hc.HostControl("lexono-bench", ausfuehren=True, runner=Abort(), journal=journal, run_id="r1",
                           startcheck_pflicht=False)
    with pytest.raises(KeyboardInterrupt):
        first.image_zuruecksetzen(Path(image["eltern_vhdx"]), child, image["soll_sha256"], tmp_path / "img1.json")
    assert _has(_startcheck(setup, journal)["befunde"], "image-zuruecksetzen")
    second = hc.HostControl("lexono-bench", ausfuehren=True, runner=setup["system"], journal=journal, run_id="r2",
                            startcheck_pflicht=False)
    second.image_zuruecksetzen(Path(image["eltern_vhdx"]), child, image["soll_sha256"], tmp_path / "img2.json")
    assert _startcheck(setup, journal)["ok"] is True


def test_journal_quittieren_refuses_while_residue_present(setup) -> None:
    """§106 (A9, Verhaltenstest statt Quelltextpruefung): journal-quittieren raeumt eine unlesbare Zeile nur, wenn ACL
    und eigene Firewall-Gruppe nachgelesen leer sind - bei Resten wird verweigert (nichts wird quittiert)."""
    journal = setup["tmp"] / "j.jsonl"
    setup["system"].acl = hc.normalize_acl(hc.soll_acl(setup["policy"]))  # Rest vorhanden
    control = hc.HostControl("lexono-bench", ausfuehren=True, runner=setup["system"], journal=journal, run_id="x")
    with pytest.raises(hc.ControlRefused, match="nicht leer"):
        control.journal_quittieren(setup["konfig"])


def test_tool_fingerprint_lists_this_file_and_the_code_path_module() -> None:
    files = isolation_gate.tool_files()
    assert "tests/test_s102_review_fixes.py" in files and "scripts/legal_bench/isolation_codepfad.py" in files
