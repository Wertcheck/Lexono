"""Realistische Dokumente durch den ECHTEN Verarbeitungspfad bis zum versiegelten Cloud-Payload (12.10.2026).

Pfad je Format (DOCX, PDF, Scan): Extraktion (app/documents/extraction.py, bei Scans OCR ueber das gebuendelte Tesseract)
-> Dokument in einer Akte -> DraftingService (lokale Aufbereitung, Privacy-Gateway, Siegel) -> Payload, den ein Fake-Provider
anstelle der Cloud entgegennimmt. Gemessen gegen den Goldstandard aus scripts/make_realistic_privacy_docs.py:
  - personenbezogene Werte im Payload (Leck),
  - Fachbegriffe/Betraege, die im Payload fehlen (falsch positive Pseudonymisierung),
  - Rueckfragen (Modus "ask") je Dokument,
  - personenbezogene Werte, die schon bei der Extraktion fehlen (Formatverlust, kein Leck, aber Inhaltsverlust).
Aufruf:  python scripts/bench_realistic_documents.py <Verzeichnis mit protokoll.docx/.pdf/_scan.pdf und gold.json>
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.ai_providers.claude_writing_provider import ClaudeWritingResult  # noqa: E402
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider  # noqa: E402
from app.documents import ocr  # noqa: E402
from app.documents.extraction import extract_text  # noqa: E402
from app.drafting.service import DraftingService  # noqa: E402
from app.models import Client, Document, Matter  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.privacy.gateway import ClaudePrivacyGateway  # noqa: E402
from app.research.service import LegalResearchService  # noqa: E402
from app.search.service import DocumentSearchService  # noqa: E402

TESSERACT_DIR = Path(os.environ.get("LEXONO_TESSERACT_DIR", r"C:\Users\Bonit\AppData\Local\Lexono\_internal\tesseract"))


class _Embedding:
    dimension = 8

    def embed(self, texts):
        return [[0.0] * 8 for _ in texts]


class _CapturingWriter:
    def __init__(self) -> None:
        self.payloads = []

    def write(self, payload):
        self.payloads.append(payload)
        return ClaudeWritingResult(text="Zusammenfassung erstellt.", token_count=1)


def _document_text(path: Path) -> str:
    result = extract_text(path)
    if not result.needs_ocr:
        return result.text or ""
    ocr.configure_tesseract(str(TESSERACT_DIR / "bin" / "tesseract.exe"))
    os.environ["TESSDATA_PREFIX"] = str(TESSERACT_DIR / "tessdata")
    return ocr.run_ocr(path, languages="deu+eng")


def run(path: Path, gold: dict) -> dict:
    text = _document_text(path)
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    matter = Matter(client=Client(name="Synthetischer Mandant"), title="Realistische Testakte")
    db.add(matter)
    db.flush()
    db.add(Document(matter_id=matter.id, file_path=str(path), extracted_text=text, classified_type="Sonstiges"))
    db.commit()
    search = DocumentSearchService(_Embedding())
    writer = _CapturingWriter()
    service = DraftingService(
        RuleBasedLocalAIProvider(search), LegalResearchService(search, min_score_for_sufficient=0.0), search,
        ClaudePrivacyGateway(), writer, model_name="synthetisch",
    )
    candidates = service.preview_privacy_review(
        matter.id, "chat_response", db, attorney_anmerkungen="Bitte fasse das Protokoll zusammen.", actor="qa",
        gespraechsverlauf=[], previous_draft=None, privacy_decisions=None,
    )
    service.create_draft(matter.id, "chat_response", db, attorney_anmerkungen="Bitte fasse das Protokoll zusammen.",
                         actor="qa", chat_triggered=True)  # Standard "mask": nicht-interaktiver Pfad
    payload = writer.payloads[0].model_dump_json() if writer.payloads else ""
    db.close()
    engine.dispose()
    payload_text = json.loads(payload) if payload else {}
    flat = json.dumps(payload_text, ensure_ascii=False)
    return {
        "extracted_missing": [v for v in gold["personal"] if v not in text],
        "leaks": [v for v in gold["personal"] if v in text and v in flat],
        "keep_missing": [v for v in gold["keep"] if v in text and v not in flat],
        "keep_absent_in_extraction": [v for v in gold["keep"] if v not in text],
        "questions": [c.term for c in candidates],
        "payload_sent": bool(payload),
    }


def main() -> None:
    folder = Path(sys.argv[1])
    gold = json.loads((folder / "gold.json").read_text(encoding="utf-8"))
    for name in ("protokoll.docx", "protokoll.pdf", "protokoll_scan.pdf"):
        r = run(folder / name, gold)
        print(f"== {name}")
        for key, value in r.items():
            print(f"   {key}: {value}")


if __name__ == "__main__":
    main()
