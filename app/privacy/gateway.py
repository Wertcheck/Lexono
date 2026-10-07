"""ClaudePrivacyGateway – der EINZIGE erlaubte Weg Richtung Claude API
(Architekturvorgabe Punkt 3, wörtlich).

WICHTIGE DESIGN-ENTSCHEIDUNG: Alle Payload-Felder (Sachverhalt,
Argumentationspunkte, Quellenverweise, Vorlage, anwaltliche Anmerkungen,
Gesprächsverlauf) werden GEMEINSAM in EINEM Pseudonymizer-Aufruf
verarbeitet, nicht Feld für Feld separat. Grund: Der Pseudonymizer vergibt
Platzhalter-Nummern pro Aufruf neu (siehe pseudonymizer.py) - würde man
Felder einzeln pseudonymisieren, könnte derselbe Name in zwei Feldern zwei
unterschiedliche Platzhalter bekommen (z. B. "Max Mustermann" im
Sachverhalt als [MANDANT_01], aber in einer anwaltlichen Anmerkung
fälschlich erneut als [MANDANT_01] einer ANDEREN Person). Durch das
Zusammenführen in einen Text (mit eindeutigen, kollisionssicheren
Trennmarkierungen) VOR der Pseudonymisierung bleiben Platzhalter über die
gesamte Anfrage hinweg konsistent - das gilt seit Prompt 23 explizit auch
für anwaltliche Anmerkungen (siebtes Allowlist-Feld, siehe
gateway_schema.py) und seit CHAT-02 (15.09.) für den Gesprächsverlauf
(achtes, letztes Feld): sie erhalten KEINEN eigenen, separaten
Pseudonymisierungsdurchlauf.

Ablauf (= der in der Vorgabe geforderte Datenfluss):
LOCAL DATA -> lokale Analyse (bereits erledigt, Ergebnis wird übergeben)
  -> ClaudePrivacyGateway.prepare_request()
      -> Zusammenführen + Pseudonymisierung
      -> SecurityCheckService (Schritt 2)
      -> bei Durchfall: GatewayResult(allowed=False), KEIN Payload
      -> bei Erfolg: GatewayResult(allowed=True, payload=...)
  -> [Schritt 4: ClaudeWritingProvider sendet NUR bei allowed=True]
  -> Claude API (noch nicht angebunden)
  -> ClaudePrivacyGateway.reconstruct_response() -> lokaler Klartext
"""

from __future__ import annotations

import re

from app.privacy.gateway_schema import ClaudeRequestPayload, GatewayResult
from app.privacy.presidio_ner import detect_presidio_entities, get_pos_tags
from app.privacy.pseudonymizer import PseudonymMapping, Pseudonymizer
from app.privacy.security_check import (
    SecurityCheckService,
    check_payload_placeholder_integrity,
)

# Interne, kollisionsarme Trennmarkierungen - werden NIE an Claude
# gesendet, dienen nur dem Zusammenfuehren/Aufteilen innerhalb des
# Gateways. "@@...@@" ist in normalem Kanzlei-Schriftverkehr praktisch
# nie vorhanden.
_SEP_SACHVERHALT = "@@GATEWAY_SACHVERHALT@@"
_SEP_ARGUMENTE = "@@GATEWAY_ARGUMENTE@@"
_SEP_QUELLEN = "@@GATEWAY_QUELLEN@@"
_SEP_VORLAGE = "@@GATEWAY_VORLAGE@@"
_SEP_ANMERKUNGEN = "@@GATEWAY_ANMERKUNGEN@@"
_SEP_LIST_ITEM = "@@GATEWAY_ITEM@@"
# CHAT-02 (15.09.): achter, letzter Abschnitt - bewusst ANGEHÄNGT statt
# zwischen bestehende Abschnitte eingefügt, damit die bereits bestehenden
# sieben Marker/ihre Reihenfolge unverändert bleiben (keine Änderung an
# etwas, das nicht Teil von CHAT-02 ist).
_SEP_VERLAUF = "@@GATEWAY_VERLAUF@@"

_ALL_MARKERS = (
    _SEP_SACHVERHALT,
    _SEP_ARGUMENTE,
    _SEP_QUELLEN,
    _SEP_VORLAGE,
    _SEP_ANMERKUNGEN,
    _SEP_LIST_ITEM,
    _SEP_VERLAUF,
)


