"""Lokale Pruefung juristischer Fundstellen in Modellantworten (§36, P0-Arbeitspaket Q).

Befund (Legal-E2E-Benchmark §34.8): erfundene Normen/Entscheidungen einer Modellantwort gingen unmarkiert in Entwurf und
Chat. Diese Pruefung ist DETERMINISTISCH und LOKAL - kein Modellaufruf, das Modell bestaetigt sich also nie selbst. Sie
trennt zwei Fragen:

1. EXISTENZ/BELEG der Fundstelle - Stufen (je Fundstelle genau eine):
   - ``aktenbelegt``: steht woertlich in einem Dokument DIESER Akte;
   - ``norm_amtlich_vorhanden``: Paragraph liegt aus dem amtlichen Import ("Gesetze im Internet", ``doknr``) vor;
   - ``norm_kuratiert_vorhanden``: Paragraph liegt in der manuell kuratierten Auswahl vor;
   - ``norm_amtlich_nicht_gefunden``: das Gesetz ist amtlich importiert, der Paragraph fehlt - starker Hinweis auf eine
     falsche oder erfundene Norm;
   - ``norm_nicht_pruefbar``: Gesetz nicht (aktiv) installiert oder nur kuratiert ausgewaehlt - ein Fehlen beweist nichts;
   - ``freigegebene_quelle``: Entscheidung steht in einer freigegebenen Quelle (Source "freigegeben") oder in
     freigegebenem Kanzleiwissen (KnowledgeItem "approved");
   - ``nicht_verifiziert``: Entscheidung weder in der Akte noch in freigegebenen Quellen.
2. STUETZUNG der Aussage: ob die Fundstelle die behauptete Aussage traegt, wird NICHT geprueft und immer ausdruecklich
   als ungeprueft gekennzeichnet (keine vorgetaeuschte Verifikation).

Externe Datenbanken werden nicht abgefragt (keine freigegebene externe Quelle)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

_LAW_ABBREVIATION = r"(?P<law>[A-ZÄÖÜ][A-Za-zÄÖÜäöü]{0,10}[A-Z](?:\s?(?:[IVX]{1,4}|\d{1,2})(?![\d/]))?)"
_QUALIFIER = r"(?:Abs\.|Absatz|UAbs\.|S\.|Satz|Nr\.|Ziff\.|lit\.|Alt\.|Var\.|Hs\.)"
_QUALIFIER_VALUE = r"(?:\d+[a-z]?|[IVX]{1,4}|[a-z]\)?)"
_NORM = re.compile(
    r"(?P<sign>§§?|Art\.|Artikel)\s*(?P<numbers>\d+[a-z]?(?:\s*(?:,|und|bis|-|–)\s*\d+[a-z]?)*)"
    r"(?:\s+[IVX]{1,4}(?:\s*(?:,|und)\s*[IVX]{1,4})*)?"  # roemische Absaetze ("§ 280 I, III BGB")
    r"(?:\s*" + _QUALIFIER + r"\s*" + _QUALIFIER_VALUE + r"(?:\s*(?:,|und|bis)\s*" + _QUALIFIER_VALUE + r")*)*"
    r"(?:\s*ff?\.)?\s+" + _LAW_ABBREVIATION + r"\b"
)
# Aktenzeichen: (roemischer oder arabischer) Spruchkoerper + Registerzeichen + lfd. Nr./Jahr (zwei- oder vierstellig);
# EuGH-Rechtssachen "C-311/18"
_FILE_NUMBER = re.compile(
    r"(?<![\w/-])(?P<az>(?:[IVX]{1,5}|\d{1,2})\s?[A-Z][A-Za-z]{0,5}\s?\d{1,5}/(?:\d{4}|\d{2})|[CT]-\d{1,4}/\d{2})(?![\w/])"
)
# Gericht vor dem Aktenzeichen. "AG"/"KG" sind zugleich Rechtsformen ("Muster AG, Vertrag 2 K 33/24") - als Gericht nur
# mit folgendem Ortsnamen ("AG München", "KG Berlin" ist die einzige KG-Form).
_COURT_NEAR = re.compile(
    r"\b(?:BGH|BAG|BFH|BSG|BVerwG|BVerfG|EuGH|EuG|OLG|LG|LAG|VG|OVG|VGH|LSG|FG|BPatG|(?:AG|KG)\s+[A-ZÄÖÜ][a-zäöü]+)\b"
)
# Zeitschriften-/Entscheidungssammlungs-Fundstellen ohne Aktenzeichen ("BGH NJW 2020, 123", "BGHZ 150, 123")
_JOURNAL = re.compile(
    r"\b(?P<cite>(?:NJW-RR|NJW|NZA-RR|NZA|NZM|NZG|NZBau|NZV|NVwZ|NStZ|ZIP|MDR|VersR|WM|GRUR|BGHZ|BGHSt|BAGE|BVerfGE|"
    r"BVerwGE|BFHE|BSGE|DB|BB|JZ|FamRZ|ZfBR|BauR)\s+(?:\d{4},\s*)?\d{1,5}(?:,\s*\d{1,5})?)\b"
)
_SGB_BOOKS = {str(i): r for i, r in enumerate(["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII",
                                                "XIII", "XIV"]) if i}
_SECTION_NUMBER = re.compile(r"(\d+[a-z]?)")
_NOT_A_LAW = frozenset({"EUR", "USD", "CHF", "GBP", "AG", "KG", "SE", "UG"})


@dataclass(frozen=True)
class Citation:
    kind: str  # "norm" | "entscheidung" | "zeitschrift"
    display: str
    law_code: str | None = None
    section: str | None = None
    file_number: str | None = None
    sign: str | None = None  # "§" | "Art." (Normen)


@dataclass(frozen=True)
class CheckedCitation(Citation):
    status: str = "nicht_verifiziert"


def _normalize_law_code(raw: str) -> str:
    code = re.sub(r"\s+", "", raw).upper()
    if code.startswith("SGB") and code[3:].isdigit():
        code = "SGB" + _SGB_BOOKS.get(code[3:], code[3:])  # "SGB 5" -> "SGBV" (wie app/chat/service.py)
    return code


def extract_citations(text: str) -> list[Citation]:
    """Normzitate ("§ 535 Abs. 1 BGB", "§§ 91, 92 ZPO", "Art. 3 GG") und Entscheidungs-Aktenzeichen ("VIII ZR 1/21") -
    jede Fundstelle einmal, in Reihenfolge des Auftretens."""
    found: list[Citation] = []
    seen: set[tuple] = set()
    for match in _NORM.finditer(text or ""):
        law = match.group("law").strip()
        if law in _NOT_A_LAW:
            continue
        sign = "Art." if match.group("sign").startswith("Art") else "§"
        for number in _SECTION_NUMBER.findall(match.group("numbers")):
            key = ("norm", _normalize_law_code(law), number.lower())
            if key in seen:
                continue
            seen.add(key)
            found.append(Citation(kind="norm", display=f"{sign} {number} {law}", law_code=_normalize_law_code(law),
                                  section=number.lower(), sign=sign))
    for match in _FILE_NUMBER.finditer(text or ""):
        context = (text or "")[max(0, match.start() - 80) : match.start()]
        if not _COURT_NEAR.search(context):
            continue  # ohne Gerichtsbezeichnung davor kein Entscheidungszitat (Rechnungs-/Kennnummern)
        number = re.sub(r"\s+", " ", match.group("az")).strip()
        key = ("entscheidung", number)
        if key in seen:
            continue
        seen.add(key)
        found.append(Citation(kind="entscheidung", display=number, file_number=number))
    for match in _JOURNAL.finditer(text or ""):
        cite = re.sub(r"\s+", " ", match.group("cite")).strip()
        if ("zeitschrift", cite) in seen:
            continue
        seen.add(("zeitschrift", cite))
        found.append(Citation(kind="zeitschrift", display=cite, file_number=cite))
    return found


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _matter_texts(matter_id: str | None, db: Session) -> list[str]:
    if not matter_id:
        return []
    from app.models import Document

    return [
        text for (text,) in db.query(Document.extracted_text).filter(
            Document.matter_id == matter_id, Document.deleted_at.is_(None)
        ) if text
    ]


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").lower()


def _identifier_pattern(identifier: str) -> re.Pattern[str]:
    """Aktenzeichen/Fundstelle mit Grenzen auf beiden Seiten ("I ZR 1/21" trifft NICHT "VIII ZR 1/21")."""
    parts = [re.escape(part) for part in re.split(r"\s+", identifier.strip().lower()) if part]
    return re.compile(r"(?<![\w/-])" + r"\s?".join(parts) + r"(?![\w/])")


def _in_matter(citation: Citation, normalized_texts: list[str]) -> bool:
    if citation.kind in ("entscheidung", "zeitschrift"):
        pattern = _identifier_pattern(citation.file_number or "")
        return any(pattern.search(text) for text in normalized_texts)
    # Norm: dieselbe Zitierstruktur wie bei der Extraktion - Paragraph (auch in einer Aufzaehlung), nur Absatz-/Satz-/Nr.-
    # Angaben dazwischen, dann das Gesetz als ganzes Wort (Review: "§ 4 Abs. 1 der Satzung und das KSchG" ist KEIN Beleg)
    law = re.escape((citation.law_code or "").lower())
    pattern = re.compile(
        r"(?:§§?|art\.|artikel)\s*(?:\d+[a-z]?\s*(?:,|und|bis|-|–)\s*)*" + re.escape(citation.section or "")
        + r"(?![\da-z])(?:\s+[ivx]{1,4}(?:\s*(?:,|und)\s*[ivx]{1,4})*)?"
        + r"(?:\s*" + _QUALIFIER.lower() + r"\s*" + _QUALIFIER_VALUE.lower() + r"(?:\s*(?:,|und|bis)\s*"
        + _QUALIFIER_VALUE.lower() + r")*)*(?:\s*ff?\.)?\s+(?<![\wäöü])" + law + r"(?![a-zäöü])"
    )
    return any(pattern.search(text) for text in normalized_texts)


def _norm_status(citation: Citation, db: Session) -> str:
    from app.models import Law, LawSection

    from sqlalchemy import func

    law = db.query(Law).filter(func.upper(Law.code) == citation.law_code, Law.is_active.is_(True)).first()
    if law is None:
        return "norm_nicht_pruefbar"
    sections = db.query(LawSection.section_number, LawSection.doknr).filter(LawSection.law_code == law.code).all()
    official = any(doknr for _number, doknr in sections)
    wants_article = citation.sign == "Art."
    for number, doknr in sections:
        label = (number or "").strip()
        if label.lower().startswith("art") != wants_article:
            continue  # "§ 5" ist nicht "Art 5" (und nicht "Art 229 § 5")
        match = _SECTION_NUMBER.search(label)
        if match and match.group(1).lower() == citation.section:
            return "norm_amtlich_vorhanden" if doknr else "norm_kuratiert_vorhanden"
    # Fehlt der Paragraph, beweist das nur bei einem AMTLICH importierten (vollstaendigen) Gesetz etwas
    return "norm_amtlich_nicht_gefunden" if official else "norm_nicht_pruefbar"


def _approved_source_texts(db: Session) -> list[str]:
    from app.models import KnowledgeItem, Source

    texts = [
        " ".join(part for part in (title, reference, notes) if part)
        for title, reference, notes in db.query(Source.title, Source.reference, Source.notes).filter(
            Source.approval_level == "freigegeben"
        )
    ]
    texts += [
        f"{title} {content}" for title, content in db.query(KnowledgeItem.title, KnowledgeItem.content).filter(
            KnowledgeItem.approval_status == "approved"
        )
    ]
    return [_normalized(text) for text in texts]


def check_citations(text: str, matter_id: str | None, db: Session) -> list[CheckedCitation]:
    citations = extract_citations(text)
    if not citations:
        return []
    matter_texts = [_normalized(t) for t in _matter_texts(matter_id, db)]
    approved = None
    checked: list[CheckedCitation] = []
    for citation in citations:
        if _in_matter(citation, matter_texts):
            status = "aktenbelegt"
        elif citation.kind == "norm":
            status = _norm_status(citation, db)
        else:
            if approved is None:
                approved = _approved_source_texts(db)
            pattern = _identifier_pattern(citation.file_number or "")
            status = "freigegebene_quelle" if any(pattern.search(source) for source in approved) else "nicht_verifiziert"
        checked.append(CheckedCitation(**citation.__dict__, status=status))
    return checked


def citation_review_notes(checked: list[CheckedCitation]) -> list[str]:
    """Hinweise fuer den Block "Offene Pruefpunkte" (nie Teil des Schreibens). Leer, wenn keine Fundstelle erkannt wurde.
    Wortwahl bewusst "gefunden", nicht "belegt": ein Aktendokument ist ungepruefter Input (z. B. Schriftsatz der
    Gegenseite) und bestaetigt eine Fundstelle nicht unabhaengig."""
    if not checked:
        return []

    def names(*statuses: str) -> str:
        return ", ".join(c.display for c in checked if c.status in statuses)

    notes = []
    if names("norm_amtlich_nicht_gefunden"):
        notes.append("Fundstellenprüfung: im lokal vorliegenden amtlichen Gesetzestext NICHT gefunden (möglicherweise falsch "
                     f"oder erfunden): {names('norm_amtlich_nicht_gefunden')}.")
    if names("nicht_verifiziert"):
        notes.append("Fundstellenprüfung: Rechtsprechung/Literatur nicht verifiziert (weder in der Akte noch in freigegebenen "
                     f"Quellen): {names('nicht_verifiziert')}.")
    if names("norm_nicht_pruefbar"):
        notes.append("Fundstellenprüfung: Existenz lokal nicht prüfbar (Gesetz nicht oder nur auszugsweise installiert): "
                     f"{names('norm_nicht_pruefbar')}.")
    if names("aktenbelegt"):
        notes.append("Fundstellenprüfung: nur in Aktendokumenten gefunden (keine unabhängige Bestätigung, Akteninhalt ist "
                     f"ungeprüft): {names('aktenbelegt')}.")
    found = sum(1 for c in checked if c.status in ("norm_amtlich_vorhanden", "norm_kuratiert_vorhanden", "freigegebene_quelle"))
    notes.append(f"Fundstellenprüfung (lokal, automatisch): {len(checked)} erkannte Fundstelle(n), davon {found} in der "
                 "Gesetzesbibliothek oder einer freigegebenen Quelle gefunden. Ob die Fundstellen die jeweilige Aussage "
                 "tragen, wurde nicht geprüft; nicht erkannte Zitierformen wurden nicht geprüft - vor Verwendung anhand des "
                 "Wortlauts prüfen.")
    return notes


def annotate_citations(text: str, matter_id: str | None, db: Session) -> str:
    """Haengt die Hinweise an den Block "Offene Pruefpunkte" der Antwort an (Schreiben selbst unveraendert)."""
    from app.drafting.review_notes import append_review_note, split_review_notes

    letter, _notes = split_review_notes(text)
    for note in citation_review_notes(check_citations(letter, matter_id, db)):
        text = append_review_note(text, note)
    return text
