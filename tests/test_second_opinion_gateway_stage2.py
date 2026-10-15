"""§43 Stufe 2 (nur Feature-Branch): Cloud-Zweitmeinung ueber den BESTEHENDEN Gateway-Pfad gegen einen lokalen
Fang-Server - nie ein echter Cloud-Aufruf, nur synthetische Dokumente.

Was der Fang-Server belegt: welche HTTP-Anfragen das Anthropic-SDK tatsaechlich abschickt (Anzahl, Pfad, Kopfzeilen,
JSON-Koerper inkl. System-/Nutzertext) und wie Lexono auf definierte Antworten, Fehler und Verzoegerungen reagiert.
Was er NICHT belegt: das Verhalten des echten Anthropic-Dienstes (Modellantworten, Latenz, Kosten, TLS, Speicherung
beim Anbieter) und die Vollstaendigkeit der Pseudonymisierung bei echten, vielfaeltigeren Texten."""

import json
import threading
import time
from collections.abc import Iterator
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai_providers.anthropic_writing_provider import AnthropicClaudeWritingProvider
from app.ai_providers.endpoint_policy import QA_LOOPBACK_FLAG, CloudEndpointNotAllowedError, resolve_anthropic_base_url
from app.models import Client, Deadline, Document, Matter
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.gateway_schema import (
    SECOND_OPINION_PURPOSE,
    PurposeNotAllowedForProviderError,
    UnsealedPayloadError,
)
from app.privacy.pseudonymizer import Pseudonymizer
from app.privacy.security_check import ALLOWED_PURPOSES
from app.second_opinion.comparison import Agreement, OpinionStatus, adopt_hint, snapshot_from
from app.second_opinion.provider import AnthropicSecondOpinionProvider
from app.second_opinion.service import SecondOpinionService

API_KEY = "sk-ant-test-fang-server-0000"  # Platzhalter, nie ein echter Schluessel

DOC_A = ("Beispiel Reinigung GmbH\nHerrn Maximilian Probst-Fiktiv\nLindenallee 12, 12345 Musterstadt\n"
         "E-Mail: m.probst@beispielpost.example\nIBAN DE89 3704 0044 0532 0130 00\nAz. 4 C 123/26\n"
         "2. Mahnung\nSehr geehrter Herr Probst-Fiktiv,\n"
         "bitte zahlen Sie den offenen Betrag aus unserer Rechnung Nr. 2026-0420 bis zum 31.07.2026.\n"
         "Der Termin zur Besprechung ist am 18.11.2026.\nMit freundlichen Grüßen")
SECRETS_A = ("Maximilian", "Probst-Fiktiv", "Lindenallee", "12345", "m.probst@beispielpost.example", "DE89",
             "3704 0044", "4 C 123/26", "2026-0420", "Beispiel Reinigung")
DOC_B = ("Amtsgericht Fiktivhausen\nLadung\nHerr Konrad Musterhuber, geb. 02.05.1975, wohnhaft Ahornweg 3, "
         "54321 Fiktivhausen, wird geladen.\nDie Klageschrift wurde am 18.08.2026 zugestellt.\n"
         "Termin zur mündlichen Verhandlung: 22.10.2026, 10:30 Uhr.")
SECRETS_B = ("Konrad", "Musterhuber", "02.05.1975", "Ahornweg", "54321")
FILENAME_A = "Mahnung_Probst-Fiktiv_vertraulich.pdf"


def _answer(**fields) -> str:
    data = {"typ": "Mahnung", "frist": ["31.07.2026"], "termin": ["18.11.2026"], "fristbeginn": [], "fristdauern": 0}
    data.update(fields)
    return json.dumps(data, ensure_ascii=False)


# --- Fang-Server --------------------------------------------------------------------------------------------------

class FangServer:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.reply_text = _answer()
        self.status = 200
        self.delay = 0.0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("content-length", 0))
                raw = self.rfile.read(length)
                server.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                        "raw": raw.decode("utf-8"), "body": json.loads(raw)})
                if server.delay:
                    time.sleep(server.delay)
                if server.status != 200:
                    body = json.dumps({"type": "error", "error": {"type": "api_error", "message": "simuliert"}})
                else:
                    body = json.dumps({
                        "id": "msg_fang", "type": "message", "role": "assistant", "model": "claude-sonnet-5",
                        "content": [{"type": "text", "text": server.reply_text}], "stop_reason": "end_turn",
                        "stop_sequence": None, "usage": {"input_tokens": 321, "output_tokens": 45}})
                try:
                    self.send_response(server.status)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body.encode())))
                    self.end_headers()
                    self.wfile.write(body.encode())
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def fang() -> Iterator[FangServer]:
    server = FangServer()
    yield server
    server.close()


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()  # produktiver Default: Presidio-NER fuer Pseudonymisierung UND Restrisiko-Scan


