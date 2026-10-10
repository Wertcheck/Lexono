"""Tests für `DraftingService.create_draft_stream` (13.09., Streaming-
Architekturentscheidung - siehe DECISIONS.md fuer die volle Herleitung).

Kernaussagen, die hier real geprüft werden (nicht nur behauptet):
1. Echtes inkrementelles Streaming NUR für den bereits etablierten
   risikobasierten Fast Path (chat_response, kein Dokument, keine von
   Presidio erkannte PII) - JEDE andere Anfrage faellt auf EIN einziges
   "delta" + "result"-Ereignis zurück (identisches Ergebnis wie
   `create_draft`, keine Abkürzung der bestehenden Garantien).
2. Ein Provider ohne `write_stream`-Fähigkeit (z. B. der bestehende
   `FakeClaudeWritingProvider`) löst IMMER den gepufferten Fallback aus,
   auch wenn die Anfrage sonst streaming-fähig wäre.
3. Ein unerwartetes platzhalterförmiges Token, das WÄHREND des Streamens
   auftaucht, bricht SOFORT ab (kein weiteres Delta, kein persistierter
   Draft) - die deterministische Stufe-1-Prüfung bleibt Pflicht.
4. Eine bereits am Privacy Gateway blockierte Anfrage liefert genau EIN
   "result"-Ereignis, KEIN "delta"."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai_providers.claude_writing_provider import ClaudeWritingResult
from app.models import Draft
from app.models.base import Base
from app.privacy.gateway_schema import ClaudeRequestPayload
from tests.test_drafting_service import FakeClaudeWritingProvider, FakeLocalLLMProvider, _matter, _service


pytestmark = pytest.mark.usefixtures("always_run_local_summary")

class FakeStreamingClaudeWritingProvider:
    """Test-Double MIT `write_stream`-Fähigkeit - liefert die konfigurierten
    `chunks` als Folge von Text-Deltas. `write()` bleibt für den
    gepufferten Fallback-Pfad ebenfalls funktionsfähig (identischer
    zusammengefügter Text), damit derselbe Fake in beiden Pfaden
    verwendbar ist."""

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.received_payloads: list[ClaudeRequestPayload] = []
        self.stream_received_payloads: list[ClaudeRequestPayload] = []

    def write(self, payload: ClaudeRequestPayload) -> ClaudeWritingResult:
        self.received_payloads.append(payload)
        return ClaudeWritingResult(text="".join(self.chunks), token_count=99)

    def write_stream(
        self, payload: ClaudeRequestPayload
    ) -> Generator[str, None, ClaudeWritingResult]:
        self.stream_received_payloads.append(payload)
        for chunk in self.chunks:
            yield chunk
        return ClaudeWritingResult(
            text="".join(self.chunks), token_count=77, input_tokens=50, output_tokens=27
        )


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_eligible_chat_message_streams_real_incremental_deltas(db_session: Session) -> None:
    matter = _matter(db_session)
    writing_provider = FakeStreamingClaudeWritingProvider(
        chunks=["§ 558 BGB ", "regelt die ", "Mieterhöhung."]
    )
    service, _ = _service(writing_provider)

    events = list(
        service.create_draft_stream(
            matter.id,
            "chat_response",
            db_session,
            attorney_anmerkungen="Was steht in § 558 BGB?",
            actor="Testnutzer",
        )
    )

    delta_events = [e for e in events if e.kind == "delta"]
    result_events = [e for e in events if e.kind == "result"]
    assert [e.text for e in delta_events] == ["§ 558 BGB ", "regelt die ", "Mieterhöhung."]
    assert len(result_events) == 1
    result = result_events[0].result
    assert result.success is True
    assert result.draft_text == "§ 558 BGB regelt die Mieterhöhung."
    assert result.draft_id is not None
    # Der eigentliche Anthropic-Aufruf ging ueber write_stream, NICHT write().
    assert len(writing_provider.stream_received_payloads) == 1
    assert writing_provider.received_payloads == []
    assert db_session.query(Draft).filter_by(id=result.draft_id).count() == 1


def test_eligible_chat_message_skips_local_llm_calls_exactly_like_non_streaming(
    db_session: Session,
) -> None:
    """Identische Garantie wie
    test_simple_chat_without_document_or_pii_skips_local_llm_calls in
    test_drafting_service.py, hier für den Streaming-Pfad."""
    matter = _matter(db_session)
    writing_provider = FakeStreamingClaudeWritingProvider(chunks=["Eine normale Antwort."])
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    events = list(
        service.create_draft_stream(
            matter.id,
            "chat_response",
            db_session,
            attorney_anmerkungen="Was steht in § 558 BGB?",
            actor="Testnutzer",
        )
    )

    assert local_llm.received_payloads == []
    assert local_llm.structured_calls == []
    assert [e for e in events if e.kind == "result"][0].result.success is True


def test_document_context_falls_back_to_buffered_single_chunk(db_session: Session) -> None:
    """Ein Aktendokument erzwingt weiterhin die volle Pipeline - der
    Streaming-Provider wird trotz vorhandener `write_stream`-Faehigkeit
    NICHT dafuer genutzt, es wird stattdessen `write()` verwendet und
    genau EIN "delta" + EIN "result"-Ereignis geliefert."""
    from app.models import Document

    matter = _matter(db_session)
    db_session.add(
        Document(
            matter=matter,
            file_path="/tmp/x.pdf",
            extracted_text="Ein Dokumenttext ohne erkennbare Namen oder Adressen.",
            classified_type="Sonstiges",
        )
    )
    db_session.commit()

    writing_provider = FakeStreamingClaudeWritingProvider(chunks=["Zusammenfassung."])
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    events = list(
        service.create_draft_stream(
            matter.id,
            "chat_response",
            db_session,
            attorney_anmerkungen="Bitte fasse das Dokument zusammen.",
            actor="Testnutzer",
        )
    )

    assert writing_provider.stream_received_payloads == []
    assert len(writing_provider.received_payloads) == 1
    assert len(local_llm.received_payloads) == 1  # volle Pipeline lief tatsächlich
    delta_events = [e for e in events if e.kind == "delta"]
    result_events = [e for e in events if e.kind == "result"]
    assert len(delta_events) == 1
    assert len(result_events) == 1
    assert result_events[0].result.success is True


def test_provider_without_write_stream_falls_back_to_buffered_single_chunk(
    db_session: Session,
) -> None:
    """Auch eine sonst streaming-fähige Anfrage (chat_response, kein
    Dokument, keine PII) bleibt gepuffert, wenn der konfigurierte Provider
    schlicht kein `write_stream` implementiert (z. B. der bestehende
    `GatewayRelayWritingProvider` oder ein einfacher Test-Fake) - kein
    Fehler, sauberer Fallback."""
    matter = _matter(db_session)
    writing_provider = FakeClaudeWritingProvider(response_text="Eine normale Antwort ohne PII.")
    assert not hasattr(writing_provider, "write_stream")
    service, _ = _service(writing_provider)

    events = list(
        service.create_draft_stream(
            matter.id,
            "chat_response",
            db_session,
            attorney_anmerkungen="Was steht in § 558 BGB?",
            actor="Testnutzer",
        )
    )

    delta_events = [e for e in events if e.kind == "delta"]
    result_events = [e for e in events if e.kind == "result"]
    assert len(delta_events) == 1
    assert delta_events[0].text == "Eine normale Antwort ohne PII."
    assert result_events[0].result.success is True


def test_placeholder_anomaly_mid_stream_aborts_immediately_and_persists_nothing(
    db_session: Session,
) -> None:
    """KRITISCHE Sicherheitsgegenprobe: taucht waehrend des Streamens ein
    unerwartetes platzhalterfoermiges Token auf (obwohl `mappings` fuer
    diesen Fast Path garantiert leer ist), MUSS sofort abgebrochen werden -
    kein weiteres Delta danach, kein Draft wird persistiert, das Ergebnis
    ist ein "result"-Ereignis mit success=False."""
    matter = _matter(db_session)
    writing_provider = FakeStreamingClaudeWritingProvider(
        chunks=["Eine Antwort mit ", "[PERSON_01] ", "einem erfundenen Platzhalter."]
    )
    service, _ = _service(writing_provider)

    events = list(
        service.create_draft_stream(
            matter.id,
            "chat_response",
            db_session,
            attorney_anmerkungen="Was steht in § 558 BGB?",
            actor="Testnutzer",
        )
    )

    delta_events = [e for e in events if e.kind == "delta"]
    result_events = [e for e in events if e.kind == "result"]
    # Nur das ERSTE, unverdaechtige Delta durfte raus - das Delta MIT dem
    # unerwarteten Token selbst wird zurueckgehalten (Fail-Closed: lieber
    # ein Delta zu wenig als eines zu viel).
    assert [e.text for e in delta_events] == ["Eine Antwort mit "]
    assert len(result_events) == 1
    result = result_events[0].result
    assert result.success is False
    assert any("nerwartete" in r or "Auffälligkeiten" in r for r in result.blocked_reasons)
    assert db_session.query(Draft).count() == 0


def test_empty_stream_response_is_blocked_not_persisted_as_empty_draft(
    db_session: Session,
) -> None:
    """ECHTER FUND (19.09., analoger Mindestinhalt-Check wie im nicht-
    streamenden Pfad, siehe test_drafting_service.py::
    test_empty_writing_response_is_blocked_not_persisted_as_empty_draft):
    `gateway_result.mappings` ist fuer diesen Fast Path IMMER leer, daher
    findet `check_response_placeholder_integrity` bei leerem Text
    STRUKTURELL nie einen Fund (weder Manipulation noch Leck moeglich ohne
    Mappings) - ohne den expliziten Mindestinhalt-Check wuerde ein
    komplett leerer Stream (z. B. durch ein verbrauchtes Token-Budget
    ohne sichtbaren Text) still als `success=True` mit leerem Entwurf
    durchgereicht."""
    matter = _matter(db_session)
    writing_provider = FakeStreamingClaudeWritingProvider(chunks=[])

    service, _ = _service(writing_provider)

    events = list(
        service.create_draft_stream(
            matter.id,
            "chat_response",
            db_session,
            attorney_anmerkungen="Was steht in § 558 BGB?",
            actor="Testnutzer",
        )
    )

    delta_events = [e for e in events if e.kind == "delta"]
    result_events = [e for e in events if e.kind == "result"]
    assert delta_events == []
    assert len(result_events) == 1
    result = result_events[0].result
    assert result.success is False
    assert any("leer" in reason.lower() for reason in result.blocked_reasons)
    assert db_session.query(Draft).count() == 0


def test_gateway_blocked_request_yields_single_result_event_no_delta(
    db_session: Session,
) -> None:
    """Ein bereits am Privacy Gateway blockierter Zweck (deterministisch,
    siehe test_disallowed_purpose_creates_no_draft) liefert GENAU EIN
    "result"-Ereignis - kein "delta" wird jemals erzeugt, da der
    Streaming-Provider gar nicht erst aufgerufen wird."""
    matter = _matter(db_session)
    writing_provider = FakeStreamingClaudeWritingProvider(chunks=["sollte nie gesendet werden"])
    service, _ = _service(writing_provider)

    events = list(
        service.create_draft_stream(matter.id, "analyze_full_file", db_session)
    )

    assert len(events) == 1
    assert events[0].kind == "result"
    assert events[0].result.success is False
    assert writing_provider.stream_received_payloads == []
    assert db_session.query(Draft).count() == 0


# --- P1 Performance-Feedback-Follow-up (17.09.): Status-Ereignisse fuer den
# NICHT-streaming-faehigen Pfad (Dokument-/Aktenkontext, Schriftsatz) -
# siehe OPEN_ISSUES.md ("Kein Streaming-Feedback fuer die eigentlichen
# Kern-Workflows"). Vorher: 80-105+ Sekunden lang KEIN Ereignis vor dem
# abschliessenden "delta"+"result"-Paar. Jetzt: ein "status"-Ereignis pro
# bereits bestehendem `trace.step(...)`-Block, aus der festen Vokabel-Liste
# `_STEP_STATUS_LABELS` - IDENTISCHES Endergebnis, nur zusaetzliche,
# inhaltsfreie Zwischenereignisse.


def test_non_streaming_path_now_emits_status_events_before_the_final_result(
    db_session: Session,
) -> None:
    """Der Kernfall aus dem P1-Fund: ein Aktendokument erzwingt die volle,
    nicht-streaming-faehige Pipeline - vorher kam dabei ausschliesslich das
    abschliessende "delta"+"result"-Paar, jetzt zusaetzlich sichtbare
    Zwischenereignisse waehrend der 80+ Sekunden."""
    from app.models import Document

    matter = _matter(db_session)
    db_session.add(
        Document(
            matter=matter,
            file_path="/tmp/x.pdf",
            extracted_text="Ein Dokumenttext ohne erkennbare Namen oder Adressen.",
            classified_type="Sonstiges",
        )
    )
    db_session.commit()

    writing_provider = FakeStreamingClaudeWritingProvider(chunks=["Zusammenfassung."])
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    events = list(
        service.create_draft_stream(
            matter.id,
            "chat_response",
            db_session,
            attorney_anmerkungen="Bitte fasse das Dokument zusammen.",
            actor="Testnutzer",
        )
    )

    status_events = [e for e in events if e.kind == "status"]
    # Vier bestehende trace.step()-Bloecke durchlaufen: lokale
    # Vorabanalyse, Claude, Validierung, Rekonstruktion.
    assert [e.status for e in status_events] == [
        "Lokale Vorabanalyse läuft…",
        "Anfrage wird an Claude gesendet…",
        "Antwort wird lokal geprüft…",
        "Antwort wird zusammengesetzt…",
    ]
    # Reihenfolge: alle "status"-Ereignisse VOR dem abschliessenden Paar.
    kinds_in_order = [e.kind for e in events]
    assert kinds_in_order == ["status", "status", "status", "status", "delta", "result"]
    # Status-Ereignisse tragen NIEMALS Sachverhalt/Text - nur den festen Code.
    for event in status_events:
        assert event.text == ""
        assert event.result is None
    assert events[-1].result.success is True


def test_non_streaming_path_status_events_stop_at_the_actual_block_point(
    db_session: Session,
) -> None:
    """Fail-Closed bleibt unveraendert: bricht die Pipeline bei der
    Validierung ab, kommt danach KEIN "reconstruction"-Status mehr - die
    Zwischenereignisse spiegeln exakt den bereits bestehenden
    Kontrollfluss, erfinden keinen Fortschritt, der nicht stattfand."""
    from app.models import Document

    matter = _matter(db_session, client_name="Erika Mustermann")
    db_session.add(
        Document(
            matter=matter,
            file_path="/tmp/x.pdf",
            extracted_text="Mandantin Erika Mustermann bittet um Rueckmeldung.",
        )
    )
    db_session.commit()
    writing_provider = FakeStreamingClaudeWritingProvider(
        chunks=["Guten Tag, wir haben bereits mit Erika Mustermann telefoniert."]
    )
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    events = list(
        service.create_draft_stream(
            matter.id, "chat_response", db_session, actor="Testnutzer"
        )
    )

    status_events = [e for e in events if e.kind == "status"]
    assert [e.status for e in status_events] == [
        "Lokale Vorabanalyse läuft…",
        "Anfrage wird an Claude gesendet…",
        "Antwort wird lokal geprüft…",
    ]
    result_events = [e for e in events if e.kind == "result"]
    assert len(result_events) == 1
    assert result_events[0].result.success is False
    assert [e.kind for e in events if e.kind == "delta"] == []


def test_finish_non_streaming_sync_wrapper_result_unchanged_by_status_events(
    db_session: Session,
) -> None:
    """Gegenprobe fuer den synchronen `create_draft`-Aufrufer (Schriftsatz-
    Generator, anwaltliche Anweisungen): das Endergebnis ist IDENTISCH zum
    Verhalten vor der Umstellung auf den Generator - die neuen "status"-
    Ereignisse werden vom Wrapper vollstaendig verworfen."""
    from app.models import Document

    matter = _matter(db_session)
    db_session.add(
        Document(
            matter=matter,
            file_path="/tmp/x.pdf",
            extracted_text="Ein Dokumenttext ohne erkennbare Namen oder Adressen.",
            classified_type="Sonstiges",
        )
    )
    db_session.commit()
    writing_provider = FakeClaudeWritingProvider(response_text="Zusammenfassung.")
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(
        matter.id,
        "chat_response",
        db_session,
        attorney_anmerkungen="Bitte fasse das Dokument zusammen.",
        actor="Testnutzer",
    )

    assert result.success is True
    assert result.draft_text == "Zusammenfassung."
    assert db_session.query(Draft).filter_by(id=result.draft_id).count() == 1


# --- ECHTER FUND (08.10., Real-User-E2E im installierten Build a6ba839): die
# Skip-Entscheidung (a6ba839) laesst Mappings aus der KI-Historie zu, der
# Streaming-Pfad setzt aber GARANTIERT LEERE Mappings voraus. Folge: nach dem
# ersten Delta fehlte "[TELEFON_01]" im akkumulierten Teiltext, die
# Stufe-1-Pruefung (require_full_coverage) brach den Stream ab - eine harmlose
# Folgefrage wurde mit "unerwarteten Platzhalter" blockiert. ---

_HISTORY_WITH_NUMBER = [
    "Anwalt: Was regelt die Modernisierungsumlage?",
    "Assistent: Seit dem Jahr 024/2025 gelten neue Regeln.",
]


def test_followup_with_mappings_only_from_ai_history_is_not_aborted_mid_stream(
    db_session: Session,
) -> None:
    writing_provider = FakeStreamingClaudeWritingProvider(
        chunks=["§ 559 BGB ", "regelt die ", "Modernisierungsumlage."]
    )
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    events = list(
        service.create_draft_stream(
            None,
            "chat_response",
            db_session,
            attorney_anmerkungen="Und was regelt dann § 559 BGB?",
            gespraechsverlauf=_HISTORY_WITH_NUMBER,
            actor="Testnutzer",
        )
    )

    result = [e for e in events if e.kind == "result"][0].result
    assert result.success is True, result.blocked_reasons
    assert result.draft_text == "§ 559 BGB regelt die Modernisierungsumlage."
    # Die Local-AI-Schichten bleiben uebersprungen (Fix a6ba839 unveraendert).
    assert local_llm.received_payloads == []
    assert local_llm.structured_calls == []


def test_ai_history_placeholder_echoed_by_claude_is_reconstructed_not_blocked(
    db_session: Session,
) -> None:
    """Claude darf einen Platzhalter aus der pseudonymisierten Historie
    wiederverwenden - er wird lokal zurueckgefuehrt statt blockiert."""
    writing_provider = FakeStreamingClaudeWritingProvider(
        chunks=["Wie oben genannt (", "[TELEFON_01]", ") gilt das weiter."]
    )
    service, _ = _service(writing_provider)

    events = list(
        service.create_draft_stream(
            None,
            "chat_response",
            db_session,
            attorney_anmerkungen="Und was regelt dann § 559 BGB?",
            gespraechsverlauf=_HISTORY_WITH_NUMBER,
            actor="Testnutzer",
        )
    )

    result = [e for e in events if e.kind == "result"][0].result
    assert result.success is True, result.blocked_reasons
    assert "024/2025" in result.draft_text
    assert "[TELEFON_01]" not in result.draft_text


def test_no_local_preanalysis_status_when_local_ai_is_skipped_for_general_chat(
    db_session: Session,
) -> None:
    """ECHTER FUND (09.10., Abschluss-Verifikation): der Status "Lokale Vorabanalyse laeuft..." wurde
    VOR der Skip-Entscheidung gesendet - bei einer allgemeinen Chat-Frage ohne PII, fuer die die
    lokale KI bewusst uebersprungen wird, zeigte die UI trotzdem eine Ladeanzeige fuer einen
    Schritt, der nie stattfindet."""
    writing_provider = FakeStreamingClaudeWritingProvider(chunks=["Eine allgemeine Antwort."])
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    events = list(
        service.create_draft_stream(
            None,
            "chat_response",
            db_session,
            attorney_anmerkungen="was kannst du",
            gespraechsverlauf=[
                "Anwalt: Wie hoch sind die Mietpreise?",
                "Assistent: Dazu hat sich Martina Quellfeld in einem Aufsatz geaeussert.",
            ],
            actor="Testnutzer",
        )
    )

    assert local_llm.received_payloads == [], "lokale KI darf hier nicht laufen"
    statuses = [e.status for e in events if e.kind == "status"]
    assert "Lokale Vorabanalyse läuft…" not in statuses
    assert events[-1].result.success is True
