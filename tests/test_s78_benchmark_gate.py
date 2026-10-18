"""§78 – adversariale Tests der Freigabesperre, des Aufrufplans und der Test-Split-Sperre des Antwort-Benchmarks.

Nur synthetische Konfigurationen und temporaere Kopien – keine Modellaufrufe, kein Netzwerk, keine Kosten. Die Originale
unter benchmarks/ werden nicht veraendert (Manipulationen ausschliesslich in tmp_path).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.legal_bench import answer_run, answer_specs, corpus

APPROVAL = {"person": "Testperson", "rolle": "Test", "datum": "2026-10-18", "entscheidung": "freigegeben"}


def _template() -> dict:
    return answer_run.load_config(answer_run.TEMPLATE)


def _valid() -> dict:
    """Synthetische, formal gueltige Testkonfiguration (Positivkontrolle) - KEINE echte Freigabe."""
    c = _template()
    c.update(run_id="test-lauf-01", approved=True)
    c["approvals"] = {k: dict(APPROVAL) for k in c["approvals"]}
    c["model"].update(provider="claude", model_id="claude-sonnet-5", model_version="TESTVERSION", temperature=0,
                      max_tokens=4000)
    c["pipeline"].update(lexono_commit=answer_run._git_commit(), prompt_version="TEST-1")
    c["scope"]["repetitions"] = 1
    c["limits"].update(max_calls=6, max_cost_eur=10, price_per_call_eur_estimate=0.5, abort_after_failed_calls=1)
    c["storage"].update(results_dir="ausserhalb_des_repos/lauf", retention="TEST")
    c["evaluation"].update(reviewers=["Testperson"], second_reviewer_for_critical=True)
    return c


def test_positive_control_is_runnable() -> None:
    result = answer_run.check(_valid())
    assert result == {"open": [], "violations": [], "runnable": True}
    answer_run.ensure_runnable(_valid())  # wirft nicht


# --- Freigabesperre: jede Manipulation wird mit konkretem Grund abgelehnt -------------------------------------------------

GATE_CASES = [
    ("Kostenlimit-Schluessel geloescht", lambda c: c["limits"].pop("max_cost_eur"), "limits.max_cost_eur: fehlt"),
    ("limits-Block geloescht", lambda c: c.pop("limits"), "limits.max_calls: fehlt"),
    ("approvals-Block geloescht", lambda c: c.pop("approvals"), "approvals.datenschutz_freigabe: fehlt"),
    ("approvals = false", lambda c: c.update(approvals={k: False for k in c["approvals"]}), "approvals.owner_freigabe"),
    ("approvals = 'nein'", lambda c: c.update(approvals={k: "nein" for k in c["approvals"]}), "approvals.datenschutz"),
    ("Entscheidung abgelehnt", lambda c: c["approvals"]["datenschutz_freigabe"].update(entscheidung="abgelehnt"),
     "approvals.datenschutz_freigabe: ungueltig"),
    ("Freigabe ohne Datum", lambda c: c["approvals"]["owner_freigabe_lauf_und_kosten"].pop("datum"),
     "approvals.owner_freigabe_lauf_und_kosten: ungueltig"),
    ("unbekannter Anbieter", lambda c: c["model"].update(provider="unbekannt-ki"), "model.provider: ungueltig"),
    ("unbekannte Modell-ID", lambda c: c["model"].update(model_id="gpt-x"), "model.model_id: ungueltig"),
    ("Temperatur 7", lambda c: c["model"].update(temperature=7), "model.temperature: ungueltig"),
    ("max_tokens 100000", lambda c: c["model"].update(max_tokens=100000), "model.max_tokens: ungueltig"),
    ("Wiederholungen -3", lambda c: c["scope"].update(repetitions=-3), "scope.repetitions: ungueltig"),
    ("Wiederholungen True", lambda c: c["scope"].update(repetitions=True), "scope.repetitions: ungueltig"),
    ("Kostenlimit 0", lambda c: c["limits"].update(max_cost_eur=0), "limits.max_cost_eur: ungueltig"),
    ("Kostenlimit 'unbegrenzt'", lambda c: c["limits"].update(max_cost_eur="unbegrenzt"), "limits.max_cost_eur"),
    ("Kostenlimit null", lambda c: c["limits"].update(max_cost_eur=None), "limits.max_cost_eur"),
    ("Abbruchgrenze fehlt", lambda c: c["limits"].pop("abort_after_failed_calls"), "abort_after_failed_calls: fehlt"),
    ("Abbruchgrenze 0", lambda c: c["limits"].update(abort_after_failed_calls=0), "abort_after_failed_calls: ungueltig"),
    ("Abbruchgrenze null", lambda c: c["limits"].update(abort_after_failed_calls=None), "abort_after_failed_calls"),
    ("max_calls < Plan", lambda c: c["limits"].update(max_calls=1), "6 geplante Aufrufe > 1"),
    ("Kosten > Limit", lambda c: c["limits"].update(max_cost_eur=2), "geschaetzt 3.00 EUR > 2"),
    ("veralteter Commit", lambda c: c["pipeline"].update(lexono_commit="0" * 40), "lexono_commit: veraltet"),
    ("Commit kein Hash", lambda c: c["pipeline"].update(lexono_commit="HEAD"), "lexono_commit: ungueltig"),
    ("unbekannte Spezifikation", lambda c: c["scope"].update(specs=["LA-X999"]), "unbekannte Spezifikation"),
    ("Test-Spec ohne test-Split", lambda c: c["scope"].update(splits=["dev"]), "nicht in scope.splits"),
    ("leerer Umfang", lambda c: c["scope"].update(specs=[]), "scope.specs: ungueltig"),
    ("approved als String", lambda c: c.update(approved="true"), "approved: ungueltig"),
    ("approved fehlt", lambda c: c.pop("approved"), "approved: fehlt"),
    ("approved false", lambda c: c.update(approved=False), "approved (ausdrueckliche Freigabe fehlt)"),
    ("leak_check 'true'", lambda c: c["privacy"].update(leak_check_required="true"), "privacy.leak_check_required"),
    ("leak_check false", lambda c: c["privacy"].update(leak_check_required=False), "privacy.leak_check_required"),
    ("payload_capture false", lambda c: c["privacy"].update(payload_capture=False), "privacy.payload_capture"),
    ("fail_closed false", lambda c: c["privacy"].update(fail_closed_required=False), "privacy.fail_closed_required"),
    ("fail_closed fehlt", lambda c: c["privacy"].pop("fail_closed_required"), "privacy.fail_closed_required: fehlt"),
    ("echte Mandantendaten", lambda c: c["privacy"].update(real_client_data=True), "privacy.real_client_data"),
    ("kein Abbruch bei Datenschutzbefund", lambda c: c["limits"].update(abort_on_first_privacy_finding=False),
     "abort_on_first_privacy_finding"),
    ("Direktpfad am Gateway vorbei", lambda c: c["pipeline"].update(path="anthropic_direkt"), "pipeline.path"),
    ("Zusatzschalter skip_privacy", lambda c: c.update(skip_privacy=True), "skip_privacy: unbekannter Schluessel"),
    ("Zusatzschalter privacy.override", lambda c: c["privacy"].update(override=True), "privacy.override: unbekannter"),
    ("Schema-Version 0", lambda c: c.update(schema_version=0), "schema_version: ungueltig"),
    ("blinde Bewertung aus", lambda c: c["evaluation"].update(blind_review=False), "evaluation.blind_review"),
    ("keine Pruefpersonen", lambda c: c["evaluation"].update(reviewers=[]), "evaluation.reviewers: ungueltig"),
]


@pytest.mark.parametrize("name, mutate, reason", GATE_CASES, ids=[c[0] for c in GATE_CASES])
def test_gate_rejects_manipulation_with_concrete_reason(name, mutate, reason) -> None:
    config = _valid()
    mutate(config)
    result = answer_run.check(config)
    reasons = result["open"] + result["violations"]
    assert result["runnable"] is False, name
    assert any(reason in r for r in reasons), (name, reasons)
    with pytest.raises(answer_run.NotApproved):
        answer_run.ensure_runnable(config)
    plan = answer_run.plan(config)  # kein alternativer Aufrufweg: auch der Plan bleibt nicht ausfuehrbar
    assert plan["ausfuehrbar"] is False and plan["nicht_ausfuehrbar_weil"]


@pytest.mark.parametrize("config", [[], "freigegeben", None, 1])
def test_gate_rejects_non_object_config(config) -> None:
    assert answer_run.check(config)["runnable"] is False
    with pytest.raises(answer_run.NotApproved):
        answer_run.ensure_runnable(config)


def _cli(command: str, config: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "scripts.legal_bench.answer_run", "--config", str(config), command],
                          capture_output=True, text=True, encoding="utf-8", cwd=Path(__file__).resolve().parents[1],
                          env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})


@pytest.mark.parametrize("command", ["check", "plan"])
def test_cli_refuses_template_and_broken_config_with_exit_code(tmp_path: Path, command: str) -> None:
    out = _cli(command, answer_run.TEMPLATE)
    assert out.returncode == 2 and "usage:" not in out.stderr, out.stderr  # echte Ablehnung, kein Argumentfehler
    assert "approvals.datenschutz_freigabe" in out.stdout
    broken = tmp_path / "run_config.kaputt.json"
    broken.write_text("{kein json", encoding="utf-8")
    out = _cli(command, broken)
    assert out.returncode == 2 and "Konfiguration nicht lesbar" in out.stdout and "Traceback" not in out.stderr


def test_cli_accepts_only_the_valid_positive_control(tmp_path: Path) -> None:
    path = tmp_path / "run_config.test.json"
    path.write_text(json.dumps(_valid()), encoding="utf-8")
    assert _cli("check", path).returncode == 0 and _cli("plan", path).returncode == 0


def test_no_execution_function_exists() -> None:
    names = {n for n in dir(answer_run) if callable(getattr(answer_run, n))}
    assert not names & {"execute", "run", "run_benchmark", "call_model", "send"}


# --- Aufrufplan: reproduzierbar, versioniert, ohne Netzwerk -----------------------------------------------------------


@pytest.fixture
def no_network(monkeypatch):
    def _blocked(*_a, **_k):
        raise AssertionError("Netzwerkzugriff im Plan/Check - verboten")
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


def test_plan_is_reproducible_versioned_and_offline(no_network) -> None:
    first, second = answer_run.plan(_template()), answer_run.plan(_template())
    assert first == second
    assert first["ausfuehrbar"] is False and first["nicht_ausfuehrbar_weil"]
    assert len(first["plan_fingerprint"]) == 64 and len(first["lock_sha256"]) == 64
    assert first["git_commit"] == answer_run._git_commit() and first["lock_errors"] == []
    assert first["anzahl_aufrufe"] == 6


def test_plan_call_count_scales_with_repetitions_and_changes_fingerprint(no_network) -> None:
    config = _valid()
    base = answer_run.plan(config)
    assert base["ausfuehrbar"] is True and base["anzahl_aufrufe"] == 6
    config["scope"]["repetitions"] = 3
    config["limits"]["max_calls"] = 18
    tripled = answer_run.plan(config)
    assert tripled["anzahl_aufrufe"] == 18 and tripled["plan_fingerprint"] != base["plan_fingerprint"]
    assert len({(c["spec_id"], c["task_id"], c["repetition"]) for c in tripled["calls"]}) == 18


def test_plan_only_executable_with_valid_approval(no_network) -> None:
    config = _valid()
    config["approvals"]["datenschutz_freigabe"] = None
    plan = answer_run.plan(config)
    assert plan["ausfuehrbar"] is False and "approvals.datenschutz_freigabe" in plan["nicht_ausfuehrbar_weil"]


def test_benchmark_modules_load_no_network_or_cloud_client() -> None:
    """Statisch und zur Laufzeit: answer_run/answer_specs ziehen keinen Cloud-/HTTP-Client nach."""
    code = ("import sys, scripts.legal_bench.answer_run, scripts.legal_bench.answer_specs; "
            "bad = ('anthropic', 'httpx', 'requests', 'urllib3', 'app.ai_providers', 'app.privacy'); "
            "print([m for m in sys.modules if m.split('.')[0] in bad or m.startswith(bad)])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                         cwd=Path(__file__).resolve().parents[1])
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


# --- Test-Split-Sperre: Manipulationen nur in temporaeren Kopien --------------------------------------------------------


@pytest.fixture
def lock_env(tmp_path, monkeypatch):
    root = tmp_path / "legal_answers_v1"
    shutil.copytree(answer_specs.ROOT, root)
    cases = tmp_path / "legal_e2e_v1"
    shutil.copytree(corpus.CORPUS_DIR, cases)
    monkeypatch.setattr(corpus, "CORPUS_DIR", cases)
    answer_specs.write_lock("Testkopie: Ausgangssperre fuer Manipulationstest", root, root / "LOCK.json")
    assert answer_specs.check_lock(root, root / "LOCK.json") == []
    return root, cases


def _edit(path: Path, fn) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _set_deadline(c: Path) -> None:
    _edit(c / "cases/test/LE-T004.json", lambda d: d["reference"]["deadlines"][0].update(date="06.02.2026"))


def _hand_edit_lock(r: Path, c: Path) -> None:
    _set_deadline(c)
    new = answer_specs.file_fingerprint(c / "cases/test/LE-T004.json")
    _edit(r / "LOCK.json", lambda d: d["entries"].update({"benchmarks/legal_e2e_v1/cases/test/LE-T004.json": new}))


LOCK_CASES = [
    ("Testakte geaendert", lambda r, c: _set_deadline(c), "LE-T004.json wurde geaendert"),
    ("Testakte geloescht", lambda r, c: (c / "cases/test/LE-T001.json").unlink(), "LE-T001.json fehlt"),
    ("zusaetzliche Testakte", lambda r, c: shutil.copy(c / "cases/test/LE-T001.json", c / "cases/test/LE-T099.json"),
     "LE-T099.json ist neu"),
    ("Test-Spezifikation geaendert",
     lambda r, c: _edit(r / "specs/test/LA-T004.json", lambda d: d["criteria"][6]["accept_any"].append(["06.02.2026"])),
     "LA-T004.json wurde geaendert"),
    ("Test-Spezifikation geloescht", lambda r, c: (r / "specs/test/LA-T004.json").unlink(), "LA-T004.json fehlt"),
    ("zusaetzliche Test-Spezifikation",
     lambda r, c: shutil.copy(r / "specs/test/LA-T004.json", r / "specs/test/LA-T005.json"), "LA-T005.json ist neu"),
    ("Rubrik-Schwellen gesetzt",
     lambda r, c: _edit(r / "rubric.json", lambda d: d["aggregation"].update(thresholds={"ergebnis": 0})),
     "rubric.json wurde geaendert"),
    ("Referenz-Quelle Nutzungsrecht entzogen",
     lambda r, c: _edit(c / "source_register.json", lambda d: [s["nutzungsrechte"].update(status="nicht_geprueft")
                                                               for s in d["sources"]
                                                               if s["id"] == "Q-RECHTSPRECHUNG-IM-INTERNET"]),
     "Q-RECHTSPRECHUNG-IM-INTERNET wurde geaendert"),
    ("Referenz-Quelle aus Register entfernt",
     lambda r, c: _edit(c / "source_register.json", lambda d: d.update(
         sources=[s for s in d["sources"] if s["id"] != "Q-GESETZE-IM-INTERNET"])),
     "Q-GESETZE-IM-INTERNET wurde geaendert"),
    ("LOCK ungueltiges JSON", lambda r, c: (r / "LOCK.json").write_text("{kaputt", encoding="utf-8"), "kein lesbares JSON"),
    ("LOCK leer", lambda r, c: (r / "LOCK.json").write_text("{}", encoding="utf-8"), "entries/history fehlen"),
    ("LOCK Liste", lambda r, c: (r / "LOCK.json").write_text("[]", encoding="utf-8"), "entries/history fehlen"),
    ("LOCK fehlt", lambda r, c: (r / "LOCK.json").unlink(), "LOCK.json fehlt"),
    ("LOCK-Hash von Hand angepasst", _hand_edit_lock, "Handbearbeitung ohne --update-lock"),
    ("LOCK-Historie entfernt", lambda r, c: _edit(r / "LOCK.json", lambda d: d.update(history=[])), "entries/history"),
]


@pytest.mark.parametrize("name, mutate, reason", LOCK_CASES, ids=[c[0] for c in LOCK_CASES])
def test_lock_detects_tampering(lock_env, name, mutate, reason) -> None:
    root, cases = lock_env
    mutate(root, cases)
    errors = answer_specs.check_lock(root, root / "LOCK.json")
    assert any(reason in e for e in errors), (name, errors)


def test_unused_register_entry_is_not_locked(lock_env) -> None:
    """Dokumentierte Grenze: nur Registereintraege, auf die Test-Spezifikationen/-Akten verweisen, sind gesperrt."""
    root, cases = lock_env
    _edit(cases / "source_register.json", lambda d: d["sources"][-1].update(hinweis="x"))
    used = set(answer_specs.locked_entries(root))
    last_id = json.loads((cases / "source_register.json").read_text(encoding="utf-8"))["sources"][-1]["id"]
    if f"benchmarks/legal_e2e_v1/source_register.json#{last_id}" not in used:
        assert answer_specs.check_lock(root, root / "LOCK.json") == []


def test_deliberate_full_lock_forgery_is_a_documented_limit(lock_env) -> None:
    """Grenze (dokumentiert in LOCK.json/AUDIT_S78): wer Datei, Eintrag UND Historien-Hash faelscht, faellt nur in Git auf."""
    root, cases = lock_env
    _hand_edit_lock(root, cases)
    _edit(root / "LOCK.json", lambda d: d["history"][-1].update(entries_sha256=hashlib.sha256(
        json.dumps(d["entries"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()))
    assert answer_specs.check_lock(root, root / "LOCK.json") == []
    assert "Git-Historie" in json.loads((root / "LOCK.json").read_text(encoding="utf-8"))["purpose"]


@pytest.mark.parametrize("reason", ["", "   ", "zu kurz", None])
def test_relock_requires_written_reason(lock_env, reason) -> None:
    root, _ = lock_env
    before = (root / "LOCK.json").read_bytes()
    with pytest.raises(ValueError):
        answer_specs.write_lock(reason, root, root / "LOCK.json")
    assert (root / "LOCK.json").read_bytes() == before


def test_relock_refuses_to_overwrite_invalid_lock(lock_env) -> None:
    root, _ = lock_env
    (root / "LOCK.json").write_text("{kaputt", encoding="utf-8")
    with pytest.raises(ValueError):
        answer_specs.write_lock("Begruendung lang genug fuer die Neusperre", root, root / "LOCK.json")
    assert (root / "LOCK.json").read_text(encoding="utf-8") == "{kaputt"


def test_relock_documents_old_and_new_hashes(lock_env) -> None:
    root, cases = lock_env
    key = "benchmarks/legal_e2e_v1/cases/test/LE-T004.json"
    old = json.loads((root / "LOCK.json").read_text(encoding="utf-8"))["entries"][key]
    _set_deadline(cases)
    lock = answer_specs.write_lock("Testkopie: begruendete Neusperre nach bewusster Aenderung", root, root / "LOCK.json")
    last = lock["history"][-1]
    assert last["reason"].startswith("Testkopie") and last["erstsperre"] is False and last["changed"] == [key]
    assert last["aenderungen"] == [{"pfad": key, "alt": old, "neu": answer_specs.file_fingerprint(cases / "cases/test/LE-T004.json")}]
    assert len(lock["history"]) >= 2 and answer_specs.check_lock(root, root / "LOCK.json") == []


def test_repository_lock_is_valid_and_documents_s78_relock() -> None:
    assert answer_specs.check_lock() == []
    lock = json.loads((answer_specs.ROOT / "LOCK.json").read_text(encoding="utf-8"))
    s78 = [h for h in lock["history"] if h["reason"].startswith("§78")]
    assert len(s78) == 1
    # §78-Neusperre: nur die neu aufgenommenen Registereintraege, keine Test-Akte/Spezifikation/Rubrik geaendert
    assert all(a["alt"] is None and "#" in a["pfad"] for a in s78[0]["aenderungen"])
