"""Ende-zu-Ende-Vollstaendigkeit (Qualitaetslauf 11.10.2026): welche Testfakten erreichen den Cloud-Payload, welche den
fertigen Schriftsatz?

Erzeugt SYNTHETISCHE Akten mit Dokumenten verschiedener Laenge. Je Dokument stehen eindeutige Testfakten am ANFANG, in
der MITTE und am ENDE: Betraege, Daten, Fristen, Rechtsfolgen, beschreibende Details OHNE Zahlen, Ausnahmen, eine
Schlussanweisung und absichtlich WIDERSPRUECHLICHE Angaben (zwei verschiedene Liefertermine/Preise).

Modi:
  payload  lokal, ohne Cloud: baut den Sachverhalt wie die Anwendung, laeuft durch das echte Datenschutz-Gateway und
           prueft je Fakt (a) im Sachverhalt vor dem Gateway, (b) im ausgehenden Payload (Klartext ODER Platzhalter-
           Mapping des Originalwerts), plus Kontextumfang und Laufzeit.
  draft    mit echtem Claude (Kosten!): erzeugt den Schriftsatz ueber `DraftingService` und prueft die Fakten im
           fertigen, rekonstruierten Text sowie ob die Widersprueche benannt werden.
  echo     lokal, ohne Cloud: ersetzt Claude durch den pseudonymisierten Sachverhalt selbst und prueft nur die
           Rekonstruktion (Roundtrip) - KEINE Aussage ueber die Qualitaet eines echten Schriftsatzes.

Aufruf:  python scripts/e2e_completeness.py payload [--cases s5,s12,s45,m3x20,m4x35]
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_FILLER = [
    "Die Parteien sind sich darüber einig, dass die Leistung nach den anerkannten Regeln der Technik zu erbringen ist.",
    "Änderungen und Ergänzungen dieses Vertrages bedürfen zu ihrer Wirksamkeit der Schriftform.",
    "Der Auftragnehmer unterrichtet den Auftraggeber unverzüglich über erkennbare Hindernisse bei der Ausführung.",
    "Mitwirkungspflichten des Auftraggebers bleiben von den Regelungen dieses Abschnitts unberührt.",
    "Für die Auslegung gelten ergänzend die gesetzlichen Vorschriften, soweit nichts Abweichendes vereinbart ist.",
    "Sollte eine Bestimmung unwirksam sein, bleibt die Wirksamkeit der übrigen Bestimmungen unberührt.",
    "Die Parteien behandeln alle im Rahmen der Zusammenarbeit erlangten Informationen vertraulich.",
    "Erfüllungsort für sämtliche Verpflichtungen ist der Sitz des jeweiligen Leistungserbringers.",
    "Rechnungen sind prüffähig einzureichen und werden nach Eingang sachlich und rechnerisch geprüft.",
    "Die Übertragung von Rechten und Pflichten aus diesem Vertrag bedarf der vorherigen Zustimmung der anderen Partei.",
    "Für die Dokumentation der Leistungen gelten die im Anhang beschriebenen Standards entsprechend.",
    "Verzögerungen sind schriftlich anzuzeigen; die Anzeige ersetzt keine Fristverlängerung.",
    "Die Parteien benennen jeweils einen Ansprechpartner für die laufende Abstimmung der Arbeiten.",
    "Der Auftraggeber stellt Zugang zu den Räumlichkeiten während der üblichen Geschäftszeiten sicher.",
    "Mehrkosten, die auf geänderten Anforderungen beruhen, sind vor Ausführung schriftlich zu vereinbaren.",
]


_SUBJ = ["Der Auftragnehmer", "Der Auftraggeber", "Die Bauleitung", "Der Käufer", "Der Verkäufer", "Die Geschäftsleitung",
         "Der Sachbearbeiter", "Die Projektleitung", "Der Subunternehmer", "Die Rechnungsprüfung"]
_VERB = ["dokumentiert", "prüft", "meldet", "bestätigt", "übermittelt", "koordiniert", "überwacht", "genehmigt", "archiviert", "beanstandet"]
_OBJ = ["die Lieferscheine der Gewerke", "die Abrechnung der Teilleistungen", "den Zeitplan der Montagearbeiten",
        "die Prüfprotokolle der Fachplaner", "die Abstimmung mit den Versorgungsunternehmen", "die Lagerhaltung der Ersatzteile",
        "die Einweisung des Betriebspersonals", "die Freigabe der Zwischenergebnisse", "die Übergabe der Revisionsunterlagen",
        "die Pflege der Wartungsverträge", "die Abnahme der Teilbereiche", "die Vergabe der Nachunternehmerleistungen"]
_ADV = ["wöchentlich", "unverzüglich", "nach Rücksprache", "in schriftlicher Form", "unter Beachtung der Normen",
        "im Rahmen der Projektbesprechung", "vor Beginn der nächsten Phase", "auf Anforderung der Gegenseite"]


def _diverse_sentence(rng: random.Random) -> str:
    """Viele verschiedene, wortreiche Fuellsaetze (kaum exakte Wiederholungen) - harter Fall fuer die Auswahl."""
    return f"{rng.choice(_SUBJ)} {rng.choice(_VERB)} {rng.choice(_ADV)} {rng.choice(_OBJ)} und {rng.choice(_VERB)} {rng.choice(_OBJ)}."


@dataclass
class Fact:
    key: str
    kind: str  # betrag | datum | frist | rechtsfolge | beschreibend | ausnahme | anweisung | widerspruch
    where: str  # anfang | mitte | ende
    doc: int
    probe: str  # Text, der im Klartext vorkommt
    is_date: bool = False  # Datumswerte werden pseudonymisiert -> Nachweis ueber das Mapping
    keywords: tuple[str, ...] = ()  # lockere Pruefung im Entwurf (Paraphrase moeglich): irgendein Schluesselwort genuegt


@dataclass
class Case:
    name: str
    docs: list[str]
    facts: list[Fact] = field(default_factory=list)
    task: str = ""


def _date(base: datetime, days: int) -> str:
    return (base + timedelta(days=days)).strftime("%d.%m.%Y")


def make_document(index: int, size: int, seed: int, diverse: bool = False) -> tuple[str, list[Fact]]:
    rng = random.Random(seed)
    base = datetime(2026, 1, 12) + timedelta(days=index * 9)
    a1 = 118750 + index * 3111
    a2 = 11200 + index * 137
    a3 = a1 - 6500  # widerspruechlicher zweiter Preis
    d_start, d_deliv1, d_deliv2, d_frist = _date(base, 0), _date(base, 40), _date(base, 47), _date(base, 190)
    amt = lambda v: f"{v:,}".replace(",", ".") + ",00 EUR"  # noqa: E731
    tag = ["erste", "zweite", "dritte", "vierte"][index] + " Ausfertigung"
    mid_words = [f"Kellergeschoss neben dem Heizraum ({tag})", f"behördliche Genehmigungsverfahren ({tag})",
                 f"Hinterhof mit Sonderrechten Dritter ({tag})"]

    start = (
        f"KAUFVERTRAG, {tag}. Der Kaufpreis beträgt {amt(a1)}. Vertragsbeginn ist der {d_start}. "
        f"Die Lieferung erfolgt am {d_deliv1}."
    )
    middle = (
        f" Die Vertragsstrafe beträgt 0,3 Prozent je Werktag, höchstens 5 Prozent. "
        f"Ausgenommen von der Vertragsstrafe sind Verzögerungen durch {mid_words[1]}. "
        f"Die Anlage wird im {mid_words[0]} aufgestellt. "
        f"Der Käufer erklärt, dass der Zugang über den {mid_words[2]} berührt. "
        f"Rücktritt ist nur nach fruchtlosem Ablauf der Nachfrist bis zum {d_frist} möglich."
    )
    end = (
        f" Die Kündigungsfrist beträgt {6 + index} Wochen zum Quartalsende. "
        f"Abweichend von oben erfolgt die Lieferung am {d_deliv2}. Der vereinbarte Preis beträgt {amt(a3)}. "
        f"SCHLUSSANWEISUNG {tag}: Bitte die Frist {d_frist} ausdrücklich nennen und den Betrag {amt(a2)} zurückfordern."
    )
    facts = [
        Fact(f"{tag}:preis", "betrag", "anfang", index, amt(a1)),
        Fact(f"{tag}:vertragsbeginn", "datum", "anfang", index, d_start, True),
        Fact(f"{tag}:lieferung1", "widerspruch", "anfang", index, d_deliv1, True),
        Fact(f"{tag}:vertragsstrafe", "rechtsfolge", "mitte", index, "0,3 Prozent je Werktag"),
        Fact(f"{tag}:ausnahme", "ausnahme", "mitte", index, mid_words[1], keywords=("Genehmigungsverfahren",)),
        Fact(f"{tag}:beschr_ort", "beschreibend", "mitte", index, mid_words[0], keywords=("Kellergeschoss", "Heizraum")),
        Fact(f"{tag}:beschr_zugang", "beschreibend", "mitte", index, mid_words[2], keywords=("Hinterhof", "Sonderrechte")),
        Fact(f"{tag}:nachfrist", "frist", "mitte", index, d_frist, True),
        Fact(f"{tag}:kuendigung", "frist", "ende", index, f"{6 + index} Wochen zum Quartalsende"),
        Fact(f"{tag}:lieferung2", "widerspruch", "ende", index, d_deliv2, True),
        Fact(f"{tag}:preis2", "widerspruch", "ende", index, amt(a3)),
        Fact(f"{tag}:rueckforderung", "anweisung", "ende", index, amt(a2)),
        Fact(f"{tag}:schlussanweisung", "anweisung", "ende", index, f"SCHLUSSANWEISUNG {tag}"),
    ]
    # Fuelltext: Anfangsblock bei ~2 %, Mitte bei 50 %, Ende am Schluss
    body: list[str] = []
    n = 0
    sentences_needed = max(4, size // 120)
    half = sentences_needed // 2
    for i in range(sentences_needed):
        if i == 2:
            body.append(start)
        if i == half:
            body.append(middle)
        sec = f"\n§ {i + 1} " if i % 6 == 0 else " "
        body.append(sec + (_diverse_sentence(rng) if diverse else _FILLER[(i * 7 + rng.randrange(len(_FILLER))) % len(_FILLER)]))
        n += 1
    body.append(end)
    text = "".join(body)
    return text, facts


CASE_SIZES = {
    "s5": [4_800],
    "s12": [12_000],
    "s45": [45_000],
    "m3x20": [20_000, 20_000, 20_000],
    "m4x35": [35_000, 35_000, 35_000, 35_000],
    "h45": [45_000],
    "h3x30": [30_000, 30_000, 30_000],
}

TASK = (
    "Erstelle ein Übersichtsschreiben an die Gegenseite, das für JEDEN der Verträge alle genannten Beträge, Fristen, "
    "Rechtsfolgen, Ausnahmen, beschreibenden Besonderheiten und die Schlussanweisung wiedergibt. Weise ausdrücklich auf "
    "alle Widersprüche zwischen den Angaben (Liefertermine, Preise) hin, ohne sie zu vereinheitlichen."
)


def make_case(name: str) -> Case:
    sizes = CASE_SIZES[name]
    docs, facts = [], []
    for i, size in enumerate(sizes):
        text, f = make_document(i, size, seed=1000 + i, diverse=name.startswith("h"))
        docs.append(text)
        facts.extend(f)
    return Case(name=name, docs=docs, facts=facts, task=TASK)


def build_session(case: Case):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base, Client, Document, Matter

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    client = Client(name="Erika Beispielfrau")
    matter = Matter(title="Beispielakte", client=client)
    db.add_all([client, matter])
    t0 = datetime(2026, 1, 1)
    for i, text in enumerate(case.docs):
        db.add(
            Document(
                matter=matter, file_path=f"/tmp/vertrag{i + 1}.pdf", extracted_text=text, classified_type="Vertrag",
                created_at=t0 + timedelta(minutes=i),
            )
        )
    db.commit()
    return db, matter


def evaluate_payload(case: Case) -> dict:
    from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
    from app.privacy.gateway import ClaudePrivacyGateway

    db, matter = build_session(case)
    prep = RuleBasedLocalAIProvider().prepare_draft_context(matter.id, db)
    t = time.perf_counter()
    result = ClaudePrivacyGateway().prepare_request(
        purpose="formulate_draft", sachverhalt=prep.sachverhalt, argumentationspunkte=prep.argumentationspunkte,
        quellenverweise=prep.quellenverweise, stil=None, vorlage=None, anwaltliche_anmerkungen=case.task,
        known_entities=prep.known_entities, gespraechsverlauf=[], skip_general_knowledge_pseudonymization=False,
    )
    gateway_s = time.perf_counter() - t
    payload_text = result.payload.anonymisierter_sachverhalt if result.payload else ""
    originals = {m.original_value for m in result.mappings}
    rows = []
    for f in case.facts:
        in_prep = f.probe in prep.sachverhalt
        in_payload = f.probe in payload_text or (f.is_date and f.probe in originals and in_prep)
        rows.append((f, in_prep, in_payload))
    return {
        "allowed": result.allowed, "reasons": result.reasons, "sach_chars": len(prep.sachverhalt),
        "payload_chars": len(payload_text), "gateway_s": gateway_s, "rows": rows, "notices": prep.notices,
        "docs_chars": sum(len(d) for d in case.docs),
    }


_CONFLICT_WORDS = ("widerspr", "abweich", "unterschiedlich", "unstimmig", "uneinheitlich", "diskrepanz", "differ", "nicht überein")


def _build_service(writing_provider):
    from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
    from app.drafting.service import DraftingService
    from app.privacy.gateway import ClaudePrivacyGateway
    from app.research.service import LegalResearchService
    from app.search.service import DocumentSearchService

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from test_drafting_service import FakeEmbeddingProvider  # noqa: E402

    search = DocumentSearchService(FakeEmbeddingProvider())
    return DraftingService(
        RuleBasedLocalAIProvider(), LegalResearchService(search, min_score_for_sufficient=0.0), search,
        ClaudePrivacyGateway(), writing_provider, model_name="claude",
    )


def _real_writer():
    from app.ai_providers.factory import build_writing_provider
    from app.config import Settings

    key = None
    for line in open(r"C:\ProgramData\Lexono\.env", encoding="utf-8", errors="ignore"):
        m = re.match(r'\s*ANTHROPIC_API_KEY\s*=\s*"?([^"\r\n]+)', line)
        if m:
            key = m.group(1)
    return build_writing_provider(Settings(anthropic_api_key=key))


def evaluate_draft(case: Case, writer) -> dict:
    """Erzeugt den Schriftsatz ueber DraftingService und prueft Fakten im FERTIGEN, rekonstruierten Text."""
    db, matter = build_session(case)
    service = _build_service(writer)
    t = time.perf_counter()
    result = service.create_draft(matter.id, "formulate_draft", db, attorney_anmerkungen=case.task, actor="test")
    seconds = time.perf_counter() - t
    text = result.draft_text or ""
    rows = [(f, f.probe in text or any(k in text for k in f.keywords)) for f in case.facts]
    conflicts = {}
    for index in sorted({f.doc for f in case.facts}):
        pair = [f for f in case.facts if f.doc == index and f.kind == "widerspruch"]
        both_dates = [f for f in pair if f.is_date]
        named = len(both_dates) == 2 and all(f.probe in text for f in both_dates)
        near = False
        if named:
            lo = min(text.index(f.probe) for f in both_dates)
            hi = max(text.index(f.probe) for f in both_dates)
            window = text[max(0, lo - 400): hi + 400].lower()
            near = any(w in window for w in _CONFLICT_WORDS)
        conflicts[index] = (named, near)
    if text:
        out = Path(__file__).resolve().parents[1] / "e2e_drafts"
        out.mkdir(exist_ok=True)
        (out / f"{case.name}_{int(time.time())}.txt").write_text(text, encoding="utf-8")
    return {"success": result.success, "blocked": result.blocked_reasons, "chars": len(text), "seconds": seconds,
            "rows": rows, "conflicts": conflicts, "notices": [p for p in result.open_review_points if "auszugsweise" in p],
            "text": text}


class _EchoWriter:
    """Ersetzt Claude durch den pseudonymisierten Sachverhalt (nur Rekonstruktions-Roundtrip, KEINE Qualitaetsaussage)."""

    def __init__(self) -> None:
        self.payloads = []

    def write(self, payload):
        from app.ai_providers.claude_writing_provider import ClaudeWritingResult

        self.payloads.append(payload)
        return ClaudeWritingResult(text=payload.anonymisierter_sachverhalt, token_count=0)


def summarize(rows, key) -> dict[str, str]:
    out: dict[str, str] = {}
    for group in ("anfang", "mitte", "ende"):
        sel = [r for r in rows if r[0].where == group]
        out[group] = f"{sum(1 for r in sel if r[key]) }/{len(sel)}"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["payload", "draft", "echo"])
    ap.add_argument("--cases", default="s5,s12,s45,m3x20,m4x35,h45,h3x30")
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args()
    if args.mode in ("draft", "echo"):
        print(f"{'Fall':7s} Lauf  {'Zeichen':>7s} {'Sek.':>5s}  Fakten im FERTIGEN Schriftsatz Anfang / Mitte / Ende   Widersprueche benannt (Dok: Werte/Hinweis)   Hinweise")
        for name in args.cases.split(","):
            for run in range(1, args.runs + 1):
                case = make_case(name)
                r = evaluate_draft(case, _real_writer() if args.mode == "draft" else _EchoWriter())
                srows = [(f, ok) for f, ok in r["rows"]]
                cnt = {g: f"{sum(1 for f, ok in srows if f.where == g and ok)}/{sum(1 for f, _ in srows if f.where == g)}" for g in ("anfang", "mitte", "ende")}
                conf = " ".join(f"{i + 1}:{'J' if n else 'n'}/{'J' if h else 'n'}" for i, (n, h) in r["conflicts"].items())
                print(f"{name:7s} {run:4d}  {r['chars']:7d} {r['seconds']:5.0f}  {cnt['anfang']:>5s} / {cnt['mitte']:>5s} / {cnt['ende']:>5s}   {conf:30s} {len(r['notices'])} {'BLOCKIERT ' + str(r['blocked']) if not r['success'] else ''}", flush=True)
        return
    print(f"{'Fall':7s} {'Dok.-Zeichen':>12s} {'Sachverh.':>9s} {'Payload':>8s} {'Gateway':>8s}  Payload-Fakten Anfang / Mitte / Ende   Hinweise")
    for name in args.cases.split(","):
        case = make_case(name)
        r = evaluate_payload(case)
        s = summarize(r["rows"], 2)
        flag = "" if r["allowed"] else f"BLOCKIERT {r['reasons']}"
        print(f"{name:7s} {r['docs_chars']:12d} {r['sach_chars']:9d} {r['payload_chars']:8d} {r['gateway_s']:7.1f}s  "
              f"{s['anfang']:>5s} / {s['mitte']:>5s} / {s['ende']:>5s}   {len(r['notices'])} Hinweis(e) {flag}")


if __name__ == "__main__":
    main()
