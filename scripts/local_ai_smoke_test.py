"""CLI-Skript: manueller End-zu-Ende-Smoke-Test für den lokalen KI-Kernpfad
(§65: Presidio -> Ollama -> Claude -> lokale Rekonstruktion).

Verwendung (auf einem echten Rechner mit laufendem Ollama UND konfiguriertem
ANTHROPIC_API_KEY, NICHT Teil der automatisierten pytest-Suite - dieses
Skript macht echte Netzwerkaufrufe):

    python scripts/local_ai_smoke_test.py

Prüft nacheinander (bricht bei einem fehlgeschlagenen Schritt kontrolliert
ab, führt niemals einen späteren Schritt "trotzdem" aus):
1. Ist Ollama unter der konfigurierten OLLAMA_BASE_URL erreichbar?
2. Ist das konfigurierte OLLAMA_MODEL lokal vorhanden?
3. Erkennt die ECHTE Presidio-NER (nicht `known_entities`) die synthetische
   Testperson im Beispieltext - über denselben `ClaudePrivacyGateway`, der
   auch produktiv der einzige erlaubte Weg Richtung Claude ist (siehe
   app/privacy/gateway.py)?
4. Wird der synthetische Name korrekt zu einem Platzhalter pseudonymisiert
   UND besteht die fertig aufgeteilte Payload das Gateway-eigene
   Privacy-Boundary-Gate (`GatewayResult.allowed`)?
4b. Explizite Boundary-Assertion (Architecture-Proof, Kernanforderung):
    der Originalname darf in der tatsächlich sendefertigen Payload NICHT
    vorkommen, der Platzhalter MUSS vorkommen.
5. Liefert der lokale Ollama-Aufruf (`OllamaLocalLLMProvider.process`) eine
   Antwort auf die bereits pseudonymisierte Payload?
6. Liefert der echte Claude-Aufruf (`AnthropicClaudeWritingProvider`) eine
   Antwort?
7. Funktioniert die lokale Rekonstruktion (Platzhalter -> Originalwert)?

WICHTIG (CLAUDE.md-Grundregel): ausschließlich synthetische, frei erfundene
Testdaten - niemals echte Mandantendaten in dieses Skript einfügen oder
damit testen.
"""

from __future__ import annotations

import sys
import time

from app.ai_providers.factory import (
    ProviderNotConfiguredError,
    build_local_llm_provider,
    build_writing_provider,
)
from app.ai_providers.local_llm_provider import LocalLLMUnavailableError
from app.config import get_settings
from app.privacy.gateway import ClaudePrivacyGateway

_SYNTHETIC_TEXT = (
    "Sehr geehrte Damen und Herren, unser Mandant Erika Testperson bittet um "
    "eine kurze Rückmeldung zur Betriebsprüfung 2027."
)
# Bewusst KEINE `known_entities` - der Name soll ausschliesslich ueber die
# echte Presidio-NER erkannt werden (Architecture-Proof Phase 6: "Wenn
# Presidio nicht erkennt: FAIL. Nicht manuell nachhelfen.").
_SYNTHETIC_TEST_VALUE = "Erika Testperson"


def _fail(step: str, detail: str) -> int:
    print(f"[FEHLGESCHLAGEN] {step}: {detail}")
    return 1


