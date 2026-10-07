"""Tests fuer app/ai_providers/claude_writing_provider.py und
app/ai_providers/anthropic_writing_provider.py.

Die konkrete `AnthropicClaudeWritingProvider` wird ausschliesslich gegen
einen gemockten Anthropic-Client getestet - kein echter API-Aufruf in
diesen Tests (kein API-Key vorhanden, und ein echter Aufruf waere in
einer Testsuite ohnehin unpassend). Ein echter End-to-End-Test mit
echtem API-Key muss auf dem Zielsystem des Anwalts erfolgen."""

from unittest.mock import MagicMock, patch

import pytest

from app.ai_providers.claude_writing_provider import (
    ClaudeWritingProvider,
    ClaudeWritingResult,
    build_writing_prompt,
)
from app.privacy.gateway_schema import ClaudeRequestPayload


def test_protocol_has_exactly_one_method() -> None:
    public_methods = [
        name for name in dir(ClaudeWritingProvider) if not name.startswith("_")
    ]
    assert public_methods == ["write"]


def test_fake_implementation_satisfies_protocol() -> None:
    class FakeProvider:
        def write(self, payload: ClaudeRequestPayload) -> ClaudeWritingResult:
            return ClaudeWritingResult(text="Antwort", token_count=10)

    provider: ClaudeWritingProvider = FakeProvider()
    payload = ClaudeRequestPayload(
        schreibauftrag="formulate_draft", anonymisierter_sachverhalt="Text"
    )
    result = provider.write(payload)
    assert result.text == "Antwort"
    assert result.token_count == 10


def test_build_writing_prompt_contains_only_allowlist_fields() -> None:
    payload = ClaudeRequestPayload(
        schreibauftrag="formulate_draft",
        gewuenschter_stil="förmlich",
        anonymisierter_sachverhalt="Sachverhalt mit [MANDANT_01].",
        anonymisierte_argumentationspunkte=["Punkt eins.", "Punkt zwei."],
        anonymisierte_quellenverweise=["§ 355 AO."],
        schreibvorlage="Sehr geehrte Damen und Herren,",
    )

    prompt = build_writing_prompt(payload)

    assert "formulate_draft" in prompt
    assert "förmlich" in prompt
    assert "[MANDANT_01]" in prompt
    assert "Punkt eins." in prompt
    assert "§ 355 AO." in prompt
    assert "Sehr geehrte Damen und Herren," in prompt


def test_build_writing_prompt_omits_empty_optional_fields() -> None:
    payload = ClaudeRequestPayload(
        schreibauftrag="formulate_draft", anonymisierter_sachverhalt="Text"
    )

    prompt = build_writing_prompt(payload)

    assert "Gewünschter Stil" not in prompt
    assert "Argumentationspunkte" not in prompt
    assert "Quellenverweise" not in prompt
    assert "Vorlage" not in prompt


def test_build_writing_prompt_does_not_leak_the_raw_chat_response_purpose_code() -> None:
    """ECHTER FUND (07.10., Owner-Direktive "CHAT UI/UX + INTENT ROOT-CAUSE
    PASS", per Code-Lese-Analyse gefunden): fuer purpose="chat_response"
    schrieb `build_writing_prompt` bisher woertlich "Schreibauftrag:
    chat_response" - ein interner Routing-Code als Prompttext, strukturell
    identisch zu einem echten Drafting-Auftrag. Die Zeile muss jetzt eine
    natuerliche Chat-Antwort-Anweisung sein, OHNE den rohen Bezeichner."""
    payload = ClaudeRequestPayload(
        schreibauftrag="chat_response",
        anonymisierter_sachverhalt="Akte: Schnellentwurf 2026-10-07",
        anonymisierte_anwaltliche_anmerkungen="Was ist § 558 BGB?",
    )

    prompt = build_writing_prompt(payload)

    assert "chat_response" not in prompt
    assert "Chat-Antwort" in prompt


def test_build_writing_prompt_keeps_the_literal_formulate_draft_purpose() -> None:
    """Gegenprobe: der bestehende Drafting-Zweck bleibt woertlich
    unveraendert (keine Verhaltensaenderung fuer den echten Schriftsatz-
    Generator, siehe app/web/schriftsatz_router.py)."""
    payload = ClaudeRequestPayload(
        schreibauftrag="formulate_draft", anonymisierter_sachverhalt="Text"
    )

    prompt = build_writing_prompt(payload)

    assert "Schreibauftrag: formulate_draft" in prompt


