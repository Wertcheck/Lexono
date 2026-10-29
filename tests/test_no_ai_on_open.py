"""P1 (17.10.2026): Oeffnen allein loest keine KI-Verarbeitung aus - fuer ALLE GET-Routen der Anwendung.

Jede GET-Route (Dashboard, API, Exporte, Vorschauen, Ansichten) wird mit synthetischen Daten (Mail, Dokumente mit
Personennamen, Entwuerfe, Frist, Wissensbasis, Chat mit Upload-Nachricht) aufgerufen. Gemessen wird auf zwei Ebenen:
- Spione auf ALLEN Cloud-Providern (direkt und Gateway-Relay, Schreiben und Pruefung) und dem Gateway-Client;
- jede ausgehende Socket-Verbindung ausser Loopback wird aufgezeichnet und verhindert.
Neue GET-Routen werden automatisch mitgeprueft (Aufzaehlung zur Laufzeit). Nur synthetische Daten, keine echten
Schluessel (Fake-Schluessel, tote Endpunkte)."""

from __future__ import annotations

import re
import socket
from collections.abc import Iterator

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import app
from app.models.base import Base
from tests.auth_test_utils import login_as_admin

_LOOPBACK = ("127.", "::1", "localhost")


def _get_routes() -> list[str]:
    paths: list[str] = []

    def walk(router, prefix=""):
        for route in router.routes:
            if type(route).__name__ == "_IncludedRouter":
                walk(route.original_router, prefix + (getattr(route.include_context, "prefix", "") or ""))
            elif isinstance(route, APIRoute) and "GET" in route.methods:
                paths.append(prefix + route.path)
            elif hasattr(route, "routes") and not getattr(route, "methods", None):
                walk(route, prefix + getattr(route, "path", ""))

    walk(app.router)
    return sorted(set(paths))


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def spies(monkeypatch) -> dict[str, list]:
    """Provider-Spione + Netzwerk-Riegel. Fake-Schluessel und tote Endpunkte, falls ein Pfad einen Provider baut."""
    calls: dict[str, list] = {"provider": [], "network": []}
    for env, value in (("ANTHROPIC_API_KEY", "sk-ant-qa-synthetisch-kein-echter-schluessel-0000000000"),
                       ("ANTHROPIC_BASE_URL", "http://127.0.0.1:9"), ("LEXONO_GATEWAY_URL", ""),
                       ("HTTPS_PROXY", "http://127.0.0.1:9"), ("HTTP_PROXY", "http://127.0.0.1:9"),
                       ("NO_PROXY", "127.0.0.1,localhost")):
        monkeypatch.setenv(env, value)

    def provider_spy(name):
        def spy(*args, **kwargs):
            calls["provider"].append(name)
            raise AssertionError(f"KI-Aufruf beim Oeffnen: {name}")
        return spy

    from app.ai_providers import anthropic_writing_provider, gateway_writing_provider
    from app.review import anthropic_review_provider, gateway_review_provider

    for module, cls, methods in (
        (anthropic_writing_provider, "AnthropicClaudeWritingProvider", ("write", "write_stream")),
        (gateway_writing_provider, "GatewayRelayWritingProvider", ("write", "write_stream")),
        (anthropic_review_provider, "AnthropicClaudeReviewProvider", ("review",)),
        (gateway_review_provider, "GatewayRelayReviewProvider", ("review",)),
    ):
        for method in methods:
            if hasattr(getattr(module, cls), method):
                monkeypatch.setattr(getattr(module, cls), method, provider_spy(f"{cls}.{method}"))
    for module in (gateway_writing_provider, gateway_review_provider):
        monkeypatch.setattr(module, "call_gateway_messages", provider_spy("call_gateway_messages"))

    original_connect = socket.socket.connect

    def guarded_connect(self, address):
        host = str(address[0]) if isinstance(address, tuple) else str(address)
        if not host.startswith(_LOOPBACK):
            calls["network"].append(host)
            raise OSError("Netzwerk im Test gesperrt (P1)")
        return original_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    return calls


