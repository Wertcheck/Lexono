"""§83: Pruefpaket der Referenzloesungen fuer die spaetere fachliche Pruefung (J1-J3) - reine Zusammenstellung der
VORHANDENEN Inhalte aus Akten (`solution`, `reference.facts`), Spezifikationen und Quellenregister als Markdown, mit
leeren Entscheidungsfeldern. Keine Bewertung, keine Freigabe, kein Pruefsystem: was hier steht, ist NICHT juristisch
geprueft. Die Datei `benchmarks/legal_answers_v1/pruefpaket/REFERENZLOESUNGEN_ZUR_PRUEFUNG.md` wird aus
`build_review_packet()` erzeugt; ein Test stellt sicher, dass sie zu den Quelldateien passt."""

from __future__ import annotations

import json

from scripts.legal_bench import answer_specs, corpus

PACKET_PATH = answer_specs.ROOT / "pruefpaket" / "REFERENZLOESUNGEN_ZUR_PRUEFUNG.md"
_DECISION = ("| Entscheidung (☐ bestätigt / ☐ korrigiert / ☐ verworfen) | Begründung | Person, Rolle | Datum |\n"
             "|---|---|---|---|\n| | | | |")


def _items(values) -> str:  # noqa: ANN001
    return "\n".join(f"- {v}" for v in values) if values else "- (keine)"


def build_review_packet() -> str:
    cases = {c["id"]: c for c in corpus.load_cases()}
    register = {s["id"]: s for s in json.loads((corpus.CORPUS_DIR / "source_register.json").read_text(encoding="utf-8"))["sources"]}
    out = ["# Referenzlösungen zur fachlichen Prüfung (`legal_answers_v1`)", "",
           "**NICHT juristisch geprüft.** Zusammenstellung der vorhandenen Inhalte für die spätere Prüfung durch eine "
           "qualifizierte juristische Person (J1–J3, `ENTSCHEIDUNGSMATRIX.md`). Die Akten sind synthetisch; Rechtsfrage und "
           "Ergebnis stammen aus amtlichen Leitsätzen. Die Entscheidungsfelder sind absichtlich leer – eingetragene "
           "Entscheidungen ändern nichts an Spezifikation, Sperre oder Gate; Änderungen erfolgen erst danach bewusst "
           "(Versionssprung, begründete Neusperre).", "",
           "Erzeugt aus den Quelldateien durch `scripts/legal_bench/review_packet.py`; `tests/test_s83_pilot_readiness.py` "
           "prüft die Übereinstimmung.", ""]
    for spec in answer_specs.load_specs():
        case = cases[spec["case"]]
        sol = case["solution"]
        facts = {f["id"]: f for f in case["reference"]["facts"]}
        tasks = {t["id"]: t for t in case["reference"]["tasks"]}
        source = sol.get("source", {})
        out += [f"## {spec['id']} – {case['title']} ({spec['case']}, Split `{spec['split']}`)", "",
                f"- Spezifikation: `benchmarks/legal_answers_v1/specs/{spec['_dir_split']}/{spec['id']}.json`",
                f"- Akte: `benchmarks/legal_e2e_v1/cases/{spec['split']}/{spec['case']}.json` (Block `solution`)",
                f"- Status: `{spec['status']}`; Alternativergebnisse: `{spec.get('alternative_results_status')}`",
                f"- Quelle: {source.get('court', '?')} {source.get('aktenzeichen', '?')} vom {source.get('date', '?')} "
                f"({source.get('ecli', 'ohne ECLI')}), verwendet: {source.get('used', '?')}",
                f"- Quellenumfang laut Spezifikation: {spec.get('source_scope', '?')}",
                "- Register: " + "; ".join(f"{r} = {register.get(r, {}).get('name', 'FEHLT im Register')}"
                                          for r in spec.get("source_refs", [])), "",
                "**Aufgaben:**", _items([f"{t} ({tasks[t]['kind']}): {tasks[t]['question']}" for t in spec["tasks"]]), "",
                "### J1 – Referenzlösung", "",
                f"**Rechtsfrage:** {sol.get('question')}", "",
                f"**Erwartetes Ergebnis:** {sol.get('expected_result')}", "",
                "**Tragende Normen:**", _items(sol.get("required_norms")), "",
                "**Entscheidende Tatsachen:**",
                _items([f"{fid}: {facts[fid]['statement']} ({', '.join(facts[fid]['values'])})" if fid in facts
                        else f"{fid}: FEHLT in reference.facts" for fid in sol.get("decisive_facts", [])]), "",
                "**Fristen der Referenz:**", _items(sol.get("deadlines")), "",
                "**Falsche Ergebnisse (als Verdacht erfasst):**", _items(sol.get("wrong_results")), "",
                "**Offene Punkte der Referenz:**", _items([f"{o['id']}: {o['topic']}" for o in sol.get("open_points", [])]), "",
                "**Herkunft:**", _items([f"belegt: {b}" for b in sol.get("provenance", {}).get("belegt", [])]
                                       + [f"synthetisch: {b}" for b in sol.get("provenance", {}).get("synthetisch", [])]), "",
                _DECISION, "",
                "### J2 – Kriterien, Antwortvarianten, gefährliche Fehler", "",
                "| Kriterium | Dimension | Aussage | zulässige Varianten (`accept_any`) |", "|---|---|---|---|"]
        for crit in spec["criteria"]:
            variants = " / ".join("+".join(v) for v in crit.get("accept_any") or [])
            out.append(f"| {crit['id']} | {crit['dimension']} | {crit.get('statement', '')} | {variants} |")
        out += ["", "| Gefährlicher Fehler | Aussage | Verdachtsformulierungen |", "|---|---|---|"]
        for err in spec["dangerous_errors"]:
            out.append(f"| {err['id']} | {err.get('statement', '')} | {' / '.join('+'.join(v) for v in err.get('suspect_any') or [])} |")
        out += ["", "**Typische Fehlentscheidungen:**", _items(spec.get("known_wrong_decisions")), "", _DECISION, "",
                "### J3 – Alternativergebnisse", "",
                f"Derzeit: {spec.get('alternative_results') or 'keine eingetragen'} (Status "
                f"`{spec.get('alternative_results_status')}`). Festzulegen: vertretbare Alternativen oder ausdrücklich keine.",
                "", _DECISION, ""]
    return "\n".join(out).rstrip() + "\n"


def write_review_packet() -> None:
    PACKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    PACKET_PATH.write_text(build_review_packet(), encoding="utf-8")
