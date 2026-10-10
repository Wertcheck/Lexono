"""Messung: welche Teile langer Dokumente erreichen den Sachverhalt (und damit Claude)? (Qualitaetslauf 10.10.)

Erzeugt SYNTHETISCHE Vertraege unterschiedlicher Laenge mit Schluesselfakten am ANFANG, in der MITTE und am ENDE,
baut daraus den Sachverhalt wie `RuleBasedLocalAIProvider._build_sachverhalt` (Quelle jeder Schriftsatz-/Chat-/
Review-Anfrage) und meldet je Groesse: Anteil gefundener Fakten je Position, Sachverhaltslaenge (Zeichen, grob
Token), sowie die Laufzeit des Datenschutz-Gateways (Presidio) fuer genau diesen Sachverhalt.

Aufruf:  python scripts/bench_document_completeness.py [--gateway]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider  # noqa: E402
from app.models import Base, Client, Document, Matter  # noqa: E402

START_FACTS = [
    "Kaufpreis von 118.750,00 EUR",
    "Vertragsgegenstand Typ HX-440",
    "Vertragsbeginn am 09.01.2026",
    "Lieferort Hafenkai 12",
    "Käufer die Brandt Anlagenbau GmbH",
    "Verkäuferin Falk Maschinen KG",
]
MIDDLE_FACTS = [
    "Vertragsstrafe von 0,3 Prozent je Werktag",
    "Sicherheitseinbehalt von 45.300,00 EUR",
    "Skonto von 2 Prozent bei Zahlung bis zum 14.04.2026",
    "Gewährleistungsfrist von 36 Monaten",
    "Abnahme binnen 12 Werktagen",
    "Haftungsbegrenzung auf 250.000,00 EUR",
]
# Beschreibende Fakten OHNE Betrag/Datum/Frist-Signal in der Mitte - bewusst der ungünstige Fall für die
# Auswahl von Schlüsselstellen (ehrliche Grenze der Methode).
MIDDLE_DESC_FACTS = [
    "Die Anlage wird in Halle 3 aufgestellt",
    "Ansprechpartnerin des Käufers ist Frau Lindqvist",
    "Die Schutzhauben sind in der Farbe Anthrazit zu liefern",
    "Das Bedienpersonal wird durch den Verkäufer eingewiesen",
]
END_FACTS = [
    "Kündigungsfrist von 6 Wochen zum Quartalsende",
    "Preisanpassung von 11.200,00 EUR bei Mehrmengen",
    "Gerichtsstand Beispielstadt",
    "Schiedsgutachter Dr. Wiebe",
    "Nachfrist bis zum 30.11.2026",
    "AUFGABE: Bitte Mängelrüge erstellen",
]

_FILLER = [
    "Die Parteien sind sich darüber einig, dass die Leistung nach den anerkannten Regeln der Technik zu erbringen ist.",
    "Änderungen und Ergänzungen dieses Vertrages bedürfen zu ihrer Wirksamkeit der Schriftform.",
    "Der Auftragnehmer ist verpflichtet, den Auftraggeber unverzüglich über erkennbare Hindernisse zu unterrichten.",
    "Mitwirkungspflichten des Auftraggebers bleiben von den Regelungen dieses Abschnitts unberührt.",
    "Für die Auslegung gelten ergänzend die gesetzlichen Vorschriften, soweit nichts Abweichendes vereinbart ist.",
    "Sollte eine Bestimmung unwirksam sein, bleibt die Wirksamkeit der übrigen Bestimmungen unberührt.",
    "Die Parteien verpflichten sich zur vertraulichen Behandlung aller im Rahmen der Zusammenarbeit erlangten Informationen.",
    "Erfüllungsort für sämtliche Verpflichtungen aus diesem Vertrag ist der Sitz des jeweiligen Leistungserbringers.",
]


def make_contract(size: int) -> tuple[str, dict[str, list[str]]]:
    """Vertragstext der ungefaehren Laenge `size` mit Fakten bei ~5 %, ~50 % und ~95 % der Laenge."""
    n_sections = max(6, size // 700)
    sections: list[str] = []
    for i in range(n_sections):
        pos = i / max(1, n_sections - 1)
        body = " ".join(_FILLER[(i + k) % len(_FILLER)] for k in range(5))
        extra = ""
        if i == 0:
            extra = " " + ". ".join(START_FACTS) + "."
        elif i == n_sections // 2:
            extra = " " + ". ".join(MIDDLE_FACTS) + "."
        elif i == n_sections // 2 + 1:
            extra = " " + ". ".join(MIDDLE_DESC_FACTS) + "."
        elif i == n_sections - 1:
            extra = " " + ". ".join(END_FACTS) + "."
        sections.append(f"§ {i + 1} Abschnitt {i + 1}\n{body}{extra}")
        del pos
    return "\n".join(sections), {"Anfang": START_FACTS, "Mitte": MIDDLE_FACTS, "Mitte-beschr.": MIDDLE_DESC_FACTS, "Ende": END_FACTS}


def build(sizes: list[int], gateway: bool) -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    rows = []
    for size in sizes:
        db = Session()
        client = Client(name="Erika Beispielfrau")
        matter = Matter(title="Beispielakte", client=client)
        text, facts = make_contract(size)
        db.add_all([client, matter, Document(matter=matter, file_path="/tmp/v.pdf", extracted_text=text, classified_type="Vertrag")])
        db.commit()
        result = RuleBasedLocalAIProvider().prepare_draft_context(matter.id, db)
        found = {pos: sum(f in result.sachverhalt for f in fs) / len(fs) for pos, fs in facts.items()}
        gw = ""
        if gateway:
            from app.privacy.gateway import ClaudePrivacyGateway

            t = time.perf_counter()
            r = ClaudePrivacyGateway().prepare_request(
                purpose="formulate_draft", sachverhalt=result.sachverhalt, argumentationspunkte=[], quellenverweise=[],
                stil=None, vorlage=None, anwaltliche_anmerkungen=None, known_entities=result.known_entities,
                gespraechsverlauf=None, skip_general_knowledge_pseudonymization=False,
            )
            gw = f"{time.perf_counter() - t:5.1f} s (allowed={r.allowed})"
        rows.append((len(text), len(result.sachverhalt), found, gw))
        db.close()
    print(f"{'Dokument':>9s} {'Sachverhalt':>11s} {'~Token':>7s}  Fakten gefunden: Anfang / Mitte(Betr./Fristen) / Mitte(beschreibend) / Ende   Gateway")
    for doc_len, sach_len, f, gw in rows:
        print(f"{doc_len:9d} {sach_len:11d} {sach_len // 3:7d}  {f['Anfang']:6.0%} / {f['Mitte']:5.0%} / {f['Mitte-beschr.']:5.0%} / {f['Ende']:5.0%}   {gw}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", action="store_true")
    ap.add_argument("--sizes", default="3000,6000,12000,25000,60000,120000")
    a = ap.parse_args()
    build([int(x) for x in a.sizes.split(",")], a.gateway)