class Budget:
    def __init__(self, allowed=True, reason=None):
        self.allowed, self.reason = allowed, reason


def _provider(fang: FangServer, timeout: float = 5.0) -> AnthropicSecondOpinionProvider:
    base_url = resolve_anthropic_base_url({"ANTHROPIC_BASE_URL": fang.url, QA_LOOPBACK_FLAG: "1"})
    return AnthropicSecondOpinionProvider(api_key=API_KEY, model="claude-sonnet-5", base_url=base_url,
                                          timeout_seconds=timeout)


def _service(gateway, fang, *, max_chars=20000, budget=None, timeout=5.0, audit=None) -> SecondOpinionService:
    return SecondOpinionService(gateway=gateway, provider=_provider(fang, timeout), max_chars=max_chars,
                                budget_check=lambda: budget or Budget(), audit=audit)


# --- lokale Ergebnisse als echte ORM-Zeilen -------------------------------------------------------------------------

@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _local(db: Session):
    client = Client(name="Synthetische Mandantin")
    matter = Matter(client=client, title="Synthetische Akte")
    db.add_all([client, matter])
    db.flush()
    document = Document(file_path="/tmp/synth.pdf", matter_id=matter.id, extracted_text=DOC_A,
                        classified_type="Rechnung", classification_confidence=0.4)
    db.add(document)
    db.flush()
    db.add_all([
        Deadline(matter_id=matter.id, document_id=document.id, source_text="bis zum 31.07.2026",
                 due_date=date(2026, 7, 31), confidence=0.5, reasoning="Mögliche Frist", review_status="unreviewed"),
        Deadline(matter_id=matter.id, document_id=document.id, source_text="Briefdatum", due_date=date(2026, 7, 10),
                 confidence=0.5, reasoning="Mögliche Frist", review_status="confirmed"),
    ])
    db.commit()
    return document


def _state(db: Session) -> list[tuple]:
    rows = [("doc", d.id, d.classified_type, d.classification_confidence) for d in db.query(Document).all()]
    rows += [("frist", f.id, f.due_date, f.review_status, f.status, f.confidence, f.reasoning)
             for f in db.query(Deadline).order_by(Deadline.id).all()]
    return rows


def _snapshot(db: Session, document: Document):
    return snapshot_from(document.classified_type, db.query(Deadline).filter_by(document_id=document.id).all())


# --- Zweckfreigabe und Zweckbindung ---------------------------------------------------------------------------------

def test_purpose_is_allowed_exactly_once_and_bound_to_its_provider(gateway, fang) -> None:
    assert SECOND_OPINION_PURPOSE in ALLOWED_PURPOSES
    gate = gateway.prepare_request(purpose=SECOND_OPINION_PURPOSE, sachverhalt=DOC_A, review_mode="ask")
    assert gate.allowed and gate.payload.schreibauftrag == SECOND_OPINION_PURPOSE
    writer = AnthropicClaudeWritingProvider(api_key=API_KEY, model="claude-sonnet-5", base_url=fang.url)
    with pytest.raises(PurposeNotAllowedForProviderError):
        writer.write(gate.payload)
    with pytest.raises(PurposeNotAllowedForProviderError):
        next(writer.write_stream(gate.payload))
    draft_gate = gateway.prepare_request(purpose="formulate_draft", sachverhalt="Die Anlage ist mangelhaft.")
    with pytest.raises(PurposeNotAllowedForProviderError):
        _provider(fang).request(draft_gate.payload)
    assert fang.requests == []


def test_endpoint_policy_still_applies() -> None:
    with pytest.raises(CloudEndpointNotAllowedError):
        resolve_anthropic_base_url({"ANTHROPIC_BASE_URL": "http://127.0.0.1:9"})  # ohne QA-Freigabe
    with pytest.raises(CloudEndpointNotAllowedError):
        resolve_anthropic_base_url({"ANTHROPIC_BASE_URL": "https://example.invalid", QA_LOOPBACK_FLAG: "1"})


