"""§79: Ausfuehrungswaechter fuer einen SPAETEREN Lauf des Antwort-Benchmarks - NUR der Teil, der sich sicher bauen
und ohne Modell pruefen laesst. Es gibt hier KEINEN Lauf, KEINE Kommandozeile und KEINEN Aufbau eines Cloud-Providers:
der Provider wird von aussen eingereicht (Tests: Fake bzw. echter Provider gegen den lokalen Aufzeichnungsserver).

Aufbau (vorgesehener Pfad, Checkliste C-14):

    Akte/Frage -> regulaerer Lexono-Pfad (ChatService/DraftingService) -> ClaudePrivacyGateway (Presidio,
    Pseudonymisierung, Siegel) -> GuardedWriter.write(payload) -> CallGuard.authorize(...) -> Provider

`CallGuard.authorize` laeuft unmittelbar vor JEDEM Provider-Aufruf und prueft ERNEUT:
- Laufkonfiguration unveraendert (Datei-Hash wie beim Start) und weiterhin freigegeben (`answer_run.ensure_runnable`:
  alle Pflichtfreigaben, Werte, aktueller Commit, Test-Split-Sperre/LOCK, Spezifikationen, Datenschutz-Pflichtwerte,
  Plan innerhalb von max_calls/max_cost_eur);
- Aufrufplan unveraendert (`plan_fingerprint` wie beim Start: Commit, Rubrik, Lock, Akten- und Spezifikations-
  Fingerabdruecke) und der Aufruf ist geplant und noch nicht gesendet;
- Zaehler: gesendete Aufrufe < max_calls, geschaetzte Kosten des naechsten Aufrufs <= max_cost_eur, fehlgeschlagene
  Aufrufe < abort_after_failed_calls, bisher kein Datenschutzbefund;
- Payload vom Gateway versiegelt und unveraendert, und kein geschuetzter Wert (inkl. Varianten) IRGENDEINER Akte darin.
Jeder Befund beendet den Lauf (fail-closed, `aborted` mit konkreten Gruenden); der Provider wird nicht aufgerufen.

NICHT umgesetzt (Luecken, siehe benchmarks/legal_answers_v1/AUDIT_S79.md): der Treiber, der je geplantem Aufruf die
Akte in eine isolierte Datenbank importiert und Chat/Schriftsatz ausloest; Ablage der Antwortdatensaetze ausserhalb des
Repositorys; tatsaechliche Kosten aus der Provider-Antwort (hier nur die Schaetzung je Aufruf); Fortsetzen nach
Abbruch. Ohne diese Teile ist kein Lauf moeglich - bewusst."""

from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.legal_bench import answer_run, corpus
from scripts.legal_bench.e2e import _leaked, _payload_text, leak_variants


