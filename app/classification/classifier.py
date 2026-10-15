"""DocumentClassifier – Protocol + Platzhalter-Implementierung.

`DocumentClassifier` ist absichtlich als Protocol gehalten (wie
`MailProvider` in app/mail/base.py), damit eine spätere LLM-basierte
Implementierung (Prompt 17/34) diese Abstraktion einfach ersetzen kann,
ohne `ClassificationService` oder das Schema zu ändern.

`PlaceholderDocumentClassifier` nutzt reine Keyword-/Regex-Heuristiken -
KEIN LLM, KEIN maschinelles Lernen. Die Konfidenz ist absichtlich niedrig
gedeckelt, damit dieser Platzhalter niemals fälschlich als "sicher genug für
automatische Aktenzuordnung" gilt.
"""

from __future__ import annotations

import difflib
import re
from typing import Protocol

from app.classification.schema import ClassificationResult

# Absichtlich niedrig: ein regelbasierter Platzhalter soll nie als
# hochsicher gelten, selbst wenn mehrere Keywords treffen.
_PLACEHOLDER_MAX_CONFIDENCE = 0.4

# Reihenfolge ist bedeutsam: bei Gleichstand gewinnt der ZUERST gefundene
# Typ (siehe _detect_document_type). Die steuerrechtlichen Typen stehen
# deshalb bewusst VOR den generischen - ein Steuerbescheid enthaelt
# regelmaessig auch Woerter wie "Rechnung"/"Frist", soll aber als
# Steuerbescheid gelten (Pilotkanzlei = Steuerfachanwaltskanzlei).
_DOCUMENT_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    # P2.1 (§40): Urteil/Beschluss nur ueber eindeutige Merkmale (siehe _JUDGMENT_HEADING) - "BGH, Urteil vom ..." in
    # einem Schriftsatz macht ihn nicht zum Urteil. Die Woerter hier dienen nur der Begruendung. P2.2 (§41): ganz vorn,
    # weil jedes Urteil eine "Rechtsbehelfsbelehrung" enthaelt, die sonst als Steuerbescheid-Merkmal gewann.
    "Urteil/Beschluss": ("im namen des volkes", "für recht erkannt", "fuer recht erkannt"),
    # P2.1 (§40): nicht-steuerliche Bescheide - NUR die ausdrueckliche Bescheidart (kein blosses "Bescheid"; ein
    # Steuerbescheid heisst "Bescheid fuer 2025 ueber Einkommensteuer"). Steht VOR "Steuerbescheid", weil dessen
    # allgemeine Merkmale ("Rechtsbehelfsbelehrung", "Festsetzung") auch auf jedem Bussgeldbescheid stehen.
    "Bescheid": (
        "bußgeldbescheid", "bussgeldbescheid", "widerspruchsbescheid", "kostenbescheid", "gebührenbescheid",
        "gebuehrenbescheid", "leistungsbescheid", "bewilligungsbescheid", "ablehnungsbescheid", "beitragsbescheid",
        "zuwendungsbescheid", "rentenbescheid",
    ),
    # --- Steuerrecht (14.09., echter Fund beim Durchspielen der
    # synthetischen Kanzlei-Datenbasis: ein Steuerbescheid wurde als
    # "Unbekannt"/0.1 eingestuft, da es KEINEN steuerrechtlichen Typ gab) ---
    # ECHTER FUND beim Gegenpruefen am realen Dokumenttext (14.09.): ein
    # echter deutscher Steuerbescheid schreibt NICHT das Kompositum
    # "Einkommensteuerbescheid", sondern "Bescheid ueber Einkommensteuer"
    # bzw. "festgesetzte Einkommensteuer" - eine reine Kompositum-Liste
    # greift deshalb an echten Bescheiden gar nicht.
    "Steuerbescheid": (
        "steuerbescheid", "einkommensteuerbescheid", "körperschaftsteuerbescheid",
        "koerperschaftsteuerbescheid", "gewerbesteuerbescheid",
        "umsatzsteuerbescheid",
        "bescheid für", "bescheid fuer", "bescheid über", "bescheid ueber",
        "festgesetzte", "festsetzung", "rechtsbehelfsbelehrung",
    ),
    # "einspruch" allein ist bewusst KEIN Treffer mehr: die
    # Rechtsbehelfsbelehrung ("Einspruch innerhalb eines Monats") steht auf
    # praktisch JEDEM Steuerbescheid - das blosse Wort haette einen grossen
    # Teil aller Bescheide faelschlich zum "Einspruch" gemacht (real
    # reproduziert). Erkannt wird deshalb nur die AKTIVE Einlegung.
    "Einspruch": (
        "einspruch ein", "einspruch eingelegt", "einspruch gegen",
        "einspruchsverfahren", "einspruchsentscheidung", "einspruchsschreiben",
    ),
    "Prüfungsanordnung": (
        "prüfungsanordnung", "pruefungsanordnung", "betriebsprüfung",
        "betriebspruefung", "außenprüfung", "aussenpruefung",
        "umsatzsteuer-nachschau", "umsatzsteuernachschau",
    ),
    "Steuererklärung": (
        "steuererklärung", "steuererklaerung", "umsatzsteuer-voranmeldung",
        "umsatzsteuervoranmeldung", "einnahmenüberschussrechnung",
        "einnahmenueberschussrechnung",
    ),
    # --- allgemeine Typen ---
    # P2.2 (§41): nur Rechnungsmerkmale bzw. "...rechnung" als Ueberschrift (siehe _INVOICE_HEADING) - "die
    # Schlussrechnung vom ..." / "zu unserer Rechnung vom ..." in einem Satz macht keine Rechnung
    "Rechnung": ("rechnungsnummer", "rechnung nr", "rechnungs-nr", "rechnungsdatum", "invoice"),
    "Vollmacht": ("vollmacht",),
    # ECHTER FUND (14.09., gleiche Fehlerklasse wie bei der
    # Rechtsbehelfsbelehrung): das blosse Fragment "kündig" traf auch die
    # **Kündigungsfrist**-Klausel, die in praktisch JEDEM Vertrag steht -
    # ein "Vertragsentwurf ... Laufzeit: 24 Monate, Kündigungsfrist 3
    # Monate" wurde dadurch als Kündigungsschreiben eingestuft (real am
    # Demo-Dokument reproduziert). Erkannt wird deshalb nur die
    # tatsaechliche Kuendigungs-HANDLUNG, nicht die blosse Erwaehnung.
    # Bleibt bewusst VOR "Vertrag": ein echtes Kuendigungsschreiben nennt
    # fast immer auch den gekuendigten Vertrag.
    "Kündigungsschreiben": (
        "kündigungsschreiben", "kuendigungsschreiben",
        "kündige", "kuendige", "kündigen wir", "kuendigen wir",
        "gekündigt", "gekuendigt",
        "fristlose kündigung", "fristlose kuendigung",
        "ordentliche kündigung", "ordentliche kuendigung",
        "kündigung des", "kuendigung des",
    ),
    "Mahnung": ("mahnung", "zahlungserinnerung"),
    "Klage/Schriftsatz": ("klage", "klageschrift", "schriftsatz", "klageerwiderung", "replik"),
    "Gutachten": ("gutachten", "sachverständiger", "sachverstaendiger", "sachverständige", "sachverstaendige"),
    "Protokoll": ("protokoll", "niederschrift", "unfallmitteilung", "unfallanzeige", "unfallaufnahme"),
    "Gerichtliches Schreiben": ("gericht", "amtsgericht", "landgericht", "aktenzeichen des gerichts"),
    "Vertrag": ("vertrag", "vereinbarung"),
}