def _seed(db: Session) -> dict[str, str]:
    from app.chat.service import ChatService
    from app.models import ChatConversation, User
    from app.synthetic_data.generator import SyntheticDataGenerator

    generator = SyntheticDataGenerator(seed=7)
    case = generator.generate_case(db, scenario_key="betriebspruefung")
    complex_case = generator.generate_complex_case(db)
    sources, knowledge = generator.generate_shared_knowledge_base(db)
    templates = generator.generate_shared_document_templates(db)
    user = db.query(User).filter_by(email="admin@kanzlei.test").one()
    service = ChatService("unbenutzt")
    conversation = ChatConversation(matter_id=case.matter.id, user_id=user.id, title="QA-Chat")
    db.add(conversation)
    db.commit()
    upload = service.record_upload(db, conversation=conversation, document_ids=[case.document.id])
    service.record_user_message(db, conversation=conversation, content="Bitte zusammenfassen.")
    draft = (case.drafts or [complex_case.draft])[-1]
    values = {
        "matter_id": case.matter.id, "document_id": case.document.id, "message_id": case.message.id,
        "draft_id": draft.id, "client_id": case.client.id, "conversation_id": conversation.id,
        "deadline_id": case.deadline.id if case.deadline else "keine", "source_id": sources[0].id,
        "knowledge_item_id": knowledge[0].id, "template_id": templates[0].id if templates else "keine",
        "task_id": "keine", "entity_type": "Matter", "entity_id": case.matter.id, "law_code": "AO",
        "section_id": "keine", "ref": "standard", "page_number": "1",
        "receipt_id": "keine",  # Finanzbuchhaltung (28.10.2026, nur Profil "full"): Belegdatei ohne Beleg -> 404
    }
    values["_upload_message"] = upload.id
    return values


def test_opening_any_get_route_triggers_no_ai_and_no_network(db_session, spies) -> None:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        client = TestClient(app, raise_server_exceptions=False)
        login_as_admin(db_session, client)
        values = _seed(db_session)
        routes = _get_routes()
        assert len(routes) >= 90  # Plausibilitaet der Aufzaehlung (Stand 17.10.2026: 99)
        unknown = sorted({p for r in routes for p in re.findall(r"{(\w+)(?::\w+)?}", r) if p not in values})
        assert unknown == [], f"neue Pfadparameter ohne Testwert: {unknown}"
        statuses = {}
        for route in routes:
            path = re.sub(r"{(\w+)(?::\w+)?}", lambda m: values[m.group(1)], route)
            statuses[route] = client.get(path, follow_redirects=False).status_code
    finally:
        app.dependency_overrides.clear()
    assert spies["provider"] == [], spies["provider"]
    assert spies["network"] == [], spies["network"]
    server_errors = {r: s for r, s in statuses.items() if s >= 500}
    assert server_errors == {}, server_errors
    assert sum(1 for s in statuses.values() if s == 200) >= 60  # die meisten Seiten wurden wirklich ausgeliefert


def test_direct_matter_upload_extracts_locally_without_ai_or_network(db_session, spies, monkeypatch, tmp_path) -> None:
    """Dritter Upload-Weg neben Chat und Schriftsatz (§57): Upload in die Akte = speichern + lokal auslesen."""
    import app.web.document_actions_router as document_actions
    from app.config.settings import Settings
    from app.models import Client, Document, Matter
    from tests.auth_test_utils import extract_csrf
    from tests.test_web_privacy_review import _pdf_bytes

    settings = Settings(_env_file=None, schriftsatz_upload_storage_dir=str(tmp_path / "akte"))
    monkeypatch.setattr(document_actions, "get_settings", lambda: settings)

    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        client = TestClient(app, raise_server_exceptions=False)
        login_as_admin(db_session, client)
        matter = Matter(client=Client(name="Synthetischer Mandant"), title="Akte ohne KI")
        db_session.add(matter)
        db_session.commit()
        page = client.get(f"/dashboard/matters/{matter.id}").text
        response = client.post(f"/dashboard/matters/{matter.id}/documents/upload", follow_redirects=False,
                               data={"csrf_token": extract_csrf(page)},
                               files={"documents": ("beschluss.pdf", _pdf_bytes(["Der Richter Zweigelt hat am",
                                                                                "03.11.2026 entschieden."]),
                                                    "application/pdf")})
        client.get(f"/dashboard/matters/{matter.id}/document/{db_session.query(Document).one().id}")  # oeffnen
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 303
    assert "Zweigelt" in (db_session.query(Document).one().extracted_text or "")  # lokal ausgelesen
    assert spies["provider"] == [] and spies["network"] == []
