"""§79/§80: Ausfuehrungswaechter des Antwort-Benchmarks - die Pruefung, die vor JEDEM Provider-Aufruf laeuft, und das
Kostenbuch (geschaetzt vs. tatsaechlich). Kein Lauf, keine Kommandozeile, kein Aufbau eines Cloud-Providers: der
Provider wird von aussen eingereicht. Den Ablauf steuert `answer_driver.py` (§80), die Ablage `answer_store.py`.

Aufbau (vorgesehener Pfad, Checkliste C-14):

    Akte/Frage -> regulaerer Lexono-Pfad (ChatService/DraftingService) -> ClaudePrivacyGateway (Presidio,
    Pseudonymisierung, Siegel) -> GuardedWriter.write(payload) -> CallGuard.authorize(...) -> Provider

`CallGuard.precheck` (vor dem Ausloesen des Workflows) und `CallGuard.authorize` (unmittelbar vor dem Provider-Aufruf,
zusaetzlich mit der Payload) pruefen jedes Mal ERNEUT:
- Laufkonfiguration unveraendert (Datei-Hash wie beim Start) und weiterhin freigegeben (`answer_run.ensure_runnable`:
  alle Pflichtfreigaben, Werte, aktueller Commit, Test-Split-Sperre/LOCK, Spezifikationen, Datenschutz-Pflichtwerte,
  Plan innerhalb von max_calls/max_cost_eur);
- Aufrufplan unveraendert (`plan_fingerprint`: Commit, Rubrik, Lock, Akten- und Spezifikations-Fingerabdruecke) und der
  Aufruf ist geplant und noch nicht gesendet;
- Zaehler und Budget (§80): gesendete Aufrufe < max_calls; tatsaechliche Kosten bisher + Schaetzung des naechsten
  Aufrufs <= max_cost_eur (und die reine Schaetzung ebenso); KEIN frueherer Aufruf mit unbekannten Kosten;
  Fehlversuche < abort_after_failed_calls; kein frueherer Datenschutzbefund;
- (nur `authorize`) Payload vom Gateway versiegelt und unveraendert, kein geschuetzter Wert IRGENDEINER Akte darin.
Jeder Befund beendet den Lauf (fail-closed, `aborted` mit konkreten Gruenden); der Provider wird nicht aufgerufen.

Tatsaechliche Kosten (§80, E3): NUR aus den Usage-Metadaten der Provider-Antwort (`input_tokens`/`output_tokens`)
mal den freigegebenen Tokenpreisen der Konfiguration. Fehlen die Metadaten (oder schlaegt der Aufruf fehl, sodass
unklar ist, ob abgerechnet wurde), sind die Kosten UNBEKANNT - dann wird kein weiterer Aufruf zugelassen."""

from __future__ import annotations

import hashlib
import math
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


def call_id(call_or_key) -> str:  # noqa: ANN001
    """Stabile Aufruf-ID (ohne Zeitanteil): <Spezifikation>.<Aufgabe>.r<Wiederholung>."""
    spec, task, rep = call_key(call_or_key) if isinstance(call_or_key, dict) else call_or_key
    return f"{spec}.{task}.r{rep}"


def actual_cost(result, limits: dict, max_tokens: int | None = None) -> tuple[float | None, dict]:  # noqa: ANN001
    """Tatsaechliche Kosten aus den Provider-Metadaten - oder (None, ...) = UNBEKANNT.

    §81: unplausible Metadaten (keine ganzen Zahlen >= 0, mehr Ausgabe-Tokens als max_tokens) gelten als UNBEKANNT; der
    Betrag wird auf 1/1.000.000 EUR AUFgerundet (konservativ - ein Kleinstbetrag faellt nie auf 0)."""
    usage = {"input_tokens": getattr(result, "input_tokens", None), "output_tokens": getattr(result, "output_tokens", None)}
    ok = all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in usage.values())
    if not ok:
        return None, {**usage, "quelle": "keine verlaesslichen Usage-Metadaten - Kosten UNBEKANNT"}
    if max_tokens is not None and usage["output_tokens"] > max_tokens:
        return None, {**usage, "quelle": f"unplausible Usage (output_tokens > max_tokens {max_tokens}) - Kosten UNBEKANNT"}
    eur = (usage["input_tokens"] * limits["price_eur_per_million_input_tokens"]
           + usage["output_tokens"] * limits["price_eur_per_million_output_tokens"]) / 1_000_000
    return math.ceil(round(eur * 1_000_000, 9)) / 1_000_000, {**usage, "quelle": "Provider-Usage x freigegebener Tokenpreis"}