def test_unsealed_or_modified_payload_is_never_sent(gateway, fang) -> None:
    gate = gateway.prepare_request(purpose=SECOND_OPINION_PURPOSE, sachverhalt=DOC_A, review_mode="ask")
    tampered = gate.payload.model_copy(update={"anonymisierter_sachverhalt": DOC_A})
    with pytest.raises(UnsealedPayloadError):
        _provider(fang).request(tampered)
    assert fang.requests == []


# --- vollstaendiger Pfad: tatsaechlich gesendete Payload ------------------------------------------------------------

def test_full_path_sends_only_pseudonymized_text_and_yields_unreviewed_hints(gateway, fang, db) -> None:
    document = _local(db)
    before = _state(db)
    audit: list[tuple[str, dict]] = []
    result = _service(gateway, fang, audit=lambda event, details: audit.append((event, details))).request(
        document_text=document.extracted_text, local=_snapshot(db, document))

    assert result.comparison.status is OpinionStatus.OK
    assert len(fang.requests) == 1
    sent = fang.requests[0]
    assert sent["path"].endswith("/v1/messages")
    assert sent["headers"].get("x-api-key") == API_KEY
    body = sent["body"]
    assert body["model"] == "claude-sonnet-5" and body["thinking"] == {"type": "disabled"}
    assert set(body) <= {"model", "max_tokens", "thinking", "system", "messages"}  # keine Tools/Websuche
    user_text = body["messages"][0]["content"]
    # kein Klartext personenbezogener/vertraulicher Werte, keine Dateinamen, kein Hinweis auf Anschriftzuordnung
    for value in (*SECRETS_A, FILENAME_A, "Dateiname", "wohnt/sitzt"):
        assert value not in sent["raw"], value
    assert "[PERSON_01]" in user_text and "[IBAN_01]" in user_text and "[EMAIL_01]" in user_text
    assert "31.07.2026" in user_text and "18.11.2026" in user_text  # Fristdaten bleiben lesbar
    # Hinweise: getrennt, ungeprueft, lokale Daten nur markiert
    hints = {h.due_date: h for h in result.comparison.hints}
    assert hints[date(2026, 7, 31)].agreement is Agreement.MATCH
    assert hints[date(2026, 7, 10)].agreement is Agreement.ONLY_LOCAL
    assert hints[date(2026, 11, 18)].agreement is Agreement.ONLY_CLOUD
    assert all(h.source == "cloud" and h.review_state == "ungeprueft" for h in result.comparison.hints)
    assert result.comparison.type_agreement is False and result.comparison.cloud_type == "Mahnung"
    assert _state(db) == before  # lokale Klassifikation und Fristen unveraendert
    # Audit ohne Inhalt
    assert audit and audit[-1][0] == "second_opinion_ok"
    assert set(audit[-1][1]) == {"purpose", "status", "reason_categories", "payload_sha256", "input_tokens",
                                 "output_tokens"}
    assert not any(value in json.dumps(audit, ensure_ascii=False) for value in SECRETS_A)
    # Uebernahme nur ausdruecklich und nur als neuer, unbestaetigter Vorschlag
    proposal = adopt_hint(result.comparison, hints[date(2026, 11, 18)], user_confirmed=True,
                          actor="anwalt@kanzlei-qa.invalid")
    assert proposal.review_status == "unreviewed" and _state(db) == before


def test_no_content_of_another_matter_is_transmitted(gateway, fang) -> None:
    service = _service(gateway, fang)
    empty = snapshot_from("Unbekannt", [])
    service.request(document_text=DOC_A, local=empty,
                    known_entities={"person": ["Maximilian Probst-Fiktiv"]})
    fang.reply_text = _answer(typ="Gerichtliches Schreiben", frist=[], termin=["22.10.2026"],
                              fristbeginn=["18.08.2026"])
    result = service.request(document_text=DOC_B, local=empty, known_entities={"person": ["Konrad Musterhuber"]})
    assert result.comparison.status is OpinionStatus.OK, result.reasons
    assert len(fang.requests) == 2
    second_raw = fang.requests[1]["raw"]
    second_user = fang.requests[1]["body"]["messages"][0]["content"]
    for value in (*SECRETS_A, *SECRETS_B):
        assert value not in second_raw, value
    for value in ("31.07.2026", "2. Mahnung", "18.11.2026", "[IBAN_01]"):  # Inhalte der ersten Akte
        assert value not in second_user, value
    assert "[GEBURTSDATUM_01]" in second_user and "22.10.2026" in second_user