# P2.1 (§40): Typen, deren Merkmale selbstbeschreibende Wendungen sind ("festgesetzte Einkommensteuer", "legen wir
# Einspruch ein", "kuendigen wir") - sie zaehlen auch im Fliesstext. Alle anderen Typwoerter ("Rechnung", "Vertrag",
# "Vollmacht", "Gericht" ...) zaehlen nur im Kopf des Dokuments: im Fliesstext sind sie meist nur ERWAEHNT ("aus der
# Schlussrechnung", "laut Mietvertrag") und machten Korrespondenz zur Rechnung bzw. zum Vertrag.
_BODY_TYPES = ("Steuerbescheid", "Einspruch", "Prüfungsanordnung", "Steuererklärung", "Kündigungsschreiben")
# Kopf des Dokuments: Briefkopf, Betreff/Titel und erste Saetze
_HEAD_CHARS = 600
# Wendungen, die ein Typwort enthalten, aber etwas anderes bedeuten (im Legal-E2E-Korpus real beobachtet)
_EXCLUDED_PHRASES = {
    "Mahnung": ("abmahnung",),  # Abmahnung (Arbeitsrecht) ist keine Zahlungsmahnung
    "Vollmacht": ("in vollmacht",),  # "Namens und in Vollmacht unserer Mandantin" - Anwaltsschreiben, keine Vollmacht
    # P2.2 (§41): Rechtsbehelfsbelehrung "kann Einspruch eingelegt werden" / "Einspruch einlegen/einzulegen" beschreibt die
    # MOEGLICHKEIT, kein eingelegter Einspruch (aktiv bleibt: "legen wir Einspruch ein", "Einspruch eingelegt.")
    "Einspruch": ("einspruch eingelegt werden", "einspruch einlegen", "einspruch einzulegen"),
}
# Verweis auf einen Bescheid ("Einspruch gegen den Bussgeldbescheid", "Betreff: Bussgeldbescheid") ist kein Bescheid -
# im Legal-E2E-Testsplit als Regression der Erstfassung beobachtet (Einspruch und E-Mail wurden zum "Bescheid")
_BESCHEID_REFERENCE = re.compile(
    r"\b(?:gegen|betreff\s*:?|betrifft|zum|zu\s+dem|wegen|bezüglich|bezueglich)\s+(?:[\wäöüß]+\s+){0,2}?[\wäöüß]*bescheid\w*",
    re.IGNORECASE,
)
# P2.2 (§41): Rechnung als Ueberschrift ("Rechnung Nr. ...", "Schlussrechnung", "Abschlagsrechnung 3")
_INVOICE_HEADING = re.compile(r"^\s*[\wäöüß-]*rechnung\b", re.IGNORECASE | re.MULTILINE)
# P2.2 (§41): Gutachten/Protokoll nur in Titelstellung (erste zwei Woerter einer Zeile) - "Stellungnahme Gutachten |
# 12.06.2026" in einer Fristentabelle ist kein Gutachten
_TITLE_POSITION_TYPES = ("Gutachten", "Protokoll")
# P2.2 (§41): angedrohte Kuendigung ("kommt eine fristlose Kündigung in Betracht", "behält sich die Kündigung vor") ist
# kein Kuendigungsschreiben - solche Saetze zaehlen fuer diesen Typ nicht
# Satzgrenze nur bei Punkt + Grossbuchstabe - Abkuerzungen wie "§ 543 Abs. 2 Satz 1 Nr. 3 BGB" trennen keinen Satz
# (auf den ORIGINALTEXT angewandt - die Satzgrenze braucht die Grossschreibung; nur die Merkmale ignorieren Gross/klein)
_THREAT_SENTENCE = re.compile(
    r"(?:[^.;!?\n]|\.(?!\s+[A-ZÄÖÜ]))*(?i:in betracht|vorbehalt|behält sich|behaelt sich|behalten uns|androh|"
    r"andernfalls|anderenfalls|sehen uns gezwungen|gezwungen sein)(?:[^.;!?\n]|\.(?!\s+[A-ZÄÖÜ]))*[.;!?]?",
)
# Urteil/Beschluss: Ueberschriftzeile oder Tenor-/Eingangsformel
_JUDGMENT_HEADING = re.compile(
    r"^\s*(?:(?:end|teil|schluss|versäumnis|versaeumnis|anerkenntnis)?urteil|beschluss)\b(?!\s+(?:vom|des|der)\b)",
    re.IGNORECASE | re.MULTILINE,
)
# Anlage: ausdrueckliche Kennzeichnung in der ersten Zeile ("Anlage K1") oder im Dateinamen
_EXHIBIT_MARKER = re.compile(r"^\s*anlage\s*[a-z]{0,3}\s*[-.]?\s*\d+\b", re.IGNORECASE)
# OCR: unscharfer Abgleich nur fuer Ueberschriftwoerter am Zeilenanfang im Kopf (Titel), nur echte Abweichungen
_OCR_TITLE_WORDS = {
    "rechnung": "Rechnung", "gutachten": "Gutachten", "bescheid": "Bescheid", "protokoll": "Protokoll",
    "vertrag": "Vertrag", "klageschrift": "Klage/Schriftsatz",
}
_OCR_MIN_RATIO = 0.85