class CallGuard:
    def __init__(self, config_path: Path, *, protect: dict[str, list[str]] | None = None) -> None:
        self.config_path = Path(config_path)
        raw = self.config_path.read_bytes()
        self.config_sha256 = hashlib.sha256(raw).hexdigest()
        self.config = answer_run.load_config(self.config_path)
        answer_run.ensure_runnable(self.config)  # wirft NotApproved mit Gruenden - kein Start ohne Freigabe
        plan = answer_run.plan(self.config)
        if not plan["ausfuehrbar"]:
            raise answer_run.NotApproved("Plan nicht ausfuehrbar", plan["nicht_ausfuehrbar_weil"])
        self.plan = plan
        self.plan_fingerprint = plan["plan_fingerprint"]
        self.planned = {call_key(c): c for c in plan["calls"]}
        self.protect = protect if protect is not None else protected_values()
        self.sent: list[tuple[str, str, int]] = []
        self.failed_calls = 0
        self.privacy_findings: list[str] = []
        self.aborted: list[str] | None = None
        # Kostenbuch (§80): strikt getrennt
        self.estimated_cost_eur = 0.0  # Summe der Schaetzungen der gesendeten Aufrufe
        self.actual_cost_eur = 0.0  # Summe der TATSAECHLICHEN Kosten (nur aus Provider-Usage)
        self.unknown_cost_calls: list[str] = []

    def start_fingerprints(self) -> dict:
        return {"config_sha256": self.config_sha256, "plan_fingerprint": self.plan_fingerprint,
                "git_commit": self.plan["git_commit"], "lock_sha256": self.plan["lock_sha256"],
                "rubric_fingerprint": self.plan["rubric_fingerprint"]}

    def restore(self, *, sent, failed_calls: int, actual_cost_eur: float, unknown_cost_calls, estimated_cost_eur: float) -> None:  # noqa: ANN001
        """Wiederanlauf (§80, E4): bereits gesendete Aufrufe, Fehlversuche und Kosten aus der Ablage uebernehmen - sie
        zaehlen weiter gegen alle Grenzen; ein gesendeter Aufruf wird nie erneut zugelassen."""
        self.sent = [tuple(k) for k in sent]
        self.failed_calls = failed_calls
        self.actual_cost_eur = actual_cost_eur
        self.unknown_cost_calls = list(unknown_cost_calls)
        self.estimated_cost_eur = estimated_cost_eur

    def _abort(self, reasons: list[str]) -> None:
        self.aborted = reasons
        raise ExecutionAborted(reasons)

    def remaining_budget_eur(self) -> float:
        return round(self.config["limits"]["max_cost_eur"] - self.actual_cost_eur, 6)

    def _reasons(self, key: tuple[str, str, int] | None) -> list[str]:
        reasons: list[str] = []
        try:
            raw = self.config_path.read_bytes()
        except OSError as exc:
            return [f"Laufkonfiguration nicht lesbar ({type(exc).__name__})"]
        if hashlib.sha256(raw).hexdigest() != self.config_sha256:
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
        price = limits["price_per_call_eur_estimate"]
        estimate = (len(self.sent) + 1) * price
        if estimate > limits["max_cost_eur"]:
            reasons.append(f"limits.max_cost_eur: naechster Aufruf ergaebe geschaetzt {estimate:.2f} EUR > "
                           f"{limits['max_cost_eur']} EUR")
        if self.actual_cost_eur + price > limits["max_cost_eur"]:
            reasons.append(f"limits.max_cost_eur: Restbudget {self.remaining_budget_eur():.4f} EUR reicht nicht fuer die "
                           f"Schaetzung des naechsten Aufrufs ({price:.4f} EUR); tatsaechlich bisher "
                           f"{self.actual_cost_eur:.4f} EUR")
        if self.unknown_cost_calls:
            reasons.append(f"tatsaechliche Kosten unbekannt fuer {', '.join(self.unknown_cost_calls)} - kein weiterer Aufruf "
                           "(sichere Abbruchregel)")
        if self.failed_calls >= limits["abort_after_failed_calls"]:
            reasons.append(f"limits.abort_after_failed_calls erreicht ({self.failed_calls})")
        if self.privacy_findings:
            reasons.append("frueherer Datenschutzbefund - Lauf beendet (abort_on_first_privacy_finding)")
        return reasons

    def precheck(self, key: tuple[str, str, int] | None) -> None:
        """Vor dem Ausloesen des Lexono-Workflows (noch ohne Payload)."""
        if self.aborted is not None:
            raise ExecutionAborted(["Lauf bereits abgebrochen", *self.aborted])
        reasons = self._reasons(key)
        if reasons:
            self._abort(reasons)

    def authorize(self, key: tuple[str, str, int] | None, payload) -> None:  # noqa: ANN001
        """Unmittelbar vor dem Provider-Aufruf. Wirft ExecutionAborted - dann darf NICHT gesendet werden."""
        from app.privacy.gateway_schema import is_gateway_sealed

        if self.aborted is not None:
            raise ExecutionAborted(["Lauf bereits abgebrochen", *self.aborted])
        reasons = self._reasons(key)
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
        self.estimated_cost_eur = round(self.estimated_cost_eur + self.config["limits"]["price_per_call_eur_estimate"], 6)

    def record_failure(self) -> None:
        self.failed_calls += 1

    def record_cost(self, key: tuple[str, str, int], cost_eur: float | None) -> None:
        if cost_eur is None:
            self.unknown_cost_calls.append(call_id(key))
        else:
            self.actual_cost_eur = round(self.actual_cost_eur + cost_eur, 6)