class TestAnthropicClaudeWritingProvider:
    def test_requires_non_blank_api_key(self) -> None:
        from app.ai_providers.anthropic_writing_provider import (
            AnthropicClaudeWritingProvider,
        )

        with pytest.raises(ValueError):
            AnthropicClaudeWritingProvider(api_key="  ", model="claude-sonnet-5")

    @patch("app.ai_providers.anthropic_writing_provider.anthropic.Anthropic")
    def test_write_sends_prompt_and_returns_text(self, mock_anthropic_cls) -> None:
        from app.ai_providers.anthropic_writing_provider import (
            AnthropicClaudeWritingProvider,
        )

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        mock_text_block = MagicMock()
        mock_text_block.type = "text"
        mock_text_block.text = "Formulierter Antworttext mit [MANDANT_01]."
        mock_response = MagicMock()
        mock_response.content = [mock_text_block]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50
        mock_client.messages.create.return_value = mock_response

        provider = AnthropicClaudeWritingProvider(
            api_key="test-key", model="claude-sonnet-5"
        )
        payload = ClaudeRequestPayload(
            schreibauftrag="formulate_draft",
            anonymisierter_sachverhalt="Sachverhalt mit [MANDANT_01].",
        )

        result = provider.write(payload)

        assert result.text == "Formulierter Antworttext mit [MANDANT_01]."
        assert result.token_count == 150

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-sonnet-5"
        # Prompt-Caching (Schritt 3): content ist jetzt eine Liste von
        # Blöcken statt eines einzelnen Strings - der erste (stabile) Block
        # trägt cache_control, siehe build_writing_prompt_cache_blocks.
        content_blocks = call_kwargs["messages"][0]["content"]
        sent_prompt = "\n".join(block["text"] for block in content_blocks)
        assert "[MANDANT_01]" in sent_prompt
        assert content_blocks[0]["cache_control"] == {"type": "ephemeral"}
        # Original-Mandantendaten koennen hier gar nicht auftauchen, da nur
        # die Payload (bereits pseudonymisiert) in den Prompt einfliesst.

    @patch("app.ai_providers.anthropic_writing_provider.anthropic.Anthropic")
    def test_write_handles_missing_usage_gracefully(self, mock_anthropic_cls) -> None:
        from app.ai_providers.anthropic_writing_provider import (
            AnthropicClaudeWritingProvider,
        )

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_text_block = MagicMock()
        mock_text_block.type = "text"
        mock_text_block.text = "Antwort."
        mock_response = MagicMock()
        mock_response.content = [mock_text_block]
        mock_response.usage = None
        mock_client.messages.create.return_value = mock_response

        provider = AnthropicClaudeWritingProvider(api_key="test-key", model="claude-sonnet-5")
        result = provider.write(
            ClaudeRequestPayload(schreibauftrag="formulate_draft", anonymisierter_sachverhalt="Text")
        )

        assert result.token_count is None

    @patch("app.ai_providers.anthropic_writing_provider.anthropic.Anthropic")
    def test_api_key_is_never_logged_or_included_in_prompt(self, mock_anthropic_cls) -> None:
        from app.ai_providers.anthropic_writing_provider import (
            AnthropicClaudeWritingProvider,
        )

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_text_block = MagicMock()
        mock_text_block.type = "text"
        mock_text_block.text = "Antwort."
        mock_response = MagicMock()
        mock_response.content = [mock_text_block]
        mock_response.usage = None
        mock_client.messages.create.return_value = mock_response

        secret_key = "sk-ant-super-secret-value"
        provider = AnthropicClaudeWritingProvider(api_key=secret_key, model="claude-sonnet-5")
        provider.write(
            ClaudeRequestPayload(schreibauftrag="formulate_draft", anonymisierter_sachverhalt="Text")
        )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert secret_key not in str(call_kwargs)

    @patch("app.ai_providers.anthropic_writing_provider.anthropic.Anthropic")
    def test_write_explicitly_disables_thinking(self, mock_anthropic_cls) -> None:
        """ECHTER FUND, LIVE REPRODUZIERT (05.10., Owner-Direktive
        "Abschließende Live-Verifikation nach Aufladung des Anthropic-
        Guthabens"): ohne diesen Parameter aktiviert claude-sonnet-5
        standardmäßig ein unsichtbares "Denken", das voll gegen
        `max_tokens` zählt, aber hochgradig variabel ist (live gemessen:
        165/1220/3352 Tokens für strukturell ähnliche Anfragen) - bei
        einem unklaren Sachverhalt verbrauchte es bis zu 82% des
        gesamten Budgets, der sichtbare Text wurde dadurch TROTZ des
        bereits erhöhten `max_tokens`-Limits mitten im Satz abgeschnitten
        (`stop_reason="max_tokens"`). Live verifiziert: derselbe,
        zuvor abgebrochene Sachverhalt lieferte mit `thinking={"type":
        "disabled"}` einen vollständigen Text (`stop_reason="end_turn"`,
        `thinking_tokens=0`)."""
        from app.ai_providers.anthropic_writing_provider import (
            AnthropicClaudeWritingProvider,
        )

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_text_block = MagicMock()
        mock_text_block.type = "text"
        mock_text_block.text = "Antwort."
        mock_response = MagicMock()
        mock_response.content = [mock_text_block]
        mock_response.usage = None
        mock_client.messages.create.return_value = mock_response

        provider = AnthropicClaudeWritingProvider(api_key="test-key", model="claude-sonnet-5")
        provider.write(
            ClaudeRequestPayload(schreibauftrag="formulate_draft", anonymisierter_sachverhalt="Text")
        )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["thinking"] == {"type": "disabled"}

    @patch("app.ai_providers.anthropic_writing_provider.anthropic.Anthropic")
    def test_write_stream_explicitly_disables_thinking(self, mock_anthropic_cls) -> None:
        """Dieselbe Garantie für den Streaming-Pfad (`write_stream`) -
        beide Methoden rufen die Anthropic-API unabhängig voneinander
        auf, siehe app/ai_providers/anthropic_writing_provider.py."""
        from app.ai_providers.anthropic_writing_provider import (
            AnthropicClaudeWritingProvider,
        )

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_stream_cm = MagicMock()
        mock_stream_cm.__enter__.return_value.text_stream = iter(["Antwort."])
        mock_final_text_block = MagicMock()
        mock_final_text_block.type = "text"
        mock_final_text_block.text = "Antwort."
        mock_final_message = MagicMock()
        mock_final_message.content = [mock_final_text_block]
        mock_final_message.usage = None
        mock_stream_cm.__enter__.return_value.get_final_message.return_value = mock_final_message
        mock_client.messages.stream.return_value = mock_stream_cm

        provider = AnthropicClaudeWritingProvider(api_key="test-key", model="claude-sonnet-5")
        gen = provider.write_stream(
            ClaudeRequestPayload(schreibauftrag="formulate_draft", anonymisierter_sachverhalt="Text")
        )
        list(gen)  # Generator vollständig konsumieren, löst den Aufruf aus.

        call_kwargs = mock_client.messages.stream.call_args.kwargs
        assert call_kwargs["thinking"] == {"type": "disabled"}