class ExecutionAborted(RuntimeError):
    """Lauf abgebrochen - vor dem Provider-Aufruf; `reasons` nennt die konkreten Gruende."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("Benchmark-Lauf abgebrochen: " + "; ".join(reasons[:3]))
        self.reasons = list(reasons)


def protected_values() -> dict[str, list[str]]:
    """Geschuetzte Werte ALLER Akten des Korpus (eigene und fremde) einschliesslich Varianten."""
    return {c["id"]: sorted({v for p in c["reference"]["privacy"]["protect"] for v in leak_variants(p)})
            for c in corpus.load_cases()}


def call_key(call: dict) -> tuple[str, str, int]:
    return (call["spec_id"], call["task_id"], call["repetition"])


class CallGuard:
    def __init__(self, config_path: Path, *, protect: dict[str, list[str]] | None = None) -> None:
        self.config_path = Path(config_path)
        raw = self.config_path.read_bytes()
        self._config_sha = hashlib.sha256(raw).hexdigest()
        self.config = answer_run.load_config(self.config_path)
        answer_run.ensure_runnable(self.config)  # wirft NotApproved mit Gruenden - kein Start ohne Freigabe
        plan = answer_run.plan(self.config)
        if not plan["ausfuehrbar"]:
            raise answer_run.NotApproved("Plan nicht ausfuehrbar", plan["nicht_ausfuehrbar_weil"])
        self.plan_fingerprint = plan["plan_fingerprint"]
        self.planned = {call_key(c): c for c in plan["calls"]}
        self.protect = protect if protect is not None else protected_values()
        self.sent: list[tuple[str, str, int]] = []
        self.failed_calls = 0
        self.privacy_findings: list[str] = []
        self.aborted: list[str] | None = None

    def _abort(self, reasons: list[str]) -> None:
        self.aborted = reasons
        raise ExecutionAborted(reasons)

    def authorize(self, key: tuple[str, str, int] | None, payload) -> None:  # noqa: ANN001
        """Unmittelbar vor dem Provider-Aufruf. Wirft ExecutionAborted - dann darf NICHT gesendet werden."""
        from app.privacy.gateway_schema import is_gateway_sealed

        if self.aborted is not None:
            raise ExecutionAborted(["Lauf bereits abgebrochen", *self.aborted])
        reasons: list[str] = []
        try:
            raw = self.config_path.read_bytes()
        except OSError as exc:
            self._abort([f"Laufkonfiguration nicht lesbar ({type(exc).__name__})"])
        if hashlib.sha256(raw).hexdigest() != self._config_sha:
            reasons.append("Laufkonfiguration waehrend des Laufs geaendert - Freigabe gilt nur fuer die gepruefte Fassung")
        try:
            answer_run.ensure_runnable(self.config)
        except answer_run.NotApproved as exc:
            reasons += exc.reasons or [str(exc)]
        current_plan = answer_run.plan(self.config)
        if current_plan["plan_fingerprint"] != self.plan_fingerprint:
            reasons.append("Aufrufplan veraendert (Commit, Rubrik, Lock, Akte oder Spezifikation) - Lauf nicht mehr freigegeben")
        if key is None or key not in self.planned:
            reasons.append(f"Aufruf {key!r} ist nicht im freigegebenen Plan")
        elif key in self.sent:
            reasons.append(f"Aufruf {key!r} wurde bereits gesendet (keine Wiederholung ausserhalb des Plans)")
        limits = self.config["limits"]
        if len(self.sent) >= limits["max_calls"]:
            reasons.append(f"limits.max_calls erreicht ({len(self.sent)}/{limits['max_calls']})")
        estimate = (len(self.sent) + 1) * limits["price_per_call_eur_estimate"]
        if estimate > limits["max_cost_eur"]:
            reasons.append(f"limits.max_cost_eur: naechster Aufruf ergaebe geschaetzt {estimate:.2f} EUR > "
                           f"{limits['max_cost_eur']} EUR")
        if self.failed_calls >= limits["abort_after_failed_calls"]:
            reasons.append(f"limits.abort_after_failed_calls erreicht ({self.failed_calls})")
        if self.privacy_findings:
            reasons.append("frueherer Datenschutzbefund - Lauf beendet (abort_on_first_privacy_finding)")
        if not is_gateway_sealed(payload):
            reasons.append("Payload nicht vom Datenschutz-Gateway versiegelt oder nachtraeglich veraendert")
        else:
            text = _payload_text(payload)
            leaks = sorted({f"{case_id}:{value[:3]}..." for case_id, values in self.protect.items()
                            for value in values if _leaked(value, text)})
            if leaks:
                self.privacy_findings += leaks
                reasons.append(f"Datenschutzbefund im Payload ({len(leaks)} geschuetzte Werte) - nicht gesendet")
        if reasons:
            self._abort(reasons)
        self.sent.append(key)

    def record_failure(self) -> None:
        self.failed_calls += 1


class GuardedWriter:
    """Schreib-Provider fuer den DraftingService: jede Payload passiert `CallGuard.authorize`, bevor der eingereichte
    Provider sie erhaelt. Der aktuelle Aufruf wird vom (spaeteren) Treiber mit `begin(call)` gesetzt.

    Hinweis: Der DraftingService wandelt Ausnahmen des Writers in ein blockiertes Ergebnis um - massgeblich fuer den
    Treiber ist daher `guard.aborted` nach jedem Aufruf, nicht die Ausnahme."""

    def __init__(self, provider, guard: CallGuard) -> None:  # noqa: ANN001
        self._provider = provider
        self.guard = guard
        self.current: tuple[str, str, int] | None = None

    def begin(self, call: dict) -> None:
        self.current = call_key(call)

    def write(self, payload):  # noqa: ANN001, ANN201
        key, self.current = self.current, None  # ein begin() erlaubt genau einen Aufruf
        self.guard.authorize(key, payload)
        try:
            return self._provider.write(payload)
        except Exception:
            self.guard.record_failure()
            raise
