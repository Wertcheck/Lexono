"""§104: Regressionstests zu AUDIT_S103 A11, A12, A13, A14, A15, A7 (und Belege zu A5/A17).

Nur lokal: tmp-Kopien, simuliertes Hyper-V (`FakeSystem`, §94), echte WinAPI-Schreibprobe gegen read-only-Attribut-
Dateien im tmp-Verzeichnis (keine Systemdateien, keine echte venv). Keine Systemaenderung. Kein Nachweis realer Isolation."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest

from scripts.legal_bench import answer_specs, isolation_acceptance as acc, isolation_codepfad as cp
from scripts.legal_bench import isolation_evidence as ev, isolation_gate, isolation_host_control as hc
from scripts.legal_bench.isolation_evidence import BLOCKIERT, PASS
from tests.isolation_test_support import make_test_gate
from tests.test_s94_real_acceptance_path import FakeSystem, setup  # noqa: F401

LOOP = {("127.0.0.1", 9)}


def _has(items, text) -> bool:  # noqa: ANN001
    return any(text in i for i in items)


def _problems(gate) -> list[str]:  # noqa: ANN001
    return gate.problems(run_id="qa80-lauf", provider_endpoints=LOOP)


def _control(setup, run_id, journal):  # noqa: ANN001
    # §106 (A5): Szenario-Aufbau (Setzen ohne vorherigen Startcheck) - Pflicht fuer diese Tests abgeschaltet
    return hc.HostControl("lexono-bench", ausfuehren=True, runner=setup["system"], journal=journal, run_id=run_id,
                          startcheck_pflicht=False)


# === A11: ACL-Lauf-Zuordnung ============================================================================================


def test_aktiver_acl_besitzer_tracks_set_and_removal() -> None:
    entries = [
        {"vorgang_id": "1", "vorgang": "acl-setzen", "lauf": "a", "status": "geplant"},
        {"vorgang_id": "1", "vorgang": "acl-setzen", "lauf": "a", "status": "abgeschlossen"},
    ]
    assert hc.aktiver_acl_besitzer(entries) == "a"
    entries += [{"vorgang_id": "2", "vorgang": "acl-entfernen", "lauf": "a", "status": "entfernt"}]
    assert hc.aktiver_acl_besitzer(entries) is None  # entfernt -> frei
    entries += [{"vorgang_id": "3", "vorgang": "acl-setzen", "lauf": "b", "status": "abgeschlossen"}]
    assert hc.aktiver_acl_besitzer(entries) == "b"
    # abgebrochener Setzvorgang bleibt Besitzer, bis der Rueckbau bestaetigt ist
    crash = [{"vorgang_id": "9", "vorgang": "acl-setzen", "lauf": "c", "status": "geplant"},
             {"vorgang_id": "9", "vorgang": "acl-setzen", "lauf": "c", "status": "begonnen", "befehl": "x"}]
    assert hc.aktiver_acl_besitzer(crash) == "c"
    assert hc.aktiver_acl_besitzer(crash + [{"vorgang_id": "9", "status": "zurueckgebaut"}]) is None


def test_old_run_cannot_remove_active_acl_of_a_new_run(setup) -> None:
    """Regression AUDIT_S103 A11/R5: `acl-entfernen --lauf <alt>` darf die aktive ACL eines anderen Laufs nicht loeschen."""
    journal = setup["tmp"] / "j.jsonl"
    policy = setup["policy"]
    _control(setup, "lauf-a", journal).acl_setzen(policy, setup["tmp"] / "a.json")
    _control(setup, "lauf-a", journal).acl_entfernen(policy)
    _control(setup, "lauf-b", journal).acl_setzen(policy, setup["tmp"] / "b.json")
    aktiv = list(setup["system"].acl)
    assert aktiv and hc.aktiver_acl_besitzer(hc.read_journal(journal)) == "lauf-b"
    with pytest.raises(hc.ControlRefused, match="gehoert zu Lauf 'lauf-b'"):
        _control(setup, "aufraeumen", journal).acl_entfernen(policy, lauf="lauf-a")
    assert setup["system"].acl == aktiv  # unangetastet
    with pytest.raises(hc.ControlRefused, match="gehoert zu Lauf 'lauf-b'"):
        _control(setup, "lauf-c", journal).acl_entfernen(policy)  # auch ohne --lauf (fremder aktiver Lauf)
    assert setup["system"].acl == aktiv


def test_active_acl_without_journal_is_blocked(setup) -> None:
    setup["system"].acl = hc.normalize_acl(hc.soll_acl(setup["policy"]))  # ACL ohne jeden Journalbeleg
    control = hc.HostControl("lexono-bench", ausfuehren=True, runner=setup["system"], journal=setup["tmp"] / "leer.jsonl",
                             run_id="neu")
    with pytest.raises(hc.ControlRefused, match="ohne eigenen Setz-Beleg"):
        control.acl_entfernen(setup["policy"])
    assert setup["system"].acl  # nichts entfernt


def test_owner_run_removes_its_own_active_acl(setup) -> None:
    journal = setup["tmp"] / "j.jsonl"
    _control(setup, "lauf-a", journal).acl_setzen(setup["policy"], setup["tmp"] / "a.json")
    result = _control(setup, "lauf-a", journal).acl_entfernen(setup["policy"])
    assert setup["system"].acl == [] and len(result["entfernt"]) == 3


def test_removal_is_idempotent_after_recovery(setup) -> None:
    journal = setup["tmp"] / "j.jsonl"
    _control(setup, "lauf-a", journal).acl_setzen(setup["policy"], setup["tmp"] / "a.json")
    _control(setup, "neu", journal).acl_entfernen(setup["policy"], lauf="lauf-a")
    assert setup["system"].acl == []
    again = _control(setup, "neu", journal).acl_entfernen(setup["policy"], lauf="lauf-a")  # nichts mehr, kein Fehler
    assert again["entfernt"] == [] and setup["system"].acl == []


# === A12: Windows-Schreibprüfung ========================================================================================


def test_probe_classify_maps_only_access_denied_to_protected() -> None:
    assert cp._classify(cp.ERROR_ACCESS_DENIED) == "geschuetzt"
    assert cp._classify(cp.ERROR_SHARING_VIOLATION) == "nicht_pruefbar"
    assert cp._classify(cp.ERROR_FILE_NOT_FOUND) == "nicht_pruefbar" and cp._classify(0) == "nicht_pruefbar"


def test_probe_file_detects_real_write_and_readonly(tmp_path) -> None:
    writable = tmp_path / "w.py"
    writable.write_text("x = 1\n", encoding="utf-8")
    assert cp._probe_file(writable) == "beschreibbar"
    readonly = tmp_path / "r.py"
    readonly.write_text("x = 1\n", encoding="utf-8")
    os.chmod(readonly, stat.S_IREAD)
    try:
        # §110 (A16): das Read-only-ATTRIBUT sperrt nur den Inhalt; der Eigentuemer hat WRITE_DAC und kann es aufheben
        # -> die eigene read-only-Datei gilt (korrekt, strenger) als beschreibbar, nicht als geschuetzt.
        assert cp._probe_file(readonly) == "beschreibbar"
    finally:
        os.chmod(readonly, stat.S_IREAD | stat.S_IWRITE)
    assert cp._probe_file(tmp_path / "fehlt.py") == "nicht_pruefbar"


def test_probe_dir_detects_real_writability(tmp_path) -> None:
    assert cp._probe_dir(tmp_path) == "beschreibbar"
    assert not list(tmp_path.glob(".lexono-schreibtest*"))  # Probedatei/-ordner wieder entfernt


@pytest.mark.skipif(sys.platform != "win32", reason="winerror nur unter Windows aussagekraeftig")
def test_running_writable_file_is_not_falsely_protected(tmp_path) -> None:
    """Regression AUDIT_S103 R7: eine laufende, beschreibbare, uns gehoerende Datei ist KEIN Schutznachweis. §110 (A16):
    der Inhalt ist freigabeverletzt, aber der Eigentuemer kann via WRITE_DAC die Rechte aendern -> nie 'geschuetzt'."""
    source = Path(getattr(sys, "_base_executable", sys.executable))
    exe = tmp_path / "python_kopie.exe"
    exe.write_bytes(source.read_bytes())
    for dll in source.parent.glob("python3*.dll"):
        (tmp_path / dll.name).write_bytes(dll.read_bytes())
    proc = subprocess.Popen([str(exe), "-c", "import time; time.sleep(6)"])
    try:
        time.sleep(1.5)
        assert cp._probe_file(exe) != "geschuetzt"  # weder faelschlich geschuetzt (hier: beschreibbar via WRITE_DAC)
    finally:
        proc.kill()
        proc.wait()


def test_write_access_uses_real_probe_and_flags_writable(tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("", encoding="utf-8")
    venv = tmp_path / "venv"
    (venv / "Lib" / "site-packages").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home=x", encoding="utf-8")
    result = cp.write_access(repo, venv=[venv], base=[])
    assert str(venv / "pyvenv.cfg") in result["beschreibbar"] and "app/__init__.py" in result["beschreibbar"]
    assert ev._write_protected(result) is False  # echte Schreibbarkeit -> FAIL


def test_a1_isolation_probe_uses_the_real_write_check(tmp_path) -> None:
    """Die Bytecode-Isolationspruefung ruft die echte Schreibprobe auf dem Elternordner des Praefix auf (A12)."""
    prefix = tmp_path / "frei" / "kein-bytecode"
    (tmp_path / "frei").mkdir()
    problems = cp.bytecode_isolation(flags=type("F", (), {"dont_write_bytecode": True})(), prefix=str(prefix))
    assert _has(problems, "beschreibbar")  # tmp-Elternordner ist beschreibbar -> echter Befund, kein Fehlalarm


# === A13: Belegalter beim Gebrauch ======================================================================================


def test_age_at_use_is_checked_against_anchor_not_only_vm_clock(tmp_path) -> None:
    gate = make_test_gate(tmp_path)
    assert _problems(gate) == []
    anchor = acc.SYNTHETIC_ZEITANKER
    gate.now = lambda: gate._now + timedelta(minutes=1)  # VM-Uhr scheinbar frisch (innerhalb der Gueltigkeit)
    gate.zeitbasis = lambda: {"tick_ms": anchor["tick_ms"] + 26 * 3_600_000, "boot_id": anchor["boot_id"]}
    assert _problems(gate)  # 26 h laut Anker -> Beleg/Bestaetigung ueberaltert, Gate geschlossen


def test_evidence_age_at_use_blocks_with_rolled_forward_now(tmp_path) -> None:
    """Direkt auf der Bewertung: dieselben Belege, spaeterer Nutzungszeitpunkt -> BLOCKIERT (Altersgrenze beim Gebrauch)."""
    from datetime import datetime, timezone

    from tests.isolation_test_support import bundle_items, synthetic_policy, write_bundle

    now = datetime.now(timezone.utc)
    policy_path = synthetic_policy(tmp_path, LOOP)
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    lauf = {"run_id": "l", "maschine": {"hostname": "vm", "machine_guid": "g"},
            "netzwerkpolitik_sha256": isolation_gate.sha256_file(policy_path),
            "pruefwerkzeug": {"commit": "c" * 40, "sha256": "d" * 64}}
    folder = write_bundle(tmp_path / "b", bundle_items(LOOP, now), lauf=lauf, policy=policy)
    assert ev.evaluate(folder, policy=policy, lauf=lauf, reference=now, now=now, allow_dry_run=True)["gesamt"] == PASS
    spaet = ev.evaluate(folder, policy=policy, lauf=lauf, reference=now, now=now + timedelta(hours=30), allow_dry_run=True)
    assert spaet["gesamt"] == BLOCKIERT


# === A14/A15: importierbare ausgeschlossene Verzeichnisse, Endungen ======================================================


def _repo(tmp_path: Path) -> tuple[Path, set[str]]:
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("", encoding="utf-8")
    return repo, {"app/__init__.py"}


def _code_problems(repo: Path, fingerprint: set[str]) -> list[str]:
    return cp.code_source_problems(repo, fingerprint, modules={}, path=[str(repo)],
                                   roots={"venv": [], "basis": [], "site_packages": []}, isolation=lambda: [])


def test_importable_excluded_subpackage_is_flagged(tmp_path) -> None:
    """Regression AUDIT_S103 A14/R2: app/build/ ist als app.build importierbar, aber gitignoriert und nicht erfasst."""
    repo, fp = _repo(tmp_path)
    (repo / "app" / "build").mkdir()
    (repo / "app" / "build" / "__init__.py").write_text("WER = 'fremd'\n", encoding="utf-8")
    assert _has(_code_problems(repo, fp), "importierbares, ausgeschlossenes Verzeichnis") and \
        _has(_code_problems(repo, fp), "app/build")


def test_excluded_dir_without_package_parent_is_not_flagged(tmp_path) -> None:
    repo, fp = _repo(tmp_path)
    (repo / "build").mkdir()  # Wurzel: build/ ist direkt unter der Wurzel importierbar -> flaggen
    (repo / "build" / "__init__.py").write_text("", encoding="utf-8")
    assert _has(_code_problems(repo, fp), "build")
    repo2, fp2 = _repo(tmp_path / "x")
    (repo2 / "data").mkdir()  # data/ ohne __init__ und ohne Code -> nicht importierbar, kein Befund
    (repo2 / "data" / "notizen.txt").write_text("", encoding="utf-8")
    assert not _has(_code_problems(repo2, fp2), "data")


def test_bytecode_extension_scan_is_case_insensitive(tmp_path) -> None:
    """Regression AUDIT_S103 A15: unter Windows werden auch .PYC/.PYD geladen."""
    repo, fp = _repo(tmp_path)
    (repo / "app" / "schnell.PYD").write_bytes(b"\x00")
    (repo / "app" / "alt.PYC").write_bytes(b"\x00")
    problems = _code_problems(repo, fp)
    assert _has(problems, "schnell.PYD") and _has(problems, "alt.PYC")


def test_real_repo_has_no_code_source_false_positives(tmp_path) -> None:
    """Das echte Repository darf in einem FRISCHEN, isolierten Prozess (wie in der VM) keine Codequellen-Probleme melden.
    Im laufenden pytest-Prozess liegen test- und tmp-Module in sys.modules/sys.path - das ist nicht die Laufzeit der VM,
    daher wird in einem Subprozess mit Bytecode-Isolation gemessen (ordnungsunabhaengig)."""
    code = "from scripts.legal_bench import isolation_gate as g; import json; print(json.dumps(g.codepfad_probleme()))"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPYCACHEPREFIX": str(tmp_path / "kein-bytecode")}
    out = subprocess.run([sys.executable, "-c", code], cwd=answer_specs.REPO, env=env, capture_output=True, text=True,
                         timeout=120, check=True)
    problems = json.loads(out.stdout.strip().splitlines()[-1])
    uebrig = [p for p in problems if "Bytecode-Isolation" not in p and "PYTHONPYCACHEPREFIX" not in p and "Elternordner" not in p]
    assert uebrig == [], uebrig


# === A7: Firewall-Entfernen gruppenweise; A5: Setzen nur bei leerer Ressource ===========================================


def test_firewall_remove_is_scoped_to_the_group() -> None:
    cmd = hc._fw_remove_command({"DisplayName": "Lexono-Abnahme r1 TCP-445 10.0.0.1"})
    assert "-Group 'Lexono-Abnahme'" in cmd and "-DisplayName 'Lexono-Abnahme r1 TCP-445 10.0.0.1'" in cmd


def test_acl_setzen_refuses_over_residue(setup) -> None:
    """§104 (A5): Setzen ist nur bei leerer Ist-ACL erlaubt - Reste verhindern einen neuen Lauf technisch."""
    setup["system"].acl = hc.normalize_acl(hc.soll_acl(setup["policy"]))
    with pytest.raises(hc.ControlRefused, match="nicht leer"):
        _control(setup, "neu", setup["tmp"] / "j.jsonl").acl_setzen(setup["policy"], setup["tmp"] / "a.json")


def test_e2e_embedding_fallback_does_not_mutate_sys_path() -> None:
    """§104 (A17): Der Fake-Provider-Fallback darf tests/ nicht dauerhaft in sys.path legen (Gate-Fehlalarm)."""
    import inspect

    from scripts.legal_bench import e2e

    source = inspect.getsource(e2e._embedding_provider)
    assert "sys.path.insert" not in source and "spec_from_file_location" in source
