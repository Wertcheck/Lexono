"""P2.1-Messung: Dokumentklassifikation und Fristvorschlaege im ECHTEN Verarbeitungspfad (Import -> Text/OCR ->
Klassifikation -> Fristenanalyse, `DocumentProcessingService`) ueber den Korpus `legal_e2e_v1`. Dev und Test werden
getrennt ausgewiesen; entwickelt wird nur an Dev. Rein lokal: kein Modellaufruf, keine Netzwerkverbindung.

Aufruf: python -m scripts.legal_bench.p21_measure --out <datei.json>

Bewertung Klassifikation (zwei Stufen, siehe PRIVACY_REVIEW_ARCHITECTURE.md §40):
- Kerntypen mit eindeutiger Entsprechung (CORE): Praezision und Recall je Lexono-Typ.
- Korrespondenz-/Rollentypen (Schreiben, E-Mail, Notiz, Anlage) haben keinen einzig richtigen Lexono-Typ: kein Recall;
  ein Kerntyp darauf zaehlt als Fehlklassifikation, alles andere wird einzeln zur Sichtpruefung ausgegeben.

Bewertung Fristen (je Fall, Datum TT.MM.JJJJ):
- richtig: Datum einer Referenzfrist; uebersehen: Referenzfrist ohne Vorschlag;
- Ereignisdatum als Frist: Datum aus Chronologie, Dokumentdatum oder datiertem Widerspruch;
- persoenliches Datum als Frist (kritisch); ungeklaert: weder Referenz noch bekanntes Falldatum.
- Quellenbezug: Referenzfrist im richtigen Quelldokument vorgeschlagen.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

#: Korpustyp -> zulaessige Lexono-Typen (Kerntypen)
CORE = {
    "vertrag": {"Vertrag"},
    "rechnung": {"Rechnung"},
    "klageschrift": {"Klage/Schriftsatz"},
    "klageerwiderung": {"Klage/Schriftsatz"},
    "replik": {"Klage/Schriftsatz"},
    "bescheid": {"Bescheid", "Steuerbescheid"},
    "urteil": {"Urteil/Beschluss"},
    "gutachten": {"Gutachten"},
    "protokoll": {"Protokoll"},
}
#: Lexono-Typen, die einem Kerntyp entsprechen - auf einem Rollendokument eine Fehlklassifikation
CORE_LABELS = set().union(*CORE.values())
UNKNOWN = {"Unbekannt", "Sonstiges"}


def _classification(rows: list[dict]) -> dict:
    by_label_pred: Counter = Counter()
    by_label_true: Counter = Counter()
    tp: Counter = Counter()
    errors, role_assignments = [], []
    for row in rows:
        predicted, ref = row["classified"], row["type_reference"]
        if ref in CORE:
            expected = sorted(CORE[ref])[0] if len(CORE[ref]) == 1 else "Bescheid"
            by_label_true[expected] += 1
            if predicted in CORE[ref]:
                tp[expected] += 1
                by_label_pred[expected] += 1
            else:
                errors.append({**_ref(row), "kind": "unbekannt" if predicted in UNKNOWN else "falsch"})
                if predicted in CORE_LABELS:
                    by_label_pred[predicted] += 1
        else:
            if predicted in CORE_LABELS:
                by_label_pred[predicted] += 1
                errors.append({**_ref(row), "kind": "kerntyp_auf_rollendokument"})
            else:
                role_assignments.append(_ref(row))
    labels = sorted(set(by_label_true) | set(by_label_pred))
    per_type = {label: {"praezision": f"{tp[label]}/{by_label_pred[label]}", "recall": f"{tp[label]}/{by_label_true[label]}"}
                for label in labels}
    return {
        "je_typ": per_type,
        "kerntyp_dokumente": sum(by_label_true.values()),
        "kerntyp_richtig": sum(tp.values()),
        "unbekannt_gesamt": f"{sum(1 for r in rows if r['classified'] in UNKNOWN)}/{len(rows)}",
        "fehler": errors,
        "rollendokumente_zur_sichtpruefung": role_assignments,
    }


def _ref(row: dict) -> dict:
    return {"doc": f"{row['case']}/{row['doc']}", "referenz": row["type_reference"], "lexono": row["classified"]}


def _known_dates(case: dict) -> set[str]:
    ref = case["reference"]
    return {e["date"] for e in ref["chronology"]} | {d["date"] for d in case["documents"] if d.get("date")} | {
        v["value"] for w in ref["contradictions"] for v in w["values"] if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", v["value"])}


def run(cases: list[dict], workdir: Path) -> dict:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.documents.service import DocumentProcessingService
    from app.ingestion.intake import IntakeService
    from app.models import Client, Deadline, Matter
    from app.models.base import Base
    from scripts.legal_bench.e2e import _tesseract
    from scripts.legal_bench.render import render_case

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    tesseract = _tesseract()
    processing = DocumentProcessingService(ocr_enabled=tesseract is not None, tesseract_cmd=tesseract)
    intake = IntakeService(workdir / "intake")

    out: dict = {"ocr": tesseract is not None, "splits": {}}
    rows_by_split: dict[str, list] = defaultdict(list)
    deadlines_by_split: dict[str, list] = defaultdict(list)
    seconds: dict[str, float] = defaultdict(float)
    for case in cases:
        client = Client(name=f"P21 {case['id']}")
        matter = Matter(client=client, title=case["matter"]["title"])
        db.add_all([client, matter])
        db.commit()
        files = render_case(case, workdir / "rendered")
        doc_ids = {}
        for spec in case["documents"]:
            t0 = time.perf_counter()
            document = intake.ingest_file(files[spec["id"]], db, stability_timeout_seconds=5.0)
            document.matter_id = matter.id
            db.commit()
            processing.process_document(document, db, actor="p21")
            db.refresh(document)
            seconds[case["split"]] += time.perf_counter() - t0
            doc_ids[document.id] = spec["id"]
            rows_by_split[case["split"]].append({"case": case["id"], "doc": spec["id"], "type_reference": spec["type"],
                                                 "classified": document.classified_type or "Unbekannt",
                                                 "format": spec["format"]})
        suggestions = [d for d in db.query(Deadline).filter(Deadline.matter_id == matter.id)]
        ref = case["reference"]
        expected = {d["date"]: d["doc"] for d in ref["deadlines"]}
        known = _known_dates(case)
        personal = {p["value"] for p in ref["privacy"]["protect"] if p["type"] == "PERS_DATUM"}
        def kind_of(d) -> str:  # noqa: ANN001 - P2.1: Art laut Begruendung; vor P2.1 gab es nur "Frist"
            reasoning = d.reasoning or ""
            return next((k for k in ("Termin", "Fristbeginn", "Fristdauer") if f"Art: {k}" in reasoning), "Frist")

        dated_all = [(d.due_date.strftime("%d.%m.%Y"), doc_ids.get(d.document_id), d) for d in suggestions if d.due_date]
        # Fristbewertung nur fuer Vorschlaege, die als FRIST ausgegeben werden; Termine werden getrennt gezaehlt
        dated = [x for x in dated_all if kind_of(x[2]) == "Frist"]
        appointments = [x for x in dated_all if kind_of(x[2]) == "Termin"]
        found_dates = {date for date, _doc, _d in dated}
        deadlines_by_split[case["split"]].append({
            "case": case["id"],
            "vorschlaege": len(suggestions),
            "ohne_datum": sum(1 for d in suggestions if d.due_date is None),
            "je_art": dict(Counter(kind_of(d) for d in suggestions)),
            "termine": sorted({date for date, _doc, _d in appointments}),
            "richtig": sorted(set(expected) & found_dates),
            "uebersehen": sorted(set(expected) - found_dates),
            "quellenbezug_richtig": sorted({date for date, doc, _d in dated if expected.get(date) == doc}),
            "ereignisdatum_als_frist": sorted({date for date, _doc, _d in dated if date not in expected and date in known}),
            "persoenliches_datum_als_frist": sorted(found_dates & personal),
            "ungeklaert": sorted({date for date, _doc, _d in dated
                                  if date not in expected and date not in known and date not in personal}),
            "einzeln": [{"datum": date, "doc": doc, "art": kind_of(d), "konfidenz": d.confidence,
                         "stelle": (d.source_text or "")[:120]}
                        for date, doc, d in sorted(dated_all, key=lambda x: (x[1] or "", x[0]))],
            "ohne_datum_einzeln": [{"doc": doc_ids.get(d.document_id), "art": kind_of(d), "angabe": (d.source_text or "")[:100]}
                                   for d in suggestions if d.due_date is None],
        })
    for split in sorted(rows_by_split):
        dl = deadlines_by_split[split]
        total = lambda key: sum(len(r[key]) for r in dl)  # noqa: E731
        out["splits"][split] = {
            "klassifikation": _classification(rows_by_split[split]),
            "fristen": {
                "referenzfristen": sum(len(r["richtig"]) + len(r["uebersehen"]) for r in dl),
                "richtig": total("richtig"),
                "uebersehen": total("uebersehen"),
                "quellenbezug_richtig": total("quellenbezug_richtig"),
                "vorschlaege_gesamt": sum(r["vorschlaege"] for r in dl),
                "vorschlaege_je_art": dict(sum((Counter(r["je_art"]) for r in dl), Counter())),
                "als_termin_ausgewiesen": total("termine"),
                "ereignisdatum_als_frist": total("ereignisdatum_als_frist"),
                "persoenliches_datum_als_frist_KRITISCH": total("persoenliches_datum_als_frist"),
                "ungeklaert": total("ungeklaert"),
                "je_fall": dl,
            },
            "laufzeit_sekunden": round(seconds[split], 2),
            "dokumente": rows_by_split[split],
        }
    return out


def main() -> None:
    from scripts.legal_bench.corpus import load_cases

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        result = run(load_cases(), Path(tmp))
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    for split, data in result["splits"].items():
        k, f = data["klassifikation"], data["fristen"]
        print(f"[{split}] Klassifikation: Kerntypen richtig {k['kerntyp_richtig']}/{k['kerntyp_dokumente']}, "
              f"Unbekannt {k['unbekannt_gesamt']}, Fehler {len(k['fehler'])}; je Typ {k['je_typ']}")
        print(f"[{split}] Fristen: richtig {f['richtig']}/{f['referenzfristen']}, Vorschlaege {f['vorschlaege_gesamt']} "
              f"{f['vorschlaege_je_art']}, "
              f"Ereignisdaten {f['ereignisdatum_als_frist']}, ungeklaert {f['ungeklaert']}, "
              f"persoenlich {f['persoenliches_datum_als_frist_KRITISCH']}, Quellenbezug {f['quellenbezug_richtig']}; "
              f"Laufzeit {data['laufzeit_sekunden']} s")


if __name__ == "__main__":
    main()
