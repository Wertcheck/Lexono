"""LocalLLMProvider – Protocol für den lokalen KI-Zwischenschritt (§65).

NAMENSABGRENZUNG (wichtig, absichtlich gewählt): Dieses Modul hat NICHTS
mit `app/ai_providers/local_ai_provider.py::LocalAIProvider` zu tun - jenes
Protocol (implementiert von `RuleBasedLocalAIProvider`) bündelt bereits
bestehende, rein deterministische Datenbankabfragen (Dokumente, Fristen,
Wissenssuche) zu einem `DraftPreparationResult` - dort läuft KEIN Modell,
keine KI im eigentlichen Sinne. "LocalLLMProvider" hier bezeichnet dagegen
einen ECHTEN lokalen Sprachmodell-Aufruf (Ollama) und ist bewusst anders
benannt, um diese beiden völlig unterschiedlichen Konzepte nicht zu
vermischen.

Architektur (siehe ARCHITECTURE.md §65): lokale KI ist ein PFLICHT-
Zwischenschritt zwischen dem Privacy Gateway und Claude, keine Alternative
zu Claude:

    Input -> Presidio/Pseudonymisierung (ClaudePrivacyGateway)
          -> pseudonymisierte ClaudeRequestPayload
          -> LocalLLMProvider (Ollama)          <- dieses Modul
          -> Claude (ClaudeWritingProvider)
          -> lokale Rekonstruktion

`process()` nimmt AUSSCHLIESSLICH die bereits pseudonymisierte, Allowlist-
geprüfte `ClaudeRequestPayload` entgegen - denselben Datentyp wie
`ClaudeWritingProvider.write()` - es gibt strukturell keinen Weg, hier
versehentlich unpseudonymisierten Text einzuschleusen.

`LocalLLMUnavailableError` ist eine kontrollierte, erwartete Fehlerart
(Ollama nicht erreichbar, Modell fehlt, Timeout, unlesbare Antwort) - der
Aufrufer (`DraftingService`) MUSS bei diesem Fehler den Vorgang komplett
abbrechen und darf NIEMALS stattdessen direkt Claude aufrufen (Datenschutz
vor Verfügbarkeit, siehe Vorgabe: "Wenn Ollama nicht verfügbar ist: NICHT
Originaltext -> Claude, sondern kontrollierter Fehlerzustand.")."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.privacy.gateway_schema import ClaudeRequestPayload


class LocalLLMUnavailableError(Exception):
    """Lokale KI (Ollama) nicht erreichbar/funktionsfähig - Runtime nicht
    gestartet, Modell nicht vorhanden, Timeout oder unlesbare Antwort.
    Bewusst EIN gemeinsamer Fehlertyp für alle diese Fälle (der Aufrufer
    behandelt sie ohnehin identisch: Vorgang kontrolliert abbrechen, nie
    einen Klartext-Fallback zu Claude)."""


@dataclass
class LocalLLMResult:
    text: str
    model: str


@dataclass(frozen=True)
class LocalAIHealthStatus:
    """Ergebnis eines Erreichbarkeits-/Funktionschecks (§65 Punkt 6) -
    bewusst DREI unabhängige Wahrheitswerte statt eines einzigen
    `ok: bool`, damit ein Aufrufer (z. B. eine künftige Diagnoseseite)
    gezielt sagen kann, WAS genau fehlt, statt nur "irgendetwas nicht
    ok"."""

    reachable: bool
    model_available: bool
    error: str | None = None


class LocalLLMProvider(Protocol):
    def process(self, payload: ClaudeRequestPayload) -> LocalLLMResult:
        """Verarbeitet die bereits pseudonymisierte Payload lokal (Ollama)
        und liefert ein Ergebnis, das als zusätzlicher Kontext in die
        anschließende Claude-Anfrage einfließt (siehe
        `DraftingService.create_draft`). Wirft `LocalLLMUnavailableError`,
        wenn die lokale KI nicht erreichbar/funktionsfähig ist - gibt
        NIEMALS eine leere/erfundene Antwort zurück, um einen Fehler zu
        verschleiern."""
        ...

    def check_health(self) -> LocalAIHealthStatus:
        """Rein lesender Erreichbarkeits-/Funktionscheck, OHNE eine echte
        Inferenz auszulösen (siehe §65 Punkt 6) - für Startup-/Diagnose-
        Zwecke, unabhängig von einem tatsächlichen `process()`-Aufruf."""
        ...

    def generate_structured(self, prompt: str, schema: dict) -> dict:
        """Fuehrt eine schema-constrained lokale Anfrage aus und liefert
        das geparste JSON-Ergebnis. Bewusst GENERISCH gehalten (nur Prompt +
        JSON-Schema als Parameter, kein Bezug zu einem konkreten Aufrufer
        wie z. B. der Claude-Antwortpruefung) - dieselbe Faehigkeit wird in
        kuenftigen Schritten fuer weitere Aufgaben wiederverwendet (u. a.
        PII-/Pseudonymisierungspruefung, Lektorat, globale Suche,
        Recherche-Vorbereitung, Pruefung eingehender Dokumente/E-Mails),
        ohne dass dieses Protocol dafuer erneut geaendert werden muss.

        Empirisch belegt (siehe ARCHITECTURE.md, lokale Modell-Evaluierung):
        JSON-Schema-Constraint reduziert auf CPU-only-Legacy-Hardware die
        Antwortzeit eines sonst >600s dauernden, unkonstrainierten Aufrufs
        auf ca. 25s UND verbessert dabei die Antwortkorrektheit - der mit
        Abstand wirkungsvollste bisher gemessene Hebel, unabhaengig von der
        Modellgroesse.

        Wirft `LocalLLMUnavailableError` bei jedem Fehler (nicht erreichbar,
        Timeout, kein gueltiges JSON) - identische Fehlerbehandlung wie
        `process()`, damit Aufrufer beide Methoden im selben Fail-Closed-Muster
        behandeln koennen."""
        ...
