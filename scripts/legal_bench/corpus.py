"""Korpus des "Realistic Legal Benchmark & End-to-End Test System" (benchmarks/legal_e2e_v1): Laden und Validieren.

Ein Fall ist eine synthetische Akte (Parteien, Dokumente mit Text) plus Referenzdaten (Fakten, Chronologie, Fristen,
Fundstellen, Widersprueche, Datenschutzanforderungen, Aufgaben). Grundregeln, die der Validator ERZWINGT:

- Keine erfundenen Referenzwerte: jeder Referenzwert (Faktenwerte, Daten, Fristen, Fundstellen, Widerspruchswerte,
  zu schuetzende und zu erhaltende Werte) muss woertlich im Text des angegebenen Dokuments (bzw. irgendeines Dokuments
  des Falls) stehen.
- Jede reale Person des Falls (Mandant, Gegner, Zeugen, Dritte) ist als zu schuetzender Wert erfasst.
- Getrennte Splits: Entwicklungsfaelle (dev, IDs "LE-D...") und unabhaengige Testfaelle (test, IDs "LE-T..."). Testfaelle
  werden nicht zur Abstimmung von Regeln verwendet; der Runner meldet sie getrennt.
- Herkunft: synthetische Faelle sind gekennzeichnet (`synthetic: true`). Ein nicht-synthetischer Fall darf nur Quellen aus
  dem Quellenregister verwenden, deren Nutzungsrechte geprueft und freigegeben sind (Status "nutzbar").

Aufruf:  python -m scripts.legal_bench.corpus      (validiert den Korpus, Exitcode 1 bei Fehlern)
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = ROOT / "benchmarks" / "legal_e2e_v1"

DOC_TYPES = {"klageschrift", "klageerwiderung", "replik", "anlage", "schreiben", "email", "protokoll", "vertrag",
             "rechnung", "gutachten", "bescheid", "notiz", "urteil"}
DOC_FORMATS = {"txt", "docx", "pdf", "scan"}
PARTY_ROLES = {"mandant", "gegner", "zeuge", "dritter", "anwalt_gegner", "gericht", "kanzlei"}
PROTECTED_ROLES = {"mandant", "gegner", "zeuge", "dritter"}
PROTECT_TYPES = {"PERSON", "ADRESSE", "ORT", "EMAIL", "TELEFON", "IBAN", "KENNUNG", "PERS_DATUM", "ORGANISATION", "URL"}
TASK_KINDS = {"chat", "schriftsatz", "suche", "fristen"}
RIGHTS_STATUS = {"nicht_geprueft", "nutzbar", "eingeschraenkt", "nicht_nutzbar"}


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def table_lines(document: dict) -> list[str]:
    """Optionale Tabelle eines Dokuments ({"header": [...], "rows": [[...]]}) als "Zelle | Zelle" - dieselbe Darstellung,
    die die DOCX-Extraktion liefert (app/documents/extraction.py::_docx_block_lines)."""
    table = document.get("table")
    if not table:
        return []
    return [" | ".join(row) for row in [table["header"], *table["rows"]]]


def doc_text(document: dict) -> str:
    return "\n".join([*document.get("paragraphs", []), *table_lines(document)])


def load_register(corpus_dir: Path = CORPUS_DIR) -> dict:
    return json.loads((corpus_dir / "source_register.json").read_text(encoding="utf-8"))


def load_cases(corpus_dir: Path = CORPUS_DIR, split: str | None = None) -> list[dict]:
    cases = []
    for path in sorted((corpus_dir / "cases").glob("*/*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        case["_path"] = str(path.relative_to(corpus_dir))
        case["_dir_split"] = path.parent.name
        if split is None or case.get("split") == split:
            cases.append(case)
    return cases


def _require(report: ValidationReport, condition: bool, message: str) -> None:
    if not condition:
        report.errors.append(message)


def validate_register(register: dict, report: ValidationReport) -> dict[str, dict]:
    entries = {}
    for entry in register.get("sources", []):
        sid = entry.get("id", "?")
        for key in ("id", "name", "url", "type", "herkunft", "umfang", "nutzungsrechte"):
            _require(report, key in entry, f"Register {sid}: Feld '{key}' fehlt")
        rights = entry.get("nutzungsrechte", {})
        _require(report, rights.get("status") in RIGHTS_STATUS, f"Register {sid}: unbekannter Rechtestatus {rights.get('status')!r}")
        if rights.get("status") == "nutzbar":
            _require(report, bool(rights.get("geprueft_am")) and bool(rights.get("begruendung")),
                     f"Register {sid}: 'nutzbar' ohne Pruefdatum/Begruendung")
        _require(report, sid not in entries, f"Register {sid}: doppelte ID")
        entries[sid] = entry
    return entries


def validate_case(case: dict, register: dict[str, dict], report: ValidationReport) -> None:
    cid = case.get("id", "?")
    prefix = f"Fall {cid}"
    for key in ("id", "split", "version", "title", "rechtsgebiet", "synthetic", "parties", "matter", "documents", "reference"):
        _require(report, key in case, f"{prefix}: Feld '{key}' fehlt")
    if report.errors and any(e.startswith(prefix) and "fehlt" in e for e in report.errors):
        return
    split = case["split"]
    _require(report, split in ("dev", "test"), f"{prefix}: split muss 'dev' oder 'test' sein")
    _require(report, case["_dir_split"] == split, f"{prefix}: liegt im Ordner '{case['_dir_split']}', split ist '{split}'")
    _require(report, cid.startswith("LE-D" if split == "dev" else "LE-T"), f"{prefix}: ID-Praefix passt nicht zum Split")

    # Herkunft
    if case["synthetic"]:
        _require(report, not case.get("source_refs"), f"{prefix}: synthetischer Fall mit source_refs - als 'abgeleitet' kennzeichnen")
    for ref in case.get("source_refs", []):
        entry = register.get(ref)
        _require(report, entry is not None, f"{prefix}: Quelle {ref} nicht im Register")
        if entry is not None:
            _require(report, entry["nutzungsrechte"]["status"] == "nutzbar",
                     f"{prefix}: Quelle {ref} ohne geprueftes Nutzungsrecht (Status {entry['nutzungsrechte']['status']})")

    # Parteien und Dokumente
    party_ids = set()
    for party in case["parties"]:
        _require(report, party.get("role") in PARTY_ROLES, f"{prefix}: Partei {party.get('id')} mit unbekannter Rolle")
        _require(report, party.get("id") not in party_ids, f"{prefix}: doppelte Partei-ID {party.get('id')}")
        party_ids.add(party.get("id"))
    _require(report, case["matter"].get("client_party") in party_ids, f"{prefix}: matter.client_party unbekannt")
    docs: dict[str, dict] = {}
    for document in case["documents"]:
        did = document.get("id", "?")
        _require(report, did not in docs, f"{prefix}: doppelte Dokument-ID {did}")
        _require(report, document.get("type") in DOC_TYPES, f"{prefix}/{did}: unbekannter Dokumenttyp {document.get('type')!r}")
        _require(report, document.get("format") in DOC_FORMATS, f"{prefix}/{did}: unbekanntes Format {document.get('format')!r}")
        _require(report, bool(document.get("filename")), f"{prefix}/{did}: filename fehlt")
        _require(report, bool(doc_text(document).strip()), f"{prefix}/{did}: kein Text")
        docs[did] = document
    all_text = "\n".join(doc_text(d) for d in docs.values())
    # optionales Kanzlei-Aktenzeichen der Akte (Matter.reference_number) - muss in einem Dokument stehen
    reference_number = case["matter"].get("reference_number")
    if reference_number is not None:
        _require(report, reference_number in all_text, f"{prefix}: matter.reference_number {reference_number!r} steht in keinem Dokument")

    def in_doc(value: str, did: str | None, where: str) -> None:
        if did is not None:
            _require(report, did in docs, f"{prefix}: {where} verweist auf unbekanntes Dokument {did}")
            if did in docs:
                _require(report, value in doc_text(docs[did]), f"{prefix}: {where}: Wert {value!r} steht nicht in {did}")
        else:
            _require(report, value in all_text, f"{prefix}: {where}: Wert {value!r} steht in keinem Dokument")

    ref = case["reference"]
    fact_ids = set()
    for fact in ref.get("facts", []):
        fact_ids.add(fact["id"])
        _require(report, bool(fact.get("values")), f"{prefix}: Fakt {fact['id']} ohne Werte")
        for value in fact.get("values", []):
            if fact.get("docs"):
                _require(report, any(value in doc_text(docs.get(d, {})) for d in fact["docs"]),
                         f"{prefix}: Fakt {fact['id']}: Wert {value!r} steht in keinem der Dokumente {fact['docs']}")
            else:
                in_doc(value, None, f"Fakt {fact['id']}")
    for item in ref.get("chronology", []):
        in_doc(item["date"], item.get("doc"), f"Chronologie {item.get('event', '')[:30]!r}")
    for item in ref.get("deadlines", []):
        in_doc(item["date"], item.get("doc"), f"Frist {item.get('kind', '')!r}")
    for item in ref.get("citations", []):
        in_doc(item["text"], item.get("doc"), "Fundstelle")
        _require(report, item.get("kind") in ("norm", "entscheidung", "literatur"), f"{prefix}: Fundstelle mit unbekanntem Typ")
        if item.get("kind") != "norm":
            _require(report, bool(item.get("verified_source")),
                     f"{prefix}: Entscheidung/Literatur {item['text']!r} ohne belegte Quelle - nicht erfinden")
    for contradiction in ref.get("contradictions", []):
        values = contradiction.get("values", [])
        _require(report, len({v["doc"] for v in values}) >= 2, f"{prefix}: Widerspruch {contradiction['id']} braucht >= 2 Dokumente")
        _require(report, len({v["value"] for v in values}) >= 2, f"{prefix}: Widerspruch {contradiction['id']} ohne abweichende Werte")
        for v in values:
            in_doc(v["value"], v["doc"], f"Widerspruch {contradiction['id']}")
    privacy = ref.get("privacy", {})
    protect_values = set()
    for entry in privacy.get("protect", []):
        _require(report, entry.get("type") in PROTECT_TYPES, f"{prefix}: Schutzwert mit unbekanntem Typ {entry.get('type')!r}")
        in_doc(entry["value"], None, "Schutzwert")
        protect_values.add(entry["value"])
    for value in privacy.get("keep", []):
        in_doc(value, None, "zu erhaltender Wert")
        _require(report, value not in protect_values, f"{prefix}: {value!r} ist zugleich Schutz- und Erhaltwert")
    for party in case["parties"]:
        if party["role"] in PROTECTED_ROLES:
            _require(report, party["name"] in protect_values, f"{prefix}: Person {party['name']!r} ({party['role']}) fehlt in privacy.protect")
            _require(report, party["name"] in all_text, f"{prefix}: Person {party['name']!r} kommt in keinem Dokument vor")
    for task in ref.get("tasks", []):
        _require(report, task.get("kind") in TASK_KINDS, f"{prefix}: Aufgabe {task.get('id')} mit unbekannter Art")
        for did in task.get("attach", []):
            _require(report, did in docs, f"{prefix}: Aufgabe {task['id']} haengt unbekanntes Dokument {did} an")
        for fid in task.get("needed_facts", []):
            _require(report, fid in fact_ids, f"{prefix}: Aufgabe {task['id']} braucht unbekannten Fakt {fid}")
    _validate_solution(case, prefix, fact_ids, all_text, report)


def _validate_solution(case: dict, prefix: str, fact_ids: set, all_text: str, report: ValidationReport) -> None:
    """§71: Referenzloesung (Block `solution`) - nur fuer aus freigegebenen Quellen abgeleitete Faelle, mit belegter
    Referenzentscheidung im Fall; jedes Kriterium muss aus den Akten ableitbar sein (keine erfundenen Begriffe/Werte)."""
    solution = case.get("solution")
    if solution is None:
        return
    _require(report, not case["synthetic"] and case.get("derivation") == "abgeleitet",
             f"{prefix}: Referenzloesung nur fuer abgeleitete Faelle (synthetic=false, derivation='abgeleitet')")
    source = solution.get("source", {})
    _require(report, source.get("register") in case.get("source_refs", []), f"{prefix}: Loesungsquelle nicht in source_refs")
    _require(report, bool(source.get("ecli")) and bool(source.get("aktenzeichen")), f"{prefix}: Loesungsquelle ohne ECLI/Az.")
    citations = case["reference"].get("citations", [])
    _require(report, any(c.get("kind") == "entscheidung" and source.get("aktenzeichen", "?") in c["text"]
                         and c.get("verified_source", {}).get("ecli") == source.get("ecli") for c in citations),
             f"{prefix}: Referenzentscheidung nicht als belegtes Zitat im Fall")
    for key in ("question", "expected_result"):
        _require(report, bool(solution.get(key)), f"{prefix}: Referenzloesung ohne '{key}'")
    _require(report, bool(solution.get("result_terms")), f"{prefix}: Referenzloesung ohne result_terms")
    for term in solution.get("result_terms", []):
        _require(report, term.lower() in solution.get("expected_result", "").lower(),
                 f"{prefix}: result_term {term!r} steht nicht im erwarteten Ergebnis")
    for fid in solution.get("decisive_facts", []):
        _require(report, fid in fact_ids, f"{prefix}: entscheidender Fakt {fid} unbekannt")
    for norm in solution.get("required_norms", []):
        _require(report, norm in all_text, f"{prefix}: Norm {norm!r} steht in keinem Dokument")
    reference_deadlines = {d["date"] for d in case["reference"].get("deadlines", [])}
    for date in solution.get("deadlines", []):
        _require(report, date in reference_deadlines, f"{prefix}: Loesungsfrist {date} nicht in reference.deadlines")
    for point in solution.get("open_points", []):
        _require(report, bool(point.get("any_terms")), f"{prefix}: offener Punkt {point.get('id')} ohne Begriffe")
        for term in point.get("any_terms", []):
            _require(report, term in all_text, f"{prefix}: offener Punkt {point.get('id')}: {term!r} steht in keinem Dokument")
    _require(report, any(t.get("analyse") for t in case["reference"].get("tasks", [])),
             f"{prefix}: Referenzloesung ohne Analyseaufgabe (analyse: true)")


def validate_corpus(corpus_dir: Path = CORPUS_DIR) -> ValidationReport:
    report = ValidationReport()
    register = validate_register(load_register(corpus_dir), report)
    cases = load_cases(corpus_dir)
    seen: set[str] = set()
    for case in cases:
        _require(report, case.get("id") not in seen, f"doppelte Fall-ID {case.get('id')}")
        seen.add(case.get("id"))
        validate_case(case, register, report)
    if not any(c.get("split") == "test" for c in cases):
        report.warnings.append("kein unabhaengiger Testfall (split=test) vorhanden")
    # Personen duerfen nicht fallübergreifend gleich heissen (Mandantentrennung messbar halten)
    names: dict[str, str] = {}
    for case in cases:
        for party in case.get("parties", []):
            if party.get("role") in PROTECTED_ROLES:
                other = names.get(party["name"])
                _require(report, other in (None, case["id"]), f"Person {party['name']!r} kommt in {other} und {case['id']} vor")
                names[party["name"]] = case["id"]
    return report


def main() -> int:
    report = validate_corpus()
    for warning in report.warnings:
        print("WARNUNG:", warning)
    for error in report.errors:
        print("FEHLER:", error)
    cases = load_cases()
    print(f"{len(cases)} Faelle ({sum(c['split'] == 'dev' for c in cases)} dev, {sum(c['split'] == 'test' for c in cases)} test), "
          f"{len(load_register()['sources'])} Registerquellen - {'OK' if report.ok else 'FEHLER'}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
