"""Antwortqualitaets-Benchmark `benchmarks/legal_answers_v1` (§76/§77) - Spezifikationen, Validator, Vorpruefung, Lock.

KONZEPT, KEIN LAUF: Es werden keine Modellantworten abgerufen. Dieses Modul
1. prueft jede Spezifikation gegen Akte, Referenz und Quellenregister (jedes Kriterium aus Akte/Quelle ableitbar, nur
   freigegebene Quellen, Fingerabdruck der Akte zur Referenzversionierung, Test-Split eingefroren);
2. erzwingt seit §77 die ABDECKUNG der Referenzloesung: jede entscheidende Tatsache und jeder offene Punkt braucht ein
   eigenes Kriterium oder einen begruendeten Ausschluss (`not_assessed`); Alternativergebnisse sind als Feld Pflicht
   (Status juristisch offen, wenn nicht festgelegt - nie vom Werkzeug erfunden); Aenderungshistorie je Spezifikation;
3. schuetzt Test-Split, Rubrik und Test-Spezifikationen per `LOCK.json` (kanonische SHA-256) vor unbemerkter Aenderung;
4. bietet eine VORPRUEFUNG fuer spaetere Antworten: je Kriterium "hinweis_gefunden" / "nicht_gefunden" und je
   gefaehrlichem Fehler "verdacht" - ausdruecklich KEINE Note und kein Urteil ueber die juristische Richtigkeit. Begriffe
   werden seit §77 als ganze Woerter gesucht (vorher Teilwort: "zu" traf "Zum ..."). Die Bewertung je Dimension vergibt
   eine qualifizierte juristische Person (rubric.json).

Aufruf: python -m scripts.legal_bench.answer_specs [--fingerprints | --update-lock --reason TEXT]"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from scripts.legal_bench import corpus
from scripts.legal_bench.solution_score import _norm

ROOT = Path(__file__).resolve().parents[2] / "benchmarks" / "legal_answers_v1"
REPO = ROOT.parents[1]
LOCK_PATH = ROOT / "LOCK.json"
EVALUATIONS = {"juristische_pruefung", "teilautomatisch"}
ALTERNATIVE_STATUS = {"juristische_pruefung_offen", "festgelegt"}
#: kuerzere Begriffe treffen beliebige Woerter ("zu", "ab", "am") - keine Aussagekraft fuer die Vorpruefung
MIN_TERM_LENGTH = 3


@dataclass
class SpecReport:
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _canonical_sha256(content: dict) -> str:
    clean = {k: v for k, v in content.items() if not k.startswith("_")}
    return hashlib.sha256(json.dumps(clean, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def case_fingerprint(case: dict) -> str:
    """SHA-256 ueber den kanonischen JSON-Inhalt des Falls (unabhaengig von Zeilenenden/Formatierung)."""
    return _canonical_sha256(case)


def spec_fingerprint(spec: dict) -> str:
    return _canonical_sha256(spec)


def file_fingerprint(path: Path) -> str:
    return _canonical_sha256(json.loads(path.read_text(encoding="utf-8")))


def load_rubric(root: Path = ROOT) -> dict:
    return json.loads((root / "rubric.json").read_text(encoding="utf-8"))


def load_specs(root: Path = ROOT) -> list[dict]:
    specs = []
    for path in sorted((root / "specs").glob("*/*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        spec["_dir_split"] = path.parent.name
        spec["_path"] = path
        specs.append(spec)
    return specs


# --- Lock: Test-Split, Rubrik, Test-Spezifikationen ------------------------------------------------------------------


def locked_paths(root: Path = ROOT) -> list[Path]:
    """Dateien, deren Inhalt fuer einen spaeteren Testlauf unveraendert bleiben muss."""
    return ([root / "rubric.json"] + sorted((root / "specs" / "test").glob("*.json"))
            + sorted((corpus.CORPUS_DIR / "cases" / "test").glob("*.json")))


def _key(path: Path, root: Path) -> str:
    """Stabiler Schluessel unabhaengig vom Ablageort (auch fuer Kopien ausserhalb des Repositories)."""
    path = path.resolve()
    for base, prefix in ((root.resolve(), "benchmarks/legal_answers_v1"), (corpus.CORPUS_DIR.resolve(), "benchmarks/legal_e2e_v1")):
        if path.is_relative_to(base):
            return f"{prefix}/{path.relative_to(base).as_posix()}"
    return path.as_posix()


def _register_entries(root: Path) -> dict[str, str]:
    """§78: Registereintraege der Quellen, auf die Test-Spezifikationen und Test-Akten verweisen - ein entzogenes
    Nutzungsrecht faellt so auch in der Sperre auf (nicht nur im Validator)."""
    refs: set[str] = set()
    for path in sorted((root / "specs" / "test").glob("*.json")):
        refs |= set(json.loads(path.read_text(encoding="utf-8")).get("source_refs", []))
    for path in sorted((corpus.CORPUS_DIR / "cases" / "test").glob("*.json")):
        refs |= set(json.loads(path.read_text(encoding="utf-8")).get("source_refs", []))
    register = json.loads((corpus.CORPUS_DIR / "source_register.json").read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in register.get("sources", [])}
    return {f"benchmarks/legal_e2e_v1/source_register.json#{ref}": _canonical_sha256(by_id[ref]) if ref in by_id else "FEHLT"
            for ref in sorted(refs)}


def locked_entries(root: Path = ROOT) -> dict[str, str]:
    entries = {_key(p, root): file_fingerprint(p) for p in locked_paths(root)}
    entries.update(_register_entries(root))
    return entries


def _entries_digest(entries: dict) -> str:
    return hashlib.sha256(json.dumps(entries, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def check_lock(root: Path = ROOT, lock_path: Path | None = None) -> list[str]:
    lock_path = lock_path or root / "LOCK.json"
    if not lock_path.exists():
        return ["LOCK.json fehlt - Test-Split nicht gegen Aenderung geschuetzt"]
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return [f"LOCK.json ungueltig (kein lesbares JSON: {type(exc).__name__}) - Sperre nicht pruefbar"]
    if not isinstance(lock, dict) or not isinstance(lock.get("entries"), dict) or not lock["entries"] \
            or not isinstance(lock.get("history"), list) or not lock["history"]:
        return ["LOCK.json ungueltig (entries/history fehlen oder leer) - Sperre nicht pruefbar"]
    expected = lock["entries"]
    errors = []
    # §78: Eintraege muessen zum letzten Historieneintrag passen - eine Handbearbeitung ohne Neusperre faellt auf
    last = lock["history"][-1] if isinstance(lock["history"][-1], dict) else {}
    if last.get("entries_sha256") != _entries_digest(expected):
        errors.append("LOCK: Eintraege passen nicht zum letzten Historieneintrag (Handbearbeitung ohne --update-lock?)")
    current = locked_entries(root)
    for path, digest in sorted(current.items()):
        if path not in expected:
            errors.append(f"LOCK: {path} ist neu und nicht gesperrt")
        elif expected[path] != digest:
            errors.append(f"LOCK: {path} wurde geaendert (gesperrt {str(expected[path])[:12]}, jetzt {digest[:12]})")
    for path in sorted(set(expected) - set(current)):
        errors.append(f"LOCK: gesperrte Datei {path} fehlt")
    return errors


def write_lock(reason: str, root: Path = ROOT, lock_path: Path | None = None) -> dict:
    """Bewusste, begruendete Neusperre - nur vor einem Testlauf bzw. mit dokumentiertem Grund; die Historie haelt je
    Aenderung alten und neuen Hash fest und den Hash aller Eintraege (Pruefung gegen Handbearbeitung)."""
    if not reason or len(reason.strip()) < 20:
        raise ValueError("Neusperre nur mit nachvollziehbarer Begruendung (mindestens 20 Zeichen)")
    lock_path = lock_path or root / "LOCK.json"
    previous: dict = {}
    if lock_path.exists():
        try:
            previous = json.loads(lock_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("vorhandene LOCK.json ist ungueltig - nicht ueberschreiben, Ursache klaeren") from None
    old_entries = previous.get("entries", {}) if isinstance(previous, dict) else {}
    entries = locked_entries(root)
    first = not old_entries
    changes = [] if first else [{"pfad": k, "alt": old_entries.get(k), "neu": entries.get(k)}
                                for k in sorted(set(old_entries) | set(entries)) if old_entries.get(k) != entries.get(k)]
    history = (previous.get("history", []) if isinstance(previous, dict) else []) + [{
        "date": date.today().isoformat(), "reason": reason.strip(), "erstsperre": first,
        "changed": [c["pfad"] for c in changes], "aenderungen": changes, "entries_sha256": _entries_digest(entries)}]
    lock = {"purpose": "Schutz von Test-Split, Rubrik, Test-Spezifikationen und der von ihnen genutzten Quellen-"
                       "Registereintraege vor unbemerkter Aenderung (kanonische SHA-256 je Datei/Eintrag). Aenderung nur "
                       "bewusst mit --update-lock --reason; die Historie dokumentiert alte/neue Hashes. Schutz gegen "
                       "VERSEHENTLICHE/unbemerkte Aenderung - gegen absichtliche Manipulation von Lock und Historie "
                       "zusammen schuetzt nur die Git-Historie.",
            "entries": entries, "history": history}
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return lock


# --- Validator -------------------------------------------------------------------------------------------------------


def _terms_ok(variants: list, where: str, need) -> None:
    need(bool(variants) and all(isinstance(v, list) and v for v in variants), f"{where}: Varianten fehlen")
    for variant in variants or []:
        for term in variant:
            need(len(_norm(term).strip()) >= MIN_TERM_LENGTH, f"{where}: Begriff {term!r} zu kurz (trifft beliebige Woerter)")


def validate_rubric(rubric: dict, report: SpecReport) -> None:
    def need(condition: bool, message: str) -> None:
        if not condition:
            report.errors.append(f"rubric: {message}")

    agg = rubric.get("aggregation") or {}
    need(agg.get("non_compensation") is True, "aggregation.non_compensation muss true sein (kritische Fehler nicht ausgleichbar)")
    need(agg.get("critical_rule") == "FAIL_KRITISCH", "aggregation.critical_rule muss FAIL_KRITISCH sein")
    need("thresholds" in agg, "aggregation.thresholds fehlt (null = juristisch offen)")
    need(any(d.get("disqualifying") for d in rubric.get("dimensions", [])), "keine disqualifizierende Dimension")


def validate_spec(spec: dict, cases: dict[str, dict], register: dict[str, dict], rubric: dict, report: SpecReport) -> None:
    sid = spec.get("id", "?")

    def need(condition: bool, message: str) -> None:
        if not condition:
            report.errors.append(f"{sid}: {message}")

    case = cases.get(spec.get("case"))
    need(case is not None, f"Fall {spec.get('case')!r} unbekannt")
    if case is None:
        return
    need(spec.get("split") == case["split"] == spec.get("_dir_split"), "split passt nicht zu Fall/Ordner")
    need(spec.get("rubric_version") == rubric["version"], "Rubrik-Version passt nicht")
    need(spec.get("case_fingerprint") == case_fingerprint(case),
         "Fingerabdruck der Akte weicht ab - Referenz geaendert, Spezifikation neu pruefen und Version erhoehen")
    if case["split"] == "test":
        need(spec.get("frozen") is True, "Test-Split-Spezifikation muss eingefroren sein (frozen: true)")
    history = spec.get("history") or []
    need(bool(history) and history[-1].get("version") == spec.get("version") and all(h.get("date") and h.get("change")
                                                                                       for h in history),
         "Aenderungshistorie fehlt oder passt nicht zur Version")
    # nur freigegebene Quellen, und nur die des Falls
    need(bool(spec.get("source_refs")) and set(spec["source_refs"]) <= set(case.get("source_refs", [])),
         "source_refs muessen Quellen des Falls sein")
    for ref in spec.get("source_refs", []):
        need(register.get(ref, {}).get("nutzungsrechte", {}).get("status") == "nutzbar", f"Quelle {ref} nicht freigegeben")
    tasks = {t["id"]: t for t in case["reference"]["tasks"]}
    for tid in spec.get("tasks", []):
        need(tid in tasks and tasks[tid].get("analyse"), f"Aufgabe {tid} ist keine Analyse-Aufgabe des Falls")
    need(isinstance(spec.get("alternative_results"), list) and spec.get("alternative_results_status") in ALTERNATIVE_STATUS,
         "alternative_results/alternative_results_status fehlen")
    if spec.get("alternative_results_status") == "festgelegt":
        need(bool(spec.get("alternative_results")), "Alternativergebnisse als festgelegt markiert, aber leer")

    solution = case.get("solution") or {}
    facts = {f["id"]: f for f in case["reference"]["facts"]}
    deadlines = {d["date"] for d in case["reference"]["deadlines"]}
    open_points = {p["id"]: p for p in solution.get("open_points", [])}
    docs_norm = _norm("\n".join(corpus.doc_text(d) for d in case["documents"]))
    dims = {d["id"] for d in rubric["dimensions"]}
    covered, covered_facts, covered_points = set(), set(), []
    for crit in spec.get("criteria", []):
        cid = crit.get("id")
        need(crit.get("dimension") in dims, f"{cid}: unbekannte Dimension {crit.get('dimension')!r}")
        need(crit.get("evaluation") in EVALUATIONS, f"{cid}: Bewertungsart fehlt/unbekannt")
        _terms_ok(crit.get("accept_any"), cid, need)
        covered.add(crit.get("dimension"))
        if crit.get("dimension") == "ergebnis":
            need(crit.get("evaluation") == "juristische_pruefung", f"{cid}: Ergebnis braucht juristische Pruefung")
        if "norm" in crit:
            need(crit["norm"] in solution.get("required_norms", []), f"{cid}: Norm {crit['norm']} nicht in der Referenzloesung")
            need(_norm(crit["norm"]) in docs_norm, f"{cid}: Norm {crit['norm']} steht in keinem Dokument")
        if "fact" in crit:
            need(crit["fact"] in facts and crit["fact"] in solution.get("decisive_facts", []),
                 f"{cid}: Fakt {crit['fact']} ist kein entscheidender Fakt des Falls")
            values = facts.get(crit["fact"], {}).get("values", [])
            need(any(_norm(v) == _norm(" ".join(variant)) for v in values for variant in crit["accept_any"]),
                 f"{cid}: keine Variante entspricht einem Aktenwert von {crit['fact']}")
            covered_facts.add(crit["fact"])
        if "deadline" in crit:
            need(crit["deadline"] in deadlines and crit["deadline"] in solution.get("deadlines", []),
                 f"{cid}: Frist {crit['deadline']} nicht in Referenz/Loesung")
        if "open_point" in crit:
            point = open_points.get(crit["open_point"])
            need(point is not None, f"{cid}: offener Punkt {crit['open_point']} unbekannt")
            if point:
                any_terms = {_norm(t) for t in point["any_terms"]}
                need(all(any(_norm(t) in any_terms for t in variant) for variant in crit["accept_any"]),
                     f"{cid}: jede Variante muss einen Begriff des offenen Punkts {point['id']} enthalten "
                     "(kein Vermischen mehrerer offener Punkte)")
            covered_points.append(crit["open_point"])
    for dim in ("ergebnis", "rechtsgrundlagen", "tatsachen", "fristen", "unsicherheiten"):
        need(dim in covered, f"Dimension {dim} ohne Kriterium")
    excluded = {e.get("ref"): e for e in spec.get("not_assessed", [])}
    for e in excluded.values():
        need(bool(e.get("reason")), f"Ausschluss {e.get('ref')} ohne Begruendung")
    for fid in solution.get("decisive_facts", []):
        need(fid in covered_facts or fid in excluded, f"entscheidender Fakt {fid} der Referenz ohne Kriterium/Ausschluss")
    for pid in open_points:
        need(pid in covered_points or pid in excluded, f"offener Punkt {pid} der Referenz ohne eigenes Kriterium/Ausschluss")
    need(len(covered_points) == len(set(covered_points)), "offener Punkt doppelt bewertet")
    for norm in solution.get("required_norms", []):
        need(any(c.get("norm") == norm for c in spec.get("criteria", [])) or norm in excluded, f"Norm {norm} ohne Kriterium")
    for deadline in solution.get("deadlines", []):
        need(any(c.get("deadline") == deadline for c in spec.get("criteria", [])) or deadline in excluded,
             f"Frist {deadline} ohne Kriterium")
    need(bool(spec.get("dangerous_errors")), "keine gefaehrlichen Fehler definiert")
    for err in spec.get("dangerous_errors", []):
        _terms_ok(err.get("suspect_any"), err.get("id", "?"), need)
    wrong = [_norm(w) for w in solution.get("wrong_results", [])]
    suspects = {_norm(" ".join(v)) for e in spec.get("dangerous_errors", []) for v in e.get("suspect_any", [])}
    for w in wrong:
        need(any(s in w or w in s for s in suspects), f"Fehlergebnis der Referenz {w!r} nicht als Verdacht erfasst")
    need(bool(spec.get("known_wrong_decisions")), "typische Fehlentscheidungen fehlen")
    need("gefaehrliche_fehler" in spec.get("requires_qualified_review", []) and
         "ergebnis" in spec.get("requires_qualified_review", []),
         "Ergebnis und gefaehrliche Fehler muessen als juristisch zu pruefen markiert sein")


def validate_all(root: Path = ROOT, check_locked: bool = True) -> SpecReport:
    report = SpecReport()
    rubric = load_rubric(root)
    validate_rubric(rubric, report)
    register = corpus.validate_register(corpus.load_register(), corpus.ValidationReport())
    cases = {c["id"]: c for c in corpus.load_cases()}
    specs = load_specs(root)
    if len({s["id"] for s in specs}) != len(specs):
        report.errors.append("doppelte Spezifikations-IDs")
    for spec in specs:
        validate_spec(spec, cases, register, rubric, report)
    if check_locked:
        report.errors.extend(check_lock(root))
    return report


# --- Vorpruefung (nur Hinweise) --------------------------------------------------------------------------------------


def _term_found(term: str, text_norm: str) -> bool:
    """Ganzes Wort bzw. ganze Wortfolge (§77) - "zu" trifft nicht "Zum", "§ 4" nicht "§ 41"."""
    return re.search(r"(?<!\w)" + re.escape(_norm(term).strip()) + r"(?!\w)", text_norm) is not None


def _variant_found(variant: list[str], text_norm: str) -> bool:
    return all(_term_found(term, text_norm) for term in variant)


_CITATION_CANDIDATE = re.compile(r"§\s*\d+[a-z]?|\b(?:[IVX]{1,5}|\d{1,3})\s+[A-Z]{1,4}\s+\d+/\d{2}\b|\b\d{1,2}\.\d{1,2}\.\d{4}\b")


def precheck(spec: dict, case: dict, answer: str) -> dict:
    """Vorpruefung einer Antwort - NUR Hinweise fuer die juristische Pruefung, keine Note, kein Urteil."""
    text_norm = _norm(answer)
    criteria = {c["id"]: {"dimension": c["dimension"], "evaluation": c["evaluation"],
                          "status": "hinweis_gefunden" if any(_variant_found(v, text_norm) for v in c["accept_any"])
                          else "nicht_gefunden"} for c in spec["criteria"]}
    dangerous = {e["id"]: "verdacht" if any(_variant_found(v, text_norm) for v in e["suspect_any"]) else "kein_hinweis"
                 for e in spec["dangerous_errors"]}
    known = _norm("\n".join(corpus.doc_text(d) for d in case["documents"]) + "\n"
                  + json.dumps({k: v for k, v in spec.items() if not k.startswith("_")}, ensure_ascii=False)
                  + "\n" + json.dumps(case.get("solution", {}), ensure_ascii=False))
    unverified = sorted({m.group(0) for m in _CITATION_CANDIDATE.finditer(answer) if not _term_found(m.group(0), known)})
    return {
        "art": "vorpruefung",
        "hinweis": "Keine Bewertung: Note je Dimension durch qualifizierte juristische Person (rubric.json).",
        "kriterien": criteria,
        "gefaehrliche_fehler": dangerous,
        "nicht_in_akte_oder_quelle": unverified,  # zur Pruefung auf unbelegte Behauptungen - Existenz NICHT widerlegt
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fingerprints", action="store_true", help="Fingerabdruecke der Faelle/Spezifikationen ausgeben")
    parser.add_argument("--update-lock", action="store_true", help="LOCK.json bewusst neu schreiben (mit --reason)")
    parser.add_argument("--reason", default="", help="Begruendung fuer --update-lock")
    args = parser.parse_args()
    if args.fingerprints:
        cases = {c["id"]: c for c in corpus.load_cases()}
        for spec in load_specs():
            print(spec["id"], spec["case"], "Akte", case_fingerprint(cases[spec["case"]]), "Spezifikation", spec_fingerprint(spec))
        return 0
    if args.update_lock:
        report = validate_all(check_locked=False)
        if not report.ok:
            for error in report.errors:
                print("FEHLER:", error)
            print("Keine Neusperre: Spezifikationen sind nicht gueltig.")
            return 1
        lock = write_lock(args.reason)
        print("LOCK.json geschrieben:", len(lock["entries"]), "Dateien; geaendert:", lock["history"][-1]["changed"])
        return 0
    report = validate_all()
    for error in report.errors:
        print("FEHLER:", error)
    print(f"{len(load_specs())} Spezifikationen - {'OK' if report.ok else 'FEHLER'}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