class TestWebSearchTool:
    """Echte Webrecherche (06.10., Owner-Direktive "LEXONO ALS VOLLWERTIGER
    AI-ARBEITSPLATZ - ARCHITEKTUR-/REQUEST-FLOW-AUDIT" §0.6-§0.10) - nutzt
    Anthropics serverseitig ausgefuehrtes Web-Search-Tool
    (`web_search_20250305`, GA im installierten SDK, kein Beta-Header
    noetig - siehe .venv/Lib/site-packages/anthropic/types/
    web_search_tool_20250305_param.py)."""

    @patch("app.ai_providers.anthropic_writing_provider.anthropic.Anthropic")
    def test_write_attaches_web_search_tool_for_chat_purpose_when_enabled(
        self, mock_anthropic_cls
    ) -> None:
        from app.ai_providers.anthropic_writing_provider import (
            AnthropicClaudeWritingProvider,
        )

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_text_block = MagicMock()
        mock_text_block.type = "text"
        mock_text_block.text = "Antwort."
        mock_response = MagicMock()
        mock_response.content = [mock_text_block]
        mock_response.usage = None
        mock_client.messages.create.return_value = mock_response

        provider = AnthropicClaudeWritingProvider(
            api_key="test-key",
            model="claude-sonnet-5",
            web_search_enabled=True,
            web_search_max_uses=7,
        )
        provider.write(
            ClaudeRequestPayload(schreibauftrag="chat_response", anonymisierter_sachverhalt="Frage")
        )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["tools"] == [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 7}
        ]

    @patch("app.ai_providers.anthropic_writing_provider.anthropic.Anthropic")
    def test_write_uses_web_search_prompt_when_tool_attached(self, mock_anthropic_cls) -> None:
        from app.ai_providers.anthropic_writing_provider import (
            AnthropicClaudeWritingProvider,
        )
        from app.ai_providers.claude_writing_provider import (
            CHAT_SYSTEM_PROMPT_WITH_WEB_SEARCH,
        )

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_text_block = MagicMock()
        mock_text_block.type = "text"
        mock_text_block.text = "Antwort."
        mock_response = MagicMock()
        mock_response.content = [mock_text_block]
        mock_response.usage = None
        mock_client.messages.create.return_value = mock_response

        provider = AnthropicClaudeWritingProvider(
            api_key="test-key", model="claude-sonnet-5", web_search_enabled=True
        )
        provider.write(
            ClaudeRequestPayload(schreibauftrag="chat_response", anonymisierter_sachverhalt="Frage")
        )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["system"][0]["text"] == CHAT_SYSTEM_PROMPT_WITH_WEB_SEARCH

    @patch("app.ai_providers.anthropic_writing_provider.anthropic.Anthropic")
    def test_write_omits_tool_when_web_search_disabled(self, mock_anthropic_cls) -> None:
        from app.ai_providers.anthropic_writing_provider import (
            AnthropicClaudeWritingProvider,
        )
        from app.ai_providers.claude_writing_provider import CHAT_SYSTEM_PROMPT

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_text_block = MagicMock()
        mock_text_block.type = "text"
        mock_text_block.text = "Antwort."
        mock_response = MagicMock()
        mock_response.content = [mock_text_block]
        mock_response.usage = None
        mock_client.messages.create.return_value = mock_response

        # web_search_enabled=False (Standardwert dieses Konstruktors) -
        # kein Tool, ehrlicher "kein Internetzugriff"-Systemprompt.
        provider = AnthropicClaudeWritingProvider(api_key="test-key", model="claude-sonnet-5")
        provider.write(
            ClaudeRequestPayload(schreibauftrag="chat_response", anonymisierter_sachverhalt="Frage")
        )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert "tools" not in call_kwargs
        assert call_kwargs["system"][0]["text"] == CHAT_SYSTEM_PROMPT

    @patch("app.ai_providers.anthropic_writing_provider.anthropic.Anthropic")
    def test_write_omits_tool_for_drafting_purpose_even_when_enabled(
        self, mock_anthropic_cls
    ) -> None:
        """Websuche ist ausschliesslich fuer den Chat vorgesehen - der
        unveraenderte Schriftsatz-/Drafting-Pfad bekommt bewusst KEIN Tool,
        selbst wenn web_search_enabled=True konfiguriert ist."""
        from app.ai_providers.anthropic_writing_provider import (
            AnthropicClaudeWritingProvider,
        )
        from app.ai_providers.claude_writing_provider import WRITING_SYSTEM_PROMPT

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_text_block = MagicMock()
        mock_text_block.type = "text"
        mock_text_block.text = "Antwort."
        mock_response = MagicMock()
        mock_response.content = [mock_text_block]
        mock_response.usage = None
        mock_client.messages.create.return_value = mock_response

        provider = AnthropicClaudeWritingProvider(
            api_key="test-key", model="claude-sonnet-5", web_search_enabled=True
        )
        provider.write(
            ClaudeRequestPayload(schreibauftrag="formulate_draft", anonymisierter_sachverhalt="Text")
        )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert "tools" not in call_kwargs
        assert call_kwargs["system"][0]["text"] == WRITING_SYSTEM_PROMPT

    @patch("app.ai_providers.anthropic_writing_provider.anthropic.Anthropic")
    def test_write_stream_attaches_web_search_tool_when_enabled(self, mock_anthropic_cls) -> None:
        from app.ai_providers.anthropic_writing_provider import (
            AnthropicClaudeWritingProvider,
        )

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_stream_cm = MagicMock()
        mock_stream_cm.__enter__.return_value.text_stream = iter(["Antwort."])
        mock_final_text_block = MagicMock()
        mock_final_text_block.type = "text"
        mock_final_text_block.text = "Antwort."
        mock_final_message = MagicMock()
        mock_final_message.content = [mock_final_text_block]
        mock_final_message.usage = None
        mock_stream_cm.__enter__.return_value.get_final_message.return_value = mock_final_message
        mock_client.messages.stream.return_value = mock_stream_cm

        provider = AnthropicClaudeWritingProvider(
            api_key="test-key", model="claude-sonnet-5", web_search_enabled=True
        )
        gen = provider.write_stream(
            ClaudeRequestPayload(schreibauftrag="chat_response", anonymisierter_sachverhalt="Frage")
        )
        list(gen)

        call_kwargs = mock_client.messages.stream.call_args.kwargs
        assert call_kwargs["tools"] == [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}
        ]

    @patch("app.ai_providers.anthropic_writing_provider.anthropic.Anthropic")
    def test_write_stream_omits_tool_when_disabled(self, mock_anthropic_cls) -> None:
        from app.ai_providers.anthropic_writing_provider import (
            AnthropicClaudeWritingProvider,
        )

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_stream_cm = MagicMock()
        mock_stream_cm.__enter__.return_value.text_stream = iter(["Antwort."])
        mock_final_text_block = MagicMock()
        mock_final_text_block.type = "text"
        mock_final_text_block.text = "Antwort."
        mock_final_message = MagicMock()
        mock_final_message.content = [mock_final_text_block]
        mock_final_message.usage = None
        mock_stream_cm.__enter__.return_value.get_final_message.return_value = mock_final_message
        mock_client.messages.stream.return_value = mock_stream_cm

        provider = AnthropicClaudeWritingProvider(api_key="test-key", model="claude-sonnet-5")
        gen = provider.write_stream(
            ClaudeRequestPayload(schreibauftrag="chat_response", anonymisierter_sachverhalt="Frage")
        )
        list(gen)

        call_kwargs = mock_client.messages.stream.call_args.kwargs
        assert "tools" not in call_kwargs


