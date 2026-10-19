"""§80 (E1/E4): Treiber fuer die geplanten Aufrufe des Antwort-Benchmarks - Import der synthetischen Akten, Ausloesen
des regulaeren Lexono-Chat- bzw. Schriftsatz-Workflows je geplantem Aufruf, Ablage (answer_store), Kostenbuch und
Wiederanlauf. Pro Aufruf: `CallGuard.precheck` vor dem Workflow, `CallGuard.authorize` unmittelbar vor dem Provider.

KEIN echter Modelllauf: es gibt keine Kommandozeile, der Provider wird eingereicht, und ein Provider aus
`app.ai_providers` (echter Anthropic-/Gateway-Client) wird nur gegen einen lokalen Pruefendpunkt (127.0.0.1 mit
LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT=1, ohne LEXONO_GATEWAY_URL) zugelassen (`assert_local_only`). Die Freigabe eines
echten Laufs (C-14, J1-J6, C-1-C-17) ist eine Owner-Entscheidung und erfordert eine bewusste Code-Aenderung hier.

Zustaende je Aufruf: geplant -> in_bearbeitung -> erledigt | antwort_verworfen | fehlgeschlagen | blockiert |
abgebrochen_vor_versand. Wiederanlauf: nur Aufrufe ohne Zustand werden ausgefuehrt; ein Aufruf "in_bearbeitung"
(Absturz waehrend des Aufrufs - eventuell gesendet, Kosten unbekannt) wird NIE automatisch wiederholt: der Lauf stoppt
mit Diagnose. Geaenderte Konfiguration, Commit, Sperre, Plan oder Freigabe -> Stopp mit Diagnose, kein Aufruf."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from scripts.legal_bench import answer_exec, answer_run, corpus, e2e, net_fence
from scripts.legal_bench.e2e import _leaked
from app.privacy.api_logger import categorize_block_reasons
from scripts.legal_bench.answer_store import RunStore, StoreError, now

TERMINAL_OK = {"erledigt", "antwort_verworfen"}
TERMINAL_SENT = {"erledigt", "antwort_verworfen", "fehlgeschlagen", "geklaert_als_gesendet"}
FAILED = {"fehlgeschlagen", "antwort_verworfen", "geklaert_als_gesendet"}
#: §81: ein Datenschutzbefund beendet den Lauf DAUERHAFT (abort_on_first_privacy_finding) - auch ueber Wiederanlaeufe
PRIVACY_STOP = {"blockiert", "abgebrochen_vor_versand", "datenschutzbefund_antwort"}
CLARIFY_DECISIONS = {"als_gesendet_werten", "nicht_gesendet_belegt"}


class RealCloudDisabled(RuntimeError):
    """Echter Cloud-Endpunkt - im Benchmark-Treiber gesperrt (kein echter Modelllauf ohne Freigabe C-14)."""


#: §83: Module, deren Objekte Netzverbindungen zu Cloud-Diensten aufbauen koennen (auch als Basisklasse einer Unterklasse)
_CLOUD_MODULES = ("app.ai_providers", "anthropic", "httpx", "httpcore", "requests", "urllib3", "openai", "aiohttp")
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


_CLIENT_ATTRS = ("base_url", "_client", "request", "send", "post", "write")


def _is_cloud_object(obj) -> bool:  # noqa: ANN001
    """Objekt aus einem Cloud-/HTTP-Modul (auch ueber eine Basisklasse) MIT Endpunkt- oder Sende-Faehigkeit - reine
    Datenobjekte (z. B. ClaudeWritingResult, anthropic.types.*) zaehlen nicht."""
    from_cloud = any((getattr(klass, "__module__", "") or "").startswith(_CLOUD_MODULES) for klass in type(obj).__mro__[:-1])
    return from_cloud and any(hasattr(obj, attr) for attr in _CLIENT_ATTRS)


def _cloud_objects(obj, depth: int = 0, seen: set | None = None):  # noqa: ANN001, ANN202
    """Alle Cloud-Client-Objekte im Provider (Attribute, Listen, Dicts; begrenzte Tiefe) - §83: vorher wurde nur die
    Klasse des uebergebenen Objekts betrachtet, ein Wrapper/eine Unterklasse umging die Sperre."""
    seen = seen if seen is not None else set()
    if id(obj) in seen or depth > 5 or isinstance(obj, (str, bytes, int, float, bool, type(None))):
        return
    seen.add(id(obj))
    if _is_cloud_object(obj):
        yield obj
        return  # Inneres des Clients nicht weiter durchsuchen; der Endpunkt wird am Client selbst geprueft
    if isinstance(obj, dict):
        children = list(obj.values())
    elif isinstance(obj, (list, tuple, set, frozenset)):
        children = list(obj)
    else:
        children = list(getattr(obj, "__dict__", {}).values())
        children += [getattr(obj, name) for name in getattr(type(obj), "__slots__", ()) if hasattr(obj, name)]
    for child in children:
        yield from _cloud_objects(child, depth + 1, seen)


def _endpoint(client) -> str | None:  # noqa: ANN001
    for candidate in (client, getattr(client, "_client", None)):
        base = getattr(candidate, "base_url", None) if candidate is not None else None
        if base:
            return str(base)
    return None


def assert_local_only(provider) -> None:  # noqa: ANN001
    """Kein echter Cloud-Endpunkt: jeder im Provider gefundene Cloud-Client muss nachweislich auf einen lokalen
    Pruefendpunkt zeigen (127.0.0.1/localhost/::1, LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT=1); ohne erkennbaren Endpunkt wird
    gesperrt (fail-closed). Zweite Linie: `net_fence.loopback_only` waehrend der Ausfuehrung."""
    if os.environ.get("LEXONO_GATEWAY_URL", "").strip():
        raise RealCloudDisabled("LEXONO_GATEWAY_URL gesetzt - Relay-/Gateway-Weg ist im Benchmark-Treiber gesperrt")
    for client in _cloud_objects(provider):
        base = _endpoint(client)
        host = urlparse(base).hostname if base else None
        name = f"{type(client).__module__}.{type(client).__qualname__}"
        if host is None:
            raise RealCloudDisabled(f"Cloud-Client {name} ohne erkennbaren Endpunkt - gesperrt (C-14)")
        if host not in _LOOPBACK_HOSTS or os.environ.get("LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT") != "1":
            raise RealCloudDisabled(f"Provider-Endpunkt {host} ({name}) ist kein lokaler Pruefendpunkt - echter "
                                    "Modelllauf ist nicht freigegeben (C-14)")


def allowed_programs() -> list[tuple[str, tuple[str, ...] | None]]:
    """§84: Programme, die waehrend eines Laufs starten duerfen - nur lokale ohne Netzwerkbedarf (ANNAHME je Programm, keine
    OS-Isolation): `git rev-parse HEAD` (Commit-Pruefung vor jedem Aufruf) und Tesseract (OCR beim Aktenimport)."""
    programs: list[tuple[str, tuple[str, ...] | None]] = [("git", ("rev-parse", "HEAD"))]
    tesseract = e2e._tesseract()
    if tesseract:
        programs.append((tesseract, None))
    return programs


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class BenchmarkDriver:
    def __init__(self, config_path: Path, store: RunStore, provider, *, workdir: Path | None = None,  # noqa: ANN001
                 embedding=None, protect: dict[str, list[str]] | None = None) -> None:  # noqa: ANN001
        self.config_path = Path(config_path)
        self.store = store
        self.provider = provider
        self.workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="lexono_bench_"))
        self.embedding = embedding
        self.protect = protect

    # --- Zustand ------------------------------------------------------------------------------------------------------

    def _stop(self, run_id: str | None, state: dict | None, status: str, diagnosis: list[str]) -> dict:
        if run_id and state is not None:
            state.update(status=status, diagnosis=diagnosis)
            state.setdefault("events", []).append({"at": now(), "event": status, "diagnose": diagnosis[:10]})
            self.store.save_state(run_id, state)
        return {"status": status, "diagnose": diagnosis, "state": state}

    def run(self) -> dict:
        assert_local_only(self.provider)
        config = answer_run.load_config(self.config_path)
        run_id = config.get("run_id") if isinstance(config, dict) else None
        state = None
        if isinstance(run_id, str) and self.store.exists(run_id):
            try:
                state = self.store.load_state(run_id)
            except StoreError as exc:
                return {"status": "gestoppt_ablage_beschaedigt", "diagnose": [str(exc)], "state": None}
        try:
            guard = answer_exec.CallGuard(self.config_path, protect=self.protect)
        except answer_run.NotApproved as exc:
            return self._stop(run_id if state else None, state, "gestoppt_nicht_freigegeben", exc.reasons or [str(exc)])
        start = guard.start_fingerprints()
        if state is None:
            state = self.store.create(run_id, {
                "status": "laeuft", "created_at": now(), "start": start,
                "plan": [answer_exec.call_id(c) for c in guard.plan["calls"]],
                "calls": {}, "ledger": {"geschaetzt_eur": 0.0, "tatsaechlich_eur": 0.0, "unbekannt": []},
                "events": [{"at": now(), "event": "gestartet"}]})
        else:
            changed = [f"{k}: {state['start'].get(k)} -> {v}" for k, v in start.items() if state["start"].get(k) != v]
            if changed:
                return self._stop(run_id, state, "gestoppt_geaendert",
                                  ["Seit dem Start geaendert - der Lauf ist nur fuer den urspruenglichen Stand freigegeben:",
                                   *changed])
            unclear = [cid for cid, c in state["calls"].items() if c["status"] == "in_bearbeitung"]
            if unclear:
                return self._stop(run_id, state, "gestoppt_unklarer_aufruf", [
                    f"Aufruf {cid} war beim Abbruch in Bearbeitung - moeglicherweise gesendet, Kosten unbekannt; keine "
                    "automatische Wiederholung (Klaerung durch die zustaendige Person)" for cid in unclear])
            if state.get("status") == "abgeschlossen":
                return {"status": "abgeschlossen", "diagnose": [], "state": state}
            stopped = [f"{cid}: {c['status']}" for cid, c in state["calls"].items() if c["status"] in PRIVACY_STOP]
            if stopped:
                return self._stop(run_id, state, "gestoppt_datenschutz_frueher", [
                    "Frueherer Datenschutzbefund bzw. Blockierung beendet den Lauf dauerhaft "
                    "(abort_on_first_privacy_finding) - Ursache klaeren, dann neuer Lauf mit neuer run_id:", *stopped])
            sent = [tuple(c["key"]) for c in state["calls"].values() if c["status"] in TERMINAL_SENT]
            guard.restore(sent=sent, failed_calls=sum(1 for c in state["calls"].values() if c["status"] in FAILED),
                          actual_cost_eur=state["ledger"]["tatsaechlich_eur"], unknown_cost_calls=state["ledger"]["unbekannt"],
                          estimated_cost_eur=state["ledger"]["geschaetzt_eur"])
            state.update(status="laeuft", diagnosis=[])
            state["events"].append({"at": now(), "event": "fortgesetzt", "bereits": len(sent)})
            self.store.save_state(run_id, state)
        return self._execute(run_id, state, guard)

    # --- Ausfuehrung --------------------------------------------------------------------------------------------------

    def _execute(self, run_id: str, state: dict, guard: answer_exec.CallGuard) -> dict:
        pending = [c for c in guard.plan["calls"] if answer_exec.call_id(c) not in state["calls"]]
        if not pending:
            return self._finish(run_id, state)
        # §83/§84: zweite Linie - nur Loopback-Verbindungen/-Namensaufloesung, Prozessstart nur fuer zugelassene Programme
        with net_fence.loopback_only(allowed_programs()):
            return self._execute_fenced(run_id, state, guard, pending)

    def _execute_fenced(self, run_id: str, state: dict, guard: answer_exec.CallGuard, pending: list) -> dict:
        env = _Environment(self.workdir, self.embedding, guard, self.provider)
        try:
            for call in pending:
                cid = answer_exec.call_id(call)
                try:
                    guard.precheck(answer_exec.call_key(call))  # vor dem Workflow: Freigabe, Commit, Lock, Budget ...
                except answer_exec.ExecutionAborted as exc:
                    return self._stop(run_id, state, "gestoppt_vor_aufruf", [f"{cid}: {r}" for r in exc.reasons])
                state["calls"][cid] = {"key": list(answer_exec.call_key(call)), "status": "in_bearbeitung",
                                       "started_at": now()}
                self.store.save_state(run_id, state)
                outcome = env.perform(call)
                entry = self._record(run_id, state, guard, call, outcome)
                self.store.save_state(run_id, state)
                if entry["status"] not in TERMINAL_OK:
                    return self._stop(run_id, state, f"gestoppt_{entry['status']}",
                                      [f"{cid}: {r}" for r in entry.get("gruende", [])] or [f"{cid}: {entry['status']}"])
                if guard.unknown_cost_calls:
                    return self._stop(run_id, state, "gestoppt_kosten_unbekannt",
                                      [f"tatsaechliche Kosten unbekannt fuer {', '.join(guard.unknown_cost_calls)} - "
                                       "sichere Abbruchregel: kein weiterer Aufruf"])
        finally:
            env.close()
        return self._finish(run_id, state)

    def _record(self, run_id: str, state: dict, guard, call: dict, outcome: dict) -> dict:  # noqa: ANN001
        cid = answer_exec.call_id(call)
        last = outcome.get("writer") or {}
        sent = last.get("outcome") in ("gesendet", "antwort_erhalten", "providerfehler")
        entry = {"key": list(answer_exec.call_key(call)), "finished_at": now(), "gesendet": sent,
                 "cost_eur": last.get("cost_eur") if sent else 0.0}
        if guard.aborted is not None and not sent:
            entry.update(status="abgebrochen_vor_versand", gruende=list(guard.aborted))
        elif not sent:
            entry.update(status="blockiert", gruende=outcome.get("gruende") or ["Lexono-Pfad hat nicht gesendet"])
        elif last.get("outcome") == "providerfehler":
            entry.update(status="fehlgeschlagen", gruende=[f"Providerfehler {last.get('error_type')}; Kosten unbekannt"])
        else:
            # Datensparsamkeit (§80, E2): Antworttext = pseudonymisierte Modellantwort; von der lokal re-identifizierten
            # Fassung (die die Kanzlei sieht) nur der Hash. Keine Spezifikation braucht geschuetzte Werte.
            answer = last.get("antwort_pseudonymisiert") or ""
            local_answer = outcome.get("answer_text") or ""
            status = "erledigt" if outcome.get("success") else "antwort_verworfen"
            leaks = sorted({f"{c}:{v[:3]}..." for c, values in guard.protect.items() for v in values if _leaked(v, answer)})
            if leaks:  # Modellantwort mit geschuetztem Wert: nicht ablegen, Lauf beenden
                guard.privacy_findings += leaks
                entry.update(status="datenschutzbefund_antwort",
                             gruende=[f"geschuetzte Werte in der Modellantwort ({len(leaks)}) - Text nicht abgelegt"])
                state["calls"][cid] = entry
                return entry
            if status == "antwort_verworfen":
                guard.record_failure()
            config = guard.config
            record = {"answer_id": f"{run_id}:{cid}", "spec_id": call["spec_id"], "task_id": call["task_id"],
                      "repetition": call["repetition"], "case": call["case"], "kind": call["kind"],
                      "model_id": config["model"]["model_id"], "model_version": config["model"]["model_version"],
                      "prompt_version": config["pipeline"]["prompt_version"], "lexono_commit": guard.plan["git_commit"],
                      "payload_sha256": last.get("payload_sha256"), "payload_pseudonymisiert": last.get("payload_json"),
                      "answer_sha256": _sha(answer), "answer_text": answer, "antwort_art": "pseudonymisiert (Modellausgabe)",
                      "antwort_lokal_sha256": _sha(local_answer) if local_answer else None,
                      "created_at": now(), "status": status,
                      "gruende": outcome.get("gruende", []), "usage": last.get("usage"),
                      "kosten": {"tatsaechlich_eur": last.get("cost_eur"),
                                 "geschaetzt_eur": config["limits"]["price_per_call_eur_estimate"]}}
            missing = answer_run.validate_answer_record(record) if status == "erledigt" else []
            if missing:
                status = "antwort_verworfen"
                record.update(status=status, gruende=[*record["gruende"], *missing])
                guard.record_failure()
            entry.update(status=status, record_sha256=self.store.write_record(run_id, cid, record),
                         gruende=record["gruende"])
        state["calls"][cid] = entry
        state["ledger"] = {"geschaetzt_eur": guard.estimated_cost_eur, "tatsaechlich_eur": guard.actual_cost_eur,
                           "unbekannt": list(guard.unknown_cost_calls),
                           "restbudget_eur": guard.remaining_budget_eur(),
                           "aufrufe_gesendet": len(guard.sent), "fehlversuche": guard.failed_calls}
        return entry

    def _finish(self, run_id: str, state: dict) -> dict:
        done = all(state["calls"].get(cid, {}).get("status") in TERMINAL_OK for cid in state["plan"])
        return self._stop(run_id, state, "abgeschlossen" if done else "unvollstaendig", [])


def resolve_unclear(store: RunStore, run_id: str, cid: str, *, entscheidung: str, person: str, rolle: str,
                    begruendung: str, beleg: str, tatsaechlich_eur: float | None = None) -> dict:
    """§81 (E4): nachvollziehbare Klaerung EINES Aufrufs im Zustand "in_bearbeitung" (Absturz/Timeout waehrend des
    Aufrufs - eventuell gesendet, Kosten unbekannt). Nie automatisch; ohne Klaerung bleibt der Lauf gesperrt.

    - "als_gesendet_werten": der Aufruf zaehlt als gesendet und fehlgeschlagen und wird NIE wiederholt. Kosten nur mit
      Beleg (`tatsaechlich_eur`, z. B. aus der Provider-Abrechnung); ohne Betrag bleiben sie UNBEKANNT - dann bleibt
      der Lauf nach der sicheren Abbruchregel gestoppt.
    - "nicht_gesendet_belegt": nur mit Beleg, dass KEINE Anfrage den Provider erreichte (z. B. Recorder/Provider-
      Protokoll). Der Aufruf wird beim naechsten Lauf genau einmal erneut ausgefuehrt - mit allen Pruefungen.
    Die Klaerung ersetzt keine Freigabe; Konfiguration, Commit, Plan, Sperre und Freigaben prueft der naechste Lauf neu."""
    import math

    state = store.load_state(run_id)  # Integritaet zuerst - beschaedigte Ablage: keine Klaerung
    entry = state["calls"].get(cid)
    if entry is None or entry.get("status") != "in_bearbeitung":
        raise StoreError(f"Aufruf {cid} ist nicht unklar (Status {entry.get('status') if entry else 'unbekannt'})")
    problems = []
    if entscheidung not in CLARIFY_DECISIONS:
        problems.append(f"entscheidung muss {sorted(CLARIFY_DECISIONS)} sein")
    if not (isinstance(person, str) and person.strip() and isinstance(rolle, str) and rolle.strip()):
        problems.append("person und rolle angeben")
    if not isinstance(begruendung, str) or len(begruendung.strip()) < 20:
        problems.append("begruendung mit mindestens 20 Zeichen")
    if not isinstance(beleg, str) or len(beleg.strip()) < 10:
        problems.append("beleg (Quelle der Feststellung) mit mindestens 10 Zeichen")
    if tatsaechlich_eur is not None and (entscheidung != "als_gesendet_werten" or isinstance(tatsaechlich_eur, bool)
                                         or not isinstance(tatsaechlich_eur, (int, float))
                                         or not math.isfinite(tatsaechlich_eur) or tatsaechlich_eur < 0):
        problems.append("tatsaechlich_eur nur bei als_gesendet_werten und als endliche Zahl >= 0")
    if problems:
        raise StoreError("Klaerung unvollstaendig: " + "; ".join(problems))
    klaerung = {"at": now(), "call_id": cid, "entscheidung": entscheidung, "person": person.strip(),
                "rolle": rolle.strip(), "begruendung": begruendung.strip(), "beleg": beleg.strip(),
                "tatsaechlich_eur": tatsaechlich_eur}
    state.setdefault("klaerungen", []).append(klaerung)
    ledger = state["ledger"]
    if entscheidung == "als_gesendet_werten":
        state["calls"][cid] = {**entry, "status": "geklaert_als_gesendet", "gesendet": True, "finished_at": now(),
                               "cost_eur": tatsaechlich_eur, "gruende": ["nach Absturz geklaert: als gesendet gewertet"]}
        if tatsaechlich_eur is None:
            ledger["unbekannt"] = sorted(set(ledger.get("unbekannt", [])) | {cid})
        else:
            ledger["tatsaechlich_eur"] = round(ledger.get("tatsaechlich_eur", 0.0) + tatsaechlich_eur, 6)
    else:
        del state["calls"][cid]  # wieder offen - der naechste Lauf fuehrt ihn EINMAL mit allen Pruefungen aus
    state.setdefault("events", []).append({"at": now(), "event": "geklaert", "call_id": cid, "entscheidung": entscheidung})
    state["status"] = "geklaert_wartet_auf_fortsetzung"
    store.save_state(run_id, state)
    return state


class _Environment:
    """Isolierte Laufumgebung: In-Memory-Datenbank (nie die produktive), alle Akten des Plans importiert (Mandanten-
    trennung in einer gemeinsamen DB wie im E2E-Benchmark), regulaerer DraftingService mit Gateway und GuardedWriter."""

    def __init__(self, workdir: Path, embedding, guard: answer_exec.CallGuard, provider) -> None:  # noqa: ANN001
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
        from app.chat.service import ChatService
        from app.documents.service import DocumentProcessingService
        from app.drafting.service import DraftingService
        from app.ingestion.intake import IntakeService
        from app.models import Client, Matter, Party, Role, User
        from app.models.base import Base
        from app.privacy.gateway import ClaudePrivacyGateway
        from app.research.service import LegalResearchService
        from app.search.service import DocumentSearchService

        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        role = Role(name="anwalt")
        self.db.add(role)
        self.db.flush()
        self.user = User(email="benchmark@kanzlei.test", password_hash="x", role_id=role.id)
        self.db.add(self.user)
        self.db.commit()
        provider_embedding, _label = embedding or e2e._embedding_provider()
        search = DocumentSearchService(provider_embedding)
        tesseract = e2e._tesseract()
        processing = DocumentProcessingService(ocr_enabled=tesseract is not None, tesseract_cmd=tesseract)
        intake = IntakeService(workdir / "intake")
        self.writer = answer_exec.GuardedWriter(provider, guard)
        self.drafting = DraftingService(RuleBasedLocalAIProvider(search),
                                        LegalResearchService(search, min_score_for_sufficient=0.0), search,
                                        ClaudePrivacyGateway(), self.writer, model_name=guard.config["model"]["model_id"])
        self.chat = ChatService(workdir / "chat")
        self.matters, self.documents = {}, {}
        cases = {c["id"]: c for c in corpus.load_cases()}
        for case_id in dict.fromkeys(c["case"] for c in guard.plan["calls"]):
            case = cases[case_id]
            parties = {p["id"]: p for p in case["parties"]}
            client = Client(name=parties[case["matter"]["client_party"]]["name"])
            matter = Matter(client=client, title=case["matter"]["title"],
                            reference_number=case["matter"].get("reference_number"))
            self.db.add_all([client, matter])
            self.db.flush()
            for party in case["parties"]:
                if party["id"] != case["matter"]["client_party"]:
                    self.db.add(Party(matter_id=matter.id, name=party["name"],
                                      role=e2e.PARTY_ROLE_LABEL.get(party["role"], party["role"])))
            self.db.commit()
            files = e2e.render_case(case, workdir / "rendered")
            docs = {}
            for spec in case["documents"]:
                document = intake.ingest_file(files[spec["id"]], self.db, stability_timeout_seconds=5.0)
                document.matter_id = matter.id
                self.db.commit()
                processing.process_document(document, self.db, actor="benchmark")
                self.db.refresh(document)
                search.index_document(document, self.db)
                docs[spec["id"]] = document
            self.db.commit()
            self.matters[case_id], self.documents[case_id] = matter, docs
        self.cases = cases

    def perform(self, call: dict) -> dict:
        matter = self.matters[call["case"]]
        task = next(t for t in self.cases[call["case"]]["reference"]["tasks"] if t["id"] == call["task_id"])
        self.writer.begin(call)
        if call["kind"] == "chat":
            conversation = self.chat.create_conversation(self.db, user=self.user, matter_id=matter.id,
                                                         title=answer_exec.call_id(call), actor=self.user.email)
            docs = self.documents[call["case"]]
            message = self.chat.record_user_message(self.db, conversation=conversation, content=call["question"],
                                                    document_ids=[docs[d].id for d in task.get("attach", []) if d in docs])
            reply = self.chat.send_message(self.db, conversation=conversation, content=call["question"],
                                           drafting_service=self.drafting, actor=self.user.email,
                                           current_message_id=message.id)
            return {"writer": self.writer.last, "success": not reply.blocked,
                    "answer_text": "" if reply.blocked else (reply.content or ""),
                    "gruende": ["chat_blockiert"] if reply.blocked else []}
        result = self.drafting.create_draft(matter.id, "formulate_draft", self.db, attorney_anmerkungen=call["question"],
                                            actor=self.user.email)
        # Rohe Blockiergruende koennen erkannte Werte im Klartext enthalten - abgelegt wird nur die inhaltsfreie Kategorie
        category = categorize_block_reasons(list(result.blocked_reasons or []))
        return {"writer": self.writer.last, "success": result.success,
                "answer_text": (result.draft_text or "") if result.success else "",
                "gruende": category.split(",") if category else []}

    def close(self) -> None:
        self.db.close()
        self.engine.dispose()
