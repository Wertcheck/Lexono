"""Allowlist-Payload-Schema (Architekturvorgabe Punkt 7, wörtlich):

"Nur explizit für die Textproduktion freigegebene Informationen dürfen
Claude erreichen." Genau diese Felder - alles andere bleibt lokal. Kein
Feld für "sonstige Daten", kein Freitext-Escape-Hatch.

Ergänzung (Vorgabe des Anwalts, wörtlich): "erweitere die bestehende
Allowlist um genau ein eigenes Feld für anonymisierte anwaltliche
Anmerkungen" - `anonymisierte_anwaltliche_anmerkungen` ist dieses siebte,
explizit benannte Feld. Es entsteht wie alle anderen Felder AUSSCHLIESSLICH
über `ClaudePrivacyGateway.prepare_request` (durchläuft denselben
gemeinsamen Pseudonymisierungs-/Security-Check-Durchlauf wie Sachverhalt,
Argumentationspunkte, Quellenverweise und Vorlage) - es gibt keinen
zweiten, ungeprüften Weg, wie anwaltliche Anmerkungen dieses Schema
erreichen könnten (siehe app/attorney_instructions/service.py).

CHAT-02 (15.09., ausdrückliche Owner-Freigabe nach vorheriger, ausführlich
begründeter Zurückstellung - siehe DECISIONS.md/OPEN_ISSUES.md für die
vollständige Herleitung): achtes, LETZTES Feld
`anonymisierter_gespraechsverlauf` für den bisherigen Chat-Gesprächsverlauf
- Root Cause der Chat-Intelligence-Forensik war, dass das Modell frühere
Turns strukturell nie erreichte. Entsteht wie alle anderen Felder
AUSSCHLIESSLICH über `ClaudePrivacyGateway.prepare_request` (identischer
gemeinsamer Pseudonymisierungs-/Security-Check-Durchlauf) - KEIN zweiter,
ungeprüfter Pseudonymisierungspfad. Jetzt GENAU ACHT Felder - diese Zahl
ist nach dieser Owner-Entscheidung die neue verbindliche Grenze, exakt wie
zuvor bei sieben.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator

from app.privacy.pseudonymizer import PseudonymMapping


class ClaudeRequestPayload(BaseModel):
    # Entspricht "Schreibauftrag" - siehe SecurityCheckService.ALLOWED_PURPOSES
    # für die zulässigen Werte.
    schreibauftrag: str
    gewuenschter_stil: str | None = None
    # Bereits pseudonymisiert, wenn dieses Objekt existiert - siehe
    # app/privacy/gateway.py, das dieses Schema als einzige Ausgabe liefert.
    anonymisierter_sachverhalt: str
    anonymisierte_argumentationspunkte: list[str] = Field(default_factory=list)
    anonymisierte_quellenverweise: list[str] = Field(default_factory=list)
    schreibvorlage: str | None = None
    # Siebtes Feld (Erweiterung s. o.): konkrete Änderungs-/Arbeitsaufträge
    # des Anwalts an die naechste Entwurfsversion (AttorneyInstruction).
    # None, wenn keine Anmerkung vorliegt - siehe WRITING_SYSTEM_PROMPT in
    # app/ai_providers/claude_writing_provider.py fuer die Regel, dass ein
    # fehlender Wert NIEMALS als inhaltliche Position ausgelegt werden darf.
    anonymisierte_anwaltliche_anmerkungen: str | None = None
    # Achtes, letztes Feld (CHAT-02, s. o.): bisheriger Chat-Gesprächsverlauf
    # als Folge bereits formatierter, pseudonymisierter Zeilen
    # ("Anwalt: ..."/"Assistent: ..." - siehe
    # app/chat/service.py::_HISTORY_ROLE_LABELS für die Formatierung, EIN
    # Eintrag pro historischer Nachricht, chronologisch aufsteigend). Leere
    # Liste = keine Historie zu übertragen (neue Konversation oder
    # Nicht-Chat-Aufrufer wie der Schriftsatz-Generator/die anwaltlichen
    # Anweisungen, die dieses Feld strukturell nie befüllen).
    anonymisierter_gespraechsverlauf: list[str] = Field(default_factory=list)

    @field_validator("schreibauftrag", "anonymisierter_sachverhalt")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Feld darf nicht leer sein")
        return value


@dataclass
class GatewayResult:
    """Ergebnis eines `ClaudePrivacyGateway.prepare_request`-Aufrufs.

    `payload` ist NUR bei `allowed=True` gesetzt - bei `allowed=False`
    (blockiert) existiert keine sendefertige Payload, nur `reasons`.
    `mappings` wird für `reconstruct_response` benötigt und muss lokal
    aufbewahrt werden (siehe Moduldocstring in gateway.py).
    """

    allowed: bool
    purpose: str
    payload: ClaudeRequestPayload | None = None
    mappings: list[PseudonymMapping] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    # ECHTER FUND (Owner-Direktive "Architektur-Audit Privacy-/Chat-
    # Pipeline", 07.10.): alles, was der Anwalt fuer DIESE Anfrage
    # plausibel selbst geliefert haben koennte - getippt (Sachverhalt,
    # Argumentationspunkte, Quellenverweise, Vorlage, Anmerkungen,
    # "Anwalt: "-Historienzeilen) ODER aus einem Dokument extrahiert
    # (ebenfalls Teil von Sachverhalt/Vorlage) - OHNE die "Assistent: "-
    # Zeilen der Historie (Claudes eigene, bereits einmal generierte
    # Prosa). Siehe app/privacy/gateway.py::_build_locally_sourced_raw_text
    # fuer den Aufbau und app/privacy/security_check.py::
    # find_lenient_leak_exempt_placeholders fuer die Verwendung (ersetzt
    # dort die vorher zu eng gefasste, nur Anmerkungen+Chat-Historie
    # umfassende lokale Rekonstruktion in app/drafting/service.py - diese
    # Version deckt zusaetzlich `sachverhalt`/`vorlage`/`quellenverweise`
    # ab, also auch dokumentbasierten Kontext). Immer gesetzt (auch bei
    # `allowed=False`), da rein lokale Textkonkatenation vor jeder
    # Pseudonymisierungs-/Security-Check-Entscheidung berechnet wird.
    locally_sourced_text: str = ""
