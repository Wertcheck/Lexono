"""Adversarialer Pseudonymisierungs-Benchmark (§31): Precision/Recall/F1 je Entitaetstyp, Klartext im tatsaechlichen
Payload, Blockaden, Platzhalterkonsistenz, Faktenerhalt, Fehler je Dokumenttyp und Pfad. Nur synthetische Daten aus
benchmarks/pseudonymization_v1/cases.json. Kein externer Aufruf: der Schreib-Provider ist ein Fake, der den Payload
unmittelbar vor dem Cloud-Aufruf erfasst.

Aufruf:  python scripts/bench_pseudonymization.py   -> benchmarks/pseudonymization_v1/results.json
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from app.privacy.gateway import ClaudePrivacyGateway  # noqa: E402

BENCH_DIR = ROOT / "benchmarks" / "pseudonymization_v1"
TYPE_MAP = {
    "person": "PERSON", "mandant": "PERSON", "gegner": "PERSON", "anwalt": "PERSON", "beteiligter": "PERSON",
    "organisation": "ORGANISATION", "gericht": "ORGANISATION", "ort": "ORT", "adresse": "ADRESSE", "email": "EMAIL",
    "telefon": "TELEFON", "iban": "IBAN", "bic": "BIC", "kennung": "KENNUNG", "steuer_id": "KENNUNG",
    "kundennummer": "KENNUNG", "vertrag": "KENNUNG", "rechnungsnummer": "KENNUNG", "aktenzeichen": "KENNUNG",
    "grundbuch": "KENNUNG", "geburtsdatum": "PERS_DATUM", "geburtstag": "PERS_DATUM", "todesdatum": "PERS_DATUM",
    "url": "URL",
}
_FINGERPRINT_FILES = ("app/privacy/gateway.py", "app/privacy/pseudonymizer.py", "app/privacy/detectors.py",
                      "app/privacy/presidio_ner.py", "app/privacy/review.py", "app/privacy/security_check.py",
                      "app/privacy/gateway_schema.py", "app/drafting/service.py", "app/chat/service.py",
                      "app/ai_providers/local_ai_provider.py")


def norm(text: str) -> str:
    """Vergleichsform: NFC, unsichtbare Zeichen entfernt, Leerraum vereinheitlicht, Kleinschreibung."""
    text = unicodedata.normalize("NFC", text or "")
    text = re.sub(r"[­​‌‍⁠﻿]", "", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _in_text(value: str, haystack_norm: str) -> bool:
    needle = norm(value)
    if not needle:
        return False
    return re.search(rf"(?<![\w]){re.escape(needle)}(?![\w])", haystack_norm) is not None


def _overlaps(a: str, b: str) -> bool:
    """Einer im anderen enthalten - mit Wortgrenzen ("12" trifft nicht "Birkenallee 123", "Mai" nicht "Maier")."""
    a, b = norm(a).strip(" .,;:"), norm(b).strip(" .,;:")
    if not a or not b:
        return False
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return re.search(rf"(?<![\w]){re.escape(short)}(?![\w])", long_) is not None


def payload_text(payload_dump: dict) -> str:
    """Alle String-Werte des Payloads (wie security_check._flatten_payload_text), durch Zeilenumbrueche getrennt."""
    parts: list[str] = []

    def walk(value) -> None:  # noqa: ANN001
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)

    walk(payload_dump)
    return "\n".join(parts)


def load_cases() -> dict:
    return json.loads((BENCH_DIR / "cases.json").read_text(encoding="utf-8"))


def production_known_entities(known: dict | None) -> dict | None:
    """Wie RuleBasedLocalAIProvider._build_known_entities: mehrteilige Namen zusaetzlich mit dem letzten Wort."""
    if not known:
        return None
    result = {}
    for category, names in known.items():
        values = list(names)
        for name in names:
            words = name.split()
            if len(words) > 1 and len(words[-1]) >= 3 and words[-1] not in values:
                values.append(words[-1])
        result[category] = values
    return result


def _call(gateway: ClaudePrivacyGateway, case: dict):
    field, text = case.get("field", "sachverhalt"), case["text"]
    kwargs = {"purpose": "formulate_draft", "sachverhalt": "Akte: Beispielakte",
              "known_entities": production_known_entities(case.get("known_entities")),
              "review_mode": case.get("mode", "mask")}
    if field == "sachverhalt":
        kwargs["sachverhalt"] = "Akte: Beispielakte\n[Schreiben] " + text
    elif field == "anmerkungen":
        kwargs["anwaltliche_anmerkungen"] = text
    elif field == "verlauf":
        kwargs["gespraechsverlauf"] = [text]
    elif field == "quellen":
        kwargs["quellenverweise"] = [text]
    elif field == "argumentationspunkte":
        kwargs["argumentationspunkte"] = [text]
    elif field == "stil":
        kwargs["stil"] = text
    elif field == "vorlage":
        kwargs["vorlage"] = text
    return gateway.prepare_request(**kwargs)


def evaluate(case: dict, allowed: bool, payload_text: str, mappings: list, error: str | None = None) -> dict:
    payload_norm = norm(payload_text)
    predicted: dict[str, str] = {}
    for mapping in mappings:
        if mapping.category in ("textmarke", "platzhaltertext"):
            continue
        predicted.setdefault(mapping.original_value, TYPE_MAP.get(mapping.category, mapping.category.upper()))
    gold = case.get("protect", [])
    items = []
    for entry in gold:
        hit_types = sorted({t for value, t in predicted.items() if _overlaps(value, entry["value"])})
        leaked = allowed and _in_text(entry["value"], payload_norm)
        parts = [p for p in re.split(r"[\s,.-]+", entry["value"]) if len(p) >= 4]
        partial = allowed and not leaked and len(parts) > 1 and any(_in_text(p, payload_norm) for p in parts)
        items.append({"value": entry["value"], "type": entry["type"],
                      # geschuetzt: nicht im Payload (bzw. Anfrage blockiert) - die eigentliche Schutzwirkung
                      "detected": not leaked,
                      "mapping_hit": bool(hit_types), "predicted_types": hit_types,
                      "type_match": entry["type"] in hit_types, "leak": leaked, "partial_leak": partial})
    preds = []
    for value, ptype in predicted.items():
        if any(_overlaps(value, g["value"]) for g in gold):
            verdict = "TP"
        elif any(_overlaps(value, n) for n in case.get("neutral", [])):
            verdict = "ignored"
        else:
            verdict = "FP"
        preds.append({"value": value, "type": ptype, "verdict": verdict,
                      "on_keep": any(_overlaps(value, k) for k in case.get("keep", []))})
    consistency = []
    for group in case.get("consistency", []):
        placeholders = sorted({m.placeholder for m in mappings
                               if m.category not in ("textmarke", "platzhaltertext") and any(_overlaps(m.original_value, v) for v in group)})
        # Payload-Sicht: welche Platzhalter stehen an Stelle der Gruppe? (Anzahl distinkter Platzhalter der Gruppe)
        consistency.append({"group": group, "placeholders": placeholders, "consistent": len(placeholders) == 1})
    expect = case.get("expect", "allow")
    return {
        "id": case["id"], "group": case["group"], "doctype": case["doctype"], "field": case.get("field", "sachverhalt"),
        "known_limit": case.get("known_limit"), "allowed": allowed, "error": error,
        "unnecessary_block": expect == "allow" and not allowed,
        "missing_block": expect == "block" and allowed,
        "protect": items, "predicted": preds,
        "keep_missing": [k for k in case.get("keep", []) if allowed and not _in_text(k, payload_norm)],
        "keep_total": len(case.get("keep", [])) if allowed else 0,
        "consistency": consistency,
    }


def run_field_case(gateway: ClaudePrivacyGateway, case: dict) -> dict:
    try:
        result = _call(gateway, case)
    except Exception as exc:  # noqa: BLE001 - Fehler werden gemessen (fail-closed: kein Payload)
        return evaluate(case, False, "", [], error=type(exc).__name__)
    text = payload_text(result.payload.model_dump()) if result.payload is not None else ""
    return evaluate(case, bool(result.allowed and result.payload is not None), text, list(result.mappings or []))


# --- End-to-End-Pfade (echter Chat-/Drafting-Pfad) -----------------------------------------------------------------

def run_e2e() -> list[dict]:
    from datetime import date

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.ai_providers.claude_writing_provider import ClaudeWritingResult
    from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
    from app.ai_providers.local_llm_provider import LocalAIHealthStatus, LocalLLMResult
    from app.chat.service import ChatService
    from app.drafting.service import DraftingService
    from app.models import Client, Deadline, Document, Matter, Party, Role, User
    from app.models.base import Base
    from app.research.service import LegalResearchService
    from app.search.service import DocumentSearchService
    from fake_embedding_provider import FakeEmbeddingProvider

    class Writer:
        def __init__(self) -> None:
            self.payloads: list = []

        def write(self, payload):  # noqa: ANN001
            self.payloads.append(payload)
            return ClaudeWritingResult(text="Antwort.", token_count=0)

    class EchoLLM:
        """Lokale Vorabanalyse, die (wie ein fehlerhaftes Modell) einen Klarnamen in die Zusammenfassung schreibt."""

        def __init__(self, inject: str) -> None:
            self.inject = inject

        def process(self, payload):  # noqa: ANN001
            return LocalLLMResult(text=f"Zusammenfassung: {self.inject} schuldet den Betrag.", model="bench")

        def check_health(self) -> LocalAIHealthStatus:
            return LocalAIHealthStatus(reachable=True, model_available=True)

        def generate_structured(self, prompt: str, schema: dict) -> dict:
            return {"passed": True, "issues": []}

    def fresh():
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        role = Role(name="anwalt")
        db.add(role)
        db.flush()
        user = User(email="bench@kanzlei.test", password_hash="x", role_id=role.id)
        client = Client(name="Karl Brenner")
        matter = Matter(client=client, title="Brenner Mietsache", practice_area="Mietrecht Wohnraum")
        db.add_all([user, client, matter])
        db.flush()
        db.add(Party(matter_id=matter.id, name="Brunhilde Sasskamp", role="Gegner"))
        db.commit()
        return db, user, matter

    def service(writer, local_llm=None, gateway=None) -> DraftingService:
        search = DocumentSearchService(FakeEmbeddingProvider())
        return DraftingService(RuleBasedLocalAIProvider(search), LegalResearchService(search, min_score_for_sufficient=0.0),
                               search, gateway or ClaudePrivacyGateway(), writer, model_name="bench",
                               local_llm_provider=local_llm)

    def ask(db, user, matter, question, *, attach=(), conversation=None, local_llm=None, gateway=None):
        chat = ChatService(Path(".") / "bench_uploads_unused")
        conversation = conversation or chat.create_conversation(db, user=user, matter_id=matter.id, title="B",
                                                                actor=user.email)
        message = chat.record_user_message(db, conversation=conversation, content=question, document_ids=list(attach))
        writer = Writer()
        reply = chat.send_message(db, conversation=conversation, content=question,
                                  drafting_service=service(writer, local_llm, gateway),
                                  actor=user.email, current_message_id=message.id)
        return conversation, reply, writer

    def record(cid, kind, protect, keep, writer, reply, expect="allow"):
        payload = writer.payloads[-1].model_dump() if writer.payloads else None
        text = norm(payload_text(payload)) if payload else ""
        allowed = payload is not None and not reply.blocked
        return {"id": cid, "kind": kind, "allowed": allowed, "cloud_calls": len(writer.payloads),
                "quellenverweise": len(payload.get("anonymisierte_quellenverweise", [])) if payload else 0,
                "quellen_text": payload.get("anonymisierte_quellenverweise", []) if payload else [],
                "leaks": [v for v in protect if payload is not None and _in_text(v, text)],
                "protect_total": len(protect), "keep_missing": [k for k in keep if allowed and not _in_text(k, text)],
                "keep_total": len(keep) if allowed else 0,
                "expect": expect, "as_expected": (allowed if expect == "allow" else not allowed and not writer.payloads)}

    results = []
    letter = ("Mietvertrag zwischen Karl Brenner, Birkenallee 17, 12345 Musterstadt, und Brunhilde Sasskamp. "
              "Miete 870,00 EUR, zahlbar auf IBAN DE89 3704 0044 0532 0130 00 bis 15.08.2026. "
              "Rückfragen an karl.brenner@beispiel-mail.de oder 0171 2345678.")
    protect_letter = ["Karl Brenner", "Birkenallee 17, 12345 Musterstadt", "Brunhilde Sasskamp",
                      "DE89 3704 0044 0532 0130 00", "karl.brenner@beispiel-mail.de", "0171 2345678"]

    # E01 Anhang (Dokumenttext ueber Aktenkontext)
    db, user, matter = fresh()
    doc = Document(matter_id=matter.id, file_path="v.txt", original_filename="Mietvertrag_Brenner.txt", extracted_text=letter,
                   classified_type="Vertrag")
    db.add(doc)
    db.commit()
    _, reply, writer = ask(db, user, matter, "Wann ist die Miete fällig?", attach=[doc.id])
    results.append(record("E01", "anhang", protect_letter + ["Mietvertrag_Brenner.txt"], ["870,00 EUR", "15.08.2026"],
                          writer, reply))

    # E02 Verlauf: Name + Telefon in einer frueheren Nutzernachricht, Folgefrage ohne Namen
    db, user, matter = fresh()
    conversation, _, _ = ask(db, user, matter, "Frau Sabine Kowalczyk (0171 9876543) fragt nach der Kaution.")
    _, reply, writer = ask(db, user, matter, "Und wie hoch ist die übliche Kaution?", conversation=conversation)
    results.append(record("E02", "verlauf", ["Sabine Kowalczyk", "0171 9876543"], [], writer, reply))

    # E03 Wissenseintrag mit Namen und IBAN (gleiches Rechtsgebiet, fremder Mandant nicht als Akte erfasst)
    db, user, matter = fresh()
    from app.knowledge.schema import KnowledgeItemImport
    from app.knowledge.service import KnowledgeItemService

    ksvc = KnowledgeItemService(search_service=DocumentSearchService(FakeEmbeddingProvider()))
    item = ksvc.import_item(KnowledgeItemImport(
        title="Mietrecht Wohnraum Kaution", practice_area="Mietrecht Wohnraum",
        content="Mietrecht Wohnraum: Kaution 1.500,00 EUR erstattet an Herrn Dietmar Quellbrunn, IBAN DE02 1203 0000 0000 2020 51."),
        db)
    ksvc.approve(item, db, actor="bench")
    _, reply, writer = ask(db, user, matter, "Wie ist die Kautionsrückzahlung geregelt?")
    results.append(record("E03", "wissen", ["Dietmar Quellbrunn", "DE02 1203 0000 0000 2020 51"], ["1.500,00 EUR"], writer, reply))

    # E04 Lokale Vorabanalyse schreibt einen Klarnamen der Akte in ihre Zusammenfassung
    db, user, matter = fresh()
    doc = Document(matter_id=matter.id, file_path="v.txt", extracted_text=letter, classified_type="Vertrag")
    db.add(doc)
    db.commit()
    _, reply, writer = ask(db, user, matter, "Fasse den Vertrag zusammen.", attach=[doc.id], local_llm=EchoLLM("Karl Brenner"))
    results.append(record("E04", "lokale_vorabanalyse", protect_letter, [], writer, reply, expect="either"))

    # E05/E05b Fail-Closed: die Presidio-NER wirft eine Ausnahme - einmal in der Pseudonymisierung, einmal nur im
    # Restrisiko-Scan -> jeweils kein Cloud-Aufruf (regulaere Regex-/Namensregeln bleiben unberuehrt)
    from app.privacy.presidio_ner import get_entity_types, get_pos_tags
    from app.privacy.pseudonymizer import Pseudonymizer
    from app.privacy.security_check import SecurityCheckService

    def failing(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("NER nicht verfuegbar (simuliert)")

    for cid, gateway in (
        ("E05", ClaudePrivacyGateway(pseudonymizer=Pseudonymizer(ner_detector=failing))),
        ("E05b", ClaudePrivacyGateway(security_check=SecurityCheckService(
            ner_detector=failing, pos_tagger=get_pos_tags, entity_type_tagger=get_entity_types))),
    ):
        db, user, matter = fresh()
        doc = Document(matter_id=matter.id, file_path="v.txt", extracted_text=letter, classified_type="Vertrag")
        db.add(doc)
        db.commit()
        _, reply, writer = ask(db, user, matter, "Wann ist die Miete fällig?", attach=[doc.id], gateway=gateway)
        results.append(record(cid, "fail_closed_ner" if cid == "E05" else "fail_closed_restrisiko_ner",
                              protect_letter, [], writer, reply, expect="block"))

    # E06 Fristhinweis aus einer E-Mail (Deadline.source_text) mit Name
    db, user, matter = fresh()
    db.add(Deadline(matter_id=matter.id, source_text="Herr Karl Brenner muss bis 31.08.2026 zahlen",
                    due_date=date(2026, 8, 31)))
    db.commit()
    _, reply, writer = ask(db, user, matter, "Welche Fristen laufen?")
    results.append(record("E06", "frist_aus_email", ["Karl Brenner"], ["31.08.2026"], writer, reply))
    return results


# --- Auswertung ----------------------------------------------------------------------------------------------------

def prf(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else (0.0 if precision == 0 or recall == 0 else None)
    r = lambda x: round(x, 3) if x is not None else None  # noqa: E731
    return {"precision": r(precision), "recall": r(recall), "f1": r(f1), "tp_pred": tp, "fp": fp, "fn": fn}


def summarise(rows: list[dict], e2e: list[dict]) -> dict:
    per_type: dict[str, dict] = defaultdict(lambda: {"gold": 0, "detected": 0, "mapping_hit": 0, "pred_tp": 0, "pred_fp": 0,
                                                     "leaks": 0, "partial_leaks": 0, "type_mismatch": 0})
    for row in rows:
        for item in row["protect"]:
            stats = per_type[item["type"]]
            stats["gold"] += 1
            stats["detected"] += item["detected"]
            stats["mapping_hit"] += item["mapping_hit"]
            stats["leaks"] += item["leak"]
            stats["partial_leaks"] += item["partial_leak"]
            stats["type_mismatch"] += bool(item["predicted_types"]) and not item["type_match"]
        for pred in row["predicted"]:
            if pred["verdict"] == "ignored":
                continue
            stats = per_type[pred["type"]]
            stats["pred_tp" if pred["verdict"] == "TP" else "pred_fp"] += 1
    metrics = {}
    for t, s in sorted(per_type.items()):
        recall = s["detected"] / s["gold"] if s["gold"] else None
        precision = s["pred_tp"] / (s["pred_tp"] + s["pred_fp"]) if s["pred_tp"] + s["pred_fp"] else None
        f1 = (2 * precision * recall / (precision + recall)) if precision and recall else None
        metrics[t] = {"recall": round(recall, 3) if recall is not None else None,
                      "precision": round(precision, 3) if precision is not None else None,
                      "f1": round(f1, 3) if f1 is not None else None,
                      "gold": s["gold"], "protected": s["detected"], "mapping_hit": s["mapping_hit"],
                      "predicted": s["pred_tp"] + s["pred_fp"],
                      "false_positives": s["pred_fp"], "payload_leaks": s["leaks"], "partial_leaks": s["partial_leaks"],
                      "type_mismatch": s["type_mismatch"]}
    by = lambda key: {k: {"cases": sum(1 for r in rows if r[key] == k),  # noqa: E731
                          "cases_with_leak": sum(1 for r in rows if r[key] == k and any(i["leak"] for i in r["protect"])),
                          "false_positives": sum(1 for r in rows if r[key] == k for p in r["predicted"] if p["verdict"] == "FP")}
                      for k in sorted({r[key] for r in rows})}
    gold_total = sum(m["gold"] for m in metrics.values())
    return {
        "field_cases": len(rows),
        "per_type": metrics,
        "overall": {
            "gold": gold_total,
            "protected": sum(m["protected"] for m in metrics.values()),
            "payload_leaks": sum(m["payload_leaks"] for m in metrics.values()),
            "partial_leaks": sum(m["partial_leaks"] for m in metrics.values()),
            "false_positives": sum(m["false_positives"] for m in metrics.values()),
            "false_positives_on_keep": sum(1 for r in rows for p in r["predicted"] if p["verdict"] == "FP" and p["on_keep"]),
            "keep_preserved": f"{sum(r['keep_total'] - len(r['keep_missing']) for r in rows)}/{sum(r['keep_total'] for r in rows)}",
            "unnecessary_blocks": sum(r["unnecessary_block"] for r in rows),
            "missing_blocks": sum(r["missing_block"] for r in rows),
            "blocked": sum(not r["allowed"] for r in rows),
            "errors": sum(1 for r in rows if r["error"]),
            "consistency": f"{sum(c['consistent'] for r in rows for c in r['consistency'])}/{sum(len(r['consistency']) for r in rows)}",
            "cases_with_leak_known_limit": sum(1 for r in rows if r["known_limit"] and any(i["leak"] for i in r["protect"])),
            "cases_with_leak_not_known_limit": [r["id"] for r in rows if not r["known_limit"] and any(i["leak"] for i in r["protect"])],
        },
        "by_doctype": by("doctype"),
        "by_field": by("field"),
        "e2e": {"cases": len(e2e), "as_expected": sum(r["as_expected"] for r in e2e),
                "leaks": sum(len(r["leaks"]) for r in e2e), "protect_total": sum(r["protect_total"] for r in e2e),
                "keep_preserved": f"{sum(r['keep_total'] - len(r['keep_missing']) for r in e2e)}/{sum(r['keep_total'] for r in e2e)}"},
    }


def _git(*args: str) -> str:
    try:
        # nur Zeilenende entfernen: Porcelain-Zeilen beginnen mit einem Leerzeichen (" M pfad")
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.rstrip("\n")
    except Exception:  # noqa: BLE001
        return "unbekannt"


def provenance() -> dict:
    dirty = [p for p in (line[3:].strip('"') for line in _git("status", "--porcelain").splitlines())
             if not p.startswith(("assets/", "benchmarks/pseudonymization_v1/results"))]
    sha = lambda p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()[:16]  # noqa: E731
    return {"git_commit": _git("rev-parse", "--short", "HEAD"), "git_dirty_files": dirty,
            "cases_sha256": sha("benchmarks/pseudonymization_v1/cases.json"),
            "code_fingerprint": {f: sha(f) for f in _FINGERPRINT_FILES}, "python": platform.python_version(),
            "packages": _package_versions()}


def _package_versions() -> dict:
    from importlib import metadata

    versions = {}
    for name in ("presidio-analyzer", "spacy", "de-core-news-lg", "de-core-news-md", "de-core-news-sm"):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return versions


def main() -> int:
    spec = load_cases()
    gateway = ClaudePrivacyGateway()
    rows = [run_field_case(gateway, case) for case in spec["cases"]]
    e2e = run_e2e()
    output = {"benchmark": f"{spec['benchmark']}_{spec['version']}", "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              **provenance(), "rules": spec["rules"], "summary": summarise(rows, e2e), "field_results": rows,
              "e2e_results": e2e}
    out = BENCH_DIR / "results.json"
    out.write_text(json.dumps(output, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(output["summary"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