class GuardedWriter:
    """Schreib-Provider fuer den DraftingService: jede Payload passiert `CallGuard.authorize`, bevor der eingereichte
    Provider sie erhaelt. Der aktuelle Aufruf wird vom Treiber mit `begin(call)` gesetzt; `last` beschreibt danach, was
    tatsaechlich geschah (fuer Ablage und Kostenbuch).

    Hinweis: Der DraftingService wandelt Ausnahmen des Writers in ein blockiertes Ergebnis um - massgeblich fuer den
    Treiber sind daher `guard.aborted` und `last`, nicht die Ausnahme."""

    def __init__(self, provider, guard: CallGuard) -> None:  # noqa: ANN001
        self._provider = provider
        self.guard = guard
        self.current: tuple[str, str, int] | None = None
        self.last: dict | None = None

    def begin(self, call: dict) -> None:
        self.current = call_key(call)
        self.last = None

    def write(self, payload):  # noqa: ANN001, ANN201
        key, self.current = self.current, None  # ein begin() erlaubt genau einen Aufruf
        try:
            self.guard.authorize(key, payload)
        except ExecutionAborted as exc:
            self.last = {"outcome": "vom_waechter_verweigert", "reasons": exc.reasons}
            raise
        payload_json = payload.model_dump_json()
        self.last = {"outcome": "gesendet", "payload_json": payload_json,
                     "payload_sha256": hashlib.sha256(payload_json.encode("utf-8")).hexdigest()}
        try:
            result = self._provider.write(payload)
        except Exception as exc:
            # unklar, ob der Provider abgerechnet hat -> Kosten UNBEKANNT (sichere Abbruchregel)
            self.guard.record_failure()
            self.guard.record_cost(key, None)
            self.last.update(outcome="providerfehler", error_type=type(exc).__name__, cost_eur=None,
                             usage={"quelle": "Aufruf fehlgeschlagen - Kosten UNBEKANNT"})
            raise
        cost, usage = actual_cost(result, self.guard.config["limits"], self.guard.config["model"]["max_tokens"])
        self.guard.record_cost(key, cost)
        # §80 (E2): abgelegt wird die PSEUDONYMISIERTE Modellantwort (Datensparsamkeit), nie die lokal re-identifizierte
        self.last.update(outcome="antwort_erhalten", cost_eur=cost, usage=usage,
                         antwort_pseudonymisiert=getattr(result, "text", "") or "")
        return result
