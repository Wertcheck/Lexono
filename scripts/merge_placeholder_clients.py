"""Führt die doppelten Sammel-Mandanten "Ohne Mandantenzuordnung" zu einem
einzigen Datensatz zusammen.

HINTERGRUND: bis zum Fix vom 15.09. legte `create_quick_matter` bei jedem
Entwurf ohne Aktenauswahl einen NEUEN Platzhalter-Mandanten an (siehe
app/drafting/quick_matter.py). Auf einer länger genutzten Installation
sammeln sich dadurch viele identische Zeilen in der Mandantenliste - auf
der Referenzmaschine waren es 28 von 40. Der Fix verhindert neue
Duplikate, räumt die bestehenden aber bewusst NICHT automatisch auf:
Aufräumen in einer Kanzleidatenbank ist eine Entscheidung der Kanzlei,
kein Nebeneffekt eines Programmstarts.

STANDARDMÄSSIG NUR ANZEIGEN. Es wird nichts geschrieben, solange nicht
ausdrücklich `--apply` angegeben ist.

    python scripts/merge_placeholder_clients.py              # nur anzeigen
    python scripts/merge_placeholder_clients.py --apply      # wirklich

Was `--apply` tut: alle Akten der doppelten Platzhalter werden auf den
ÄLTESTEN Platzhalter umgehängt, danach werden die leer gewordenen
Duplikate gelöscht. Ein Platzhalter, an dem noch etwas anderes hängt als
Akten, bleibt unangetastet. Namentlich benannte Mandanten werden NIE
angefasst - zwei Personen können denselben Namen tragen.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.settings import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.drafting.quick_matter import PLACEHOLDER_CLIENT_NAME  # noqa: E402
from app.models import AuditEvent, Client, Matter  # noqa: E402
from app.setup.paths import resolve_data_dir  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Änderungen wirklich schreiben (ohne dieses Flag wird nur angezeigt).",
    )
    args = parser.parse_args()

    # WELCHE Datenbank - immer zuerst und immer sichtbar. Aus dem
    # Projektverzeichnis heraus greift der Standardwert
    # `sqlite:///./data/kanzlei_ai.db` (Entwicklungsdatenbank), NICHT die
    # Datenbank der installierten Instanz unter %PROGRAMDATA%\Lexono. Wer
    # ein Zusammenführungsskript laufen lässt, muss sehen, worauf es zeigt -
    # sonst räumt es im Zweifel die falsche Datenbank auf.
    settings = get_settings()
    print(f"Datenbank: {settings.database_url}")
    print(f"(Datenverzeichnis der Installation: {resolve_data_dir()})\n")

    db = SessionLocal()
    try:
        placeholders = (
            db.query(Client)
            .filter(Client.name == PLACEHOLDER_CLIENT_NAME)
            .order_by(Client.created_at)
            .all()
        )
        if len(placeholders) <= 1:
            print(
                f'Nichts zu tun: {len(placeholders)} Mandant(en) namens '
                f'"{PLACEHOLDER_CLIENT_NAME}".'
            )
            return 0

        keeper, duplicates = placeholders[0], placeholders[1:]
        total_clients = db.query(Client).count()
        matters = (
            db.query(Matter)
            .filter(Matter.client_id.in_([c.id for c in duplicates]))
            .all()
        )

        print(f'Mandanten insgesamt:            {total_clients}')
        print(f'davon "{PLACEHOLDER_CLIENT_NAME}": {len(placeholders)}')
        print(f"behalten wird (ältester):       {keeper.id}  ({keeper.created_at})")
        print(f"zusammenzuführende Duplikate:   {len(duplicates)}")
        print(f"davon betroffene Akten:         {len(matters)}")
        print(f"Mandanten danach:               {total_clients - len(duplicates)}")

        if not args.apply:
            print("\nNur Anzeige - es wurde nichts geändert. Mit --apply ausführen.")
            return 0

        for matter in matters:
            matter.client_id = keeper.id
        db.flush()

        removed = 0
        for duplicate in duplicates:
            still_linked = db.query(Matter).filter(Matter.client_id == duplicate.id).count()
            if still_linked:
                print(f"  übersprungen (noch {still_linked} Akten): {duplicate.id}")
                continue
            db.delete(duplicate)
            removed += 1

        db.add(
            AuditEvent(
                entity_type="Client",
                entity_id=keeper.id,
                event_type="placeholder_clients_merged",
                actor="scripts/merge_placeholder_clients.py",
                details=(
                    f"{removed} doppelte Sammel-Mandanten zusammengeführt, "
                    f"{len(matters)} Akten umgehängt"
                ),
            )
        )
        db.commit()
        print(f"\nFertig: {removed} Duplikate entfernt, {len(matters)} Akten umgehängt.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