_ACTION_REQUIRED_KEYWORDS = (
    "frist",
    "dringend",
    "bis zum",
    "innerhalb von",
    "spätestens",
    "spaetestens",
)

# Grobe Muster fuer ein Aktenzeichen (z. B. "Az.: 123/24", "Aktenzeichen 45-2024").
_MATTER_REFERENCE_PATTERN = re.compile(
    r"(?:az\.?|aktenzeichen)\s*[:.]?\s*([A-Za-z0-9][A-Za-z0-9/\-]{2,20})",
    re.IGNORECASE,
)


class DocumentClassifier(Protocol):
    def classify(
        self, text: str, *, filename: str | None = None
    ) -> ClassificationResult: ...


class PlaceholderDocumentClassifier:
    """Regelbasierter Platzhalter ohne LLM/ML - siehe Moduldocstring."""

    def classify(
        self, text: str, *, filename: str | None = None
    ) -> ClassificationResult:
        normalized = text.lower()

        document_type, matched_keywords, location = self._detect_document_type(text, filename)
        matter_reference = self._detect_matter_reference(text)
        action_required = any(kw in normalized for kw in _ACTION_REQUIRED_KEYWORDS)

        confidence = self._compute_confidence(matched_keywords)
        reasoning = self._build_reasoning(
            document_type, matched_keywords, matter_reference, action_required, location
        )

        return ClassificationResult(
            document_type=document_type,
            possible_matter_reference=matter_reference,
            possible_parties=[],  # Platzhalter: keine Namenserkennung ohne LLM
            topic=None,  # Platzhalter: keine Themenzusammenfassung ohne LLM
            action_required=action_required,
            confidence=confidence,
            reasoning=reasoning,
        )

    def _detect_document_type(self, text: str, filename: str | None = None) -> tuple[str, list[str], str]:
        """(Typ, Merkmale, Fundort). P2.1 (§40): zuerst der Kopf des Dokuments, dann nur selbstbeschreibende Wendungen
        im Fliesstext; Unbekannt, wenn nichts davon traegt (begruendete Unsicherheit statt erzwungener Einordnung)."""
        head = text[:_HEAD_CHARS]
        exhibit = bool(_EXHIBIT_MARKER.match(text.lstrip()[:40])) or bool(
            filename and filename.lower().lstrip().startswith("anlage"))
        found = self._match_types(head.lower(), _DOCUMENT_TYPE_KEYWORDS, judgment_text=head, original=head)
        if found and not (exhibit and found[0] == "Gerichtliches Schreiben"):
            return found[0], found[1], "im Kopf des Dokuments" + (", als Anlage gekennzeichnet" if exhibit else "")
        fuzzy = self._fuzzy_heading_type(head)
        if fuzzy:
            return fuzzy[0], fuzzy[1], "im Kopf des Dokuments, unscharf (mögliche OCR-Abweichung)"
        if exhibit:
            return "Anlage", ["anlage"], "ausdrücklich als Anlage gekennzeichnet (erste Zeile oder Dateiname)"
        body_keywords = {t: _DOCUMENT_TYPE_KEYWORDS[t] for t in _BODY_TYPES}
        found = self._match_types(text.lower(), body_keywords, original=text)
        if found:
            return found[0], found[1], "im Text (selbstbeschreibende Wendung)"
        return "Unbekannt", [], ""

    @staticmethod
    def _match_types(
        normalized: str, table: dict[str, tuple[str, ...]], *, judgment_text: str | None = None,
        original: str | None = None,
    ) -> tuple[str, list[str]] | None:
        # P2.2 (§41): Zeilenumbrueche/Mehrfach-Leerraum (PDF-Umbruch "Einspruch\nein.") wie ein Leerzeichen behandeln
        normalized = re.sub(r"\s+", " ", normalized)
        for document_type, keywords in table.items():
            if document_type == "Urteil/Beschluss":
                if judgment_text is None:
                    continue
                heading = _JUDGMENT_HEADING.search(judgment_text)
                matched = [kw for kw in keywords if kw in normalized] + ([heading.group(0).strip()] if heading else [])
            elif document_type in _TITLE_POSITION_TYPES:
                if judgment_text is None:
                    continue
                # Titel: die ersten zwei Woerter einer Zeile, keine Tabellenzeilen ("Zelle | Zelle")
                titles = " / ".join(" ".join(line.split()[:2]).lower() for line in judgment_text.splitlines()
                                    if "|" not in line)
                matched = [kw for kw in keywords if kw in titles]
            else:
                scope = normalized
                for phrase in _EXCLUDED_PHRASES.get(document_type, ()):
                    scope = scope.replace(phrase, " ")
                if document_type == "Bescheid":
                    scope = _BESCHEID_REFERENCE.sub(" ", scope)
                if document_type == "Kündigungsschreiben" and original is not None:
                    scope = re.sub(r"\s+", " ", _THREAT_SENTENCE.sub(" ", original).lower())
                matched = [kw for kw in keywords if kw in scope]
                if document_type == "Rechnung" and judgment_text is not None:
                    heading = _INVOICE_HEADING.search(judgment_text)
                    matched += [heading.group(0).strip()] if heading else []
            if matched:
                return document_type, matched
        return None

    @staticmethod
    def _fuzzy_heading_type(head: str) -> tuple[str, list[str]] | None:
        """Ueberschriftwort am Zeilenanfang mit OCR-Fehler ("Rechnunq", "Privatgutachlen", "Bussgeldbescheld")."""
        for line in head.splitlines():
            first = re.match(r"\s*([A-Za-zÄÖÜäöüß]{6,})", line)
            if not first:
                continue
            token = first.group(1).lower()
            for word, document_type in _OCR_TITLE_WORDS.items():
                tail = token[-len(word):] if len(token) >= len(word) else token
                ratio = difflib.SequenceMatcher(None, tail, word).ratio()
                if _OCR_MIN_RATIO <= ratio < 1.0:
                    if document_type == "Bescheid" and token.startswith("steuer"):
                        document_type = "Steuerbescheid"
                    return document_type, [first.group(1)]
        return None

    def _detect_matter_reference(self, text: str) -> str | None:
        match = _MATTER_REFERENCE_PATTERN.search(text)
        return match.group(1) if match else None

    def _compute_confidence(self, matched_keywords: list[str]) -> float:
        if not matched_keywords:
            return 0.1
        # Mehr Treffer = etwas mehr (aber weiterhin niedrige) Konfidenz;
        # nie über _PLACEHOLDER_MAX_CONFIDENCE.
        # `round` (14.09.): ohne Rundung liefert die Gleitkomma-Addition
        # Werte wie 0.30000000000000004 - dieser Wert wird PERSISTIERT
        # (Document.classification_confidence) und kann in Oberflaeche/
        # Export auftauchen. Real beobachtet beim Durchspielen der
        # synthetischen Datenbasis gegen den produktiven Verarbeitungspfad.
        return round(
            min(_PLACEHOLDER_MAX_CONFIDENCE, 0.2 + 0.1 * len(matched_keywords)), 2
        )

    def _build_reasoning(
        self,
        document_type: str,
        matched_keywords: list[str],
        matter_reference: str | None,
        action_required: bool,
        location: str = "",
    ) -> str:
        parts = [
            "Platzhalter-Klassifikation (regelbasiert, kein LLM).",
            f"Dokumenttyp '{document_type}' "
            + (
                f"aufgrund der Schlüsselwörter {matched_keywords} vermutet"
                + (f" ({location})." if location else ".")
                if matched_keywords
                else "konnte nicht anhand bekannter Schlüsselwörter erkannt werden."
            ),
        ]
        if matter_reference:
            parts.append(f"Mögliches Aktenzeichen im Text gefunden: '{matter_reference}'.")
        if action_required:
            parts.append("Hinweise auf Handlungsbedarf/Frist im Text gefunden.")
        parts.append(
            "Konfidenz ist bewusst niedrig gehalten - dieses Ergebnis darf NICHT für "
            "automatische Aktenzuordnung verwendet werden."
        )
        return " ".join(parts)
