"""§64 P3: Sperrklinken fuer zwei Datenschutz-Benchmarks, die bisher NUR als Skript liefen (kein Regressionsschutz):

- `scripts/bench_role_object_negatives.py` (§53): Rollenwort + Objekt ("der Zeuge Mangel ...") - kein Fachbegriff geht
  verloren, kein Personen-Platzhalter im Schriftsatz ohne Namen.
- `scripts/bench_chat_without_file.py` (§48): Chat ohne Akte - 55 Nachnamen x 8 Kontexte, 14 Fachfragen, 100
  Fachbegriffe x 4 Kontexte im tatsaechlichen Payload (`prepare_request`, lokal, kein Cloud-Aufruf).

Stand 17.10.2026 (reproduziert, gleiche Methodik wie §61.4): 0/12 Saetze mit Verlust; 0/440 Lecks, 0 Blockaden, 0/14
Fachfragen veraendert, 20/100 Fachbegriffe in mindestens einem Kontext maskiert (bekannte Fehlalarme). Erlaubt ist genau
dieser Stand: jedes neue Leck, jede Blockade, jede veraenderte Fachfrage und jeder NEU betroffene Fachbegriff schlaegt
fehl; ein Begriff, der nicht mehr betroffen ist, darf aus der Liste gestrichen werden. Nur synthetische Daten."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

KNOWN_AFFECTED_TERMS = {
    "Außenwand", "Basiszinssatz", "Baumangel", "Dachrinne", "Dampfsperre", "Estrich", "Feuchtigkeit", "Flachdach",
    "Gewährleistungsausschluss", "Grundbesitzwert", "Kündigungsschutzklage", "Nebenkostenvorauszahlung", "Rauchmelder",
    "Sachverständiger", "Streitwert", "Testamentsvollstrecker", "Vermächtnis", "Vertragsstrafe", "Verzugszinsen",
    "Zugluft",
}


def test_role_word_and_object_sentences_lose_nothing() -> None:
    import bench_role_object_negatives as bench

    result = bench.run()
    assert result["saetze_mit_verlust"] == 0, result["satz_beispiele"]
    assert result["saetze_total"] == 12
    assert result["schriftsatz_personen_platzhalter"] == 0 and result["schriftsatz_verloren"] == []


def test_chat_without_file_has_no_leak_block_or_new_false_positive() -> None:
    import bench_chat_without_file as bench

    result = bench.run("Produktivcode")
    assert result["total"] == 440 and result["terms_total"] == 100  # Korpus unveraendert
    assert result["leaks"] == 0, result["leaks_by_context"]
    assert result["blocks"] == 0
    assert result["changed_questions"] == []
    new = set(result["terms_affected"]) - KNOWN_AFFECTED_TERMS
    assert not new, f"neu maskierte Fachbegriffe: {sorted(new)}"
