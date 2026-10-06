"""ModelProvider-Abstraktion (Prompt 34; seit §63 ausschliesslich Claude/
Anthropic, siehe ARCHITECTURE.md §63 - vorher Local-First mit Ollama, §60;
seit §65 zusaetzlich `build_local_llm_provider` fuer den lokalen
PFLICHT-Zwischenschritt vor Claude, siehe ARCHITECTURE.md §65).

Dieses Modul bleibt die EINZIGE Stelle im Projekt, die eine konkrete
Provider-Instanz baut - `DraftingService`/`ReviewEngine` kennen nur die
Protokolle (`ClaudeWritingProvider`/`ClaudeReviewProvider`/
`LocalLLMProvider`, siehe app/ai_providers/claude_writing_provider.py,
app/review/provider.py bzw. app/ai_providers/local_llm_provider.py), nie
eine konkrete Implementierung. Fuer Claude gibt es keine lokale
Alternative - `build_writing_provider`/`build_review_provider` bauen immer
einen `AnthropicClaudeWritingProvider`/`AnthropicClaudeReviewProvider`;
fehlt der API-Key, wird das als Konfigurationsfehler gemeldet
(`ProviderNotConfiguredError`), nicht stillschweigend auf einen lokalen
Fallback umgeschaltet.
"""

from __future__ import annotations

from app.ai_providers.anthropic_writing_provider import AnthropicClaudeWritingProvider
from app.ai_providers.claude_writing_provider import ClaudeWritingProvider
from app.ai_providers.gateway_writing_provider import GatewayRelayWritingProvider
from app.ai_providers.local_llm_provider import LocalLLMProvider
from app.ai_providers.ollama_provider import OllamaLocalLLMProvider
from app.config import Settings
from app.review.anthropic_review_provider import AnthropicClaudeReviewProvider
from app.review.gateway_review_provider import GatewayRelayReviewProvider
from app.review.provider import ClaudeReviewProvider


class ProviderNotConfiguredError(Exception):
    """Wird ausgelöst, wenn weder ein Lexono-Gateway noch ein direkter
    `ANTHROPIC_API_KEY` konfiguriert ist. Bewusst EINE gemeinsame Exception
    für Writing UND Review (statt zwei praktisch identischer Typen) - der
    Dashboard-Router fängt sie ab, um dem Anwalt eine verständliche
    Meldung statt eines Stacktrace zu zeigen (siehe app/web/drafts_router.py)."""


def _require_anthropic_api_key(settings: Settings) -> str:
    api_key = (
        settings.anthropic_api_key.get_secret_value()
        if settings.anthropic_api_key is not None
        else None
    )
    if not api_key or not api_key.strip():
        raise ProviderNotConfiguredError(
            "Weder ein Lexono-Gateway noch ANTHROPIC_API_KEY sind konfiguriert - "
            "in der .env-Datei hinterlegen."
        )
    return api_key


def _require_gateway_credentials(settings: Settings) -> tuple[str, str]:
    client_id = settings.lexono_gateway_client_id
    client_secret = (
        settings.lexono_gateway_client_secret.get_secret_value()
        if settings.lexono_gateway_client_secret is not None
        else None
    )
    if not client_id or not client_secret:
        raise ProviderNotConfiguredError(
            "LEXONO_GATEWAY_URL ist gesetzt, aber LEXONO_GATEWAY_CLIENT_ID/"
            "LEXONO_GATEWAY_CLIENT_SECRET fehlen - beide in der .env-Datei hinterlegen."
        )
    return client_id, client_secret


def build_writing_provider(settings: Settings) -> ClaudeWritingProvider:
    """Baut den Schreib-Provider - über den Lexono-Gateway, wenn
    `lexono_gateway_url` gesetzt ist (Produktionspfad, ARCHITECTURE.md
    §70), sonst über den bisherigen direkten Anthropic-Zugriff (NUR
    Entwicklung/Qualitätstests). Wirft `ProviderNotConfiguredError`, wenn
    keins von beidem vollständig konfiguriert ist."""
    if settings.lexono_gateway_url:
        # ECHTE WEBRECHERCHE (06.10., §0.6-§0.10): bewusst OHNE
        # web_search_enabled/web_search_max_uses hier - der eigentliche
        # Claude-Aufruf passiert bei gesetztem lexono_gateway_url auf einem
        # separaten, nicht zu diesem Repository gehoerenden Lexono-Gateway-
        # Server (siehe call_gateway_messages, fixe Request-Form ohne
        # "tools"-Feld). Ein hier erdachtes Tool-Flag wuerde dort entweder
        # einen Fehler ausloesen oder (schlimmer) still ignoriert werden -
        # beides waere keine ehrliche Web-Zugriffs-Zusage. Um dieser
        # Produktvorgabe nachzukommen, muss der separate Gateway-Server
        # selbst ein Web-Search-Tool anhaengen und dies dem Client ueber die
        # Relay-Antwort mitteilen - ausserhalb des Umfangs dieses
        # Repositories, siehe Abschlussbericht "Offene Punkte".
        client_id, client_secret = _require_gateway_credentials(settings)
        return GatewayRelayWritingProvider(
            base_url=settings.lexono_gateway_url,
            client_id=client_id,
            client_secret=client_secret,
            model=settings.claude_model_name,
            max_tokens=settings.claude_max_tokens,
            timeout_seconds=settings.lexono_gateway_timeout_seconds,
        )
    api_key = _require_anthropic_api_key(settings)
    return AnthropicClaudeWritingProvider(
        api_key=api_key,
        model=settings.claude_model_name,
        max_tokens=settings.claude_max_tokens,
        web_search_enabled=settings.web_search_enabled,
        web_search_max_uses=settings.web_search_max_uses,
    )


def build_review_provider(settings: Settings) -> ClaudeReviewProvider:
    """Wie `build_writing_provider`, für die Review-Engine."""
    if settings.lexono_gateway_url:
        client_id, client_secret = _require_gateway_credentials(settings)
        return GatewayRelayReviewProvider(
            base_url=settings.lexono_gateway_url,
            client_id=client_id,
            client_secret=client_secret,
            model=settings.claude_model_name,
            max_tokens=settings.claude_max_tokens,
            timeout_seconds=settings.lexono_gateway_timeout_seconds,
        )
    api_key = _require_anthropic_api_key(settings)
    return AnthropicClaudeReviewProvider(
        api_key=api_key,
        model=settings.claude_model_name,
        max_tokens=settings.claude_max_tokens,
    )


def build_local_llm_provider(settings: Settings) -> LocalLLMProvider | None:
    """Baut den lokalen KI-Provider (§65) - oder `None`, wenn
    `settings.local_ai_enabled=False` (Standard). `None` bedeutet fuer
    `DraftingService`: kein lokaler Zwischenschritt, unveraendertes
    Verhalten wie vor §65 - NICHT "lokale KI deaktiviert, aber trotzdem
    versuchen". Ist `local_ai_enabled=True`, wird IMMER ein Provider
    zurueckgegeben (aktuell ausschliesslich `local_ai_runtime="ollama"` -
    von `Settings.local_ai_runtime_must_be_supported` bereits validiert)."""
    if not settings.local_ai_enabled:
        return None
    return OllamaLocalLLMProvider(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
    )
