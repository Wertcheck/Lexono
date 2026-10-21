"""§105: Regressionstests zu AUDIT_S104 A5 (Startcheck erzwungen), A8 (Integrität beim Signieren), A10 (gelöschte/
hinzugefügte Dateien). Nur lokal: tmp-Kopien, simuliertes Hyper-V (`FakeSystem`), git-Stubs, synthetische Schlüssel im
Speicher. Keine echte Signatur, keine Systemänderung, keine reale Isolation."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.legal_bench import answer_specs, isolation_gate, isolation_host_control as hc, owner_sign
from tests.isolation_test_support import HOST_IDENTITY, PRUEFER, committed_like_worktree, make_test_gate
from tests.test_s94_real_acceptance_path import FakeSystem, setup  # noqa: F401

NOW = datetime.now(timezone.utc)


def _has(items, text) -> bool:  # noqa: ANN001
    return any(text in i for i in items)


# === A5: Startcheck technisch erzwungen =================================================================================


def _sc(lauf, ok, delta):  # noqa: ANN001
    return {"vorgang": "startcheck", "lauf": lauf, "status": "startcheck_ergebnis", "ok": ok,
            "zeit": (NOW - delta).isoformat()}


def test_startcheck_frisch_requires_recent_successful_check() -> None:
    assert hc.startcheck_frisch([], "r", NOW)[0] is False
    assert hc.startcheck_frisch([_sc("r", True, timedelta(minutes=5))], "r", NOW)[0] is True
    assert hc.startcheck_frisch([_sc("r", True, timedelta(hours=13))], "r", NOW)[0] is False  # zu alt
    assert hc.startcheck_frisch([_sc("r", False, timedelta(minutes=5))], "r", NOW)[0] is False  # nicht ok
    assert hc.startcheck_frisch([_sc("anderer", True, timedelta(minutes=5))], "r", NOW)[0] is False  # anderer Lauf
    # spaetere Inspektion (not-ok) nach einem frischen ok-Startcheck entwaffnet den Lauf nicht
    assert hc.startcheck_frisch([_sc("r", True, timedelta(minutes=10)), _sc("r", False, timedelta(minutes=1))],
                                "r", NOW)[0] is True


def test_startcheck_frisch_blocks_after_foreign_mutation() -> None:
    entries = [_sc("r", True, timedelta(minutes=10)),
               {"vorgang": "fw-setzen", "lauf": "fremd", "status": "geplant",
                "zeit": (NOW - timedelta(minutes=5)).isoformat()}]
    ok, grund = hc.startcheck_frisch(entries, "r", NOW)
    assert ok is False and "fremder Eingriff" in grund


def test_startcheck_records_its_result_in_the_journal(setup) -> None:
    journal = setup["tmp"] / "aenderungsjournal.jsonl"
    Path(setup["konfig"]["golden_image"]["kind_vhdx"]).write_bytes(b"kind")
    control = hc.HostControl("lexono-bench", ausfuehren=False, runner=setup["system"], journal=journal,
                             run_id=setup["konfig"]["run_id"])
    result = control.startcheck(setup["konfig"])
    assert result["ok"] is True
    entries = hc.read_journal(journal)
    assert any(e.get("status") == "startcheck_ergebnis" and e.get("ok") is True for e in entries)
    assert hc.startcheck_frisch(entries, setup["konfig"]["run_id"], datetime.now(timezone.utc))[0] is True


def test_startcheck_records_failure(setup) -> None:
    journal = setup["tmp"] / "aenderungsjournal.jsonl"  # Kinddatentraeger fehlt -> startcheck nicht ok
    control = hc.HostControl("lexono-bench", ausfuehren=False, runner=setup["system"], journal=journal,
                             run_id=setup["konfig"]["run_id"])
    assert control.startcheck(setup["konfig"])["ok"] is False
    assert hc.startcheck_frisch(hc.read_journal(journal), setup["konfig"]["run_id"], datetime.now(timezone.utc))[0] is False


def test_cli_refuses_real_mutation_without_fresh_startcheck(setup, capsys) -> None:
    """Die CLI fuehrt keine reale Aenderung (hier fw-setzen) ohne aktuellen ok-Startcheck aus - Abbruch VOR jedem
    Systemaufruf (leeres Journal). Kein PowerShell-Aufruf, da der Guard vorher greift."""
    rc = hc.main(["fw-setzen", "--konfig", str(setup["konfig_path"]), "--ausfuehren", "--protokoll",
                  str(setup["tmp"] / "fw.json")])
    assert rc == 2
    assert "Startcheck" in capsys.readouterr().err


@pytest.mark.parametrize("cmd", ["acl-setzen", "fw-setzen", "image-zuruecksetzen"])
def test_cli_mutations_all_require_startcheck(setup, capsys, cmd) -> None:
    rc = hc.main([cmd, "--konfig", str(setup["konfig_path"]), "--ausfuehren", "--protokoll", str(setup["tmp"] / "p.json")])
    assert rc == 2 and "Startcheck" in capsys.readouterr().err


def test_cli_plan_and_read_do_not_require_startcheck(setup, capsys) -> None:
    """Nur-Plan/Lesen (ohne --ausfuehren) sind keine realen Aenderungen und brauchen keinen Startcheck."""
    assert hc.main(["acl-plan", "--konfig", str(setup["konfig_path"])]) == 0
    assert "Startcheck" not in capsys.readouterr().err


# === A8: Integrität beim Signieren ======================================================================================


def _sign(gate, *, tool=None, committed=committed_like_worktree):  # noqa: ANN001
    return owner_sign.sign_attestation(
        gate._test_key, "test-key-1", keylist_path=gate.keylist_path, bundle=gate.evidence_path,
        policy_path=gate.policy_path, run_id="qa80-lauf", maschine=isolation_gate.machine_identity(),
        pruefer_name=PRUEFER["name"], hours=2, pinned_roots=gate.pinned_roots, test_only=True, now=gate._now,
        signer_identity=lambda: HOST_IDENTITY, committed=committed, tool=tool or isolation_gate.pruefwerkzeug_stand)


def test_signing_succeeds_on_a_consistent_state(tmp_path) -> None:
    gate = make_test_gate(tmp_path)
    document = _sign(gate)  # synthetischer Testschluessel, Loopback - KEINE echte Freigabe
    assert document["key_id"] == "test-key-1" and len(document["signature_hex"]) == 128


def test_signing_requires_the_runtime_fingerprint(tmp_path) -> None:
    gate = make_test_gate(tmp_path)
    real = isolation_gate.pruefwerkzeug_stand()
    with pytest.raises(owner_sign.SigningRefused, match="Laufzeitumgebung"):
        _sign(gate, tool=lambda: {**real, "laufzeit_sha256": None})


def test_signing_refuses_when_worktree_differs_from_commit(tmp_path) -> None:
    gate = make_test_gate(tmp_path)
    with pytest.raises(owner_sign.SigningRefused, match="weicht vom Commit ab"):
        _sign(gate, committed=lambda commit: {"commit": commit, "sha256": "f" * 64})


def test_signing_detects_a_change_during_signing_toctou(tmp_path) -> None:
    gate = make_test_gate(tmp_path)
    real = isolation_gate.pruefwerkzeug_stand()
    calls = {"n": 0}

    def shifting_tool():  # noqa: ANN202
        calls["n"] += 1
        return real if calls["n"] == 1 else {**real, "sha256": "e" * 64}  # 2. Lesung weicht ab

    with pytest.raises(owner_sign.SigningRefused, match="waehrend des Signierens veraendert"):
        _sign(gate, tool=shifting_tool)


# === A10: gelöschte / hinzugefügte / unlesbare Dateien beim Signieren ===================================================


def _git(*, ls_names=None, cat_missing=(), ls_fail=False):  # noqa: ANN002
    names = list(isolation_gate.tool_files())

    def run(argv, input=b"", **_kw):  # noqa: A002, ANN001, ANN202
        if "ls-tree" in argv:
            if ls_fail:
                return subprocess.CompletedProcess(argv, 1, "", "fehler")
            return subprocess.CompletedProcess(argv, 0, "\n".join(names if ls_names is None else ls_names), "")
        out = b""
        for line in input.decode().splitlines():
            name = line.split(":", 1)[1]
            if name in cat_missing:
                out += f"{line} missing\n".encode()
                continue
            data = (answer_specs.REPO / name).read_bytes()
            out += f"{'0' * 40} blob {len(data)}\n".encode() + data + b"\n"
        return subprocess.CompletedProcess(argv, 0, out, b"")

    return run


def test_committed_stand_clean_matches_worktree() -> None:
    assert owner_sign.committed_stand("x", runner=_git())["sha256"] == isolation_gate.pruefwerkzeug_stand()["sha256"]


def test_committed_stand_detects_deleted_tracked_file() -> None:
    """§105 (A10): eine im Commit verfolgte, im Arbeitsbaum fehlende Fingerabdruck-Datei -> nichts wird still ausgelassen."""
    names = list(isolation_gate.tool_files())
    stand = owner_sign.committed_stand("x", runner=_git(ls_names=[*names, "app/geloescht.py"]))
    assert stand["sha256"] is None and "geloescht" in stand["fehlend"] and "app/geloescht.py" in stand["fehlend"]


def test_committed_stand_detects_added_untracked_file() -> None:
    names = list(isolation_gate.tool_files())
    stand = owner_sign.committed_stand("x", runner=_git(ls_names=names[:-1]))  # Commit kennt eine Datei weniger
    assert stand["sha256"] is None and "nicht committet" in stand["fehlend"]


def test_committed_stand_detects_unreadable_blob() -> None:
    names = list(isolation_gate.tool_files())
    stand = owner_sign.committed_stand("x", runner=_git(cat_missing=(names[0],)))
    assert stand["sha256"] is None and stand["fehlend"] == names[0]


def test_committed_stand_handles_ls_tree_failure() -> None:
    stand = owner_sign.committed_stand("x", runner=_git(ls_fail=True))
    assert stand["sha256"] is None and "ls-tree" in stand["fehlend"]


def test_real_git_reports_uncommitted_tool_files() -> None:
    """Echt (nur lesend): §90-§105 sind uncommittet -> Arbeitsbaum weicht vom HEAD ab, Signieren bleibt unmoeglich."""
    assert owner_sign.committed_stand("HEAD")["sha256"] is None


def test_im_fingerprint_matches_tool_files() -> None:
    files = set(isolation_gate.tool_files())
    assert all(isolation_gate.im_fingerprint(n) for n in files)
    assert not isolation_gate.im_fingerprint("build/x.py") and not isolation_gate.im_fingerprint("app/build/x.py")
    assert not isolation_gate.im_fingerprint("data/notiz.txt") and isolation_gate.im_fingerprint("pyproject.toml")
