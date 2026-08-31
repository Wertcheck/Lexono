"""Regressionsschutz: kein KI-Gateway/Proxy eines DRITTANBIETERS (z. B.
Portkey) im Anfragepfad - UND strukturelle Absicherung des eigenen,
bewusst eingeführten Lexono-Gateway (ARCHITECTURE.md §70).

Hintergrund (20.08., §57): ein Prompt verlangte, die Anthropic-Anbindung
über "Portkey" (api.portkey.ai, Header `x-portkey-api-key`, fester
Provider-Slug `@lexono-1/...`) umzuleiten - ein SaaS-Gateway eines
Drittanbieters mit einem nicht verifizierbaren externen Konto. Das wurde
nach Rückfrage explizit verworfen.

**Bewusst und sichtbar aktualisiert am 31.08. (§70), NICHT gelöscht:** die
damalige Entscheidung "kein zentraler Proxy, direkte Anthropic-API-
Anbindung" wurde durch eine spätere, ausführlich begründete Produkt- und
Sicherheitsentscheidung ersetzt - der echte `ANTHROPIC_API_KEY` darf im
Produktivbetrieb niemals auf einem Kanzlei-PC existieren, sondern nur auf
einem von Lexono selbst betriebenen, deutschen Relay-Server
(`gateway/`, siehe ARCHITECTURE.md §70 für den vollständigen Datenfluss
und die Abgrenzung "kein SaaS-Gateway eines Dritten" vs. "eigener,
ausschließlich für dieses Produkt bestimmter Relay").

Dieser Test schützt nach wie vor unverändert davor, dass:
1. ein DRITTANBIETER-Marker (Portkey/OpenRouter/LiteLLM) im Anwendungscode
   auftaucht,
2. die weiterhin bestehenden, NUR für lokale Entwicklung/Qualitätstests
   vorgesehenen Direkt-Provider (`AnthropicClaudeWritingProvider`/
   `AnthropicClaudeReviewProvider`) still mit einem `base_url`-Override
   auf einen beliebigen Proxy umgeleitet werden.

UND zusätzlich (neu, §70) davor, dass:
3. der Kanzlei-Client (`app/`, außerhalb der beiden o. g. Dev-Only-Dateien)
   selbst einen `anthropic.Anthropic(...)`-Client konstruiert - das darf
   ausschließlich `gateway/relay.py` (mit dem serverseitigen Key) tun,
4. eine Kanzlei-Credential ein Format hat, das mit einem echten
   Anthropic-API-Key (`sk-ant-...`) verwechselbar wäre.

Ein künftiger Wechsel des AI-Zugriffspfads muss weiterhin IMMER eine
bewusste, im Code sichtbare Änderung dieser Datei auslösen, niemals eine
stille Umleitung."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from app.privacy.gateway_schema import ClaudeRequestPayload

_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP_DIR = _REPO_ROOT / "app"
_GATEWAY_DIR = _REPO_ROOT / "gateway"

# Anbieter-/Gateway-Namen, die im Anwendungscode (nicht in Tests/Doku, die
# bewusst über sie SCHREIBEN wie diese Datei hier) nichts verloren haben.
_FORBIDDEN_GATEWAY_MARKERS = ("portkey", "openrouter", "litellm")

# Die einzigen Dateien im gesamten Repository, die `anthropic.Anthropic(`
# tatsaechlich konstruieren duerfen: die beiden Dev-Only-Direkt-Provider,
# der admin-getriggerte Erreichbarkeits-Check (kein Content-Aufruf), und
# serverseitig ausschliesslich gateway/relay.py (ARCHITECTURE.md §70).
_ALLOWED_ANTHROPIC_CLIENT_CONSTRUCTION_FILES = {
    "app/ai_providers/anthropic_writing_provider.py",
    "app/review/anthropic_review_provider.py",
    "app/system_health/service.py",
    "gateway/relay.py",
}


def test_no_ai_gateway_marker_anywhere_in_application_code() -> None:
    offenders: list[str] = []
    for path in _APP_DIR.rglob("*.py"):
        content = path.read_text(encoding="utf-8", errors="replace").lower()
        for marker in _FORBIDDEN_GATEWAY_MARKERS:
            if marker in content:
                offenders.append(f"{path.relative_to(_REPO_ROOT)}: {marker!r}")
    assert not offenders, f"KI-Gateway-Referenz im Anwendungscode gefunden: {offenders}"


def test_writing_provider_constructs_client_without_base_url_override(
    monkeypatch,
) -> None:
    """`anthropic.Anthropic(...)` darf ausschließlich mit `api_key` (und
    optional `timeout`) aufgerufen werden - NIE mit `base_url`, das wäre
    der technische Mechanismus, über den ein Gateway/Proxy untergeschoben
    werden könnte."""
    from app.ai_providers.anthropic_writing_provider import AnthropicClaudeWritingProvider

    with patch("app.ai_providers.anthropic_writing_provider.anthropic.Anthropic") as mock_cls:
        AnthropicClaudeWritingProvider(api_key="sk-ant-test", model="claude-sonnet-5")

    _, kwargs = mock_cls.call_args
    assert "base_url" not in kwargs
    assert kwargs.get("api_key") == "sk-ant-test"


def test_review_provider_constructs_client_without_base_url_override() -> None:
    from app.review.anthropic_review_provider import AnthropicClaudeReviewProvider

    with patch("app.review.anthropic_review_provider.anthropic.Anthropic") as mock_cls:
        AnthropicClaudeReviewProvider(api_key="sk-ant-test", model="claude-sonnet-5")

    _, kwargs = mock_cls.call_args
    assert "base_url" not in kwargs


def test_write_call_never_sends_a_portkey_style_header() -> None:
    """Beweis auf der tatsächlichen Aufrufebene: selbst wenn jemand
    versehentlich einen `extra_headers`-Parameter ergänzen würde, darf
    dort niemals ein Gateway-spezifischer Header (z. B.
    `x-portkey-api-key`) auftauchen."""
    from app.ai_providers.anthropic_writing_provider import AnthropicClaudeWritingProvider

    with patch("app.ai_providers.anthropic_writing_provider.anthropic.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_text_block = MagicMock()
        mock_text_block.type = "text"
        mock_text_block.text = "Antwort."
        mock_response = MagicMock()
        mock_response.content = [mock_text_block]
        mock_response.usage = None
        mock_client.messages.create.return_value = mock_response

        provider = AnthropicClaudeWritingProvider(api_key="sk-ant-test", model="claude-sonnet-5")
        provider.write(
            ClaudeRequestPayload(schreibauftrag="formulate_draft", anonymisierter_sachverhalt="Text")
        )

    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert "extra_headers" not in call_kwargs
    serialized = repr(call_kwargs).lower()
    assert "portkey" not in serialized


def test_settings_have_no_portkey_or_gateway_configuration_fields() -> None:
    """Strukturelle Absicherung: `Settings` (app/config/settings.py) darf
    kein Feld für einen DRITTANBIETER-API-Endpunkt/Key enthalten. Die
    eigenen `lexono_gateway_*`-Felder (§70) sind davon bewusst NICHT
    betroffen - das ist kein Drittanbieter-Gateway."""
    from app.config import Settings

    field_names = set(Settings.model_fields.keys())
    for marker in _FORBIDDEN_GATEWAY_MARKERS:
        assert not any(marker in name.lower() for name in field_names), (
            f"Gateway-bezogenes Settings-Feld gefunden (Marker {marker!r})"
        )


# --- Lexono-Gateway (§70): strukturelle Absicherung der neuen Architektur ---


def test_gateway_relay_client_module_never_imports_anthropic_sdk() -> None:
    """app/ai_providers/gateway_relay_client.py spricht ausschließlich HTTP
    mit dem Gateway - es darf das `anthropic`-SDK nicht einmal
    importieren, sonst wäre ein direkter Cloud-Aufruf am Gateway vorbei
    zumindest technisch möglich."""
    path = _APP_DIR / "ai_providers" / "gateway_relay_client.py"
    content = path.read_text(encoding="utf-8")
    assert "import anthropic" not in content


def test_gateway_writing_and_review_providers_never_import_anthropic_sdk() -> None:
    for relative_path in (
        "ai_providers/gateway_writing_provider.py",
        "review/gateway_review_provider.py",
    ):
        path = _APP_DIR / relative_path
        assert path.exists(), f"Datei nicht gefunden: {relative_path}"
        content = path.read_text(encoding="utf-8")
        assert "import anthropic" not in content, f"{path} importiert das Anthropic-SDK"


def test_anthropic_client_is_constructed_only_in_allowed_files() -> None:
    """Repository-weite Kontrolle: `anthropic.Anthropic(` darf NUR in den
    explizit dafür vorgesehenen Dateien konstruiert werden (zwei
    Dev-Only-Direkt-Provider, der Health-Check, und serverseitig
    ausschließlich gateway/relay.py). Jede weitere Fundstelle wäre ein
    zweiter, ungeprüfter Cloud-Pfad."""
    offenders: list[str] = []
    for search_root in (_APP_DIR, _GATEWAY_DIR):
        for path in search_root.rglob("*.py"):
            relative = path.relative_to(_REPO_ROOT).as_posix()
            content = path.read_text(encoding="utf-8", errors="replace")
            if "anthropic.Anthropic(" in content and relative not in (
                _ALLOWED_ANTHROPIC_CLIENT_CONSTRUCTION_FILES
            ):
                offenders.append(relative)
    assert not offenders, f"Unerwartete Anthropic-Client-Konstruktion in: {offenders}"


def test_kanzlei_credential_format_is_distinct_from_anthropic_api_key() -> None:
    """Eine generierte Kanzlei-Credential darf niemals mit dem Format
    eines echten Anthropic-API-Keys (`sk-ant-...`) verwechselbar sein -
    strukturelle Absicherung gegen eine versehentliche Verwendung eines
    Kanzlei-Secrets als vermeintlichen Anthropic-Key oder umgekehrt."""
    from gateway.security import generate_client_secret

    secret = generate_client_secret()
    assert not secret.startswith("sk-ant-")
    assert secret.startswith("lxg_secret_")


def test_gateway_relay_writing_provider_has_no_api_key_attribute() -> None:
    """Der Client-seitige Gateway-Provider darf strukturell gar kein Feld
    besitzen, das wie ein Anthropic-API-Key benannt ist - er kennt den
    echten Key nicht und kann ihn nicht kennen."""
    from app.ai_providers.gateway_writing_provider import GatewayRelayWritingProvider

    provider = GatewayRelayWritingProvider(
        base_url="http://127.0.0.1:8700",
        client_id="cid",
        client_secret="lxg_secret_test",
        model="claude-sonnet-5",
    )
    for attr_name in vars(provider):
        assert "api_key" not in attr_name.lower() and "anthropic_key" not in attr_name.lower()
