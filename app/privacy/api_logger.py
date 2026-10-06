"""ApiCallLogger – protokolliert externe API-Aufrufe ohne personenbezogene
Inhalte (Architekturvorgabe Punkt 10).

WICHTIGE, während der Entwicklung bewusst vermiedene Falle: Die
`reasons`-Texte aus `SecurityCheckResult` (Schritt 2) enthalten teils die
tatsächlich erkannten, potenziell sensiblen Werte im Klartext (z. B.
"Möglicherweise nicht erkannte Namen/Entitäten gefunden: ['Peter Müller']"
- der Name selbst steht im Grund!). Würde man diese Gründe direkt in
`ApiCallLog.error_status` speichern, würde genau die Information, die der
Security-Check verhindern soll, im Log landen - ein klassischer
"Fehlerbehandlung umgeht die eigentliche Schutzmaßnahme"-Fehler.

Deshalb übersetzt `categorize_block_reasons()` die Gründe in eine feste,
inhaltsfreie Kategorie-Vokabular (siehe `_BLOCK_CATEGORIES`), BEVOR
irgendetwas geloggt wird. Nur diese Kategorien landen in der Datenbank.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.cost_control.pricing import estimate_cost_usd
from app.models import ApiCallLog
from app.privacy.gateway_schema import ClaudeRequestPayload

# Feste, inhaltsfreie Kategorien - werden anhand von Textmustern in den
# Security-Check-Gründen erkannt, OHNE die Gründe selbst zu speichern.
_BLOCK_CATEGORIES = (
    ("Zweck", "purpose_not_allowed"),
    ("weiterhin erkennbare Muster", "residual_pii_detected"),
    ("Platzhalter", "mapping_inconsistency"),
    ("nicht erkannte Namen", "unrecognized_entity_suspected"),
    ("Kostenlimit", "budget_exceeded"),
    # ECHTER FUND (Chat-Intelligence-Forensik, 15.09.): ein TECHNISCHER
    # Fehlschlag der Textproduktion ("Interner Fehler bei der
    # Textproduktion", siehe app/ai_providers/orchestrator.py und
    # app/drafting/service.py) landete hier in keiner Kategorie und wurde
    # dem Anwalt als "Die Anfrage wurde aus Datenschutzgründen blockiert."
    # angezeigt. Das ist doppelt schaedlich: es verschleiert einen echten
    # technischen Fehler und untergraebt zugleich das Vertrauen in die
    # Datenschutzmeldungen, weil dieselbe Meldung dann auch dann erscheint,
    # wenn datenschutzrechtlich gar nichts vorgefallen ist.
    ("Interner Fehler", "technical_error"),
    # ECHTER FUND (UI-Live-Validierung "Zusammenfassen"-Aktion, 17.09.,
    # reproduziert per direktem DraftingService-Aufruf mit echter
    # Pseudonymisierung + echtem lokalem LLM + Spy-Writing-Provider, siehe
    # DECISIONS.md): der Grund, den `check_response_placeholder_integrity`
    # (security_check.py) erzeugt, wenn ein URSPRÜNGLICHER, nicht
    # pseudonymisierter Wert in der Claude-Antwort auftaucht - der
    # schwerwiegendste und am präzisesten verstandene der drei Stufe-1-
    # Befunde - matchte bisher KEINES der obigen Muster und landete
    # dadurch, genau wie der technische Fehler oben, im nichtssagenden
    # "unknown_block_reason"-Eimer. Das ist irreführend in beide
    # Richtungen: der Anwalt sieht dieselbe generische Meldung wie bei
    # jedem anderen, unklassifizierten Fall, UND im Audit-Log
    # (`ApiCallLog.error_status`) ist dieser sicherheitskritischste Befund
    # nicht von einem harmlosen unbekannten Fall unterscheidbar. Pattern
    # bewusst auf den eindeutigen, inhaltsfreien Teil des Grundtexts
    # beschränkt ("... nicht pseudonymisierter Wert ... gefunden -
    # möglicher Datenschutzverstoß").
    ("nicht pseudonymisierter Wert", "original_value_leaked"),
    # ECHTER FUND (19.09., live am echten Server reproduziert, Owner-
    # Direktive "CONTINUE AUTONOMOUS PRODUCT COMPLETION"): derselbe
    # Kategorisierungs-Fehler wie oben ("Interner Fehler"/
    # "original_value_leaked") traf jetzt den neuen Mindestinhalt-Check
    # in app/drafting/service.py (leere/abgeschnittene KI-Antwort, siehe
    # dortiger Kommentar) - landete ebenfalls im nichtssagenden
    # "unknown_block_reason"-Eimer und wurde dem Anwalt als "aus
    # Datenschutzgründen blockiert" angezeigt, obwohl datenschutzrechtlich
    # nichts vorgefallen war (reines Token-Limit-/Truncation-Problem).
    ("keinen verwertbaren Text", "empty_writing_response"),
    # ECHTER FUND (05.10., Owner-Direktive "P1-BUGFIX" - siehe die
    # ausfuehrliche Begruendung bei `_FRIENDLY_BLOCK_MESSAGES
    # ["local_quality_check_uncertain"]` unten): Stufe 2 (lokales LLM,
    # reine Qualitaetspruefung) landete bisher ebenfalls im
    # nichtssagenden "unknown_block_reason"-Eimer, FAELSCHLICH als
    # "Datenschutzgruende" angezeigt. Pattern matcht bewusst den
    # SELBST KONTROLLIERTEN `user_message`-Text aus
    # app/drafting/service.py (NICHT die vom lokalen Modell frei
    # erfundenen `validation.issues` - deren Wortlaut ist nicht
    # zuverlaessig genug fuer einen Mustervergleich, siehe dort).
    ("lokale Qualitätsprüfung konnte", "local_quality_check_uncertain"),
)


def categorize_block_reasons(reasons: list[str]) -> str | None:
    """Übersetzt Security-Check-Gründe in inhaltsfreie Kategorie-Codes.

    Gibt NIEMALS die Original-Gründe zurück - nur eine kommagetrennte
    Liste bekannter Kategorien, oder "unknown_block_reason" falls keine
    der bekannten Kategorien zutrifft.
    """
    if not reasons:
        return None

    matched_categories: list[str] = []
    for reason in reasons:
        for pattern, category in _BLOCK_CATEGORIES:
            if pattern in reason and category not in matched_categories:
                matched_categories.append(category)

    if not matched_categories:
        return "unknown_block_reason"
    return ",".join(matched_categories)


_FRIENDLY_BLOCK_MESSAGES: dict[str, str] = {
    "purpose_not_allowed": "Der angeforderte Zweck ist nicht freigegeben.",
    "residual_pii_detected": (
        "Es wurden nach der Pseudonymisierung weiterhin erkennbare Muster gefunden."
    ),
    # 26.09., Owner-Direktive "DOCUMENT WORKSPACE / SCHRIFTSATZ
    # PRODUCT-COMPLETION" §17 (echter E2E-Test): reproduziert, dass diese
    # Kategorie ("Platzhalter") in der Praxis fast immer bedeutet, dass
    # das Modell selbst einen nie zugewiesenen Platzhalter erfunden hat
    # (siehe app/ai_providers/claude_writing_provider.py fuer die
    # Instruktions-Gegenmassnahme UND deren dokumentierte Grenzen) - KEIN
    # echter Datenschutzvorfall (siehe "original_value_leaked" fuer den
    # tatsaechlich schwerwiegenden, davon getrennten Fall). Die
    # vorherige Meldung klang wie ein dauerhafter interner Fehler, obwohl
    # ein erneuter Versuch (neue Formulierung derselben Anfrage) das
    # Problem in der Praxis oft loest - der Nutzer wurde dadurch
    # faelschlich zum Aufgeben statt zum erneuten Versuch angeleitet.
    "mapping_inconsistency": (
        "Die von der KI erzeugte Antwort enthielt einen unerwarteten "
        "Platzhalter und wurde sicherheitshalber nicht übernommen - kein "
        "Datenschutzvorfall, sondern ein Formulierungsfehler der KI-Antwort "
        "selbst. Ein erneuter Versuch löst das in der Regel."
    ),
    "original_value_leaked": (
        "Die von der KI erzeugte Antwort enthielt einen nicht ausreichend "
        "anonymisierten Wert und wurde deshalb sicherheitshalber blockiert - "
        "kein Text wurde übernommen."
    ),
    "unrecognized_entity_suspected": (
        "Im Text wurden möglicherweise nicht erkannte Namen/Daten gefunden."
    ),
    "budget_exceeded": (
        "Das monatliche Kostenlimit für KI-Aufrufe wurde erreicht. Bitte einen Administrator "
        "kontaktieren."
    ),
    # Ehrliche, aber weiterhin inhaltsfreie Meldung: WAS schiefging, ohne
    # den rohen Grund preiszugeben (der Leak-Schutz unten bleibt unberührt).
    "technical_error": (
        "Die Antwort konnte aus technischen Gründen nicht erzeugt werden. "
        "Es handelt sich nicht um eine Datenschutz-Blockierung."
    ),
    "empty_writing_response": (
        "Die KI hat keinen verwertbaren Text zurückgegeben (möglicherweise "
        "durch das Token-Limit abgeschnitten) - kein Text wurde übernommen. "
        "Es handelt sich nicht um eine Datenschutz-Blockierung. Bitte "
        "erneut versuchen, ggf. mit kürzeren Anmerkungen oder weniger "
        "Dokumenten."
    ),
    "unknown_block_reason": "Die Anfrage wurde aus Datenschutzgründen blockiert.",
    # ECHTER FUND (05.10., Owner-Direktive "P1-BUGFIX: Schriftsatz
    # unvollständig, Folgefragen blockiert, Datenschutzprüfung fehlerhaft",
    # mit dem real konfigurierten lokalen Modell reproduziert): Stufe 2
    # der Antwortprüfung (app/drafting/response_validation.py,
    # "AUSDRÜCKLICH KEINE juristische Bewertung", nur Grammatik-/
    # Struktur-Konsistenz) landete bisher bei einem Fund IMMER im selben
    # Topf wie Stufe 1 (`response_validation_failed`/"unknown_block_
    # reason" bei nicht erkanntem Freitext-Muster) - inklusive derselben
    # "Datenschutzgründen"-Formulierung, OBWOHL diese Stufe KEINE
    # Datenschutzentscheidung trifft. Reproduziert mit dem real
    # konfigurierten lokalen Modell (qwen2.5:1.5b): hielt auf einem
    # vollständigen, fehlerfreien Entwurf OHNE jeden Platzhalter frei
    # erfundene "Befunde" fuer echte Probleme (u. a. einen nicht
    # vorhandenen Platzhalter "[KATEGORIE_XX]" beanstandet). Diese
    # Kategorie wird NICHT ueber `categorize_block_reasons` (Freitext-
    # Mustervergleich auf vom lokalen Modell frei erfundenen Formulierungen,
    # siehe dort - nicht robust genug) erreicht, sondern direkt vom
    # Aufrufer gesetzt, wenn `ResponseValidationResult.stage == "semantic"`
    # ist (siehe app/drafting/service.py). Aendert NICHTS an der
    # Fail-Closed-Entscheidung selbst (der Entwurf wird weiterhin NICHT
    # uebernommen) - nur die Einordnung wird ehrlich: keine Datenschutz-
    # Behauptung fuer einen Fund, der keiner ist.
    "local_quality_check_uncertain": (
        "Die lokale Qualitätsprüfung konnte die KI-Antwort nicht eindeutig "
        "bestätigen - kein Datenschutzvorfall, sondern eine unsichere "
        "automatische Einschätzung (z. B. durch ein schwächeres lokales "
        "Modell). Der Entwurf wurde sicherheitshalber nicht übernommen. "
        "Bitte erneut versuchen oder den Entwurf manuell prüfen."
    ),
}


def friendly_block_message(reasons: list[str]) -> str:
    """Wie `categorize_block_reasons`, aber als für den Anwalt lesbarer
    Satz statt Kategorie-Codes - WICHTIG (Security Review, Prompt 27,
    gefundene Schwachstelle): darf NIEMALS die rohen `reasons` selbst
    zurückgeben oder enthalten. Diese können laut Modul-Docstring oben
    tatsächlich erkannte, sensible Werte im Klartext enthalten (z. B.
    einen erkannten Namen). Roh angezeigt (insbesondere in einer
    Redirect-URL, siehe app/web/drafts_router.py) würden diese Werte
    sonst per Referer-Header an extern geladene Ressourcen (z. B. Google
    Fonts) durchsickern oder in Web-Server-Zugriffslogs landen - genau
    die Art Leck, die die gesamte Privacy-Gateway-Architektur verhindern
    soll. Siehe
    tests/test_security_review.py::test_blocked_reason_never_leaks_pii_into_redirect_url.
    """
    categories = categorize_block_reasons(reasons)
    if categories is None:
        return "Unbekannter Fehler."
    messages = [
        _FRIENDLY_BLOCK_MESSAGES.get(cat, "Blockiert aus Datenschutzgründen.")
        for cat in categories.split(",")
    ]
    # dedupliziert, Reihenfolge erhalten
    seen: list[str] = []
    for msg in messages:
        if msg not in seen:
            seen.append(msg)
    return " ".join(seen)


def compute_anonymized_prompt_id(payload: ClaudeRequestPayload) -> str:
    """Nicht-umkehrbarer Hash der (bereits pseudonymisierten) Payload -
    für Nachvollziehbarkeit ('war das derselbe Aufruf'), ohne den Inhalt
    zu speichern. SHA-256 ist praktisch nicht umkehrbar."""
    payload_json = json.dumps(payload.model_dump(), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()[:16]


class ApiCallLogger:
    def log_success(
        self,
        db: Session,
        *,
        workflow_id: str | None,
        model: str,
        purpose: str,
        payload: ClaudeRequestPayload,
        token_count: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> ApiCallLog:
        # Kostenschaetzung (Prompt 33) - bevorzugt die genaue Input-/
        # Output-Aufteilung, faellt sonst auf die Gesamtzahl zurueck
        # (siehe app/cost_control/pricing.py).
        estimate = estimate_cost_usd(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=token_count,
        )
        log_entry = ApiCallLog(
            workflow_id=workflow_id,
            model=model,
            purpose=purpose,
            token_count=token_count,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimate.usd if estimate is not None else None,
            anonymized_prompt_id=compute_anonymized_prompt_id(payload),
            result_status="success",
            error_status=None,
        )
        db.add(log_entry)
        db.commit()
        db.refresh(log_entry)
        return log_entry

    def log_blocked(
        self,
        db: Session,
        *,
        workflow_id: str | None,
        model: str,
        purpose: str,
        reasons: list[str],
    ) -> ApiCallLog:
        log_entry = ApiCallLog(
            workflow_id=workflow_id,
            model=model,
            purpose=purpose,
            token_count=None,
            anonymized_prompt_id=None,  # keine Payload vorhanden - wurde blockiert
            result_status="blocked",
            error_status=categorize_block_reasons(reasons),
        )
        db.add(log_entry)
        db.commit()
        db.refresh(log_entry)
        return log_entry

    def log_error(
        self,
        db: Session,
        *,
        workflow_id: str | None,
        model: str,
        purpose: str,
        payload: ClaudeRequestPayload | None = None,
        error_status: str = "writing_provider_exception",
    ) -> ApiCallLog:
        log_entry = ApiCallLog(
            workflow_id=workflow_id,
            model=model,
            purpose=purpose,
            token_count=None,
            anonymized_prompt_id=(
                compute_anonymized_prompt_id(payload) if payload else None
            ),
            result_status="error",
            # Bewusst KEINE Exception-Nachricht gespeichert - koennte
            # ebenfalls Inhalte enthalten (z. B. bei einem Parsing-Fehler
            # mit Textausschnitt in der Fehlermeldung). `error_status`
            # bleibt ein fester, inhaltsfreier Kategorie-Code (§65:
            # "local_ai_unavailable" fuer den Ollama-Fehlerfall, Default
            # unveraendert "writing_provider_exception").
            error_status=error_status,
        )
        db.add(log_entry)
        db.commit()
        db.refresh(log_entry)
        return log_entry