# --- Fail-Closed ------------------------------------------------------------------------------------------------

def test_residual_scan_blocks_when_pseudonymization_misses_a_name(fang, db) -> None:
    """Pseudonymisierung ohne NER (simuliertes Versagen), Restrisiko-Scan mit Presidio: der Scan blockiert."""
    weak = ClaudePrivacyGateway(pseudonymizer=Pseudonymizer())
    document = _local(db)
    before = _state(db)
    result = _service(weak, fang).request(document_text="Herrn Maximilian Probst-Fiktiv bitten wir, bis zum "
                                                        "31.07.2026 zu zahlen.", local=_snapshot(db, document))
    assert result.comparison.status is OpinionStatus.BLOCKED_PRIVACY
    assert result.comparison.hints == () and fang.requests == [] and _state(db) == before


def test_review_candidates_block_without_any_request(gateway, fang, db) -> None:
    document = _local(db)
    before = _state(db)
    result = _service(gateway, fang).request(
        document_text="Dazu erklärt Fuerst, die Anlage sei mangelhaft. Frist bis zum 31.07.2026.",
        local=_snapshot(db, document))
    assert result.comparison.status is OpinionStatus.REVIEW_REQUIRED
    assert result.comparison.hints == () and fang.requests == [] and _state(db) == before


def test_too_long_document_is_not_truncated_and_not_sent(gateway, fang, db) -> None:
    document = _local(db)
    before = _state(db)
    result = _service(gateway, fang, max_chars=len(DOC_A) - 1).request(document_text=DOC_A,
                                                                        local=_snapshot(db, document))
    assert result.comparison.status is OpinionStatus.TOO_LONG
    assert fang.requests == [] and _state(db) == before
    assert _service(gateway, fang, max_chars=len(DOC_A)).request(
        document_text=DOC_A, local=_snapshot(db, document)).comparison.status is OpinionStatus.OK


@pytest.mark.parametrize("budget", [Budget(False, "Kostenlimit erreicht"), "raises"])
def test_budget_exceeded_or_unknown_blocks_before_the_gateway(gateway, fang, db, budget) -> None:
    document = _local(db)
    before = _state(db)

    def check():
        if budget == "raises":
            raise RuntimeError("Datenbank nicht erreichbar")
        return budget

    service = SecondOpinionService(gateway=gateway, provider=_provider(fang), max_chars=20000, budget_check=check)
    result = service.request(document_text=DOC_A, local=_snapshot(db, document))
    assert result.comparison.status is OpinionStatus.BUDGET_EXCEEDED
    assert fang.requests == [] and _state(db) == before


def test_timeout_blocks_without_retry(gateway, fang, db) -> None:
    document = _local(db)
    before = _state(db)
    fang.delay = 2.0
    result = _service(gateway, fang, timeout=0.5).request(document_text=DOC_A, local=_snapshot(db, document))
    assert result.comparison.status is OpinionStatus.TIMEOUT
    assert result.comparison.hints == () and len(fang.requests) == 1  # keine automatische Wiederholung
    assert _state(db) == before


@pytest.mark.parametrize("status", [400, 401, 429, 500, 529])
def test_provider_errors_block_without_retry(gateway, fang, db, status) -> None:
    document = _local(db)
    before = _state(db)
    fang.status = status
    result = _service(gateway, fang).request(document_text=DOC_A, local=_snapshot(db, document))
    assert result.comparison.status is OpinionStatus.ERROR
    assert result.comparison.hints == () and len(fang.requests) == 1 and _state(db) == before


@pytest.mark.parametrize("reply", [
    "",
    "Gerne! Die Frist endet am 31.07.2026.",
    _answer(frist=["15.08.2026"]),                                   # erfundenes Datum
    _answer(frist=["31.07.2026"], termin=["31.07.2026"]),            # widerspruechlich
    _answer(typ="Zahlungsaufforderung"),                             # unbekannter Typ
    _answer(begruendung="weil"),                                     # unzulaessiges Feld
    _answer(frist=["[DATUM_01]"]),                                   # Platzhalter
    _answer(frist=[], termin=[], fristbeginn=[]).replace('"fristdauern"', '"hinweis": "Herr Probst-Fiktiv", '
                                                                             '"fristdauern"'),  # Klartext-Leck
])
def test_invalid_answers_are_rejected_and_nothing_is_adopted(gateway, fang, db, reply) -> None:
    document = _local(db)
    before = _state(db)
    fang.reply_text = reply
    result = _service(gateway, fang).request(document_text=DOC_A, local=_snapshot(db, document))
    assert result.comparison.status is OpinionStatus.INVALID and result.reasons  # mit konkretem Ablehnungsgrund
    assert result.comparison.hints == () and _state(db) == before