class TestPromptCaching:
    @patch("app.ai_providers.anthropic_writing_provider.anthropic.Anthropic")
    def test_system_prompt_is_cached(self, mock_anthropic_cls) -> None:
        from app.ai_providers.anthropic_writing_provider import (
            AnthropicClaudeWritingProvider,
        )
        from app.ai_providers.claude_writing_provider import WRITING_SYSTEM_PROMPT

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_text_block = MagicMock()
        mock_text_block.type = "text"
        mock_text_block.text = "Antwort."
        mock_response = MagicMock()
        mock_response.content = [mock_text_block]
        mock_response.usage = None
        mock_client.messages.create.return_value = mock_response

        provider = AnthropicClaudeWritingProvider(api_key="test-key", model="claude-sonnet-5")
        provider.write(
            ClaudeRequestPayload(schreibauftrag="formulate_draft", anonymisierter_sachverhalt="Text")
        )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["system"] == [
            {
                "type": "text",
                "text": WRITING_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def test_stable_case_context_stays_identical_across_revisions(self) -> None:
        """Beweis für die eigentliche Kosteneinsparung: derselbe
        Sachverhalt/dieselben Quellen erzeugen bei zwei unterschiedlichen
        Schreibaufträgen (z. B. zwei Entwurfsversionen) einen BYTE-
        IDENTISCHEN gecachten Block - nur so kann Anthropic den Cache
        tatsächlich treffen."""
        from app.ai_providers.claude_writing_provider import (
            build_writing_prompt_cache_blocks,
        )

        base_kwargs = dict(
            anonymisierter_sachverhalt="Sachverhalt mit [MANDANT_01].",
            anonymisierte_quellenverweise=["§ 355 AO."],
        )
        version_1 = ClaudeRequestPayload(schreibauftrag="formulate_draft", **base_kwargs)
        version_2 = ClaudeRequestPayload(
            schreibauftrag="revise_draft",
            anonymisierte_anwaltliche_anmerkungen="Bitte kürzer fassen.",
            **base_kwargs,
        )

        blocks_1 = build_writing_prompt_cache_blocks(version_1)
        blocks_2 = build_writing_prompt_cache_blocks(version_2)

        assert blocks_1[0]["text"] == blocks_2[0]["text"]
        assert blocks_1[0]["cache_control"] == {"type": "ephemeral"}
        assert blocks_1[1]["text"] != blocks_2[1]["text"]
        assert "cache_control" not in blocks_1[1]


class TestSelectSystemPrompt:
    """ECHTER FUND (realer Abnahme-Test, 13.09.): der zentrale Chat schickte
    IMMER WRITING_SYSTEM_PROMPT ("Du hilfst bei der sprachlichen
    Formulierung eines Antwortschreibens...") an Claude, unabhaengig vom
    tatsaechlichen Nutzerinhalt - eine normale Frage erzeugte dadurch einen
    formellen Briefentwurf. `select_system_prompt` waehlt jetzt anhand des
    Zwecks (siehe app/chat/service.py::_looks_like_drafting_request fuer
    die Erkennung selbst, hier nur die Auswahl-Funktion)."""

    def test_chat_response_purpose_gets_the_conversational_prompt(self) -> None:
        from app.ai_providers.claude_writing_provider import (
            CHAT_SYSTEM_PROMPT,
            select_system_prompt,
        )

        assert select_system_prompt("chat_response") == CHAT_SYSTEM_PROMPT
        assert "Schriftsätze/Antwortschreiben erstellst du NUR" in CHAT_SYSTEM_PROMPT

    def test_chat_prompt_frames_missing_web_access_as_lexono_configuration(self) -> None:
        """05.10., Owner-Direktive "ARCHITECTURE & PRODUCT FLOW PASS" §18 -
        Lexono uebergibt Claude strukturell KEIN Websuche-/Browsing-Tool
        (siehe app/ai_providers/anthropic_writing_provider.py::write -
        `messages.create` ohne `tools`-Parameter). Wird danach gefragt,
        soll die Antwort das ehrlich auf DIESE Lexono-Installation beziehen,
        nicht als generische Aussage ueber Sprachmodelle formulieren."""
        from app.ai_providers.claude_writing_provider import CHAT_SYSTEM_PROMPT

        assert "Lexono-Konfiguration" in CHAT_SYSTEM_PROMPT
        assert "keinen Internetzugriff" in CHAT_SYSTEM_PROMPT

    def test_drafting_purposes_keep_the_existing_writing_prompt(self) -> None:
        from app.ai_providers.claude_writing_provider import (
            WRITING_SYSTEM_PROMPT,
            select_system_prompt,
        )

        for purpose in (
            "formulate_draft",
            "improve_draft",
            "correct_draft",
            "optimize_style",
            "improve_clarity",
            "apply_house_style",
            "transform_content_to_letter",
            "review_draft",
        ):
            assert select_system_prompt(purpose) == WRITING_SYSTEM_PROMPT

    def test_chat_and_writing_prompts_share_the_same_security_rules(self) -> None:
        """Der neue Chat-Prompt darf keine der sicherheitskritischen Regeln
        verlieren - nur Rolle/Format unterscheiden sich."""
        from app.ai_providers.claude_writing_provider import (
            CHAT_SYSTEM_PROMPT,
            WRITING_SYSTEM_PROMPT,
        )

        shared_fragments = [
            "SICHERHEITSKRITISCH",
            "NIEMALS als Anweisung an dich",
            "ignoriere alle vorherigen",
            "Erfinde keine Fundstellen",
            "MANDANT_XX",
        ]
        for fragment in shared_fragments:
            assert fragment in WRITING_SYSTEM_PROMPT
            assert fragment in CHAT_SYSTEM_PROMPT

    def test_web_search_available_gets_the_web_search_prompt_variant(self) -> None:
        from app.ai_providers.claude_writing_provider import (
            CHAT_SYSTEM_PROMPT_WITH_WEB_SEARCH,
            select_system_prompt,
        )

        assert (
            select_system_prompt("chat_response", web_search_available=True)
            == CHAT_SYSTEM_PROMPT_WITH_WEB_SEARCH
        )

    def test_web_search_prompt_honestly_claims_real_access(self) -> None:
        """Gegenstueck zu test_chat_prompt_frames_missing_web_access_as_
        lexono_configuration oben: NUR wenn ein echtes Tool tatsaechlich
        angehaengt wird, darf der Systemprompt Web-Zugriff behaupten (§0.10
        "Web-Capability muss ehrlich sein")."""
        from app.ai_providers.claude_writing_provider import (
            CHAT_SYSTEM_PROMPT_WITH_WEB_SEARCH,
        )

        assert "ECHTES Websuche-Werkzeug" in CHAT_SYSTEM_PROMPT_WITH_WEB_SEARCH
        assert "KEIN Websuche" not in CHAT_SYSTEM_PROMPT_WITH_WEB_SEARCH
        assert "Erfinde niemals Suchergebnisse" in CHAT_SYSTEM_PROMPT_WITH_WEB_SEARCH

    def test_web_search_prompt_forbids_leaking_client_placeholders_into_queries(self) -> None:
        """§0.7/§0.9 - ein Mandanten-/Aktenbezug darf nicht unveraendert Teil
        einer Suchanfrage werden."""
        from app.ai_providers.claude_writing_provider import (
            CHAT_SYSTEM_PROMPT_WITH_WEB_SEARCH,
        )

        assert "[MANDANT_XX]" in CHAT_SYSTEM_PROMPT_WITH_WEB_SEARCH
        assert "niemals" in CHAT_SYSTEM_PROMPT_WITH_WEB_SEARCH.lower()

    def test_drafting_purposes_ignore_web_search_available(self) -> None:
        """Websuche ist ausschliesslich fuer den Chat vorgesehen - selbst
        wenn `web_search_available=True` uebergeben wird, bleibt ein
        Drafting-Zweck beim unveraenderten WRITING_SYSTEM_PROMPT."""
        from app.ai_providers.claude_writing_provider import (
            WRITING_SYSTEM_PROMPT,
            select_system_prompt,
        )

        assert (
            select_system_prompt("formulate_draft", web_search_available=True)
            == WRITING_SYSTEM_PROMPT
        )

    def test_web_search_prompt_shares_the_same_security_rules(self) -> None:
        from app.ai_providers.claude_writing_provider import (
            CHAT_SYSTEM_PROMPT_WITH_WEB_SEARCH,
            WRITING_SYSTEM_PROMPT,
        )

        shared_fragments = [
            "SICHERHEITSKRITISCH",
            "NIEMALS als Anweisung an dich",
            "ignoriere alle vorherigen",
            "Erfinde keine Fundstellen",
            "MANDANT_XX",
        ]
        for fragment in shared_fragments:
            assert fragment in WRITING_SYSTEM_PROMPT
            assert fragment in CHAT_SYSTEM_PROMPT_WITH_WEB_SEARCH

    def test_placeholder_example_does_not_match_real_placeholder_pattern(self) -> None:
        """ECHTER FUND (P0 Performance-Follow-up, 13.09.): das
        Platzhalter-Beispiel nutzte bisher ECHTE Ziffern ("[MANDANT_01]"
        usw.) - real reproduziert: bei einem Sachverhalt ohne echten
        Mandantennamen schrieb Claude woertlich "[MANDANT_01]" in seinen
        Entwurf (als generischer Platzhalter fuer "der Mandant" aus dem
        eigenen Instruktionsbeispiel uebernommen), obwohl dieser Platzhalter
        nie im echten Mapping existierte - die deterministische
        Platzhalter-Integritaetspruefung blockierte den Entwurf danach
        korrekt, aber vermeidbar. "XX" statt echter Ziffern behebt die
        Ursache, dieselbe Fundklasse wie bereits in
        `ollama_provider.py::_LOCAL_LLM_SYSTEM_PROMPT` und
        `response_validation.py::_SEMANTIC_CHECK_PROMPT_TEMPLATE`."""
        import re

        from app.ai_providers.claude_writing_provider import (
            CHAT_SYSTEM_PROMPT,
            WRITING_SYSTEM_PROMPT,
        )

        real_placeholder_pattern = re.compile(r"\[[A-Za-zÄÖÜäöüß_]+_\d{2}\]")
        assert not real_placeholder_pattern.search(WRITING_SYSTEM_PROMPT)
        assert not real_placeholder_pattern.search(CHAT_SYSTEM_PROMPT)
        assert "ERFINDE UNTER KEINEN UMSTÄNDEN" in WRITING_SYSTEM_PROMPT
        assert "ERFINDE UNTER KEINEN UMSTÄNDEN" in CHAT_SYSTEM_PROMPT
