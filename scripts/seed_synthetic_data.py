"""CLI-Skript: befüllt die Datenbank mit synthetischen Testfällen
(Prompt 29).

Verwendung:
    python scripts/seed_synthetic_data.py --count 20
    python scripts/seed_synthetic_data.py --count 20 --seed 42  # reproduzierbar
    python scripts/seed_synthetic_data.py --reset                # nur aufräumen
    python scripts/seed_synthetic_data.py --reset --count 12 --seed 42
                                          # reproduzierbarer Frischstand

Erzeugt AUSSCHLIESSLICH fiktive Daten (siehe app/synthetic_data/) - ruft
KEINE Claude API auf, verursacht keine Kosten.

Wiederholbarkeit (14.09., Nachtrag §9): jeder erzeugte Mandant trägt eine
"DEMO-"-Mandantennummer. `--reset` entfernt **ausschließlich** diese
Demo-Mandanten samt ihrer Akten/Nachrichten/Dokumente/Fristen/Aufgaben -
echte Mandanten ohne dieses Präfix sind strukturell nicht betroffen.
`--reset --count N --seed S` liefert damit einen deterministischen,
idempotenten Frischstand: derselbe Aufruf führt immer zum selben
Datenbestand, statt bei jedem Lauf weitere Fälle anzuhäufen.

Die gemeinsame Rechtsquellen-/Wissensbasis (--with-knowledge-base) ist
seit 14.09. selbst idempotent - ein wiederholter Aufruf legt nichts
doppelt an.
"""

from __future__ import annotations

import argparse
import sys

from app.db.session import SessionLocal
from app.synthetic_data import SyntheticDataGenerator
from app.synthetic_data.generator import reset_demo_data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--count", type=int, default=20, help="Anzahl zu erzeugender Fälle (Standard: 20)"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Zufalls-Seed für reproduzierbare Fälle (Standard: zufällig)",
    )
    parser.add_argument(
        "--with-knowledge-base",
        action="store_true",
        help="Zusätzlich eine gemeinsame Rechtsquellen-/Wissensbasis anlegen "
        "(idempotent - ein wiederholter Aufruf legt nichts doppelt an)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Vorhandene Demo-Daten (alle Mandanten mit 'DEMO-'-Nummer samt "
        "Akten/Nachrichten/Dokumenten/Fristen/Aufgaben) VOR dem Seeden "
        "entfernen. Echte Mandanten sind nicht betroffen.",
    )
    parser.add_argument(
        "--document-dir",
        default=None,
        help="Verzeichnis, in das echte, extrahierbare PDF-Dateien zu jedem "
        "Dokument geschrieben werden (z. B. data/synthetic_documents). Ohne "
        "Angabe tragen die Dokumente nur einen fiktiven Pfad - reale "
        "Dokument-Workflows sind dann nicht ausführbar.",
    )
    parser.add_argument(
        "--count-zero-ok",
        action="store_true",
        help=argparse.SUPPRESS,  # intern: erlaubt '--reset' ohne Neuanlage
    )
    parser.add_argument(
        "--complex-cases",
        type=int,
        default=0,
        help="Zusaetzlich N vollstaendige, mehrseitige Faelle erzeugen "
        "(abwechselnd ein Gesellschaftsrecht- und ein Erbschaftsteuer-Fall, "
        "je sechs verbundene Dokumente/Stationen statt nur einem Dokument - "
        "Owner-Direktive 'WORKSTREAM B — SYNTHETISCHE KANZLEI-WELT', 20.09., "
        "erweitert um den Erbschaftsteuer-Fall am 24.09.). Standard: 0.",
    )
    args = parser.parse_args()

    # `--reset` allein (ohne Neuanlage) ist ein legitimer Aufruf: nur
    # aufräumen. Dafür muss --count 0 erlaubt sein. `--complex-cases`
    # allein (ohne einfache Faelle) ist ebenfalls legitim (20.09.).
    reset_only = args.reset and args.count == 0 and args.complex_cases == 0
    only_complex = args.complex_cases > 0 and args.count == 0
    if args.count < 0 or (args.count == 0 and not reset_only and not only_complex):
        print("FEHLER: --count muss positiv sein", file=sys.stderr)
        return 1

    db = SessionLocal()
    try:
        if args.reset:
            removed = reset_demo_data(db)
            print(
                "Demo-Daten zurückgesetzt: "
                + ", ".join(f"{count} {name}" for name, count in sorted(removed.items()))
            )
            if reset_only:
                return 0

        generator = SyntheticDataGenerator(
            seed=args.seed, document_storage_dir=args.document_dir
        )

        if args.with_knowledge_base:
            sources, knowledge_items = generator.generate_shared_knowledge_base(db)
            print(
                f"{len(sources)} Rechtsquellen, {len(knowledge_items)} "
                "Wissenselemente angelegt."
            )
            # Gemeinsam mit der Wissensbasis erzeugt (15.09.): beides ist
            # kanzleiweit geteiltes, nicht aktengebundenes Referenzmaterial -
            # derselbe --with-knowledge-base-Schalter deckt beides ab, statt
            # einen weiteren, fast identisch begruendeten CLI-Schalter
            # einzufuehren. Behebt den zuvor gefundenen Gap: der
            # Dokumentengenerator hatte ohne dies KEINE einzige Vorlage in
            # der Demo-Datenbasis (document_templates war leer).
            document_templates = generator.generate_shared_document_templates(db)
            print(f"{len(document_templates)} Dokument-Mustertexte angelegt.")

        if args.count > 0:
            cases = generator.generate_many(db, args.count)
            print(f"{len(cases)} synthetische Fälle erzeugt:")
            for case in cases:
                print(
                    f"  - [{case.scenario_key}] {case.matter.title} "
                    f"({case.matter.reference_number})"
                )

        # Abwechselnd Gesellschaftsrecht-/Erbschaftsteuer-Fall (24.09.,
        # "ROADMAP-ALIGNED PRODUCT COMPLETION" §10) - vermeidet eine reine
        # Wiederholung desselben Falltyps bei --complex-cases > 1.
        complex_case_generators = (
            generator.generate_complex_case,
            generator.generate_complex_case_erbschaftsteuer,
        )
        for i in range(args.complex_cases):
            complex_case = complex_case_generators[i % len(complex_case_generators)](db)
            print(
                f"  - [KOMPLEX: {complex_case.scenario_key}] "
                f"{complex_case.matter.title} "
                f"({complex_case.matter.reference_number}) - "
                f"{len(complex_case.documents)} Dokumente"
            )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
