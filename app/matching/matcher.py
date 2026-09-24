"""MatterMatchingService – kombiniert Signale zu einer Zuordnungsentscheidung.

Signale (mit Gewichtung):
1. **Aktenzeichen-Treffer** (0.9): exakter Treffer eines im Text gefundenen
   Aktenzeichens gegen `Matter.reference_number`. Bewusst hoch gewichtet:
   ein eindeutiges, exaktes Aktenzeichen ist das stärkste verfügbare
   deterministische Signal und soll allein (bei fehlender Ambiguität und
   ausreichender Dokumentklassifikation) für eine automatische Zuordnung
   genügen können.
2. **E-Mail-Treffer** (0.3): Absender-E-Mail entspricht einer bekannten
   `Party.email` oder `Client.contact_email` der Akte.
3. **Beteiligten-Namens-Treffer** (0.2): Absendername ähnelt einem
   bekannten `Party.name` der Akte (unscharfer String-Vergleich).
4. **Themen-Ähnlichkeit** (0.1): grobe Textähnlichkeit zwischen
   Nachrichtentext und `Matter.title`/`practice_area`.

WICHTIG zu Signal 4: Das ist bewusst NUR ein einfacher String-Vergleich
(`difflib`), KEIN semantisches Embedding/keine Vektorsuche. Echte
semantische Suche ist Teil der noch offenen RAG-Layer-Entscheidung
(Prompt 11/12) und bewusst nicht Teil dieses Prompts - siehe
ARCHITECTURE.md §10. Die geringe Gewichtung (0.1) spiegelt das wider:
dieses Signal darf allein niemals eine automatische Zuordnung auslösen.

Zusätzliche Sicherheitsregel (Verknüpfung zu Prompt 08): Ist die
Dokumentklassifikation eines zur Nachricht gehörenden Dokuments mit
niedriger Konfidenz erfolgt (oder fehlt), wird eine automatische Zuordnung
verhindert - selbst bei hohem Matching-Score. Das Ergebnis wird dann
höchstens "needs_review".
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.matching.schema import MatchCandidate, MatchResult
from app.models import Matter, Message

_MATTER_REFERENCE_PATTERN = re.compile(
    r"(?:az\.?|aktenzeichen)\s*[:.]?\s*([A-Za-z0-9][A-Za-z0-9/\-]{2,20})",
    re.IGNORECASE,
)
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# Score-Differenz, unterhalb derer zwei Top-Kandidaten als nicht eindeutig
# unterscheidbar gelten - dann wird trotz hohem Score nicht automatisch
# zugeordnet (Ambiguität).
_AMBIGUITY_MARGIN = 0.05


@dataclass
class _ScoredMatter:
    matter: Matter
    score: float
    matched_signals: list[str]


class MatterMatchingService:
    def __init__(
        self, *, auto_assign_threshold: float, review_threshold: float
    ) -> None:
        self.auto_assign_threshold = auto_assign_threshold
        self.review_threshold = review_threshold

    def match_message(
        self,
        message: Message,
        db: Session,
        *,
        classification_ok: bool = True,
    ) -> MatchResult:
        """Ermittelt die wahrscheinlichste Akte für eine Nachricht.

        `classification_ok` wird vom Aufrufer übergeben (True nur, wenn ALLE
        zugehörigen Dokumente entweder keine Klassifikation benötigen oder
        mit ausreichender Konfidenz klassifiziert wurden - siehe
        app/matching/service.py für die konkrete Ermittlung).
        """
        text = " ".join(
            part for part in (message.subject, message.body_text) if part
        )
        sender_email = self._extract_email(message.sender)
        sender_name = self._extract_display_name(message.sender)

        scored = self._score_all_matters(
            db, text=text, sender_email=sender_email, sender_name=sender_name
        )
        scored.sort(key=lambda s: s.score, reverse=True)

        candidates = [
            MatchCandidate(
                matter_id=s.matter.id, score=s.score, matched_signals=s.matched_signals
            )
            for s in scored
            if s.score > 0
        ]

        return self._decide(candidates, classification_ok=classification_ok)

    def _decide(
        self, candidates: list[MatchCandidate], *, classification_ok: bool
    ) -> MatchResult:
        if not candidates:
            return MatchResult(
                decision="no_match",
                confidence=0.0,
                reasoning="Keine Akte mit übereinstimmenden Signalen gefunden.",
                candidates=[],
            )

        best = candidates[0]
        ambiguous = (
            len(candidates) > 1
            and (best.score - candidates[1].score) < _AMBIGUITY_MARGIN
        )

        if best.score < self.review_threshold:
            return MatchResult(
                decision="no_match",
                confidence=best.score,
                reasoning=(
                    f"Bester Kandidat (Score {best.score:.2f}) liegt unter der "
                    f"Review-Schwelle ({self.review_threshold})."
                ),
                candidates=candidates,
            )

        if (
            best.score >= self.auto_assign_threshold
            and not ambiguous
            and classification_ok
        ):
            return MatchResult(
                decision="auto_assigned",
                matter_id=best.matter_id,
                confidence=best.score,
                reasoning=(
                    f"Automatische Zuordnung: Score {best.score:.2f} über Schwelle "
                    f"({self.auto_assign_threshold}), Signale: {best.matched_signals}."
                ),
                candidates=candidates,
            )

        reason_parts = [f"Bester Kandidat: Score {best.score:.2f}."]
        if ambiguous:
            reason_parts.append("Mehrere Akten mit ähnlichem Score gefunden (Ambiguität).")
        if not classification_ok:
            reason_parts.append(
                "Dokumentklassifikation mit niedriger/fehlender Konfidenz - "
                "automatische Zuordnung deshalb ausgeschlossen."
            )
        if best.score < self.auto_assign_threshold:
            reason_parts.append(
                f"Score liegt unter der Auto-Zuordnungs-Schwelle "
                f"({self.auto_assign_threshold})."
            )
        reason_parts.append("Manuelle Prüfung erforderlich.")

        return MatchResult(
            decision="needs_review",
            confidence=best.score,
            reasoning=" ".join(reason_parts),
            candidates=candidates,
        )

    def _score_all_matters(
        self,
        db: Session,
        *,
        text: str,
        sender_email: str | None,
        sender_name: str | None,
    ) -> list[_ScoredMatter]:
        found_reference = self._extract_matter_reference(text)
        results: list[_ScoredMatter] = []

        for matter in db.query(Matter).all():
            score = 0.0
            signals: list[str] = []

            if found_reference and matter.reference_number:
                if found_reference.strip().lower() == matter.reference_number.strip().lower():
                    score += 0.9
                    signals.append("aktenzeichen_match")

            if sender_email:
                known_emails = {p.email.lower() for p in matter.parties if p.email}
                if matter.client and matter.client.contact_email:
                    known_emails.add(matter.client.contact_email.lower())
                if sender_email.lower() in known_emails:
                    score += 0.3
                    signals.append("email_match")

            if sender_name:
                for party in matter.parties:
                    if self._names_similar(sender_name, party.name):
                        score += 0.2
                        signals.append("party_name_match")
                        break
                else:
                    # ECHTER FUND (14.09., Posteingangs-Zuordnung auf der
                    # synthetischen Kanzlei-Datenbasis): der Namensabgleich
                    # betrachtete AUSSCHLIESSLICH `matter.parties` - der
                    # MANDANT selbst, der wichtigste Beteiligte einer Akte
                    # und im Kanzleialltag der haeufigste Absender
                    # ueberhaupt, wurde nie verglichen. Eine Mail des
                    # eigenen Mandanten erhielt dadurch kein einziges
                    # Namenssignal. Gleiches Gewicht wie ein Party-Treffer
                    # (der Mandant ist kein schwaecherer Beteiligter),
                    # bewusst im else-Zweig: zaehlt nicht doppelt, wenn der
                    # Mandant zusaetzlich als Party gefuehrt wird.
                    if matter.client and matter.client.name and self._names_similar(
                        sender_name, matter.client.name
                    ):
                        score += 0.2
                        signals.append("client_name_match")

            topic_similarity = self._topic_similarity(text, matter)
            if topic_similarity > 0.3:
                score += 0.1 * topic_similarity
                signals.append("topic_similarity_placeholder")

            score = min(score, 1.0)
            if score > 0:
                results.append(_ScoredMatter(matter=matter, score=score, matched_signals=signals))

        return results

    @staticmethod
    def _extract_matter_reference(text: str) -> str | None:
        match = _MATTER_REFERENCE_PATTERN.search(text)
        return match.group(1) if match else None

    @staticmethod
    def _extract_email(sender: str | None) -> str | None:
        if not sender:
            return None
        match = _EMAIL_PATTERN.search(sender)
        return match.group(0) if match else None

    @staticmethod
    def _extract_display_name(sender: str | None) -> str | None:
        if not sender:
            return None
        # "Max Mustermann <max@example.test>" -> "Max Mustermann"
        name_part = sender.split("<")[0].strip().strip('"')
        if not name_part:
            return None
        # ECHTER FUND (14.09., beim Durchspielen der Posteingangs-Zuordnung
        # auf der synthetischen Kanzlei-Datenbasis): steht KEIN Anzeigename
        # im Absender (also eine blosse Adresse "vorname.name@..."), lieferte
        # diese Funktion die vollstaendige E-MAIL-ADRESSE als vermeintlichen
        # "Anzeigenamen" zurueck. Der Namensabgleich verglich dann eine
        # Adresse mit einem Personennamen - im besten Fall wirkungslos, im
        # schlechten Fall ein Zufallstreffer. Ohne echten Anzeigenamen gibt
        # es schlicht keinen Namen, der verglichen werden koennte.
        if _EMAIL_PATTERN.fullmatch(name_part):
            return None
        return name_part

    @staticmethod
    def _names_similar(a: str, b: str, *, threshold: float = 0.8) -> bool:
        ratio = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
        return ratio >= threshold

    @staticmethod
    def _topic_similarity(text: str, matter: Matter) -> float:
        """Platzhalter-Ähnlichkeit (kein Embedding) - siehe Moduldocstring."""
        reference = " ".join(
            part for part in (matter.title, matter.practice_area) if part
        )
        if not text.strip() or not reference.strip():
            return 0.0
        return difflib.SequenceMatcher(None, text.lower(), reference.lower()).ratio()
