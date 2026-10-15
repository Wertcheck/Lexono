"""Anthropic-Provider der Cloud-Zweitmeinung (§43, Stufe 2 - nur Feature-Branch, nur Fang-Server-Tests).

Datenschutz wie beim Schreib-Provider strukturell: Diese Klasse bekommt AUSSCHLIESSLICH eine vom
`ClaudePrivacyGateway` versiegelte `ClaudeRequestPayload` (`require_gateway_seal`) mit dem Zweck
`SECOND_OPINION_PURPOSE` - jeder andere Zweck wird abgewiesen (Zweckbindung). Kein Zugriff auf Dokument-, Akten- oder
Mandantenmodelle.

Datenminimierung: gesendet wird NUR `anonymisierter_sachverhalt` (der pseudonymisierte Dokumenttext), keine
Argumentationspunkte, kein Dateiname, kein Verlauf. Genau dieser Text ist `sent_text` - gegen ihn prueft die
Antwortvalidierung, dass jedes vorgeschlagene Datum tatsaechlich gesendet wurde.

Fail-closed: kurzes, explizites Timeout und KEINE automatischen Wiederholungen (das SDK wuerde sonst bis zu 600 s
warten und zweimal wiederholen). Fehler werden als `SecondOpinionTimeout`/`SecondOpinionProviderError` gemeldet, ohne
Antwortinhalt. Nur Anthropic; das Ziel kommt aus `endpoint_policy.resolve_anthropic_base_url` (offiziell oder
Pruefendpunkt auf diesem Rechner mit QA-Freigabe).
"""

from __future__ import annotations

from dataclasses import dataclass

import anthropic

from app.ai_providers.endpoint_policy import OFFICIAL_ANTHROPIC_BASE_URL
from app.privacy.gateway_schema import (
    SECOND_OPINION_PURPOSE,
    ClaudeRequestPayload,
    PurposeNotAllowedForProviderError,
    require_gateway_seal,
)

SECOND_OPINION_SYSTEM_PROMPT = (
    "Du gibst eine zweite Einschätzung zu einem deutschen Kanzleidokument ab. Der Text ist pseudonymisiert; "
    "Platzhalter in eckigen Klammern sind Personen, Orte usw. - übernimm sie nie in die Antwort. "
    "Antworte ausschließlich mit EINEM JSON-Objekt ohne weiteren Text: "
    '{"typ": "<genau ein Typ aus der Liste>", "frist": ["TT.MM.JJJJ"], "termin": ["TT.MM.JJJJ"], '
    '"fristbeginn": ["TT.MM.JJJJ"], "fristdauern": <Zahl>}. '
    "Typliste: Rechnung, Vollmacht, Kündigungsschreiben, Mahnung, Klage/Schriftsatz, Gerichtliches Schreiben, "
    "Vertrag, Steuerbescheid, Einspruch, Prüfungsanordnung, Steuererklärung, Urteil/Beschluss, Bescheid, Gutachten, "
    "Protokoll, Anlage, Sonstiges, Unbekannt. "
    "frist: ausdrücklich genannte Fristenden/Fälligkeiten; termin: anstehende Termine; fristbeginn: Datum eines "
    "fristauslösenden Ereignisses (Zugang, Zustellung, Bekanntgabe); fristdauern: Anzahl relativer Fristen ohne "
    "Enddatum. Nenne nur Daten, die wörtlich im Text stehen; berechne keine Daten. Keine Begründung, keine "
    "weiteren Felder. Unsicher: weglassen."
)


class SecondOpinionTimeout(RuntimeError):
    """Zeitueberschreitung beim Cloud-Aufruf (kein Ergebnis)."""


class SecondOpinionProviderError(RuntimeError):
    """Jeder andere Fehler beim Cloud-Aufruf (kein Ergebnis, kein Antwortinhalt in der Meldung)."""


@dataclass(frozen=True)
class ProviderAnswer:
    text: str
    sent_text: str
    input_tokens: int | None
    output_tokens: int | None


class AnthropicSecondOpinionProvider:
    def __init__(self, *, api_key: str, model: str, base_url: str = OFFICIAL_ANTHROPIC_BASE_URL,
                 timeout_seconds: float = 20.0, max_tokens: int = 600) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("api_key darf nicht leer sein")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds muss positiv sein")
        self._client = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=timeout_seconds,
                                           max_retries=0)
        self.model = model
        self.max_tokens = max_tokens

    def request(self, payload: ClaudeRequestPayload) -> ProviderAnswer:
        require_gateway_seal(payload)  # Pre-Cloud-Gate serverseitig erzwungen (PRIVACY_REVIEW_ARCHITECTURE.md M4)
        if payload.schreibauftrag != SECOND_OPINION_PURPOSE:
            raise PurposeNotAllowedForProviderError("Zweitmeinungs-Provider: nur der Zweck der Zweitmeinung.")
        sent_text = payload.anonymisierter_sachverhalt
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                thinking={"type": "disabled"},  # siehe anthropic_writing_provider.py (05.10.)
                system=SECOND_OPINION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": sent_text}],
            )
        except anthropic.APITimeoutError as exc:
            raise SecondOpinionTimeout("Zeitüberschreitung bei der Zweitmeinung") from exc
        except anthropic.APIError as exc:
            status = getattr(exc, "status_code", None)
            raise SecondOpinionProviderError(f"Fehler bei der Zweitmeinung ({type(exc).__name__}, {status})") from exc
        text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
        usage = response.usage
        return ProviderAnswer(text=text, sent_text=sent_text,
                              input_tokens=getattr(usage, "input_tokens", None),
                              output_tokens=getattr(usage, "output_tokens", None))
