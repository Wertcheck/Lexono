"""E2E-Runner des Realistic Legal Benchmark (benchmarks/legal_e2e_v1) ueber echte Lexono-Pfade - ohne Cloud-Aufruf.

Je Fall, alle Faelle in EINER Datenbank (Mandantentrennung wird so messbar):
  1. Import:      Dateien rendern (TXT/DOCX/PDF/Scan) -> IntakeService.ingest_file -> Akte zuordnen
  2. Verarbeitung: DocumentProcessingService.process_document (Extraktion, OCR, Klassifikation, Fristen)
  3. Suche:       DocumentSearchService.index_document + search_within_matter
  4. Chat:        ChatService.send_message; der Schreib-Provider ist ein Fake, der den Payload UNMITTELBAR vor dem
                  Cloud-Aufruf erfasst (nach Pseudonymisierung, Rueckfrage, Restrisiko-Scan, Siegel) - es wird nichts gesendet
  5. Schriftsatz: DraftingService.create_draft (Fake-Antwort aus Platzhaltern des Payloads -> lokale Rueckfuehrung)
  6. Bearbeitung: EditorService.autosave_draft
  7. Speicherung/Export: Draft-Datensatz, DraftDocxExportService/DraftPdfExportService

Messungen werden getrennt ausgewiesen; kritische Datenschutzbefunde (Klartext im Payload, Daten anderer Akten,
Platzhalterreste im Export) stehen einzeln im Abschnitt "kritisch" und gehen in keinen Durchschnitt ein.

Aufruf:  python -m scripts.legal_bench.e2e [--split dev|test] [--out PFAD]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"  # nie ein Modell aus dem Netz nachladen (auch nicht bei abweichender Umgebung)

from scripts.legal_bench.corpus import CORPUS_DIR, ROOT, doc_text, load_cases, validate_corpus  # noqa: E402
from scripts.legal_bench.render import render_case  # noqa: E402

PARTY_ROLE_LABEL = {"gegner": "Gegner", "zeuge": "Zeuge", "dritter": "Beteiligter", "anwalt_gegner": "Rechtsanwalt Gegenseite",
                    "gericht": "Gericht", "kanzlei": "Kanzlei"}
_PLACEHOLDER = re.compile(r"\[[A-ZÄÖÜ_]+_\d+\]")
_FINGERPRINT_FILES = ("app/privacy/gateway.py", "app/privacy/detectors.py", "app/privacy/pseudonymizer.py",
                      "app/documents/service.py", "app/documents/extraction.py", "app/deadlines/extractor.py",
                      "app/search/service.py", "app/chat/service.py", "app/drafting/service.py",
                      "app/drafting/editor_service.py", "app/export/docx_export_service.py", "app/export/pdf_export_service.py")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _contains(value: str, text: str) -> bool:
    return _norm(value) in _norm(text)


_MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November",
           "Dezember")


def leak_variants(entry: dict) -> list[str]:
    """Formen, in denen ein geschuetzter Wert im Payload auftauchen kann (Review §34: nur der volle Wert wurde
    geprueft). Personen: zusaetzlich der Nachname ("Herr Wiegelmann"); Organisationen: das erste Wort; persoenliche
    Daten: Kurzjahr, ohne fuehrende Nullen, Monatsname."""
    value, kind = entry["value"], entry["type"]
    variants = [value]
    words = value.split()
    if kind == "PERSON" and len(words) >= 2 and len(words[-1]) >= 4:
        variants.append(words[-1])
    if kind == "ORGANISATION" and len(words) >= 2 and len(words[0]) >= 5:
        variants.append(words[0])
    if kind == "PERS_DATUM":
        from app.privacy.date_chronology import parse_german_date

        parsed = parse_german_date(value)
        if parsed is not None:
            day, month, year = f"{parsed.day:02d}", f"{parsed.month:02d}", str(parsed.year)
            variants += [f"{day}.{month}.{year}", f"{day}.{month}.{year[2:]}", f"{int(day)}.{int(month)}.{year}",
                         f"{int(day)}.{int(month)}.{year[2:]}", f"{day}. {month}. {year}",
                         f"{int(day)}. {_MONTHS[int(month) - 1]} {year}"]
    return list(dict.fromkeys(variants))


def _leaked(variant: str, text: str) -> bool:
    """Wortgrenzen-genau (ein Nachname soll nicht in einem laengeren Wort anschlagen; Genitiv-s zaehlt mit), Leerraum
    normalisiert."""
    pattern = r"(?<![\wÄÖÜäöüß])" + r"\s+".join(re.escape(part) for part in variant.split()) + r"s?(?![\wÄÖÜäöüß])"
    return re.search(pattern, text) is not None


def _payload_text(payload) -> str:  # noqa: ANN001
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

    walk(payload.model_dump())
    return "\n".join(parts)


class CapturingWriter:
    """Erfasst den Payload vor der Cloud-Grenze. Antwort: optional ein Auszug des pseudonymisierten Sachverhalts (damit
    Rueckfuehrung und Export echte Platzhalter verarbeiten); es wird nichts gesendet."""

    def __init__(self, echo: bool = False) -> None:
        self.echo = echo
        self.payloads: list = []

    def write(self, payload):  # noqa: ANN001
        from app.ai_providers.claude_writing_provider import ClaudeWritingResult

        self.payloads.append(payload)
        if self.echo:
            body = payload.anonymisierter_sachverhalt[:1200]
            return ClaudeWritingResult(text=f"Sehr geehrte Damen und Herren,\n\n{body}\n\nMit freundlichen Gruessen", token_count=0)
        return ClaudeWritingResult(text="Antwort.", token_count=0)


def _tesseract() -> str | None:
    """Wie im Installer-Bundle: vendored Tesseract samt explizitem TESSDATA_PREFIX (sonst findet tesseract.exe im
    Dev-Betrieb keine Sprachdaten); System-Installation nur als Rueckfall."""
    vendor = ROOT / "windows" / "vendor" / "tesseract"
    if (vendor / "bin" / "tesseract.exe").exists() and (vendor / "tessdata").is_dir():
        os.environ.setdefault("TESSDATA_PREFIX", str(vendor / "tessdata"))
        return str(vendor / "bin" / "tesseract.exe")
    return shutil.which("tesseract")


def _embedding_provider():  # noqa: ANN202
    """Produktionsmodell (FastEmbed, lokal im Cache, offline erzwungen); sonst Test-Embedding mit Kennzeichnung."""
    try:
        from app.config import get_settings
        from app.search.embeddings import FastEmbedProvider

        provider = FastEmbedProvider(model_name=get_settings().embedding_model_name)
        provider.embed("Probe")
        return provider, "fastembed:" + get_settings().embedding_model_name
    except Exception as exc:  # noqa: BLE001
        sys.path.insert(0, str(ROOT / "tests"))
        from fake_embedding_provider import FakeEmbeddingProvider

        return FakeEmbeddingProvider(), f"fake (Produktionsmodell nicht verfuegbar: {type(exc).__name__}) - Retrieval-Qualitaet NICHT VERIFIZIERT"


def run(cases: list[dict], workdir: Path, embedding=None) -> dict:  # noqa: ANN001
    """`embedding`: (Provider, Bezeichnung) - nur fuer Tests; Standard ist das Produktionsmodell (siehe _embedding_provider)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
    from app.attorney_instructions.service import AttorneyInstructionService
    from app.chat.service import ChatService
    from app.documents.service import DocumentProcessingService
    from app.drafting.editor_service import EditorService
    from app.drafting.service import DraftingService
    from app.export.docx_export_service import DraftDocxExportService
    from app.export.pdf_export_service import DraftPdfExportService
    from app.ingestion.intake import IntakeService
    from app.models import Client, Deadline, Draft, Matter, Party, Role, User
    from app.models.base import Base
    from app.privacy.gateway import ClaudePrivacyGateway
    from app.research.service import LegalResearchService
    from app.search.service import DocumentSearchService

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    role = Role(name="anwalt")
    db.add(role)
    db.flush()
    user = User(email="benchmark@kanzlei.test", password_hash="x", role_id=role.id)
    db.add(user)
    db.commit()

    tesseract = _tesseract()
    processing = DocumentProcessingService(ocr_enabled=tesseract is not None, tesseract_cmd=tesseract)
    intake = IntakeService(workdir / "intake")
    provider, embedding_label = embedding or _embedding_provider()
    search = DocumentSearchService(provider)
    gateway = ClaudePrivacyGateway()
    # Zuordnungen (Platzhalter -> Original) des jeweils letzten Gateway-Laufs mitlesen, um Fehlalarme der Maskierung zu
    # messen. Der Gateway selbst bleibt unveraendert; es wird nur sein Ergebnis beobachtet.
    seen_mappings: list = []
    _prepare = gateway.prepare_request

    def observed_prepare(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        result = _prepare(*args, **kwargs)
        seen_mappings[:] = list(getattr(result, "mappings", None) or [])
        return result

    gateway.prepare_request = observed_prepare  # type: ignore[method-assign]
    all_protect = {c["id"]: sorted({v for p in c["reference"]["privacy"]["protect"] for v in leak_variants(p)}) for c in cases}

    def drafting(writer: CapturingWriter) -> DraftingService:
        return DraftingService(RuleBasedLocalAIProvider(search), LegalResearchService(search, min_score_for_sufficient=0.0),
                               search, gateway, writer, model_name="benchmark-offline")

    # --- 1./2. Import und Verarbeitung (alle Faelle zuerst: Mandantentrennung ueber eine gemeinsame DB) -----------------
    state: dict[str, dict] = {}
    for case in cases:
        t0 = time.perf_counter()
        parties = {p["id"]: p for p in case["parties"]}
        client_emails = [p["value"] for p in case["reference"]["privacy"]["protect"] if p["type"] == "EMAIL"]
        client = Client(name=parties[case["matter"]["client_party"]]["name"],
                        contact_email=client_emails[0] if client_emails else None)
        matter = Matter(client=client, title=case["matter"]["title"],
                        reference_number=case["matter"].get("reference_number"))
        db.add_all([client, matter])
        db.flush()
        for party in case["parties"]:
            if party["id"] != case["matter"]["client_party"]:
                db.add(Party(matter_id=matter.id, name=party["name"], role=PARTY_ROLE_LABEL.get(party["role"], party["role"])))
        db.commit()
        files = render_case(case, workdir / "rendered")
        documents = {}
        doc_results = {}
        for spec in case["documents"]:
            start = time.perf_counter()
            entry: dict = {"format": spec["format"], "type_reference": spec["type"]}
            try:
                document = intake.ingest_file(files[spec["id"]], db, stability_timeout_seconds=5.0)
                document.matter_id = matter.id
                db.commit()
                processing.process_document(document, db, actor="benchmark")
                db.refresh(document)
                documents[spec["id"]] = document
                text = document.extracted_text or ""
                values = _reference_values_for(case, spec["id"])
                entry.update(status=document.ocr_status, classified=document.classified_type, chars=len(text),
                             values_total=len(values), values_found=[v for v in values if _contains(v, text)],
                             values_missing=[v for v in values if not _contains(v, text)])
                search.index_document(document, db)
            except Exception as exc:  # noqa: BLE001 - Fehler werden gemessen
                entry.update(status="error", error=type(exc).__name__)
            entry["seconds"] = round(time.perf_counter() - start, 2)
            doc_results[spec["id"]] = entry
        db.commit()
        state[case["id"]] = {"case": case, "matter": matter, "documents": documents, "doc_results": doc_results,
                             "setup_seconds": round(time.perf_counter() - t0, 2)}

    results = []
    for case in cases:
        st = state[case["id"]]
        matter, documents = st["matter"], st["documents"]
        foreign = [v for cid, values in all_protect.items() if cid != case["id"] for v in values]
        own_protect = all_protect[case["id"]]
        record: dict = {"case": case["id"], "split": case["split"], "documents": st["doc_results"], "tasks": [],
                        "critical": []}

        # Fristen (Verarbeitung)
        found = sorted({d.due_date.strftime("%d.%m.%Y") for d in db.query(Deadline).filter(Deadline.matter_id == matter.id) if d.due_date})
        expected = sorted({d["date"] for d in case["reference"]["deadlines"]})
        personal = [p["value"] for p in case["reference"]["privacy"]["protect"] if p["type"] == "PERS_DATUM"]
        # bekannte Daten des Falls: Chronologie, Dokumentdaten, datierte Widerspruchswerte
        events = {e["date"] for e in case["reference"]["chronology"]} | {d["date"] for d in case["documents"]} | {
            v["value"] for w in case["reference"]["contradictions"] for v in w["values"]
            if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", v["value"])}
        extra = sorted(set(found) - set(expected))
        # Zusaetzliche Vorschlaege getrennt nach Ursache: bekannte Ereignisdaten (Chronologie) sind Fehlalarme der
        # Fristerkennung, aber keine erfundenen Daten; alles andere ist ungeklaert und wird gesondert ausgewiesen.
        record["deadlines"] = {"expected": expected, "found": found,
                               "recall": f"{len(set(expected) & set(found))}/{len(expected)}",
                               "missing": sorted(set(expected) - set(found)),
                               "not_in_reference": extra,
                               "known_dates_as_deadline": [d for d in extra if d in events],
                               "unexplained": [d for d in extra if d not in events and d not in personal],
                               "personal_dates_as_deadline": sorted(set(found) & set(personal))}
        if record["deadlines"]["personal_dates_as_deadline"]:
            record["critical"].append({"kind": "persoenliches_datum_als_frist", "values": record["deadlines"]["personal_dates_as_deadline"]})

        for task in case["reference"]["tasks"]:
            t0 = time.perf_counter()
            seen_mappings.clear()
            item: dict = {"id": task["id"], "kind": task["kind"]}
            try:
                if task["kind"] == "suche":
                    hits = search.search_within_matter(matter.id, task["question"], db, limit=5)
                    ids = [h.entity_id for h in hits]
                    # ein nicht importiertes erwartetes Dokument zaehlt als Fehltreffer (Review §34), nicht als "entfaellt"
                    expected_ids = [documents[d].id if d in documents else None for d in task.get("expected_documents", [])]
                    item.update(rank=[ids.index(e) + 1 if e in ids else None for e in expected_ids],
                                foreign_hits=sum(1 for h in hits if h.matter_id not in (None, matter.id)))
                    if item["foreign_hits"]:
                        record["critical"].append({"kind": "suchtreffer_fremder_akte", "task": task["id"]})
                elif task["kind"] == "fristen":
                    item.update(expected=task["expected_deadlines"], found=[d for d in task["expected_deadlines"] if d in found])
                elif task["kind"] == "chat":
                    item.update(_run_chat(db, user, matter, documents, task, drafting))
                elif task["kind"] == "schriftsatz":
                    item.update(_run_draft(db, user, matter, task, drafting, case, Draft, EditorService,
                                           AttorneyInstructionService, DraftDocxExportService, DraftPdfExportService))
                    try:  # eigene Fehlerbehandlung: die Probe darf das Schriftsatz-Ergebnis nicht verfaelschen
                        item["quellenintegritaet"] = _probe_source_integrity(db, user, matter, drafting, Draft)
                    except Exception as exc:  # noqa: BLE001
                        item["quellenintegritaet"] = {"status": "error", "error": type(exc).__name__}
            except Exception as exc:  # noqa: BLE001
                item.update(status="error", error=f"{type(exc).__name__}: {str(exc)[:120]}")
            sent = item.pop("_payload_text", None)
            if sent is not None:
                leaks = [v for v in own_protect if _leaked(v, sent)]
                cross = [v for v in foreign if _leaked(v, sent) and v not in own_protect]
                item.update(payload_chars=len(sent), pii_leaks=leaks, cross_matter_values=cross,
                            needed_present=[v for v in task.get("needed_values", []) if _contains(v, sent)],
                            needed_missing=[v for v in task.get("needed_values", []) if not _contains(v, sent)])
                if leaks:
                    record["critical"].append({"kind": "klartext_im_payload", "task": task["id"], "values": leaks})
                if cross:
                    record["critical"].append({"kind": "daten_anderer_akte_im_payload", "task": task["id"], "values": cross})
                if task["kind"] == "schriftsatz":
                    item["erhalt"] = _preservation(case, sent)
                item["maskiert_ohne_referenz"] = _unreferenced_masks(case, seen_mappings)
            for leftover in item.get("export_placeholders", []):
                record["critical"].append({"kind": "platzhalterrest_im_export", "task": task["id"], "value": leftover})
            item["seconds"] = round(time.perf_counter() - t0, 2)
            record["tasks"].append(item)
        results.append(record)

    matching = _matching_probes(db, cases, {cid: st["matter"].id for cid, st in state.items()})
    foreign_attachment = _foreign_attachment_probes(db, user, cases, state, all_protect, drafting)
    ocr_degraded = _ocr_degraded_probe(db, cases, workdir, intake, processing)

    db.close()
    engine.dispose()
    return {"results": results, "matching": matching, "foreign_attachment": foreign_attachment,
            "ocr_degraded": ocr_degraded, "environment": {"embedding": embedding_label, "ocr": tesseract or "nicht verfuegbar",
                                                 "writer": "CapturingWriter (offline, kein Cloud-Aufruf)"}}


_INTENDED_CATEGORIES = {"geburtsdatum", "todesdatum", "geburtstag"}


def _unreferenced_masks(case: dict, mappings: list) -> list[dict]:
    """Maskierte Originalwerte, die NICHT zu den geschuetzten Werten der Referenz gehoeren (Fehlalarm-Kandidaten, z. B.
    "Gruen" -> [PERSON_02]). Ein Original gilt als referenziert, wenn es einen geschuetzten Wert (inkl. Nachname,
    Datumsvarianten, Parteinamen) enthaelt oder Teil davon ist. Ortsnamen ausserhalb von Adressen sind eine bewusste
    Policy-Frage und werden mit Kategorie ausgewiesen, nicht stillschweigend als Fehler gezaehlt."""
    protected = {v for p in case["reference"]["privacy"]["protect"] for v in leak_variants(p)}
    protected |= {p["name"] for p in case["parties"]} | {w for p in case["parties"] for w in p["name"].split() if len(w) >= 4}
    out = []
    for mapping in mappings:
        original = (mapping.original_value or "").strip()
        if not original or mapping.category in _INTENDED_CATEGORIES:
            continue
        # nur "Original enthaelt einen Schutzwert" (z. B. "Herrn Wiegelmann") - ein allein maskierter Teil einer
        # geschuetzten Adresse (Ortsname, "Haus Lindenstraße") bleibt sichtbar (Review §34.6: sonst verdeckt)
        if any(v in original for v in protected if len(v) >= 3):
            continue
        out.append({"original": original, "kategorie": mapping.category, "platzhalter": mapping.placeholder})
    return out


def _preservation(case: dict, sent: str) -> dict:
    """Informationserhalt im Payload eines Schriftsatzes (ganze Akte als Kontext): kommen nach Pseudonymisierung die
    zu erhaltenden Werte (Fehlalarme der Maskierung), Chronologiedaten, Normen und BEIDE Seiten jedes Widerspruchs beim
    Modell an? Misst Verfuegbarkeit, nicht die Antwortqualitaet (offline gibt es keine echte Antwort)."""
    ref = case["reference"]
    personal = {p["value"] for p in ref["privacy"]["protect"]}

    def split(values: list[str]) -> dict:
        values = sorted(set(values) - personal)
        return {"present": [v for v in values if _contains(v, sent)], "missing": [v for v in values if not _contains(v, sent)]}

    contradictions = {w["id"]: [v["value"] for v in w["values"]] for w in ref["contradictions"]}
    return {"keep": split(ref["privacy"]["keep"]), "chronologie": split([c["date"] for c in ref["chronology"]]),
            "normen": split([c["text"] for c in ref["citations"]]),
            "widersprueche_beide_seiten": sorted(w for w, vals in contradictions.items() if all(_contains(v, sent) for v in vals)),
            "widersprueche_unvollstaendig": sorted(w for w, vals in contradictions.items() if not all(_contains(v, sent) for v in vals))}


def _reference_values_for(case: dict, doc_id: str) -> list[str]:
    """Alle Referenzwerte, die laut Korpus im Dokument stehen (Grundlage fuer Extraktions-/OCR-Erhalt)."""
    text = doc_text(next(d for d in case["documents"] if d["id"] == doc_id))
    ref = case["reference"]
    candidates = [v for f in ref["facts"] for v in f["values"]] + [c["date"] for c in ref["chronology"]] + \
        [d["date"] for d in ref["deadlines"]] + [c["text"] for c in ref["citations"]] + \
        [p["value"] for p in ref["privacy"]["protect"]] + list(ref["privacy"]["keep"])
    return sorted({v for v in candidates if v in text})


def _ocr_degraded_probe(db, cases, workdir: Path, intake, processing) -> dict:  # noqa: ANN001
    """OCR-Robustheit: jedes Scan-Dokument zusaetzlich als schlechter Scan (render_degraded_scan) durch den echten
    Import- und Verarbeitungspfad, in einer eigenen Probe-Akte (laeuft nach allen anderen Messungen). Gezaehlt werden die
    Referenzwerte des Dokuments im OCR-Text - getrennt nach Split, ohne Mittelung mit den sauberen Scans."""
    from app.models import Client, Matter

    from scripts.legal_bench.render import render_degraded_scan

    client = Client(name="OCR-Probe (synthetisch)")
    matter = Matter(client=client, title="OCR-Robustheitsprobe")
    db.add_all([client, matter])
    db.commit()
    out: dict = {"dev": {"found": 0, "total": 0, "fehlend": []}, "test": {"found": 0, "total": 0, "fehlend": []}}
    for case in cases:
        for spec in case["documents"]:
            if spec["format"] != "scan":
                continue
            target_dir = workdir / "degraded" / case["id"]
            target_dir.mkdir(parents=True, exist_ok=True)
            path = render_degraded_scan(spec, target_dir / Path(spec["filename"]).name)
            document = intake.ingest_file(path, db, stability_timeout_seconds=5.0)
            document.matter_id = matter.id
            db.commit()
            processing.process_document(document, db, actor="benchmark")
            db.refresh(document)
            text = document.extracted_text or ""
            values = _reference_values_for(case, spec["id"])
            bucket = out[case["split"]]
            bucket["total"] += len(values)
            bucket["found"] += sum(1 for v in values if _contains(v, text))
            bucket["fehlend"] += [f"{case['id']}/{spec['id']}:{v}" for v in values if not _contains(v, text)]
    return {split: {"ocr_werte_schlechter_scan": f"{b['found']}/{b['total']}", "fehlend": b["fehlend"]}
            for split, b in out.items()}


def _foreign_attachment_probes(db, user, cases, state, all_protect, drafting) -> list[dict]:  # noqa: ANN001
    """Mandantentrennung: ein Chat in Akte A haengt ein Dokument der Akte B an (z. B. manipulierte Dokument-ID). Kritisch,
    wenn ein Schutzwert von B im Payload steht. Erwartet: Anhang wird nicht verwendet oder die Anfrage blockiert."""
    rows = []
    ids = [c["id"] for c in cases]
    for index, case in enumerate(cases):
        other_id = ids[(index + 1) % len(ids)]
        if other_id == case["id"] or not state[other_id]["documents"]:
            continue
        foreign_doc = next(iter(state[other_id]["documents"].values()))
        task = {"id": "FA", "question": "Was steht in diesem Dokument?", "attach": []}
        documents = {"X": foreign_doc}
        task["attach"] = ["X"]
        try:
            result = _run_chat(db, user, state[case["id"]]["matter"], documents, task, drafting)
        except Exception as exc:  # noqa: BLE001
            rows.append({"case": case["id"], "fremde_akte": other_id, "status": "error", "error": type(exc).__name__})
            continue
        sent = result.pop("_payload_text", None)
        foreign_values = [v for v in all_protect[other_id] if sent and _leaked(v, sent) and v not in all_protect[case["id"]]]
        rows.append({"case": case["id"], "fremde_akte": other_id, "status": result["status"],
                     "werte_fremder_akte_im_payload": foreign_values, "kritisch": bool(foreign_values)})
    return rows


def _email_message(document: dict) -> tuple[str | None, str | None, str]:
    """Absender ("Von:"), Betreff ("Betreff:") und Text einer E-Mail des Korpus."""
    sender = subject = None
    for line in document["paragraphs"]:
        if line.startswith("Von:"):
            sender = line[4:].strip()
        elif line.startswith("Betreff:"):
            subject = line[8:].strip()
    return sender, subject, doc_text(document)


def _matching_probes(db, cases: list[dict], matter_ids: dict[str, str]) -> dict:  # noqa: ANN001
    """Automatische Aktenzuordnung eingehender Nachrichten (MatterAssignmentService, Produktionsschwellen) gegen ALLE
    Akten der Benchmark-Datenbank. Kritisch: eine automatische Zuordnung zu einer FREMDEN Akte ohne widerspruechliche
    Signale - und (seit §34.10) auch im Konfliktfall: bekannte Absenderadresse von Mandant A nennt das Aktenzeichen von
    Akte B; Lexono verlangt dann eine Pruefung durch die Kanzlei."""
    from app.config import get_settings
    from app.matching.matcher import MatterMatchingService
    from app.matching.service import MatterAssignmentService
    from app.models import Message

    settings = get_settings()
    service = MatterAssignmentService(
        MatterMatchingService(auto_assign_threshold=settings.matching_auto_assign_threshold,
                              review_threshold=settings.matching_review_threshold),
        classification_low_confidence_threshold=settings.classification_low_confidence_threshold)

    def assign(sender: str | None, subject: str | None, body: str) -> dict:
        message = Message(direction="inbound", sender=sender, subject=subject, body_text=body)
        db.add(message)
        db.commit()
        result = service.assign_matter(message, db)
        return {"decision": result.decision, "matter_id": result.matter_id}

    owner = {mid: cid for cid, mid in matter_ids.items()}
    rows: list[dict] = []
    for case in cases:
        own = matter_ids[case["id"]]
        for document in case["documents"]:
            if document["type"] != "email":
                continue
            outcome = assign(*_email_message(document))
            rows.append({"probe": "korpus_email", "case": case["id"], "doc": document["id"], "decision": outcome["decision"],
                         "zugeordnet": owner.get(outcome["matter_id"]),
                         "fremd": outcome["decision"] == "auto_assigned" and outcome["matter_id"] != own})
        ref = case["matter"].get("reference_number")
        if ref:
            outcome = assign("unbekannt@extern.example", f"Ihr Schreiben, Az. {ref}", "Bitte um Rückruf.")
            rows.append({"probe": "aktenzeichen", "case": case["id"], "decision": outcome["decision"],
                         "zugeordnet": owner.get(outcome["matter_id"]), "korrekt": outcome["matter_id"] == own,
                         "fremd": outcome["decision"] == "auto_assigned" and outcome["matter_id"] != own})
            outcome = assign("unbekannt@extern.example", f"Ihr Zeichen: {ref}", "Bitte um Rückruf.")
            rows.append({"probe": "ihr_zeichen", "case": case["id"], "decision": outcome["decision"],
                         "zugeordnet": owner.get(outcome["matter_id"]), "korrekt": outcome["matter_id"] == own,
                         "fremd": outcome["decision"] == "auto_assigned" and outcome["matter_id"] != own})
    # Konflikt: Mandant A (bekannte Adresse) nennt das Aktenzeichen der naechsten Akte B mit Aktenzeichen
    with_ref = [c for c in cases if c["matter"].get("reference_number")]
    for case in cases:
        emails = [p["value"] for p in case["reference"]["privacy"]["protect"] if p["type"] == "EMAIL"]
        other = next((c for c in with_ref if c["id"] != case["id"]), None)
        if not emails or other is None:
            continue
        outcome = assign(emails[0], f"Rückfrage Az. {other['matter']['reference_number']}", "Ich habe noch eine Frage.")
        rows.append({"probe": "konflikt", "case": case["id"], "aktenzeichen_von": other["id"], "decision": outcome["decision"],
                     "zugeordnet": owner.get(outcome["matter_id"]),
                     # seit §34.10 entscheidet Lexono hier "needs_review"; eine automatische Fremdzuordnung ist Regression
                     "fremd": outcome["decision"] == "auto_assigned" and outcome["matter_id"] != matter_ids[case["id"]]})

    def count(probe: str, key: str) -> str:
        sel = [r for r in rows if r["probe"] == probe]
        return f"{sum(1 for r in sel if r.get(key))}/{len(sel)}"

    return {
        "kritisch_fremdzuordnung": [r for r in rows if r.get("fremd")],
        "korpus_email_entscheidungen": sorted({f"{r['case']}/{r['doc']}:{r['decision']}" for r in rows if r["probe"] == "korpus_email"}),
        "aktenzeichen_korrekt_zugeordnet": count("aktenzeichen", "korrekt"),
        "ihr_zeichen_korrekt_zugeordnet": count("ihr_zeichen", "korrekt"),
        "konflikt_automatisch_zur_fremden_akte": [f"{r['case']}->{r['zugeordnet']}" for r in rows
                                                  if r["probe"] == "konflikt" and r["decision"] == "auto_assigned"],
        "konflikt_entscheidungen": [f"{r['case']}:{r['decision']}" for r in rows if r["probe"] == "konflikt"],
        "details": rows,
    }


#: Erfundene Fundstellen fuer die Quellenintegritaets-Probe (es gibt weder § 9999 BGB noch dieses Aktenzeichen)
INVENTED_CITATIONS = ("§ 9999 BGB", "BGH, Urt. v. 01.01.2020 - I ZR 999/19")


class InventingWriter(CapturingWriter):
    """Simuliert ein Modell, das Fundstellen erfindet (adversariale Probe); sendet nichts."""

    def write(self, payload):  # noqa: ANN001
        from app.ai_providers.claude_writing_provider import ClaudeWritingResult

        self.payloads.append(payload)
        return ClaudeWritingResult(text="Sehr geehrte Damen und Herren,\n\nDer Anspruch folgt aus "
                                   f"{INVENTED_CITATIONS[0]}; vgl. {INVENTED_CITATIONS[1]}.\n\nMit freundlichen Grüßen",
                                   token_count=0)


def _probe_source_integrity(db, user, matter, drafting, Draft) -> dict:  # noqa: ANN001, N803
    """Werden erfundene Fundstellen einer Modellantwort im gespeicherten Entwurf als ungeprueft markiert (Abschnitt
    "Offene Pruefpunkte")? Misst Lexonos lokale Absicherung, nicht das Modell."""
    from app.drafting.review_notes import split_review_notes

    outcome = drafting(InventingWriter()).create_draft(matter.id, "formulate_draft", db,
                                                       attorney_anmerkungen="Anspruchsschreiben.", actor=user.email)
    if not outcome.success or not outcome.draft_id:
        return {"status": "blockiert"}
    draft = db.get(Draft, outcome.draft_id)
    letter, notes = split_review_notes(draft.content or "")
    return {"status": "ok", "im_entwurf": [c for c in INVENTED_CITATIONS if _contains(c, letter)],
            "als_pruefpunkt_markiert": [c for c in INVENTED_CITATIONS if _contains(c, notes)]}


def _run_chat(db, user, matter, documents, task, drafting) -> dict:  # noqa: ANN001
    from app.chat.service import ChatService

    chat = ChatService(Path(tempfile.mkdtemp()) / "chat")
    conversation = chat.create_conversation(db, user=user, matter_id=matter.id, title=task["id"], actor=user.email)
    message = chat.record_user_message(db, conversation=conversation, content=task["question"],
                                       document_ids=[documents[d].id for d in task.get("attach", []) if d in documents])
    writer = CapturingWriter()
    reply = chat.send_message(db, conversation=conversation, content=task["question"], drafting_service=drafting(writer),
                              actor=user.email, current_message_id=message.id)
    result = {"status": "payload" if writer.payloads else ("blockiert" if reply.blocked else "ohne_cloud_aufruf"),
              "reply_excerpt": (reply.content or "")[:160]}
    if writer.payloads:
        result["_payload_text"] = _payload_text(writer.payloads[-1])
    return result


def _run_draft(db, user, matter, task, drafting, case, Draft, EditorService, AttorneyInstructionService,  # noqa: ANN001, N803
               DraftDocxExportService, DraftPdfExportService) -> dict:  # noqa: N803
    writer = CapturingWriter(echo=True)
    service = drafting(writer)
    outcome = service.create_draft(matter.id, "formulate_draft", db, attorney_anmerkungen=task["question"], actor=user.email)
    result: dict = {"status": "ok" if outcome.success else "blockiert", "blocked_reasons": list(outcome.blocked_reasons or [])[:3]}
    if writer.payloads:
        result["_payload_text"] = _payload_text(writer.payloads[-1])
    if not outcome.success or not outcome.draft_id:
        return result
    draft = db.get(Draft, outcome.draft_id)
    client_name = next(p["name"] for p in case["parties"] if p["id"] == case["matter"]["client_party"])
    result.update(stored=draft is not None, draft_placeholders=sorted(set(_PLACEHOLDER.findall(draft.content or ""))),
                  client_reconstructed=client_name in (draft.content or ""))
    # Bearbeitung
    editor = EditorService(AttorneyInstructionService(service))
    editor.autosave_draft(db, draft=draft, content=(draft.content or "") + "\n\nErgaenzung der Kanzlei: Frist notiert.",
                          subject=None, recipient=None, content_format="text", actor=user.email)
    db.refresh(draft)
    result["edit_saved"] = "Ergaenzung der Kanzlei" in (draft.content or "")
    # Export
    import docx
    import pymupdf

    docx_text = "\n".join(p.text for p in docx.Document(DraftDocxExportService().export_draft(draft, matter)).paragraphs)
    pdf_bytes = DraftPdfExportService().export_draft(draft, matter).getvalue()
    pdf_text = "".join(page.get_text() for page in pymupdf.open(stream=pdf_bytes, filetype="pdf"))
    result.update(export_docx_has_edit="Ergaenzung der Kanzlei" in docx_text, export_pdf_has_edit="Ergaenzung der Kanzlei" in pdf_text,
                  export_placeholders=sorted(set(_PLACEHOLDER.findall(docx_text + pdf_text))))
    return result


def _preservation_summary(items: list[dict]) -> dict:
    """Erhalt im Schriftsatz-Payload je Kategorie als "vorhanden/gesamt" plus fehlende Werte (nicht gemittelt)."""
    out: dict = {}
    for key in ("keep", "chronologie", "normen"):
        present = sum(len(i[key]["present"]) for i in items)
        total = present + sum(len(i[key]["missing"]) for i in items)
        out[f"erhalt_{key}"] = f"{present}/{total}"
        out[f"erhalt_{key}_fehlend"] = sorted({v for i in items for v in i[key]["missing"]})
    both = sum(len(i["widersprueche_beide_seiten"]) for i in items)
    out["widersprueche_beide_seiten"] = f"{both}/{both + sum(len(i['widersprueche_unvollstaendig']) for i in items)}"
    return out


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {"n": len(ordered), "median": ordered[len(ordered) // 2], "max": ordered[-1]}


def _runtime_and_size(docs: list[dict], tasks: list[dict]) -> dict:
    """Laufzeit je Stufe (Sekunden, lokal gemessen) und Payload-Groesse (Zeichen) je Aufgabenart. Kosten werden NICHT
    geschaetzt: offline gibt es keine Tokenzahlen des Anbieters (eine Zeichen-pro-Token-Annahme waere erfunden)."""
    out = {"laufzeit_dokument_s": _stats([d["seconds"] for d in docs if "seconds" in d])}
    for kind in ("chat", "schriftsatz", "suche"):
        out[f"laufzeit_{kind}_s"] = _stats([t["seconds"] for t in tasks if t["kind"] == kind and "seconds" in t])
        if kind != "suche":
            out[f"payload_zeichen_{kind}"] = _stats([t["payload_chars"] for t in tasks if t["kind"] == kind and "payload_chars" in t])
    return out


def summarise(results: list[dict]) -> dict:
    def per_split(split: str) -> dict:
        rows = [r for r in results if r["split"] == split]
        docs = [d for r in rows for d in r["documents"].values()]
        tasks = [t for r in rows for t in r["tasks"]]
        chats = [t for t in tasks if t["kind"] in ("chat", "schriftsatz") and "payload_chars" in t]
        searches = [t for t in tasks if t["kind"] == "suche"]
        drafts = [t for t in tasks if t["kind"] == "schriftsatz"]
        return {
            "cases": len(rows),
            "kritisch": [dict(c, case=r["case"]) for r in rows for c in r["critical"]],
            "extraktion_werte": f"{sum(len(d.get('values_found', [])) for d in docs)}/{sum(d.get('values_total', 0) for d in docs)}",
            "ocr_dokumente": sum(1 for d in docs if d["format"] == "scan"),
            "ocr_werte": f"{sum(len(d.get('values_found', [])) for d in docs if d['format'] == 'scan')}/"
                         f"{sum(d.get('values_total', 0) for d in docs if d['format'] == 'scan')}",
            "dokumentfehler": sum(1 for d in docs if d.get("status") in ("error", "failed", "pending")),
            # nur Datumsgleichheit - Art der Frist und Quelldokument werden NICHT geprueft
            "fristen_datum_recall": f"{sum(int(r['deadlines']['recall'].split('/')[0]) for r in rows)}/"
                              f"{sum(int(r['deadlines']['recall'].split('/')[1]) for r in rows)}",
            "fristen_nicht_in_referenz": sum(len(r["deadlines"]["not_in_reference"]) for r in rows),
            "davon_bekannte_falldaten": sum(len(r["deadlines"]["known_dates_as_deadline"]) for r in rows),
            "davon_ungeklaert": [f"{r['case']}:{d}" for r in rows for d in r["deadlines"]["unexplained"]],
            # nur ob ueberhaupt ein Typ vergeben wurde - die Korrektheit gegen den Referenztyp ist NICHT bewertet
            "klassifiziert_nicht_unbekannt": f"{sum(1 for d in docs if d.get('classified') not in (None, 'Unbekannt'))}/{len(docs)}",
            "payload_aufgaben": len(chats),
            "pii_leaks": sum(len(t["pii_leaks"]) for t in chats),
            "fremde_akte_im_payload": sum(len(t["cross_matter_values"]) for t in chats),
            "benoetigte_werte_im_payload": f"{sum(len(t['needed_present']) for t in chats)}/"
                                           f"{sum(len(t['needed_present']) + len(t['needed_missing']) for t in chats)}",
            "aufgaben_ohne_payload": [f"{r['case']}/{t['id']}:{t.get('status')}" for r in rows for t in r["tasks"]
                                      if t["kind"] in ("chat", "schriftsatz") and "payload_chars" not in t],
            # top1: ein erwartetes Dokument steht auf Platz 1; top5: alle erwarteten Dokumente unter den ersten fuenf.
            "suche_top1": f"{sum(1 for t in searches if 1 in (t.get('rank') or []))}/{len(searches)}",
            "suche_top5": f"{sum(1 for t in searches if t.get('rank') and all(t['rank']))}/{len(searches)}",
            "schriftsatz_ok": f"{sum(1 for t in drafts if t.get('status') == 'ok')}/{len(drafts)}",
            "export_mit_bearbeitung": f"{sum(1 for t in drafts if t.get('export_docx_has_edit') and t.get('export_pdf_has_edit'))}/{len(drafts)}",
            **_preservation_summary([t["erhalt"] for t in drafts if "erhalt" in t]),
            # Fehlalarm-Kandidaten der Maskierung je Akte (dedupliziert ueber die Aufgaben), mit Kategorie
            "maskiert_ohne_referenz": sorted({f"{r['case']}:{m['kategorie']}:{m['original']}" for r in rows for t in r["tasks"]
                                              for m in t.get("maskiert_ohne_referenz", [])}),
            **_runtime_and_size(docs, tasks),
            # erfundene Fundstellen einer (simulierten) Modellantwort: markiert / im Entwurf enthalten
            "erfundene_fundstellen_markiert": f"{sum(len(t['quellenintegritaet'].get('als_pruefpunkt_markiert', [])) for t in drafts if 'quellenintegritaet' in t)}/"
                                              f"{sum(len(t['quellenintegritaet'].get('im_entwurf', [])) for t in drafts if 'quellenintegritaet' in t)}",
            "aufgabenfehler": [f"{r['case']}/{t['id']}" for r in rows for t in r["tasks"] if t.get("status") == "error"],
        }

    return {"dev": per_split("dev"), "test": per_split("test")}


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.rstrip("\n")
    except Exception:  # noqa: BLE001
        return "unbekannt"


def provenance() -> dict:
    dirty = [p for p in (line[3:].strip('"') for line in _git("status", "--porcelain").splitlines())
             if not p.startswith(("assets/", "benchmarks/legal_e2e_v1/results"))]
    corpus_hash = hashlib.sha256()
    for path in sorted(CORPUS_DIR.rglob("*.json")):
        if "results" not in path.name:
            corpus_hash.update(path.read_bytes())
    sha = lambda p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()[:16]  # noqa: E731
    return {"git_commit": _git("rev-parse", "--short", "HEAD"), "git_dirty_files": dirty,
            "corpus_sha256": corpus_hash.hexdigest()[:16], "code_fingerprint": {f: sha(f) for f in _FINGERPRINT_FILES},
            "python": platform.python_version()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "test"], default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    report = validate_corpus()
    if not report.ok:
        print("Korpus ungueltig:", *report.errors, sep="\n  ")
        return 1
    cases = load_cases(split=args.split)
    workdir = Path(tempfile.mkdtemp(prefix="legal_e2e_"))
    started = time.perf_counter()
    try:
        outcome = run(cases, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    output = {"benchmark": "legal_e2e_v1", "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              **provenance(), "environment": outcome["environment"], "seconds_total": round(time.perf_counter() - started, 1),
              "kosten": "keine (kein Cloud-Aufruf); echte Modellkosten NICHT VERIFIZIERT - gemessen ist nur die Payload-Groesse in Zeichen",
              "nicht_verifiziert": ["Antwortqualitaet eines echten Modells", "Kosten", "installierter Build (eigener Lauf)"],
              "summary": summarise(outcome["results"]), "aktenzuordnung": outcome["matching"],
              "fremder_anhang": outcome["foreign_attachment"], "ocr_schlechter_scan": outcome["ocr_degraded"],
              "results": outcome["results"]}
    out = Path(args.out) if args.out else CORPUS_DIR / "results.json"
    out.write_text(json.dumps(output, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(output["summary"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