# Spiegelt exakt app/chat/service.py::_HISTORY_ROLE_LABELS["assistant"]
# + das dortige "{label}: "-Zeilenformat (bewusst als Literal dupliziert,
# nicht importiert - app.chat haengt bereits von app.privacy ab, ein
# Import in umgekehrter Richtung waere ein Zirkelimport; siehe exakt
# denselben, bereits bestehenden Duplizierungs-Kommentar bei
# `lawyer_authored_text` in app/drafting/service.py fuer den analogen
# Fall mit "Anwalt: "). Nur zur Erkennung, welche Gespraechsverlauf-
# Zeilen KI-generiert sind, siehe _build_unrecognized_name_scan_text.
_ASSISTANT_HISTORY_LINE_PREFIX = "Assistent: "


def _sanitize_input(text: str) -> str:
    """Entfernt zufällige/absichtliche Vorkommen der internen
    Trennmarkierungen aus Eingabetext (Verteidigung gegen einen
    Struktur-Injection-Versuch, der die Feldaufteilung durcheinander
    bringen könnte)."""
    result = text
    for marker in _ALL_MARKERS:
        result = result.replace(marker, "[ENTFERNT]")
    return result


class ClaudePrivacyGateway:
    def __init__(
        self,
        pseudonymizer: Pseudonymizer | None = None,
        security_check: SecurityCheckService | None = None,
    ) -> None:
        # Produktiv-Default: echte Presidio-NER (app/privacy/presidio_ner.py)
        # sowohl bei der Pseudonymisierung als auch beim Restrisiko-Scan des
        # SecurityCheckService - der Gateway ist der einzige erlaubte Weg
        # Richtung Claude (siehe Moduldocstring), muss also sicher-by-default
        # sein. Tests/Aufrufer, denen die Presidio-Ladezeit nicht wichtig
        # ist, koennen explizit `Pseudonymizer()`/`SecurityCheckService()`
        # ohne `ner_detector` injizieren.
        self.pseudonymizer = pseudonymizer or Pseudonymizer(
            ner_detector=detect_presidio_entities
        )
        self.security_check = security_check or SecurityCheckService(
            ner_detector=detect_presidio_entities, pos_tagger=get_pos_tags
        )

    def prepare_request(
        self,
        *,
        purpose: str,
        sachverhalt: str,
        argumentationspunkte: list[str] | None = None,
        quellenverweise: list[str] | None = None,
        stil: str | None = None,
        vorlage: str | None = None,
        anwaltliche_anmerkungen: str | None = None,
        known_entities: dict[str, list[str]] | None = None,
        gespraechsverlauf: list[str] | None = None,
    ) -> GatewayResult:
        """Baut eine sendefertige, pseudonymisierte Payload - oder
        blockiert (siehe GatewayResult.allowed). Ruft selbst KEINE Claude
        API auf (das übernimmt erst Schritt 4).

        `anwaltliche_anmerkungen` (siebtes Allowlist-Feld, siehe
        gateway_schema.py) durchläuft GENAU DENSELBEN gemeinsamen
        Pseudonymisierungs-/Security-Check-Durchlauf wie alle anderen
        Felder - es gibt keinen Pfad, der anwaltliche Anmerkungen ungeprüft
        an Claude weiterreichen könnte.

        `gespraechsverlauf` (CHAT-02, achtes/letztes Allowlist-Feld):
        bereits als "Rolle: Text"-Zeilen formatierte, aber NOCH NICHT
        pseudonymisierte History-Einträge (siehe app/chat/service.py) -
        durchläuft GENAU DENSELBEN gemeinsamen Durchlauf wie jedes andere
        Feld. Das gilt ausdrücklich auch für bereits einmal rekonstruierte
        Assistant-Antworten aus früheren Turns: sie erreichen diese Methode
        hier erneut als Klartext und werden bei DIESEM Aufruf erneut vom
        Presidio-Detektor geprüft - es gibt keinen "bereits sicher"-Fast-
        Path an der Pseudonymisierung vorbei, unabhängig davon, ob der Text
        schon einmal pseudonymisiert war."""
        argumentationspunkte = argumentationspunkte or []
        quellenverweise = quellenverweise or []
        gespraechsverlauf = gespraechsverlauf or []

        combined = self._build_combined_text(
            sachverhalt,
            argumentationspunkte,
            quellenverweise,
            vorlage,
            anwaltliche_anmerkungen,
            gespraechsverlauf,
        )

        pseudonymized_combined, mappings = self.pseudonymizer.pseudonymize(
            combined, known_entities=known_entities
        )

        (
            pseudo_sachverhalt,
            pseudo_argumente,
            pseudo_quellen,
            pseudo_vorlage,
            pseudo_anmerkungen,
            pseudo_verlauf,
        ) = self._split_combined_text(pseudonymized_combined)

        # ECHTER FUND (07.10., Owner-Direktive "Chat-Pipeline Privacy-
        # False-Positive bei allgemeinen Fragen"): Punkt 6 im
        # SecurityCheckService (Heuristik gegen uebersehene Namen) darf
        # nicht auf fruehere Claude-Antworten im Gespraechsverlauf
        # anschlagen - siehe SecurityCheckService.check Docstring zu
        # `unrecognized_name_scan_text` fuer die volle Begruendung. Alle
        # anderen Pruefungen (Punkt 2/3/4/5/7) bekommen weiterhin den
        # VOLLEN `pseudonymized_combined` inkl. Assistant-Zeilen - nur
        # Punkt 6 scannt stattdessen diesen bereinigten Text.
        unrecognized_name_scan_text = self._build_unrecognized_name_scan_text(
            pseudo_sachverhalt,
            pseudo_argumente,
            pseudo_quellen,
            pseudo_vorlage,
            pseudo_anmerkungen,
            original_gespraechsverlauf=gespraechsverlauf,
            pseudo_verlauf=pseudo_verlauf,
        )

        check_result = self.security_check.check(
            pseudonymized_combined,
            mappings,
            purpose=purpose,
            unrecognized_name_scan_text=unrecognized_name_scan_text,
        )
        if not check_result.passed:
            return GatewayResult(
                allowed=False,
                purpose=purpose,
                payload=None,
                mappings=mappings,
                reasons=check_result.reasons,
            )

        payload = ClaudeRequestPayload(
            schreibauftrag=purpose,
            gewuenschter_stil=stil,
            anonymisierter_sachverhalt=pseudo_sachverhalt,
            anonymisierte_argumentationspunkte=pseudo_argumente,
            anonymisierte_quellenverweise=pseudo_quellen,
            schreibvorlage=pseudo_vorlage,
            anonymisierte_anwaltliche_anmerkungen=pseudo_anmerkungen,
            anonymisierter_gespraechsverlauf=pseudo_verlauf,
        )

        # FINAL PAYLOAD GATE: prueft die tatsaechlich fertig aufgeteilte
        # Payload noch einmal, unmittelbar bevor sie als sendefertig
        # zurueckgegeben wird - siehe check_payload_placeholder_integrity
        # fuer die Begruendung, warum das trotz des bereits bestandenen
        # SecurityCheckService-Durchlaufs oben eine eigenstaendige Pruefung
        # ist (deckt Fehler im Aufteilungsschritt selbst ab).
        payload_gate_reasons = check_payload_placeholder_integrity(payload, mappings)
        if payload_gate_reasons:
            return GatewayResult(
                allowed=False,
                purpose=purpose,
                payload=None,
                mappings=mappings,
                reasons=payload_gate_reasons,
            )

        return GatewayResult(
            allowed=True, purpose=purpose, payload=payload, mappings=mappings, reasons=[]
        )

    def reconstruct_response(
        self, claude_response_text: str, mappings: list[PseudonymMapping]
    ) -> str:
        """Lokale Rückführung: Platzhalter im Claude-Antworttext werden
        durch die Originalwerte ersetzt. Rein lokal, kein Netzwerkzugriff."""
        return self.pseudonymizer.reconstruct(claude_response_text, mappings)

    @staticmethod
    def _build_combined_text(
        sachverhalt: str,
        argumentationspunkte: list[str],
        quellenverweise: list[str],
        vorlage: str | None,
        anwaltliche_anmerkungen: str | None,
        gespraechsverlauf: list[str],
    ) -> str:
        clean_sachverhalt = _sanitize_input(sachverhalt)
        clean_argumente = [_sanitize_input(a) for a in argumentationspunkte]
        clean_quellen = [_sanitize_input(q) for q in quellenverweise]
        clean_vorlage = _sanitize_input(vorlage) if vorlage else ""
        clean_anmerkungen = (
            _sanitize_input(anwaltliche_anmerkungen) if anwaltliche_anmerkungen else ""
        )
        # CHAT-02: dieselbe Sanitisierung wie jedes andere Feld - jeder
        # History-Eintrag (auch ein bereits rekonstruierter Assistant-Turn)
        # könnte theoretisch einen der internen Trennmarker enthalten.
        clean_verlauf = [_sanitize_input(v) for v in gespraechsverlauf]

        parts = [
            _SEP_SACHVERHALT,
            clean_sachverhalt,
            _SEP_ARGUMENTE,
            _SEP_LIST_ITEM.join(clean_argumente),
            _SEP_QUELLEN,
            _SEP_LIST_ITEM.join(clean_quellen),
            _SEP_VORLAGE,
            clean_vorlage,
            _SEP_ANMERKUNGEN,
            clean_anmerkungen,
            _SEP_VERLAUF,
            _SEP_LIST_ITEM.join(clean_verlauf),
        ]
        return "\n".join(parts)

    @staticmethod
    def _split_combined_text(
        combined: str,
    ) -> tuple[str, list[str], list[str], str | None, str | None, list[str]]:
        pattern = re.compile(
            rf"{re.escape(_SEP_SACHVERHALT)}\n(.*?)\n{re.escape(_SEP_ARGUMENTE)}\n"
            rf"(.*?)\n{re.escape(_SEP_QUELLEN)}\n(.*?)\n{re.escape(_SEP_VORLAGE)}\n(.*?)\n"
            rf"{re.escape(_SEP_ANMERKUNGEN)}\n(.*?)\n{re.escape(_SEP_VERLAUF)}\n(.*)",
            re.DOTALL,
        )
        match = pattern.match(combined)
        if not match:
            raise ValueError(
                "Interner Fehler: pseudonymisierter Text konnte nicht in "
                "Felder zurückgeteilt werden - Trennmarkierungen wurden "
                "möglicherweise durch die Pseudonymisierung verändert."
            )

        (
            sachverhalt_text,
            argumente_text,
            quellen_text,
            vorlage_text,
            anmerkungen_text,
            verlauf_text,
        ) = match.groups()

        argumente = (
            argumente_text.split(_SEP_LIST_ITEM) if argumente_text else []
        )
        quellen = quellen_text.split(_SEP_LIST_ITEM) if quellen_text else []
        vorlage = vorlage_text if vorlage_text else None
        anmerkungen = anmerkungen_text if anmerkungen_text else None
        verlauf = verlauf_text.split(_SEP_LIST_ITEM) if verlauf_text else []

        return sachverhalt_text, argumente, quellen, vorlage, anmerkungen, verlauf

    @staticmethod
    def _build_unrecognized_name_scan_text(
        pseudo_sachverhalt: str,
        pseudo_argumente: list[str],
        pseudo_quellen: list[str],
        pseudo_vorlage: str | None,
        pseudo_anmerkungen: str | None,
        *,
        original_gespraechsverlauf: list[str],
        pseudo_verlauf: list[str],
    ) -> str:
        """Text fuer SecurityCheckService Punkt 6 (siehe dortigen
        Docstring zu `unrecognized_name_scan_text`): identisch zum vollen
        kombinierten Text, aber ohne die Gespraechsverlauf-Zeilen, die von
        Claude selbst stammen ("Assistent: "-Praefix) - nur echte
        Anwalt-Zeilen der Historie bleiben fuer diese eine Pruefung
        erhalten. `original_gespraechsverlauf` (VOR Pseudonymisierung) und
        `pseudo_verlauf` (danach, siehe _split_combined_text) haben
        garantiert dieselbe Laenge/Reihenfolge - Pseudonymisierung
        ersetzt nur Zeichen INNERHALB jedes Eintrags, nie die Anzahl
        oder Reihenfolge der Listeneintraege."""
        lawyer_verlauf = [
            pseudo_entry
            for original_entry, pseudo_entry in zip(
                original_gespraechsverlauf, pseudo_verlauf
            )
            if not original_entry.startswith(_ASSISTANT_HISTORY_LINE_PREFIX)
        ]
        parts = [
            pseudo_sachverhalt,
            "\n".join(pseudo_argumente),
            "\n".join(pseudo_quellen),
            pseudo_vorlage or "",
            pseudo_anmerkungen or "",
            "\n".join(lawyer_verlauf),
        ]
        return "\n".join(parts)
