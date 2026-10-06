"""AnthropicClaudeWritingProvider – erste konkrete Implementierung von
`ClaudeWritingProvider`, über das offizielle Anthropic-SDK.

DSGVO-/Datenschutz-Garantie (struktureller, nicht nur konventioneller
Schutz): Diese Klasse bekommt AUSSCHLIESSLICH eine `ClaudeRequestPayload`
(die sechs Allowlist-Felder, bereits pseudonymisiert und durch den
Security-Check aus Schritt 2 geprüft). Es gibt in dieser Datei keinen
Codepfad, der auf `Document`, `Matter`, `Message` oder andere
Mandantendaten-Modelle zugreifen könnte - der Datenfluss dorthin ist
bereits beim `ClaudePrivacyGateway` (Schritt 3) beendet.

Der API-Key wird ausschließlich zur Laufzeit aus der Konfiguration
gelesen (`SecretStr.get_secret_value()`), nie geloggt, nie im Klartext
gespeichert.
"""

from __future__ import annotations

from collections.abc import Generator

import anthropic

from app.ai_providers.claude_writing_provider import (
    ClaudeWritingResult,
    build_writing_prompt_cache_blocks,
    select_system_prompt,
)
from app.privacy.gateway_schema import ClaudeRequestPayload


class AnthropicClaudeWritingProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tokens: int = 2000,
        web_search_enabled: bool = False,
        web_search_max_uses: int = 3,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError(
                "api_key darf nicht leer sein - ANTHROPIC_API_KEY in .env setzen"
            )
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        # Echte Webrecherche (06.10., §0.6-§0.10) - NUR fuer den direkten
        # Anthropic-Pfad (dieser Provider) verfuegbar, NICHT fuer
        # GatewayRelayWritingProvider (siehe dortiger Kommentar: der
        # eigentliche Claude-Aufruf passiert dort auf einem separaten,
        # nicht zu diesem Repository gehoerenden Lexono-Gateway-Server -
        # ein hier erdachtes "tools"-Feld koennte dort schlicht ignoriert
        # werden, was eine falsche Web-Zugriffs-Behauptung waere).
        self.web_search_enabled = web_search_enabled
        self.web_search_max_uses = web_search_max_uses

    def _use_web_search(self, payload: ClaudeRequestPayload) -> bool:
        # Websuche ist ausschliesslich fuer den allgemeinen Chat vorgesehen
        # (§0.6 "Webrecherche ist eine LLM-Tool-Faehigkeit" steht im
        # Chat-Kontext) - der unveraenderte Schriftsatz-/Drafting-Pfad
        # (app/web/schriftsatz_router.py, immer "formulate_draft" u. Ä.)
        # bekommt bewusst KEIN Tool angehaengt, um dessen bestehendes,
        # vollstaendig getestetes Verhalten (inkl. Prompt-Caching-Annahmen)
        # nicht zu veraendern.
        return self.web_search_enabled and payload.schreibauftrag == "chat_response"

    def _tools_for(self, payload: ClaudeRequestPayload) -> list[dict] | None:
        if not self._use_web_search(payload):
            return None
        return [
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": self.web_search_max_uses,
            }
        ]

    def _system_blocks_for(self, payload: ClaudeRequestPayload) -> list[dict]:
        return [
            {
                "type": "text",
                "text": select_system_prompt(
                    payload.schreibauftrag,
                    web_search_available=self._use_web_search(payload),
                ),
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def write(self, payload: ClaudeRequestPayload) -> ClaudeWritingResult:
        # Prompt-Caching (Schritt 3): je Zweck (siehe select_system_prompt)
        # weiterhin projektweit IDENTISCH fuer jeden Aufruf mit demselben
        # Zweck - als gecachter Block markiert, spart es ab dem zweiten
        # Aufruf innerhalb des Cache-Fensters Eingabe-Tokens. Der
        # Nachrichtentext trennt zusätzlich den wiederkehrenden
        # Aktenkontext vom variablen Schreibauftrag (siehe
        # build_writing_prompt_cache_blocks).
        create_kwargs: dict = {}
        tools = self._tools_for(payload)
        if tools is not None:
            create_kwargs["tools"] = tools
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            **create_kwargs,
            # ECHTER FUND, LIVE REPRODUZIERT (05.10., Owner-Direktive
            # "Abschließende Live-Verifikation nach Aufladung des
            # Anthropic-Guthabens"): OHNE diesen Parameter aktiviert das
            # Modell (claude-sonnet-5) STANDARDMAESSIG ein unsichtbares
            # "Denken" (`response.content` enthaelt einen `thinking`-
            # Block), dessen Laenge HOCHGRADIG VARIABEL und vom Modell
            # selbst gesteuert ist (live gemessen: 165/1220/3352 Tokens
            # fuer strukturell aehnliche Anfragen) - zaehlt dabei VOLL
            # gegen `max_tokens`, OHNE im sichtbaren `text` zu erscheinen.
            # Bei einem unklaren/laengeren Sachverhalt verbrauchte das
            # Denken bis zu 82% des gesamten Budgets (3352 von 4096
            # Tokens) - der sichtbare Text wurde dadurch TROTZ des bereits
            # auf 4096 angehobenen Limits (vorherige Runde) weiterhin
            # mitten im Satz abgeschnitten (`stop_reason="max_tokens"`,
            # live reproduziert). Eine weitere pauschale Erhoehung von
            # `max_tokens` haette dieses Problem NICHT zuverlaessig
            # geloest (keine erkennbare Obergrenze fuer die Denkdauer
            # beobachtet) - `thinking={"type": "disabled"}` dagegen loest
            # es deterministisch: live mit demselben, zuvor abgebrochenen
            # Sachverhalt erneut getestet -> `stop_reason="end_turn"`,
            # `thinking_tokens=0`, vollstaendiger 5079-Zeichen-Text, weit
            # unter dem Limit. Fuer diesen Schreibauftrag (ein fertig
            # formuliertes Schreiben, keine mehrstufige Werkzeugnutzung/
            # komplexe Herleitung) ist internes "Denken" ohnehin nicht
            # der Zweck dieses Aufrufs. KEIN `temperature`-Parameter
            # (unveraendert, siehe Bestandskommentar unten) - dieser
            # Parameter betrifft etwas anderes und bleibt bewusst nicht
            # gesetzt.
            thinking={"type": "disabled"},
            # KEIN expliziter `temperature`-Parameter: fuer das aktuell
            # konfigurierte Modell (claude-sonnet-5) lehnt die Anthropic-API
            # diesen Parameter mit "temperature is deprecated for this
            # model" hart ab (echter Fund beim realen End-zu-Ende-Smoketest,
            # 20.08. - eine fruehere Annahme "temperature=0.0 fuer
            # deterministische Ausgabe" war mit reinen Mock-Tests nicht
            # aufgefallen). Modellseitiger Default gilt.
            system=self._system_blocks_for(payload),
            messages=[{"role": "user", "content": build_writing_prompt_cache_blocks(payload)}],
        )

        # `response.content` kann bei aktiver Websuche zusaetzlich
        # `server_tool_use`-/`web_search_tool_result`-Bloecke enthalten
        # (die eigentliche, von Anthropic serverseitig ausgefuehrte Suche) -
        # wie bisher wird NUR `text` zu einer Antwort zusammengefuehrt, die
        # uebrigen Blocktypen sind fuer Lexono kein Freitext und werden
        # hier bewusst nicht weiterverarbeitet (siehe Offene Punkte im
        # Abschlussbericht: eine dedizierte "Quellen aus der Websuche"-UI
        # ist ein moegliches Folge-Scope, nicht Teil dieser Aenderung -
        # Claude wird per Systemprompt angewiesen, Websuche-Ergebnisse
        # bereits im sichtbaren Antworttext kenntlich zu machen).
        text = "".join(
            block.text for block in response.content if block.type == "text"
        )

        token_count = None
        input_tokens = None
        output_tokens = None
        if response.usage is not None:
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            token_count = input_tokens + output_tokens

        return ClaudeWritingResult(
            text=text,
            token_count=token_count,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def write_stream(
        self, payload: ClaudeRequestPayload
    ) -> Generator[str, None, ClaudeWritingResult]:
        """Streaming-Variante von `write()` (13.09., Streaming-
        Architekturentscheidung, siehe app/drafting/service.py::
        DraftingService.create_draft_stream) - nutzt das offizielle
        Anthropic-SDK-Streaming (`messages.stream`, `with`-Block schliesst
        die zugrundeliegende HTTP-Verbindung auch bei vorzeitigem Abbruch
        korrekt, siehe Aufrufer). Gibt ROHE (weiterhin pseudonymisierte)
        Text-Deltas zurueck - identische Datenschutzgarantie wie `write()`,
        nur inkrementell statt am Stueck. Der Rueckgabewert (`return`, PEP
        380) traegt dieselben Token-Zaehlungen wie `write()`, aus
        `stream.get_final_message()` statt `response.usage`."""
        stream_kwargs: dict = {}
        stream_tools = self._tools_for(payload)
        if stream_tools is not None:
            stream_kwargs["tools"] = stream_tools
        with self._client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            **stream_kwargs,
            # Siehe `write()` oben fuer die volle, live reproduzierte
            # Begruendung (05.10.) - identischer Fund/Fix gilt hier
            # unveraendert.
            thinking={"type": "disabled"},
            system=self._system_blocks_for(payload),
            messages=[{"role": "user", "content": build_writing_prompt_cache_blocks(payload)}],
        ) as stream:
            # `stream.text_stream` liefert laut Anthropic-SDK ausschliesslich
            # echte Text-Deltas - server_tool_use/web_search_tool_result-
            # Bloecke (die eigentliche Websuche) tauchen dort nicht auf,
            # dieselbe Garantie wie bei `response.content` in `write()` oben.
            yield from stream.text_stream

            final_message = stream.get_final_message()
            text = "".join(
                block.text for block in final_message.content if block.type == "text"
            )
            token_count = None
            input_tokens = None
            output_tokens = None
            if final_message.usage is not None:
                input_tokens = final_message.usage.input_tokens
                output_tokens = final_message.usage.output_tokens
                token_count = input_tokens + output_tokens

            return ClaudeWritingResult(
                text=text,
                token_count=token_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
