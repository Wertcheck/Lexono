"""Qualitaetsbenchmark der Chat-Dokumentauswahl (§29/§30): A = ganze Akte (Referenz), B = aktuelle Chatregel (bei Anhaengen
der aktuellen Nachricht nur deren Dokumenttext). Nur synthetische Daten aus benchmarks/chat_scope_v1/cases.json.

Gemessen wird der ClaudeRequestPayload, den der Schreib-Provider UNMITTELBAR vor dem Cloud-Aufruf erhaelt - also nach
Pseudonymisierung, Rueckfrage, Restrisiko-Pruefung und Siegel, einschliesslich Gespraechsverlauf. Der Produktivcode bleibt
unveraendert; Variante A ersetzt nur den lokalen Kontext-Provider durch einen, der die Dokumentauswahl ignoriert.

Aufruf (offline, kein externer Aufruf):
    python scripts/bench_chat_scope.py                 -> benchmarks/chat_scope_v1/results_offline.json
Live (nur nach ausdruecklicher Freigabe: ausschliesslich synthetische Daten UND Ausgabenlimit beim Anbieter festgelegt):
    python scripts/bench_chat_scope.py --live --confirm-synthetic-only --confirm-spend-limit
Ohne beide Bestaetigungen bricht der Live-Modus vor jedem Aufruf ab.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.ai_providers.claude_writing_provider import ClaudeWritingResult  # noqa: E402
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider  # noqa: E402
from app.chat.service import ChatService  # noqa: E402
from app.drafting.review_notes import split_review_notes  # noqa: E402
from app.drafting.service import DraftingService  # noqa: E402
from app.models import Client, Deadline, Document, Matter, Party, Role, User  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.privacy.gateway import ClaudePrivacyGateway  # noqa: E402
from app.research.service import LegalResearchService  # noqa: E402
from app.search.service import DocumentSearchService  # noqa: E402

BENCH_DIR = ROOT / "benchmarks" / "chat_scope_v1"
NOTE_MARKER = "wurde nicht berücksichtigt"
# Fingerabdruck der Quellen, die Payload und Prompt bestimmen (Review §30: Prompt-/Gateway-Version festhalten)
_FINGERPRINT_FILES = (
    "app/privacy/gateway.py", "app/privacy/gateway_schema.py", "app/ai_providers/claude_writing_provider.py",
    "app/ai_providers/anthropic_writing_provider.py", "app/ai_providers/local_ai_provider.py", "app/chat/service.py",
    "app/drafting/service.py",
)
_MONTHS = {"januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7, "august": 8,
           "september": 9, "oktober": 10, "november": 11, "dezember": 12}
_DATE_NUMERIC = re.compile(r"(?<![\d.])(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})(?!\d)")
_DATE_WORDS = re.compile(r"(?<![\d.])(\d{1,2})\.\s?(" + "|".join(_MONTHS) + r")\s+(\d{4})(?!\d)", re.IGNORECASE)
# Betraege: "4.250,00", "4250,00", "4.250 EUR", "4.250,- €", "350 Euro" - Tausenderpunkte optional
_AMOUNT = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{2})|,-)?(?=\s*(?:EUR|€|Euro)|(?<=,\d\d))(?![\d])",
    re.IGNORECASE,
)


def canonical_values(text: str) -> set[str]:
    """Normalisiert Datums- und Betragsangaben (Review §30): "15. August 2026", "15.8.2026" -> "2026-08-15";
    "4.250 EUR", "4250,00" -> "4250.00". Zahlen innerhalb laengerer Zahlen zaehlen nicht ("14.250,00" != "4.250,00")."""
    text = text or ""
    values: set[str] = set()
    for day, month, year in _DATE_NUMERIC.findall(text):
        if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
            values.add(f"{int(year):04d}-{int(month):02d}-{int(day):02d}")
    for day, month, year in _DATE_WORDS.findall(text):
        values.add(f"{int(year):04d}-{_MONTHS[month.lower()]:02d}-{int(day):02d}")
    for whole, cents in _AMOUNT.findall(text):
        values.add(f"{int(whole.replace('.', ''))}.{cents or '00'}")
    return values


def _fact_in(fact: str, text: str) -> bool:
    """Fakt im Payload: exakte Schreibweise mit Zahlgrenzen (kein Treffer innerhalb laengerer Zahlen)."""
    return re.search(rf"(?<![\d.]){re.escape(fact)}(?![\d])", text or "") is not None


def _note_count(notes_text: str) -> int | None:
    match = re.search(r"Text von (\d+) weiteren", notes_text)
    if match:
        return int(match[1])
    return 1 if "Text eines weiteren" in notes_text else None


class _WholeMatterProvider(RuleBasedLocalAIProvider):
    """Variante A (Referenz): ignoriert die Dokumentauswahl - Verhalten vor §29."""

    def prepare_draft_context(self, matter_id, db, *, document_ids=None):  # noqa: ANN001
        return super().prepare_draft_context(matter_id, db)


class _CapturingWriter:
    """Offline: erfasst den Payload und antwortet fest (keine Antwortqualitaet messbar)."""

    def __init__(self, reply: str = "Antwort.") -> None:
        self.reply = reply
        self.payloads: list = []

    def write(self, payload):  # noqa: ANN001
        self.payloads.append(payload)
        return ClaudeWritingResult(text=self.reply, token_count=0)


class _LiveCapturingWriter(_CapturingWriter):
    """Live: erfasst den Payload und leitet ihn an den echten Schreib-Provider weiter."""

    def __init__(self, real) -> None:  # noqa: ANN001
        super().__init__()
        self.real = real
        self.results: list[ClaudeWritingResult] = []

    def write(self, payload):  # noqa: ANN001
        self.payloads.append(payload)
        result = self.real.write(payload)
        self.results.append(result)
        return result


def load_cases() -> dict:
    return json.loads((BENCH_DIR / "cases.json").read_text(encoding="utf-8"))


def _seed(db, spec: dict, *, with_duplicate: bool) -> tuple[Matter, dict[str, str]]:
    role = Role(name="anwalt")
    db.add(role)
    db.flush()
    db.add(User(email="bench@kanzlei.test", password_hash="x", role_id=role.id))
    m = spec["matter"]
    client = Client(name=m["client"])
    matter = Matter(client=client, title=m["title"])
    db.add_all([client, matter])
    db.flush()
    db.add(Party(matter_id=matter.id, name=m["opponent"], role="Gegner"))
    ids: dict[str, str] = {}
    texts = {d["id"]: d.get("text") for d in m["documents"]}
    for index, doc in enumerate(m["documents"]):
        if doc.get("duplicate_of") and not with_duplicate:
            continue
        text = texts[doc["duplicate_of"]] if doc.get("duplicate_of") else doc["text"]
        row = Document(matter_id=matter.id, file_path=f"{doc['id']}.txt", original_filename=f"{doc['id']}.txt",
                       extracted_text=text, classified_type=doc["type"],
                       created_at=datetime(2026, 1, 1, 12, index, tzinfo=timezone.utc))
        db.add(row)
        db.flush()
        ids[doc["id"]] = row.id
    for deadline in m["deadlines"]:
        db.add(Deadline(matter_id=matter.id, document_id=ids[deadline["document"]], source_text=deadline["source_text"],
                        due_date=date.fromisoformat(deadline["due_date"])))
    db.commit()
    return matter, ids


def _drafting(local_ai, writer) -> DraftingService:  # noqa: ANN001
    search = DocumentSearchService(_NoEmbeddings())
    return DraftingService(local_ai(search), LegalResearchService(search, min_score_for_sufficient=0.0), search,
                           ClaudePrivacyGateway(), writer, model_name="bench")


class _NoEmbeddings:
    """Keine Wissensbasis im Benchmark: Quellen kommen nur aus der lokalen Rechtsrecherche (leer) - dokumentierte Grenze."""

    def embed(self, text: str) -> list[float]:
        return [0.0] * 8


def _chain(cases: dict, case: dict) -> list[dict]:
    chain = [case]
    while chain[0].get("follow_up_of"):
        chain.insert(0, cases[chain[0]["follow_up_of"]])
    return chain


def run_case(spec: dict, case: dict, variant: str, *, live_writer_factory=None) -> dict:  # noqa: ANN001
    cases = {c["id"]: c for c in spec["cases"]}
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        matter, ids = _seed(db, spec, with_duplicate=bool(case.get("with_duplicate")))
        user = db.query(User).first()
        chat = ChatService(Path(".") / "bench_uploads_unused")
        conversation = chat.create_conversation(db, user=user, matter_id=matter.id, title="Bench", actor=user.email)
        local_ai = _WholeMatterProvider if variant == "A" else RuleBasedLocalAIProvider
        chain = _chain(cases, case)
        record: dict = {}
        for position, turn in enumerate(chain):
            final = position == len(chain) - 1
            follower = chain[position + 1] if not final else None
            if live_writer_factory is not None and final:
                writer = live_writer_factory()
            else:
                writer = _CapturingWriter((follower or {}).get("history_reply", "Antwort."))
            message = chat.record_user_message(db, conversation=conversation, content=turn["question"],
                                               document_ids=[ids[d] for d in turn["attach"]])
            started = time.perf_counter()
            error = None
            try:
                reply = chat.send_message(db, conversation=conversation, content=turn["question"],
                                          drafting_service=_drafting(local_ai, writer), actor=user.email,
                                          current_message_id=message.id)
            except Exception as exc:  # noqa: BLE001 - Fehlerquote wird gemessen
                reply, error = None, type(exc).__name__
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            if final:
                record = _evaluate(spec, turn, writer, reply, error, elapsed_ms, variant)
        return record
    finally:
        db.close()
        engine.dispose()


def _evaluate(spec: dict, case: dict, writer, reply, error, elapsed_ms: int, variant: str) -> dict:  # noqa: ANN001
    m = spec["matter"]
    payload = writer.payloads[-1].model_dump() if writer.payloads else None
    text = json.dumps(payload, ensure_ascii=False) if payload else ""
    history_text = json.dumps(payload.get("anonymisierter_gespraechsverlauf", []), ensure_ascii=False) if payload else ""
    without_history = (
        json.dumps({k: v for k, v in payload.items() if k != "anonymisierter_gespraechsverlauf"}, ensure_ascii=False) if payload else ""
    )
    all_facts = sorted({f for facts in m["facts"].values() for f in facts})
    needed = case["needed_facts"]
    unneeded = [f for f in all_facts if f not in needed]
    body, notes = split_review_notes(reply.content) if reply is not None else ("", [])
    note_present = NOTE_MARKER in str(notes)
    note_count = _note_count(str(notes))
    # Review §30: ein nach dem Schreibaufruf blockiertes oder fehlgeschlagenes Ergebnis ist ein Fehler, auch wenn der
    # Payload bereits erfasst wurde (z. B. Antwortpruefung, Integritaetssperre, Provider-Fehler)
    if error is None and reply is not None and reply.blocked:
        error = "blocked"
    result = {
        "case": case["id"], "kind": case["kind"], "variant": variant,
        "error": error or (None if payload else ("blocked" if reply is not None and reply.blocked else "no_payload")),
        "payload_chars": len(text),
        "runtime_ms": elapsed_ms,
        "needed_facts_present": [f for f in needed if _fact_in(f, text)],
        "needed_facts_missing": [f for f in needed if not _fact_in(f, text)],
        "unneeded_facts_total": len(unneeded),
        "unneeded_facts_present": [f for f in unneeded if _fact_in(f, text)],
        "unneeded_facts_in_history_only": [
            f for f in unneeded if _fact_in(f, history_text) and not _fact_in(f, without_history)
        ],
        "sensitive_terms_total": len(m["sensitive_terms"]),
        "sensitive_plaintext": [t for t in m["sensitive_terms"] if t in text],
        # Variante A ersetzt nur den Kontext-Provider; der Chat-Hinweis bezieht sich auf die Regel B und ist in A
        # gegenstandslos (A sendet alle Dokumente) - daher nicht ausgewiesen.
        "note_present": note_present if variant == "B" else None,
        "note_count": note_count if variant == "B" else None,
    }
    if variant == "B":
        expected_missing = set(case["omission_expected_in_b"])
        result["unexpected_missing"] = [f for f in result["needed_facts_missing"] if f not in expected_missing]
        result["omission_without_note"] = bool(result["needed_facts_missing"]) and not note_present
        result["note_as_expected"] = note_present == case["note_expected_in_b"]
        if case.get("expected_note_count") is not None:
            result["note_count_as_expected"] = note_count == case["expected_note_count"]
    if isinstance(writer, _LiveCapturingWriter) and reply is not None:
        result["live"] = score_answer(spec, case, body)
        tokens = writer.results[-1] if writer.results else None
        result["live"]["input_tokens"] = getattr(tokens, "input_tokens", None)
        result["live"]["output_tokens"] = getattr(tokens, "output_tokens", None)
    return result


def score_answer(spec: dict, case: dict, answer: str) -> dict:
    """VORLAEUFIGE, fachlich nicht validierte automatische Bewertung einer Live-Antwort (kein LLM-Selbsturteil):
    benoetigte Fakten genannt; Betraege/Daten, die in keinem Aktendokument stehen (unbelegt); bei erwarteter Auslassung
    keine erfundenen Werte (jede genannte Zahl muss in einem Aktendokument stehen)."""
    known: set[str] = set()
    for doc in spec["matter"]["documents"]:
        known |= canonical_values(doc.get("text") or "")
    for facts in spec["matter"]["facts"].values():
        known |= canonical_values(" ".join(f + (" EUR" if "," in f else "") for f in facts))
    question_values = canonical_values(case["question"])
    mentioned = canonical_values(answer or "")
    needed = {f: canonical_values(f + (" EUR" if "," in f else "")) for f in case["needed_facts"]}
    return {
        "needed_facts_in_answer": [f for f, vals in needed.items() if vals & mentioned],
        "needed_facts_not_in_answer": [f for f, vals in needed.items() if not vals & mentioned],
        "unsupported_values": sorted(mentioned - known - question_values),
        "status": "VORLAEUFIG - fachlich nicht validiert",
    }


def summarise(results: list[dict]) -> dict:
    summary = {}
    for variant in ("A", "B"):
        rows = [r for r in results if r["variant"] == variant]
        needed = sum(len(r["needed_facts_present"]) + len(r["needed_facts_missing"]) for r in rows)
        unneeded_total = sum(len(r["unneeded_facts_present"]) for r in rows)
        unneeded_denominator = sum(r["unneeded_facts_total"] for r in rows)
        sensitive_denominator = sum(r["sensitive_terms_total"] for r in rows)
        summary[variant] = {
            "cases": len(rows),
            "errors": sum(1 for r in rows if r["error"]),
            "needed_facts_present": f"{sum(len(r['needed_facts_present']) for r in rows)}/{needed}",
            "unneeded_facts_present": f"{unneeded_total}/{unneeded_denominator}",
            "sensitive_plaintext": f"{sum(len(r['sensitive_plaintext']) for r in rows)}/{sensitive_denominator}",
            "payload_chars_total": sum(r["payload_chars"] for r in rows),
            "runtime_ms_median": sorted(r["runtime_ms"] for r in rows)[len(rows) // 2] if rows else None,
        }
        if variant == "B":
            summary[variant]["unexpected_missing"] = sum(len(r["unexpected_missing"]) for r in rows)
            summary[variant]["omission_without_note"] = sum(1 for r in rows if r["omission_without_note"])
            summary[variant]["note_not_as_expected"] = sum(1 for r in rows if not r["note_as_expected"])
            summary[variant]["note_count_wrong"] = sum(1 for r in rows if r.get("note_count_as_expected") is False)
    return summary


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unbekannt"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def provenance() -> dict:
    """Reproduzierbarkeit (Review §30): Commit, ob Arbeitskopie davon abweicht, Hash des Datensatzes und der
    payload-/promptbestimmenden Quellen."""
    dirty = [
        path for path in (line[3:].strip('"') for line in _git("status", "--porcelain").splitlines())
        if not path.startswith(("assets/", "benchmarks/chat_scope_v1/results"))
    ]  # fremde Bild-Aenderungen und die Ergebnisdatei selbst zaehlen nicht
    return {
        "git_commit": _git("rev-parse", "--short", "HEAD"),
        "git_dirty_files": dirty,
        "cases_sha256": _sha256(BENCH_DIR / "cases.json"),
        "code_fingerprint": {f: _sha256(ROOT / f) for f in _FINGERPRINT_FILES},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-synthetic-only", action="store_true")
    parser.add_argument("--confirm-spend-limit", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    spec = load_cases()
    live_factory = None
    model = "offline (Fake-Provider, feste Antwort)"
    if args.live:
        if not (args.confirm_synthetic_only and args.confirm_spend_limit):
            print("ABBRUCH: Live-Lauf nur mit --confirm-synthetic-only UND --confirm-spend-limit (Freigabe des Owners).")
            return 2
        from app.ai_providers.factory import build_writing_provider
        from app.config import get_settings

        settings = get_settings()
        real = build_writing_provider(settings)
        model = settings.claude_model_name
        live_factory = lambda: _LiveCapturingWriter(real)  # noqa: E731
    run_at = datetime.now(timezone.utc)
    if args.out:
        out = Path(args.out)
    elif args.live:
        # Live-Ergebnisse nie ueberschreiben (bezahlter Lauf); Zwischenstand nach jedem Fall sichern
        out = BENCH_DIR / f"results_live_{run_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    else:
        out = BENCH_DIR / "results_offline.json"
    partial = out.with_suffix(".partial.jsonl")
    results = []
    for case in spec["cases"]:
        for variant in ("A", "B"):
            results.append(run_case(spec, case, variant, live_writer_factory=live_factory))
            if args.live:
                with partial.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(results[-1], ensure_ascii=False) + "\n")
    output = {
        "benchmark": f"{spec['benchmark']}_{spec['version']}",
        "run_at": run_at.isoformat(timespec="seconds"),
        **provenance(),
        "python": platform.python_version(),
        "mode": "live" if args.live else "offline",
        "model": model,
        "rules": spec["rules"],
        "summary": summarise(results),
        "results": results,
    }
    out.write_text(json.dumps(output, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(output["summary"], ensure_ascii=False, indent=1))
    print(f"-> {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
