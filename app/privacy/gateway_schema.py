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

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, PrivateAttr, field_validator

from app.privacy.pseudonymizer import PseudonymMapping

# Prozesslokaler Schluessel fuer das Gateway-Siegel (12.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md M4). Nur dieses Modul
# kennt ihn; er verlaesst den Prozess nie. Ein Siegel beweist: genau dieser Inhalt hat den Gateway passiert.
_SEAL_KEY = secrets.token_bytes(32)


class UnsealedPayloadError(RuntimeError):
    """Eine Cloud-Payload ohne gueltiges Gateway-Siegel (nicht vom Gateway erzeugt oder danach veraendert)."""


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
    # Gateway-Siegel (privat, nicht serialisiert, nie Teil der Cloud-Anfrage) - siehe seal_payload/require_gateway_seal.
    _gateway_seal: str | None = PrivateAttr(default=None)

    @field_validator("schreibauftrag", "anonymisierter_sachverhalt")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Feld darf nicht leer sein")
        return value


def _seal_digest(payload: ClaudeRequestPayload) -> str:
    # Serialisierung ueber die BASISKLASSE (Review): eine Unterklasse mit ueberschriebenem `model_dump_json` und kopiertem
    # Siegel haette sonst einen anderen Inhalt versenden koennen, als versiegelt wurde.
    return hmac.new(_SEAL_KEY, ClaudeRequestPayload.model_dump_json(payload).encode("utf-8"), hashlib.sha256).hexdigest()


def seal_payload(payload: ClaudeRequestPayload) -> ClaudeRequestPayload:
    """Versiegelt eine vom Gateway gepruefte Payload. NUR app/privacy/gateway.py ruft dies auf."""
    payload._gateway_seal = _seal_digest(payload)
    return payload


def is_gateway_sealed(payload: object) -> bool:
    if type(payload) is not ClaudeRequestPayload:  # exakt der Gateway-Typ, keine Unterklasse (Review)
        return False
    seal = payload._gateway_seal
    return bool(seal) and hmac.compare_digest(seal, _seal_digest(payload))


def require_gateway_seal(payload: object) -> None:
    """Serverseitige Erzwingung des Pre-Cloud-Gates: jeder Cloud-Provider ruft dies unmittelbar vor dem Versand auf.
    Wirft, wenn die Payload nicht vom Gateway stammt oder nach der Pruefung veraendert wurde (fail-closed)."""
    if not is_gateway_sealed(payload):
        raise UnsealedPayloadError(
            "Cloud-Versand verweigert: Die Anfrage stammt nicht unverändert aus der Datenschutzprüfung."
        )


#: Einziger Zweck der Cloud-Zweitmeinung (§43, Owner-Freigabe Stufe 2 nur im Feature-Branch): manuell angeforderte,
#: ungepruefte Hinweise zu Dokumenttyp und Fristen - KEINE Textproduktion. Zweckbindung in beide Richtungen: die
#: Schreib-/Review-Provider lehnen diesen Zweck ab, der Zweitmeinungs-Provider jeden anderen.
SECOND_OPINION_PURPOSE = "second_opinion_document"


class PurposeNotAllowedForProviderError(RuntimeError):
    """Eine versiegelte Payload erreicht einen Provider, der fuer ihren Zweck nicht vorgesehen ist (fail-closed)."""


def reject_second_opinion_payload(payload: ClaudeRequestPayload) -> None:
    """Fuer Schreib-/Review-Provider: eine Zweitmeinungs-Payload darf nie ueber den Textproduktionspfad gehen."""
    if payload.schreibauftrag == SECOND_OPINION_PURPOSE:
        raise PurposeNotAllowedForProviderError(
            "Cloud-Versand verweigert: Zweitmeinungs-Anfragen laufen nicht über den Schreibpfad."
        )


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
    # Human in the Loop (12.10.2026): `review_required` -> der Anwalt muss `review_candidates` entscheiden, bevor
    # irgendetwas die Cloud erreicht (allowed ist dann False). `auto_masked_terms`: im Modus "mask" vorsichtshalber
    # pseudonymisierte unsichere Begriffe (nur lokal verwenden, nie loggen).
    review_required: bool = False
    review_candidates: list = field(default_factory=list)
    auto_masked_terms: list[str] = field(default_factory=list)
