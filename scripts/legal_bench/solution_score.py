"""Bewertung gegen die Referenzloesung eines Falls (§71, Block `solution` in benchmarks/legal_e2e_v1/cases).

Explizite, getrennte Kriterien - keine Textaehnlichkeit, keine Modell-Selbstbewertung:

- ergebnis: alle `result_terms` genannt UND keine Formulierung aus `wrong_results` (falsches Ergebnis schlaegt fehl,
  auch wenn die Schluesselbegriffe vorkommen);
- normen: Anteil der `required_norms`;
- fakten: je entscheidendem Fakt (`decisive_facts` -> reference.facts) mindestens ein Referenzwert genannt
  (Tatsachentreue/Vollstaendigkeit);
- fristen: alle `deadlines` (Fristengenauigkeit - nur Datumsgleichheit);
- offene_punkte: je fehlender/widerspruechlicher Angabe mindestens ein Begriff aus `any_terms` (Umgang mit Luecken).

Zwei Anwendungen:
1. `score_answer(case, antwort)`: Antwort eines Modells oder einer Person bewerten. Mit dem Offline-Runner (kein echtes
   Modell) ist die juristische Qualitaet echter Antworten NICHT VERIFIZIERT - das Modul ist fuer spaetere Laeufe und mit
   Referenz-/Gegenbeispielen getestet.
2. `payload_basis(case, payload)`: ob der TATSAECHLICH gesendete Payload die fuer die Referenzloesung noetigen Fakten,
   Normen, Fristen und Hinweise auf offene Punkte enthaelt (Kontextvollstaendigkeit von Lexono, messbar ohne Modell).
Datenschutz wird NICHT hier bewertet (getrennt im Runner: Lecks, fremde Akten)."""

from __future__ import annotations

import re
import unicodedata


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = text.replace("–", "-").replace("—", "-").replace(" ", " ")
    return re.sub(r"\s+", " ", text).lower()


def _has(term: str, text_norm: str) -> bool:
    return _norm(term) in text_norm


def _facts(case: dict) -> dict[str, dict]:
    return {fact["id"]: fact for fact in case["reference"]["facts"]}


def _criteria(case: dict, text: str) -> dict:
    solution = case["solution"]
    text_norm = _norm(text)
    facts = _facts(case)
    norms = {n: _has(n, text_norm) for n in solution["required_norms"]}
    fact_hits = {fid: any(_has(v, text_norm) for v in facts[fid]["values"]) for fid in solution["decisive_facts"]}
    deadlines = {d: _has(d, text_norm) for d in solution.get("deadlines", [])}
    open_points = {p["id"]: any(_has(t, text_norm) for t in p["any_terms"]) for p in solution.get("open_points", [])}
    return {"text_norm": text_norm, "normen": norms, "fakten": fact_hits, "fristen": deadlines,
            "offene_punkte": open_points}


def _ratio(values: dict) -> str:
    return f"{sum(1 for v in values.values() if v)}/{len(values)}"


def score_answer(case: dict, answer: str) -> dict:
    solution = case["solution"]
    c = _criteria(case, answer)
    terms = {t: _has(t, c["text_norm"]) for t in solution["result_terms"]}
    wrong = [w for w in solution.get("wrong_results", []) if _has(w, c["text_norm"])]
    ergebnis_ok = all(terms.values()) and not wrong
    result = {
        "ergebnis": {"ok": ergebnis_ok, "begriffe": terms, "falsches_ergebnis": wrong},
        "normen": _ratio(c["normen"]), "normen_fehlend": [k for k, v in c["normen"].items() if not v],
        "fakten": _ratio(c["fakten"]), "fakten_fehlend": [k for k, v in c["fakten"].items() if not v],
        "fristen": _ratio(c["fristen"]), "fristen_fehlend": [k for k, v in c["fristen"].items() if not v],
        "offene_punkte": _ratio(c["offene_punkte"]),
        "offene_punkte_fehlend": [k for k, v in c["offene_punkte"].items() if not v],
    }
    result["bestanden"] = (ergebnis_ok and not result["normen_fehlend"] and not result["fakten_fehlend"]
                           and not result["fristen_fehlend"] and not result["offene_punkte_fehlend"])
    return result


def payload_basis(case: dict, payload: str) -> dict:
    """Enthaelt der gesendete Payload die Grundlage der Referenzloesung? (pseudonymisierter Text - Werte wie Daten,
    Betraege, Normen und Fachbegriffe muessen lesbar sein; personenbezogene Werte sind bewusst nicht Teil davon)."""
    c = _criteria(case, payload)
    result = {
        "normen": _ratio(c["normen"]), "normen_fehlend": [k for k, v in c["normen"].items() if not v],
        "fakten": _ratio(c["fakten"]), "fakten_fehlend": [k for k, v in c["fakten"].items() if not v],
        "fristen": _ratio(c["fristen"]), "fristen_fehlend": [k for k, v in c["fristen"].items() if not v],
        "offene_punkte": _ratio(c["offene_punkte"]),
        "offene_punkte_fehlend": [k for k, v in c["offene_punkte"].items() if not v],
    }
    result["vollstaendig"] = not (result["normen_fehlend"] or result["fakten_fehlend"] or result["fristen_fehlend"]
                                  or result["offene_punkte_fehlend"])
    return result
