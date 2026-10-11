"""Diagnose (12.10.2026): Ursachenanalyse QA-Fehler: identische Payload, drei Entscheidungen, zwei Cloud-Antworten (Fang-Server-Festtext vs.
plausible Antwort), echte lokale Qualitaetspruefung (Ollama qwen3:4b). Je Zelle mehrere Wiederholungen.

Ergebnis 12.10.2026 (3 Wiederholungen je Zelle): Fang-Server-Festtext 0/3 in ALLEN drei Entscheidungen (auch "personal"),
plausible Antwort 3/3 in allen drei - die Ablehnung im QA-Lauf lag an der festen Test-Antwort des Fang-Servers, die die
Anfrage nicht beantwortet, nicht an der Datenschutz-Entscheidung. Erfordert laufendes Ollama mit qwen3:4b.
Aufruf: python scripts/diagnose_review_quality_check.py [Wiederholungen]"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ai_providers.ollama_provider import OllamaLocalLLMProvider
from app.drafting.response_validation import validate_claude_response
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.review import PrivacyDecisions

DOC = ("Entwurf Klagebegruendung (synthetisch)\n\nIn der Sache Beispielmann ./. Musterbau GmbH bestaetigen wir\n"
       "den Eingang des Schreibens von Richter vom 16.03.2024.\nDie Klaegerin verlangt Schadensersatz in Hoehe von 12.400,00 EUR.\n"
       "Die Maengel wurden fristgerecht angezeigt.")
FIXED = "Erhalten. Dies ist eine feste Test-Antwort des lokalen Fang-Servers."
REPEATS = int(sys.argv[1]) if len(sys.argv) > 1 else 3

llm = OllamaLocalLLMProvider(base_url="http://localhost:11434", model="qwen3:4b")
g = ClaudePrivacyGateway()
for decision in ("personal", "not_personal", "unsure"):
    d = PrivacyDecisions(personal=frozenset({"Richter"})) if decision != "not_personal" else PrivacyDecisions(not_personal=frozenset({"Richter"}))
    r = g.prepare_request(purpose="chat_response", sachverhalt="Akte: (kein spezifischer Fall zugeordnet)\n[Klage/Schriftsatz] " + DOC,
                          anwaltliche_anmerkungen="Bitte fasse das Protokoll zusammen.", privacy_decisions=d, review_mode="ask")
    assert r.allowed, r.reasons
    sach = r.payload.anonymisierter_sachverhalt
    ph = {m.original_value: m.placeholder for m in r.mappings}
    who = ph.get("Richter", "Richter")
    plausible = (f"Zusammenfassung: Der Eingang des Schreibens von {who} vom {ph.get('16.03.2024', '16.03.2024')} wird bestätigt. "
                 f"Die Klägerin verlangt Schadensersatz in Höhe von 12.400,00 EUR; die Mängel wurden fristgerecht angezeigt.")
    for label, text in (("Fang-Server-Festtext", FIXED), ("plausible Antwort", plausible)):
        results = []
        t0 = time.perf_counter()
        for _ in range(REPEATS):
            v = validate_claude_response(text, r.mappings, sach, llm, require_full_placeholder_coverage=False)
            results.append(v.passed)
        reasons = v.reasons if hasattr(v, "reasons") else getattr(v, "issues", "")
        print(f"{decision:13s} {label:22s} bestanden {sum(results)}/{REPEATS}  ({time.perf_counter() - t0:.0f} s) letzte Gruende: {str(reasons)[:160]}", flush=True)
