"""Ablauf der manuell angeforderten Cloud-Zweitmeinung (§43, Stufe 2 - nur Feature-Branch, nicht eingebunden).

Reihenfolge, jeder Schritt fail-closed (Status statt Ergebnis, nie eine Uebernahme):

1. Laenge (`check_length`): zu lang -> `zu_lang`, NICHT gekuerzt, nichts gesendet.
2. Budget (injizierte Pruefung, produktiv `CostControlService.check_before_call`) -> `budget_erschoepft`.
3. `ClaudePrivacyGateway.prepare_request(purpose=SECOND_OPINION_PURPOSE, review_mode="ask")` - die BESTEHENDE
   Pseudonymisierung, Kandidatenpruefung, Restrisiko-Scan, Final Payload Gate und Siegel. Rueckfrage-Kandidaten ->
   `rueckfrage_erforderlich` (fuer Dokumente gibt es keine Rueckfrage-UI); jede andere Blockade ->
   `datenschutz_blockiert`. Ein Dateiname wird NICHT uebergeben (Owner-Vorgabe Stufe 2).
4. Provider (nur Anthropic, versiegelt, zweckgebunden): Zeitueberschreitung -> `zeitueberschreitung`, sonst `fehler`.
5. Antwort: Leck-Pruefung des Gateways (`check_response_placeholder_integrity` gegen die Pseudonym-Zuordnungen),
   danach `validate_response` gegen den tatsaechlich gesendeten Text -> sonst `ungueltig`.
6. `compare` mit dem unveraenderlichen lokalen Schnappschuss - ergibt nur ungepruefte Cloud-Hinweise.

Der Service liest und schreibt KEINE Datenbank und aendert keine lokalen Ergebnisse; Audit nur ueber einen
injizierten Rueckruf (Status, Zweck, inhaltsfreie Grundkategorien, Tokens, Payload-Hash - nie Inhalt).
`SecondOpinionResult.reasons` sind lokale Anzeigegruende (wie `DraftingResult.blocked_reasons`) und koennen
Begriffe aus dem Dokument enthalten - sie gehen nie in Audit oder Cloud."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.privacy.api_logger import categorize_block_reasons
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.gateway_schema import SECOND_OPINION_PURPOSE, ClaudeRequestPayload, UnsealedPayloadError
from app.privacy.security_check import check_response_placeholder_integrity
from app.second_opinion.comparison import Comparison, LocalSnapshot, OpinionOutcome, OpinionStatus, compare
from app.second_opinion.provider import ProviderAnswer, SecondOpinionTimeout
from app.second_opinion.validation import check_length, validate_response


class SecondOpinionProvider(Protocol):
    def request(self, payload: ClaudeRequestPayload) -> ProviderAnswer: ...


class BudgetDecision(Protocol):
    allowed: bool
    reason: str | None


@dataclass(frozen=True)
class SecondOpinionResult:
    comparison: Comparison
    reasons: tuple[str, ...] = ()
    payload_sha256: str | None = None  # nur der Hash der gesendeten Payload, nie ihr Inhalt
    input_tokens: int | None = None
    output_tokens: int | None = None


AuditHook = Callable[[str, dict], None]


class SecondOpinionService:
    def __init__(self, *, gateway: ClaudePrivacyGateway, provider: SecondOpinionProvider, max_chars: int,
                 budget_check: Callable[[], BudgetDecision], audit: AuditHook | None = None) -> None:
        if max_chars <= 0:
            raise ValueError("max_chars muss positiv sein")
        self.gateway = gateway
        self.provider = provider
        self.max_chars = max_chars
        self.budget_check = budget_check
        self.audit = audit

    def request(self, *, document_text: str, local: LocalSnapshot,
                known_entities: dict[str, list[str]] | None = None) -> SecondOpinionResult:
        length = check_length(document_text, self.max_chars)
        if not length.may_send:
            status = OpinionStatus.TOO_LONG if (length.reason or "").startswith("zu_lang") else OpinionStatus.INVALID
            return self._fail(local, status, [length.reason or "nicht sendefaehig"])

        try:
            budget = self.budget_check()
            budget_ok = bool(budget.allowed)
            budget_reason = budget.reason
        except Exception:  # noqa: BLE001 - unbekannter Budgetstand blockiert (fail-closed)
            budget_ok, budget_reason = False, "Budgetprüfung nicht möglich"
        if not budget_ok:
            return self._fail(local, OpinionStatus.BUDGET_EXCEEDED, [budget_reason or "Kostenlimit erreicht"])

        try:
            gate = self.gateway.prepare_request(purpose=SECOND_OPINION_PURPOSE, sachverhalt=document_text,
                                                known_entities=known_entities, review_mode="ask")
        except Exception:  # noqa: BLE001 - jede Ausnahme im Gate: kein Versand
            return self._fail(local, OpinionStatus.BLOCKED_PRIVACY, ["Datenschutzprüfung nicht abgeschlossen"])
        if not gate.allowed or gate.payload is None:
            if gate.review_required:
                return self._fail(local, OpinionStatus.REVIEW_REQUIRED, list(gate.reasons))
            return self._fail(local, OpinionStatus.BLOCKED_PRIVACY, list(gate.reasons) or ["blockiert"])

        payload_hash = hashlib.sha256(ClaudeRequestPayload.model_dump_json(gate.payload).encode("utf-8")).hexdigest()
        try:
            answer = self.provider.request(gate.payload)
        except SecondOpinionTimeout:
            return self._fail(local, OpinionStatus.TIMEOUT, ["Zeitüberschreitung"], payload_hash)
        except UnsealedPayloadError:
            return self._fail(local, OpinionStatus.BLOCKED_PRIVACY, ["Siegelprüfung fehlgeschlagen"], payload_hash)
        except Exception as exc:  # noqa: BLE001 - Providerfehler: kein Ergebnis
            return self._fail(local, OpinionStatus.ERROR, [type(exc).__name__], payload_hash)

        leak_reasons = check_response_placeholder_integrity(answer.text, gate.mappings, require_full_coverage=False)
        if leak_reasons:
            return self._fail(local, OpinionStatus.INVALID,
                              ["Antwort enthält Platzhalter- oder Klartextauffälligkeiten"], payload_hash, answer)
        validation = validate_response(answer.text, answer.sent_text)
        if not validation.accepted:
            return self._fail(local, OpinionStatus.INVALID, list(validation.reasons), payload_hash, answer)

        comparison = compare(local, OpinionOutcome(OpinionStatus.OK, validation.opinion))
        result = SecondOpinionResult(comparison=comparison, payload_sha256=payload_hash,
                                     input_tokens=answer.input_tokens, output_tokens=answer.output_tokens)
        self._audit(OpinionStatus.OK, (), result)
        return result

    def _fail(self, local: LocalSnapshot, status: OpinionStatus, reasons: list[str], payload_hash: str | None = None,
              answer: ProviderAnswer | None = None) -> SecondOpinionResult:
        result = SecondOpinionResult(
            comparison=compare(local, OpinionOutcome(status, detail="; ".join(reasons))),
            reasons=tuple(reasons), payload_sha256=payload_hash,
            input_tokens=answer.input_tokens if answer else None,
            output_tokens=answer.output_tokens if answer else None)
        self._audit(status, tuple(reasons), result)
        return result

    def _audit(self, status: OpinionStatus, reasons: tuple[str, ...], result: SecondOpinionResult) -> None:
        if self.audit is None:
            return
        # Gruende NUR als inhaltsfreie Kategorien (wie ApiCallLog) - Gate-Gruende koennen Begriffe enthalten
        self.audit("second_opinion_" + status.value, {
            "purpose": SECOND_OPINION_PURPOSE, "status": status.value,
            "reason_categories": categorize_block_reasons(list(reasons)),
            "payload_sha256": result.payload_sha256, "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        })