@pytest.mark.parametrize("leak", ["Maximilian Probst-Fiktiv", "Herr Probst-Fiktiv", "m.probst@beispielpost.example"])
def test_answer_with_original_value_is_caught_by_the_gateway_leak_check(gateway, fang, leak) -> None:
    fang.reply_text = leak + " " + _answer()
    result = _service(gateway, fang).request(document_text=DOC_A, local=snapshot_from("Unbekannt", []))
    assert result.comparison.status is OpinionStatus.INVALID
    assert "Klartextauffälligkeiten" in " ".join(result.reasons)


def test_bare_surname_is_not_a_gateway_leak_finding_but_still_rejected_as_free_text(gateway, fang) -> None:
    """Bestehendes Verhalten der Leck-Pruefung (unveraendert): ein nackter Nachname ohne Anrede ist kein Fund. Fuer
    die Zweitmeinung unkritisch - jede Antwort mit Text ausserhalb des JSON wird verworfen, nichts wird angezeigt."""
    fang.reply_text = "Probst-Fiktiv " + _answer()
    result = _service(gateway, fang).request(document_text=DOC_A, local=snapshot_from("Unbekannt", []))
    assert result.comparison.status is OpinionStatus.INVALID and result.comparison.hints == ()
    assert "Klartextauffälligkeiten" not in " ".join(result.reasons)


def test_blocked_reasons_never_reach_the_audit_hook(fang) -> None:
    audit: list[tuple[str, dict]] = []
    weak = ClaudePrivacyGateway(pseudonymizer=Pseudonymizer())
    _service(weak, fang, audit=lambda event, details: audit.append((event, details))).request(
        document_text="Herrn Maximilian Probst-Fiktiv bitten wir, bis zum 31.07.2026 zu zahlen.",
        local=snapshot_from("Unbekannt", []))
    assert audit and audit[0][0] == "second_opinion_datenschutz_blockiert"
    assert "Probst" not in json.dumps(audit, ensure_ascii=False)


# --- Zweckbindung im Schreibpfad: DraftingService (auch Editor mit Formularfeld `purpose`) --------------------------

def test_drafting_service_refuses_the_second_opinion_purpose_before_any_side_effect(db) -> None:
    from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
    from app.drafting.service import DraftingService
    from app.research.service import LegalResearchService
    from app.search.service import DocumentSearchService
    from tests.fake_embedding_provider import FakeEmbeddingProvider

    class NeverCalled:
        def write(self, payload):
            raise AssertionError("Schreib-Provider darf nicht aufgerufen werden")

        def write_stream(self, payload):
            raise AssertionError("Schreib-Provider darf nicht aufgerufen werden")

    search = DocumentSearchService(FakeEmbeddingProvider())
    service = DraftingService(RuleBasedLocalAIProvider(), LegalResearchService(search, min_score_for_sufficient=0.0),
                              search, ClaudePrivacyGateway(), NeverCalled(), model_name="claude-sonnet-5")
    result = service.create_draft(None, SECOND_OPINION_PURPOSE, db)
    assert result.success is False and result.blocked_reasons
    assert db.query(Matter).count() == 0  # keine Schnellakte angelegt


def test_provider_defaults_to_the_official_endpoint_and_ignores_the_environment(monkeypatch) -> None:
    """Wie bei den bestehenden Direkt-Providern (test_no_ai_gateway_proxy.py): ohne ausdrueckliches Ziel nie die
    Umgebungsvariable ANTHROPIC_BASE_URL still uebernehmen; kurzes Timeout, keine Wiederholungen."""
    from unittest.mock import patch

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.invalid")
    with patch("app.second_opinion.provider.anthropic.Anthropic") as mock_cls:
        AnthropicSecondOpinionProvider(api_key=API_KEY, model="claude-sonnet-5")
    _, kwargs = mock_cls.call_args
    assert kwargs["base_url"] == "https://api.anthropic.com"
    assert kwargs["max_retries"] == 0 and 0 < kwargs["timeout"] <= 30
