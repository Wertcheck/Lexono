"""Reproduktion: erfindet Claude Platzhalter, die nie bereitgestellt wurden (z. B. "[ADRESSE_01]")? (12.10.2026)

Erzeugt Entwuerfe mit echtem Claude (Kosten!) fuer synthetische Akten und vergleicht die im ROHEN Modelltext vorkommenden
Platzhalter ([KATEGORIE_NN]) mit den im Payload bereitgestellten. Ausgegeben wird je Lauf: bereitgestellte Platzhalter
(Anzahl je Kategorie), erfundene Platzhalter und der umgebende Satz (synthetischer Text), sowie ob der Entwurf blockiert wurde.

Aufruf:  python scripts/repro_invented_placeholders.py --case s5 --runs 12 [--task standard|empfaenger]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_completeness as e  # noqa: E402

TOKEN = re.compile(r"\[[A-Za-zÄÖÜäöüß_]+_\d{2}\]")
TASKS = {
    "standard": e.TASK,
    "empfaenger": (
        "Erstelle ein vollständiges Schreiben an die Gegenseite mit Empfängerblock (Name und Anschrift der Gegenseite), "
        "Betreff, Anrede, Text und Grußformel, das die wesentlichen Beträge, Fristen und Widersprüche der Verträge nennt."
    ),
}


def _prompt_without_literal_address_tokens(prompt: str) -> str:
    """Prompt-Variante P1: entfernt die woertlichen Adress-Platzhalter-Beispiele aus der Anweisung und sagt ausdruecklich,
    was bei fehlender Anschrift zu schreiben ist."""
    replacements = [
        ("Platzhalternamen (PERSON_01, ADRESSE_02 usw.)", "Platzhalternamen in eckigen Klammern mit Nummer"),
        (
            "Steht die Anschrift der Gegenseite als Platzhalter [ADRESSE_NN] im Sachverhalt, verwende genau diesen Platzhalter im Briefkopf.",
            "Steht die Anschrift der Gegenseite als Adress-Platzhalter wörtlich im Sachverhalt, verwende genau diesen Platzhalter im "
            "Briefkopf; steht dort keiner, schreibe im Empfängerblock exakt \"[Anschrift einsetzen]\".",
        ),
        ("steht ein [ADRESSE_NN] im Sachverhalt unmittelbar bei einer Person", "steht ein Adress-Platzhalter im Sachverhalt unmittelbar bei einer Person"),
    ]
    for old, new in replacements:
        assert old in prompt, old
        prompt = prompt.replace(old, new)
    return prompt


class RecordingWriter:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.last_text = ""
        self.last_payload = ""

    def write(self, payload):
        result = self.inner.write(payload)
        self.last_text = result.text
        self.last_payload = payload.model_dump_json()
        return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="s5")
    ap.add_argument("--runs", type=int, default=12)
    ap.add_argument("--task", default="standard", choices=sorted(TASKS))
    ap.add_argument("--prompt", default="aktuell", choices=["aktuell", "p1"], help="p1 = Prompt ohne woertliche [ADRESSE_NN]-Beispiele")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    if args.prompt == "p1":
        import app.ai_providers.claude_writing_provider as cwp

        patched = _prompt_without_literal_address_tokens(cwp.WRITING_SYSTEM_PROMPT)
        assert "ADRESSE" not in patched and patched != cwp.WRITING_SYSTEM_PROMPT
        cwp.WRITING_SYSTEM_PROMPT = patched
    invented_total = blocked_total = 0
    for run in range(1, args.runs + 1):
        case = e.make_case(args.case)
        case.task = TASKS[args.task]
        writer = RecordingWriter(e._real_writer())
        db, matter = e.build_session(case)
        service = e._build_service(writer)
        result = service.create_draft(matter.id, "formulate_draft", db, attorney_anmerkungen=case.task, actor="repro")
        provided = Counter(re.sub(r"_\d{2}\]", "]", t) for t in set(TOKEN.findall(writer.last_payload)))
        used = set(TOKEN.findall(writer.last_text))
        invented = sorted(t for t in used if t not in set(TOKEN.findall(writer.last_payload)))
        invented_total += bool(invented)
        blocked_total += not result.success
        if args.quiet and not invented and result.success:
            continue
        print(f"Lauf {run:2d}: bereitgestellt {dict(provided)} | erfunden {invented or '-'} | {'BLOCKIERT' if not result.success else 'ok'}")
        for token in invented:
            i = writer.last_text.index(token)
            print("         Kontext:", writer.last_text[max(0, i - 120): i + 60].replace("\n", " | "))
    print(f"\n{invented_total} von {args.runs} Läufen mit erfundenem Platzhalter, {blocked_total} blockiert")


if __name__ == "__main__":
    main()