def main() -> int:
    overall_start = time.monotonic()
    settings = get_settings()

    if not settings.local_ai_enabled:
        print(
            "LOCAL_AI_ENABLED=false - lokale KI ist nicht aktiviert, "
            "dieser Smoke-Test prüft nur Presidio + Claude."
        )
        local_llm_provider = None
    else:
        local_llm_provider = build_local_llm_provider(settings)

    # --- 1+2: Ollama-Erreichbarkeit + Modell ---
    if local_llm_provider is not None:
        health = local_llm_provider.check_health()
        if not health.reachable:
            return _fail("Ollama-Erreichbarkeit", health.error or "unbekannter Fehler")
        print(f"[OK] Ollama erreichbar unter {settings.ollama_base_url}")

        if not health.model_available:
            return _fail("Ollama-Modell", health.error or "Modell nicht gefunden")
        print(f"[OK] Modell '{settings.ollama_model}' ist lokal vorhanden")

    # --- 3+4: echte Presidio-NER + Pseudonymisierung ueber den
    # produktiven ClaudePrivacyGateway (einziger erlaubter Weg Richtung
    # Claude, siehe app/privacy/gateway.py) ---
    gateway = ClaudePrivacyGateway()
    presidio_start = time.monotonic()
    gateway_result = gateway.prepare_request(
        purpose="formulate_draft", sachverhalt=_SYNTHETIC_TEXT
    )
    presidio_seconds = time.monotonic() - presidio_start

    if not gateway_result.allowed or gateway_result.payload is None:
        return _fail(
            "Presidio/Privacy-Gateway",
            f"Gateway hat blockiert: {gateway_result.reasons}",
        )
    mappings = gateway_result.mappings
    if not mappings:
        return _fail(
            "Presidio-Erkennung",
            "Presidio hat die synthetische Testperson NICHT erkannt "
            "(keine Mapping-Eintraege) - kein manuelles Nachhelfen erlaubt",
        )
    detected_categories = sorted({m.category for m in mappings})
    print(
        f"[OK] Presidio real ausgefuehrt: entities_detected={len(mappings)} "
        f"categories={detected_categories} latency={presidio_seconds:.2f}s"
    )

    payload = gateway_result.payload
    if _SYNTHETIC_TEST_VALUE in payload.anonymisierter_sachverhalt:
        return _fail(
            "Pseudonymisierung", "Synthetischer Name wurde NICHT aus der Payload entfernt"
        )
    placeholder = mappings[0].placeholder
    if placeholder not in payload.anonymisierter_sachverhalt:
        return _fail(
            "Pseudonymisierung", f"Platzhalter {placeholder} fehlt in der Payload"
        )
    print(f"[OK] Pseudonymisierung erfolgreich: {payload.anonymisierter_sachverhalt!r}")

    # --- 4b: PRIVACY BOUNDARY GATE (Architecture-Proof, wichtigstes Gate) ---
    # Prueft programmatisch genau die Payload, die tatsaechlich Richtung
    # Ollama/Claude verlassen wird - nicht nur ein Zwischenwert.
    cloud_payload_text = payload.model_dump_json()
    assert _SYNTHETIC_TEST_VALUE not in cloud_payload_text, (
        "PRIVACY BOUNDARY VERLETZT: Originalname im Cloud-Payload gefunden"
    )
    assert placeholder in cloud_payload_text, (
        "PRIVACY BOUNDARY FEHLER: Platzhalter fehlt im Cloud-Payload"
    )
    print(
        "[OK] Privacy-Boundary-Gate bestanden: Originalname NICHT im "
        f"Cloud-Payload, Platzhalter {placeholder} vorhanden "
        f"(payload_length={len(cloud_payload_text)} Zeichen)"
    )

    # --- 5: lokaler Ollama-Aufruf ---
    if local_llm_provider is not None:
        ollama_start = time.monotonic()
        try:
            local_result = local_llm_provider.process(payload)
        except LocalLLMUnavailableError as exc:
            return _fail("Ollama-Aufruf", str(exc))
        ollama_seconds = time.monotonic() - ollama_start
        print(
            f"[OK] Ollama-Antwort erhalten ({len(local_result.text)} Zeichen) "
            f"in {ollama_seconds:.1f}s"
        )
    else:
        ollama_seconds = None

    # --- 6: echter Claude-Aufruf ---
    try:
        writing_provider = build_writing_provider(settings)
    except ProviderNotConfiguredError as exc:
        return _fail("Claude-Konfiguration", str(exc))

    claude_start = time.monotonic()
    try:
        writing_result = writing_provider.write(payload)
    except Exception as exc:  # noqa: BLE001 - Smoke-Test soll jeden Fehler klar melden
        return _fail("Claude-Aufruf", f"{type(exc).__name__}: {exc}")
    claude_seconds = time.monotonic() - claude_start
    print(
        f"[OK] Claude-Antwort erhalten ({len(writing_result.text)} Zeichen) "
        f"in {claude_seconds:.1f}s"
    )

    # --- 7: lokale Rekonstruktion ---
    reconstructed = gateway.reconstruct_response(writing_result.text, mappings)
    print(f"[OK] Rekonstruktion durchgeführt: {reconstructed!r}")
    if placeholder in reconstructed:
        return _fail(
            "Rekonstruktion", f"Platzhalter {placeholder} wurde NICHT durch Original ersetzt"
        )

    overall_seconds = time.monotonic() - overall_start
    print("\nAlle Schritte erfolgreich.")
    if ollama_seconds is not None:
        print(f"Ollama-Latenz: {ollama_seconds:.1f}s")
    print(f"Claude-Latenz: {claude_seconds:.1f}s")
    print(f"Gesamtlatenz (inkl. Presidio/Rekonstruktion): {overall_seconds:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
