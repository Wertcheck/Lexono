"""Tests für app/synthetic_data/ (Prompt 29)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.classification.classifier import PlaceholderDocumentClassifier
from app.classification.schema import ALLOWED_DOCUMENT_TYPES
from app.models import Client, Document, DocumentTemplate, KnowledgeItem, Matter, Message, Party, Source
from app.models.base import Base
from app.synthetic_data import SCENARIOS, SyntheticDataGenerator
from app.synthetic_data.generator import DEMO_CLIENT_NUMBER_PREFIX, reset_demo_data


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# --- Grundfunktion ---


def test_generate_case_creates_consistent_records(db_session: Session) -> None:
    generator = SyntheticDataGenerator(seed=1)
    case = generator.generate_case(db_session)

    assert case.matter.client_id == case.client.id
    assert case.message.matter_id == case.matter.id
    assert case.document.matter_id == case.matter.id
    assert case.document.message_id == case.message.id
    if case.deadline is not None:
        assert case.deadline.matter_id == case.matter.id
        assert case.deadline.document_id == case.document.id


def test_generate_case_uses_only_synthetic_email_domain(db_session: Session) -> None:
    """Der Absender traegt seit 14.09. zusaetzlich einen Anzeigenamen
    ("Name <adresse>", siehe generator.py) - die eigentliche Garantie
    bleibt unveraendert und wird hier sogar praeziser geprueft als zuvor:
    die enthaltene ADRESSE muss die RFC-2606-Testdomain nutzen (technisch
    nie zustellbar)."""
    import re

    generator = SyntheticDataGenerator(seed=2)
    case = generator.generate_case(db_session)

    addresses = re.findall(r"[\w.+-]+@[\w.-]+", case.message.sender)
    assert addresses, f"kein Absender gefunden in {case.message.sender!r}"
    for address in addresses:
        assert address.endswith("@example-testdomain.invalid"), address


def test_generate_case_classified_type_is_valid(db_session: Session) -> None:
    generator = SyntheticDataGenerator(seed=3)
    for scenario in SCENARIOS:
        case = generator.generate_case(db_session, scenario_key=scenario.key)
        assert case.document.classified_type in ALLOWED_DOCUMENT_TYPES


def test_unknown_scenario_key_raises(db_session: Session) -> None:
    generator = SyntheticDataGenerator(seed=4)
    with pytest.raises(ValueError):
        generator.generate_case(db_session, scenario_key="nicht-vorhanden")


# --- Determinismus (wichtig für den Benchmark aus Prompt 30) ---


def test_same_seed_produces_identical_case_content() -> None:
    engine_a = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine_a)
    db_a = sessionmaker(bind=engine_a)()
    case_a = SyntheticDataGenerator(seed=99).generate_case(db_a)

    engine_b = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine_b)
    db_b = sessionmaker(bind=engine_b)()
    case_b = SyntheticDataGenerator(seed=99).generate_case(db_b)

    assert case_a.client.name == case_b.client.name
    assert case_a.matter.title == case_b.matter.title
    assert case_a.message.subject == case_b.message.subject
    assert case_a.document.extracted_text == case_b.document.extracted_text

    db_a.close()
    db_b.close()
    engine_a.dispose()
    engine_b.dispose()


def test_different_seeds_produce_different_cases(db_session: Session) -> None:
    case_a = SyntheticDataGenerator(seed=1).generate_case(db_session)
    case_b = SyntheticDataGenerator(seed=2).generate_case(db_session)
    assert case_a.client.name != case_b.client.name


# --- generate_many ---


def test_generate_many_creates_requested_count(db_session: Session) -> None:
    generator = SyntheticDataGenerator(seed=5)
    cases = generator.generate_many(db_session, 12)
    assert len(cases) == 12
    assert db_session.query(Matter).count() == 12
    assert db_session.query(Client).count() == 12
    assert db_session.query(Message).count() == 12
    assert db_session.query(Document).count() == 12


def test_generate_many_covers_all_scenarios_when_count_exceeds_scenario_count(
    db_session: Session,
) -> None:
    generator = SyntheticDataGenerator(seed=6)
    cases = generator.generate_many(db_session, len(SCENARIOS) * 3)
    used_scenarios = {c.scenario_key for c in cases}
    assert used_scenarios == {s.key for s in SCENARIOS}


def test_generate_many_is_deterministic_with_seed() -> None:
    engine_a = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine_a)
    db_a = sessionmaker(bind=engine_a)()
    cases_a = SyntheticDataGenerator(seed=77).generate_many(db_a, 10)

    engine_b = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine_b)
    db_b = sessionmaker(bind=engine_b)()
    cases_b = SyntheticDataGenerator(seed=77).generate_many(db_b, 10)

    assert [c.matter.title for c in cases_a] == [c.matter.title for c in cases_b]

    db_a.close()
    db_b.close()
    engine_a.dispose()
    engine_b.dispose()


# --- Kollisionsschutz bei reference_number (echter Fund während der Entwicklung) ---


def test_repeated_generation_against_same_db_never_collides_on_reference_number(
    db_session: Session,
) -> None:
    """Regressionstest für einen während der Entwicklung gefundenen Bug:
    `Matter.reference_number` trägt eine UNIQUE-Constraint - ohne
    Kollisionsprüfung hätte eine wiederholte Generator-Nutzung gegen
    dieselbe Datenbank (z. B. mehrere Demo-Sitzungen) irgendwann einen
    harten IntegrityError auslösen können."""
    generator = SyntheticDataGenerator()  # kein Seed -> echte Zufälligkeit
    for _ in range(60):
        generator.generate_case(db_session)

    reference_numbers = [m.reference_number for m in db_session.query(Matter).all()]
    assert len(reference_numbers) == len(set(reference_numbers))


# --- Wissensbasis ---


def test_generate_shared_knowledge_base_creates_approved_entries(
    db_session: Session,
) -> None:
    generator = SyntheticDataGenerator(seed=7)
    sources, knowledge_items = generator.generate_shared_knowledge_base(db_session)

    assert len(sources) > 0
    assert all(s.approval_level == "freigegeben" for s in sources)
    assert len(knowledge_items) > 0
    assert all(k.approval_status == "approved" for k in knowledge_items)


def test_generated_sources_are_indexable_by_existing_search_service(
    db_session: Session,
) -> None:
    """Beweis, dass die generierte Wissensbasis mit der BESTEHENDEN
    Such-/Recherche-Infrastruktur (Prompt 11/14) zusammenspielt, ohne
    Sonderbehandlung - keine Ausnahme wird geworfen."""
    from app.search.service import DocumentSearchService
    from tests.fake_embedding_provider import FakeEmbeddingProvider

    generator = SyntheticDataGenerator(seed=8)
    sources, _ = generator.generate_shared_knowledge_base(db_session)

    search_service = DocumentSearchService(FakeEmbeddingProvider())
    for source in sources:
        search_service.index_source(source, db_session)


# --- Grundregel: nie echte Personen/Domains ---


def test_no_real_looking_domains_used(db_session: Session) -> None:
    generator = SyntheticDataGenerator(seed=9)
    cases = generator.generate_many(db_session, 10)
    for case in cases:
        assert "@example-testdomain.invalid" in case.message.sender
        assert "gmail" not in case.message.sender
        assert "gmx" not in case.message.sender
        assert "web.de" not in case.message.sender


# --- Demo-Kennzeichnung, Idempotenz, Reset (14.09., Nachtrag §9) ---


def test_generated_clients_carry_full_master_data(db_session: Session) -> None:
    """ECHTER FUND (14.09., UI/UX-Abgleich gegen
    assets/ux-ui/29_mandanten_uebersicht.png): der Generator setzte bisher
    NUR `Client.name` - die Mandantenliste zeigte in jeder Demo-/Test-
    umgebung durchgehend "–" bei Nummer/Kontakt/Rechtsgebiet, der
    Referenzzustand war damit gar nicht herstellbar."""
    generator = SyntheticDataGenerator(seed=11)
    case = generator.generate_case(db_session)

    assert case.client.client_number is not None
    assert case.client.contact_email is not None
    assert case.client.contact_phone is not None
    assert case.client.practice_area is not None
    assert case.client.status == "active"


def test_generated_clients_are_marked_as_demo_data(db_session: Session) -> None:
    """Nachtrag §9: Demo-Daten muessen "klar als Test-/Demo-Daten
    erkennbar" sein - und zwar sichtbar in der Oberflaeche, nicht nur
    intern."""
    generator = SyntheticDataGenerator(seed=12)
    cases = generator.generate_many(db_session, 3)

    for case in cases:
        assert case.client.client_number.startswith(DEMO_CLIENT_NUMBER_PREFIX)
    numbers = {c.client.client_number for c in cases}
    assert len(numbers) == 3, "Mandantennummern muessen eindeutig sein"


def test_reset_removes_all_demo_data(db_session: Session) -> None:
    generator = SyntheticDataGenerator(seed=13)
    generator.generate_many(db_session, 4)
    assert db_session.query(Client).count() == 4
    assert db_session.query(Matter).count() == 4

    removed = reset_demo_data(db_session)

    assert removed["clients"] == 4
    assert removed["matters"] == 4
    assert db_session.query(Client).count() == 0
    assert db_session.query(Matter).count() == 0
    assert db_session.query(Message).count() == 0
    assert db_session.query(Document).count() == 0


def test_reset_removes_parties_of_demo_matters(db_session: Session) -> None:
    """ECHTER FUND (17.09., beim Bauen von app/web/parties_router.py selbst
    entdeckt, VOR dem Fertigmelden behoben): `Party` ist erst seit heute
    ueberhaupt anlegbar - dieser Reset kannte das Modell noch nicht. Ohne
    die Korrektur wuerde `Query.delete()` (Bulk-SQL, OHNE ORM-Cascade) eine
    an eine geloeschte Demo-Akte gebundene Partei als Waise zuruecklassen -
    exakt die Art verwaister Zeile, die bereits einmal real in der
    Produktions-DB gefunden wurde (siehe OPEN_ISSUES.md,
    DOCX-Export-Absturz-Fund)."""
    generator = SyntheticDataGenerator(seed=17)
    cases = generator.generate_many(db_session, 1)
    matter = cases[0].matter
    db_session.add(Party(matter_id=matter.id, name="Gegner GmbH", role="Gegner"))
    db_session.commit()
    assert db_session.query(Party).count() == 1

    removed = reset_demo_data(db_session)

    assert removed["parties"] == 1
    assert db_session.query(Party).count() == 0


def test_reset_removes_processing_errors_of_deleted_demo_documents(
    db_session: Session,
) -> None:
    """ECHTER FUND (20.09., beim realen GUI-Durchgang durch die
    installierte Anwendung entdeckt, siehe OPEN_ISSUES.md fuer die volle
    Reproduktion): `ProcessingError` referenziert ein `Document` nur per
    freiem `entity_id`-String OHNE Fremdschluessel/Cascade - ohne diese
    Korrektur hinterliess ein Reset verwaiste Fehler-Eintraege, die in der
    "Fehler & Wiederholungen"-Uebersicht fuer immer auf ein nicht mehr
    existierendes Dokument zeigten (real in der Produktions-DB gefunden,
    eine davon durch einen frueheren Retry-Versuch sogar dauerhaft in
    "retrying" haengengeblieben)."""
    from app.errors import RetryService
    from app.models import ProcessingError

    generator = SyntheticDataGenerator(seed=18)
    case = generator.generate_case(db_session)
    RetryService().record_failure(
        db_session,
        entity_type="Document",
        entity_id=case.document.id,
        operation="ocr",
        error_category="transient",
        error_message="Testfehler",
    )
    assert db_session.query(ProcessingError).count() == 1

    reset_demo_data(db_session)

    assert db_session.query(ProcessingError).count() == 0


def test_reset_never_touches_real_clients(db_session: Session) -> None:
    """SICHERHEITSKRITISCH: der Reset darf ausschliesslich Demo-Daten
    treffen. Ein echter Mandant - auch einer mit einem "Muster"-aehnlichen
    Namen, wie er in einer echten Kanzlei durchaus vorkommen kann - muss
    unangetastet bleiben."""
    echter_mandant = Client(name="Max Mustermann", client_number="2024-0815")
    db_session.add(echter_mandant)
    db_session.flush()
    echte_akte = Matter(client_id=echter_mandant.id, title="Echte Akte")
    db_session.add(echte_akte)
    db_session.commit()

    SyntheticDataGenerator(seed=14).generate_many(db_session, 3)
    reset_demo_data(db_session)

    assert db_session.query(Client).count() == 1
    assert db_session.get(Client, echter_mandant.id) is not None
    assert db_session.query(Matter).count() == 1
    assert db_session.get(Matter, echte_akte.id) is not None


def test_reset_on_empty_database_is_a_safe_no_op(db_session: Session) -> None:
    removed = reset_demo_data(db_session)
    assert removed["clients"] == 0
    assert removed["matters"] == 0


def test_reset_then_seed_is_idempotent(db_session: Session) -> None:
    """Kernforderung des Nachtrags: "Wiederholte Testlaeufe duerfen nicht
    unkontrolliert Datensaetze anhaeufen." Zweimal dasselbe
    reset+seed mit demselben Seed muss exakt denselben Bestand liefern."""
    def seed_once() -> list[str]:
        reset_demo_data(db_session)
        cases = SyntheticDataGenerator(seed=99).generate_many(db_session, 5)
        return [c.matter.title for c in cases]

    first = seed_once()
    second = seed_once()

    assert first == second
    assert db_session.query(Client).count() == 5
    assert db_session.query(Matter).count() == 5


def test_documents_without_storage_dir_keep_previous_behaviour(
    db_session: Session,
) -> None:
    """Rueckwaertskompatibilitaet: ohne konfiguriertes Verzeichnis bleibt es
    beim bisherigen, rein fiktiven Pfad - bestehende Aufrufer/Tests, die
    keine Dateien anlegen wollen, bleiben unveraendert."""
    case = SyntheticDataGenerator(seed=16).generate_case(db_session)
    assert case.document.file_path.startswith("/data/synthetic/")


def test_generated_documents_are_real_files_readable_by_real_extraction(
    db_session: Session, tmp_path
) -> None:
    """ECHTER FUND (14.09., Nachtrag §8 "Keine Test-Illusion"): die erzeugten
    Dokumente trugen einen Pfad OHNE Datei dahinter - reale
    Dokument-Workflows (Extraktion/Analyse/Vorschau) waren auf Demo-Daten
    damit gar nicht ausfuehrbar. Dieser Test prueft bewusst NICHT nur, dass
    eine Datei existiert, sondern dass die ECHTE Extraktionslogik der
    Anwendung ihren Inhalt auch tatsaechlich wieder herausliest."""
    from app.documents.extraction import extract_text

    generator = SyntheticDataGenerator(seed=17, document_storage_dir=tmp_path)
    case = generator.generate_case(db_session, scenario_key="einspruch_steuerbescheid")

    path = Path(case.document.file_path)
    assert path.exists(), "Dokumentdatei wurde nicht wirklich geschrieben"
    assert path.suffix == ".pdf"

    result = extract_text(path, min_text_length=20)
    assert not result.needs_ocr, "Erzeugtes PDF muss echten Textlayer haben"
    assert result.text is not None
    # Inhaltlicher Abgleich: ein markanter Begriff aus dem Szenario muss
    # real wieder aus der Datei gelesen werden koennen.
    assert "Steuerbescheid" in result.text or "Einspruch" in result.text


def test_demo_documents_never_claim_impossible_confidence(
    db_session: Session,
) -> None:
    """ECHTER FUND (14.09., Nachtrag §8 "Keine Test-Illusion"): die
    Szenarien setzten eine ERFUNDENE Konfidenz zwischen 0.6 und 0.95 - der
    produktive `PlaceholderDocumentClassifier` kann konstruktionsbedingt
    aber NIE ueber 0.4 kommen. Demo-Daten zeigten damit Werte, die im echten
    Betrieb unmoeglich sind, und haetten eine automatische Aktenzuordnung
    (Schwellwert 0.6) als funktionierend erscheinen lassen, die real gar
    nicht greifen kann."""
    cases = SyntheticDataGenerator(seed=18).generate_many(db_session, 6)
    for case in cases:
        assert case.document.classification_confidence <= 0.4, (
            f"{case.document.original_filename} behauptet eine Konfidenz, die der "
            "echte Klassifikator nie liefern koennte"
        )


def test_demo_documents_carry_real_classifier_output(db_session: Session) -> None:
    """Demo-Daten muessen exakt das zeigen, was die ECHTE Pipeline liefern
    wuerde - nicht fest verdrahtete Szenario-Werte (die inhaltlich
    ausserdem ueberholt waren: eine Pruefungsanordnung war als
    "Gerichtliches Schreiben" gesetzt)."""
    classifier = PlaceholderDocumentClassifier()
    case = SyntheticDataGenerator(seed=19).generate_case(
        db_session, scenario_key="betriebspruefung"
    )

    expected = classifier.classify(case.document.extracted_text)
    assert case.document.classified_type == expected.document_type
    assert case.document.classification_confidence == expected.confidence
    # Inhaltliche Gegenprobe: eine Pruefungsanordnung ist kein Gerichtsschreiben.
    assert case.document.classified_type == "Prüfungsanordnung"


def test_shared_knowledge_base_is_idempotent(db_session: Session) -> None:
    """Das CLI-Skript warnte bisher ausdruecklich davor, diese Funktion
    mehrfach aufzurufen ("fuehrt zu doppelten Eintraegen") - genau der
    Datenmuell, den der Nachtrag ausschliesst."""
    generator = SyntheticDataGenerator(seed=15)
    sources_first, items_first = generator.generate_shared_knowledge_base(db_session)
    sources_second, items_second = generator.generate_shared_knowledge_base(db_session)

    assert len(sources_second) == len(sources_first)
    assert len(items_second) == len(items_first)
    assert db_session.query(Source).count() == len(sources_first)
    assert db_session.query(KnowledgeItem).count() == len(items_first)


# --- Nicht zugeordnete Eingangspost (14.09., Gold-Workflow-Startzustand) ---


def test_generate_many_produces_unassigned_incoming_messages(
    db_session: Session,
) -> None:
    """ECHTER FUND beim UI-Durchgang: der Posteingang meldete "0 ohne
    Aktenzuordnung", weil JEDE erzeugte Nachricht bereits fest einer Akte
    zugeordnet war. Der wichtigste Zustand des Posteingangs - frisch
    eingegangene, noch nicht triagierte Post, also der Startzustand des
    Gold-Workflows - war mit Demo-Daten gar nicht darstellbar."""
    cases = SyntheticDataGenerator(seed=20).generate_many(db_session, 6)

    unassigned = [c for c in cases if c.message.matter_id is None]
    assigned = [c for c in cases if c.message.matter_id is not None]
    assert unassigned, "keine einzige unzugeordnete Nachricht erzeugt"
    assert assigned, "es muessen weiterhin auch zugeordnete Vorgaenge existieren"
    # Die passende Akte existiert trotzdem - sie ist die RICHTIGE Antwort,
    # die die Zuordnung finden soll.
    for case in unassigned:
        assert case.matter.id is not None
        assert case.document.matter_id is None


def test_reset_also_removes_unassigned_demo_messages(db_session: Session) -> None:
    """REGRESSION (14.09., beim Bau selbst gefunden): unzugeordnete
    Nachrichten haengen an keiner Akte. Die Aufraeumlogik loeschte aber
    ueber `matter_id` - die Waisen waeren zurueckgeblieben und haetten den
    Posteingang bei jedem erneuten Seeden weiter angefuellt, genau entgegen
    der Idempotenz-Zusage."""
    SyntheticDataGenerator(seed=21).generate_many(db_session, 6)
    assert db_session.query(Message).filter(Message.matter_id.is_(None)).count() > 0

    reset_demo_data(db_session)

    assert db_session.query(Message).count() == 0
    assert db_session.query(Document).count() == 0
    assert db_session.query(Client).count() == 0


# --- Aktentitel und Aktenzeichen (15.09.) ---


def test_matter_reference_suffix_matches_the_practice_area(db_session: Session) -> None:
    """ECHTER FUND (15.09., beim Nachmessen des Generators): das
    Sachgebiets-Kuerzel im Aktenzeichen wurde per `random.choice`
    GEWUERFELT. Reale Ausgabe vorher: "Umsatzsteuer-Nachschau
    (2023/0160-BP)", "Betriebspruefung (2022/0719-Sonst)", "Widerspruch
    Kuendigung (2025/0167-USt)". In einer Steuerkanzlei kodiert genau
    dieses Kuerzel das Sachgebiet - wuerfelt man es, ist der gesamte
    Demo-Datenbestand in sich widerspruechlich."""
    expected = {
        "Einkommensteuer": "ESt",
        "Betriebsprüfung": "BP",
        "Umsatzsteuer": "USt",
    }
    generator = SyntheticDataGenerator(seed=42)

    for scenario in SCENARIOS:
        case = generator.generate_case(db_session, scenario_key=scenario.key)
        suffix = case.matter.reference_number.rsplit("-", 1)[1]
        assert suffix == expected.get(scenario.practice_area, "Sonst"), (
            f"{scenario.key} ({scenario.practice_area}) -> "
            f"{case.matter.reference_number}"
        )


def test_matter_reference_number_still_unique_and_well_formed(db_session: Session) -> None:
    """Das feste Kuerfel darf die Eindeutigkeit nicht aushebeln - vorher
    trug der Zufall im Suffix mit zur Streuung bei."""
    generator = SyntheticDataGenerator(seed=3)
    cases = generator.generate_many(db_session, 12)

    numbers = [c.matter.reference_number for c in cases]
    assert len(set(numbers)) == len(numbers)
    for number in numbers:
        jahr, rest = number.split("/")
        laufnummer, suffix = rest.rsplit("-", 1)
        assert jahr.isdigit() and len(jahr) == 4
        assert laufnummer.isdigit()
        assert suffix in {"ESt", "BP", "USt", "Sonst"}


def test_matter_title_uses_a_readable_client_short_name(db_session: Session) -> None:
    """ECHTER FUND: `_short_name` war `full_name.split()[0].lower()`.
    Reale Aktentitel vorher: "Einspruch Steuerbescheid 2024 - musterbau",
    "Betriebspruefung 2022 - julia", "Vertragspruefung - claudia".
    Kleingeschriebene Namensfragmente sehen in der Aktenliste nicht nach
    Kanzlei aus, sondern nach kaputten Testdaten - und diese Liste ist die
    Demo-Oberflaeche fuer die Pilotkanzlei."""
    generator = SyntheticDataGenerator(seed=11)
    cases = generator.generate_many(db_session, 12)

    for case in cases:
        kurz = case.matter.title.split("–")[-1].strip()
        assert kurz, case.matter.title
        assert kurz[0].isupper(), f"Kurzname nicht grossgeschrieben: {case.matter.title}"
        assert kurz.lower() != kurz, f"Kurzname komplett klein: {case.matter.title}"


def test_short_name_drops_the_legal_form_and_keeps_company_names_intact() -> None:
    """Kanzleiuebliche Kurzbezeichnung: Firma ohne Rechtsform, Privatperson
    mit Nachname. Der erste Anlauf dieses Fixes hat aus
    "Handwerk Schmidt & Söhne" faelschlich "Söhne" gemacht - eine Firma
    ohne angehaengte Rechtsform darf nicht wie ein Personenname behandelt
    werden."""
    generator = SyntheticDataGenerator(seed=1)

    assert generator._short_name("Musterbau GmbH") == "Musterbau"
    assert generator._short_name("Beispiel Consulting AG") == "Beispiel Consulting"
    assert generator._short_name("Testhandel Weber KG") == "Testhandel Weber"
    assert generator._short_name("Handwerk Schmidt & Söhne") == "Handwerk Schmidt & Söhne"
    # Privatperson: Nachname, nicht Vorname.
    assert generator._short_name("Julia Schmidt") == "Schmidt"
    assert generator._short_name("Anna Maria Neumann") == "Neumann"
    # Einzelwort bleibt unveraendert (kein Absturz, kein leerer Titel).
    assert generator._short_name("Musterbau") == "Musterbau"


def test_document_filenames_stay_filesystem_friendly(db_session: Session) -> None:
    """SELBST EINGEBAUTE REGRESSION (15.09.), hier festgenagelt: der Fix an
    `_short_name` fuer lesbare Aktentitel schlug ungewollt auch auf
    `document_filename_template` durch, weil beide denselben Platzhalter
    benutzten. Reale Ausgabe danach: "mahnung_Handwerk Schmidt & Söhne.pdf",
    "vertragsentwurf_Beispiel Logistik.pdf". Aufgefallen beim Nachsehen der
    tatsaechlich erzeugten Dokumente, NICHT durch einen Test - deshalb
    dieser."""
    generator = SyntheticDataGenerator(seed=7)
    cases = generator.generate_many(db_session, 10)

    for case in cases:
        name = case.document.original_filename
        assert name == name.lower(), name
        assert " " not in name, name
        assert "&" not in name, name
        assert all(ch.isalnum() or ch in "._" for ch in name), name


def test_filename_slug_transliterates_umlauts_and_strips_punctuation() -> None:
    generator = SyntheticDataGenerator(seed=1)

    assert generator._filename_slug("Handwerk Schmidt & Söhne") == "handwerk_schmidt_soehne"
    assert generator._filename_slug("Beispiel Logistik") == "beispiel_logistik"
    assert generator._filename_slug("Müller") == "mueller"
    assert generator._filename_slug("Weiß") == "weiss"
    assert generator._filename_slug("Musterbau") == "musterbau"


# --- Entwuerfe und Postausgang in der Demo-Datenbasis (15.09.) ---


def test_generated_cases_include_drafts_and_outbox_entries(db_session: Session) -> None:
    """REALER FUND, der diesen Block ausgeloest hat: in der installierten
    Instanz hingen ALLE 41 Entwuerfe an "Schnellentwurf"-Akten aus dem
    Chat, KEINER an einer echten Akte, und der Postausgang war komplett
    leer. Die Gold-Workflow-Stationen am Ende (Entwurf -> anwaltliche
    Freigabe -> Postausgang) liessen sich mit Demo-Daten also weder
    vorfuehren noch ehrlich End-to-End pruefen."""
    from app.models import Draft, OutboxEntry

    generator = SyntheticDataGenerator(seed=5)
    generator.generate_many(db_session, 12)

    assert db_session.query(Draft).count() > 0
    assert db_session.query(OutboxEntry).count() > 0


def test_demo_data_covers_every_draft_and_outbox_state(db_session: Session) -> None:
    """EIGENER FEHLER IM ERSTEN ANLAUF, hier festgenagelt: die
    Entwurfsverteilung war an denselben Index gekoppelt wie die
    Nicht-Zuordnung (`i % 3 == 2`) und kollidierte mit ihr. Folge:
    "approved_pending" entstand NIE - der Postausgang enthielt
    ausschliesslich bereits versendete Eintraege, ausgerechnet der Zustand
    "wartet auf Versand" fehlte."""
    from app.models import Draft, OutboxEntry

    generator = SyntheticDataGenerator(seed=5)
    generator.generate_many(db_session, 12)

    draft_states = {s for (s,) in db_session.query(Draft.status).distinct()}
    outbox_states = {s for (s,) in db_session.query(OutboxEntry.status).distinct()}

    assert {"draft", "legal_review", "approved"} <= draft_states
    assert {"pending", "sent"} == outbox_states


def test_generated_drafts_hang_on_real_matters_with_a_version_chain(
    db_session: Session,
) -> None:
    from app.models import Draft

    generator = SyntheticDataGenerator(seed=5)
    generator.generate_many(db_session, 12)

    drafts = db_session.query(Draft).all()
    assert all(d.matter_id is not None for d in drafts)
    # Mindestens eine echte Versionskette (v2 zeigt auf v1 DERSELBEN Akte).
    chained = [d for d in drafts if d.previous_version_id is not None]
    assert chained
    for draft in chained:
        previous = db_session.get(Draft, draft.previous_version_id)
        assert previous.matter_id == draft.matter_id
        assert previous.version < draft.version


def test_unassigned_cases_get_no_draft(db_session: Session) -> None:
    """Ein fertiger Antwortentwurf zu noch nicht triagierter Post ist ein
    Zustand, den der echte Workflow nie erzeugt - gilt auch, wenn
    `include_draft=True` ausdruecklich angefordert wird (die
    `unassigned`-Sperre in `_maybe_create_draft_chain` muss unabhaengig
    vom Opt-in greifen)."""
    generator = SyntheticDataGenerator(seed=5)

    case = generator.generate_case(db_session, unassigned=True, include_draft=True)

    assert case.drafts == []
    assert case.outbox_entry is None


def test_generate_case_creates_no_draft_by_default(db_session: Session) -> None:
    """ECHTER FUND (15.09.): `generate_case()` wird auch von Tests
    aufgerufen, die zwei UNABHAENGIGE, minimale Faelle aufbauen wollen
    (siehe test_end_to_end.py::test_full_case_journey_from_synthetic_data_to_sent_outbox,
    Cross-Matter-Isolation). Ein automatisch erzeugter Entwurf beim
    zweiten Aufruf haette genau diese Isolations-Zusicherung gebrochen.
    Deshalb ist Entwurfserzeugung ein bewusstes Opt-in
    (`include_draft=True`), nicht der Default der Einzelfall-API."""
    generator = SyntheticDataGenerator(seed=5)

    case = generator.generate_case(db_session, scenario_key="einspruch_steuerbescheid")

    assert case.drafts == []
    assert case.outbox_entry is None


def test_reset_removes_drafts_and_outbox_entries(db_session: Session) -> None:
    """Ohne dies blieben Demo-Entwuerfe und Postausgangs-Eintraege als
    Waisen zurueck und wuerden die Listen bei jedem Seeden weiter
    anfuellen - derselbe Waisen-Fehler, der bei den Demo-Nachrichten
    schon einmal auftrat."""
    from app.models import Draft, OutboxEntry

    generator = SyntheticDataGenerator(seed=5)
    generator.generate_many(db_session, 12)
    assert db_session.query(Draft).count() > 0
    assert db_session.query(OutboxEntry).count() > 0

    removed = reset_demo_data(db_session)

    assert db_session.query(Draft).count() == 0
    assert db_session.query(OutboxEntry).count() == 0
    assert removed["drafts"] > 0
    assert removed["outbox_entries"] > 0


def test_generator_makes_no_ai_calls_for_drafts(db_session: Session) -> None:
    """Der Entwurfstext stammt aus einer festen Szenario-Vorlage - der
    Generator ruft KEINE KI auf (keine Kosten, deterministisch). Belegt
    ueber den Inhalt: er enthaelt die Szenario-Formulierung woertlich."""
    generator = SyntheticDataGenerator(seed=5)
    cases = generator.generate_many(db_session, 12)

    with_draft = [c for c in cases if c.drafts]
    assert with_draft
    for case in with_draft:
        scenario = next(s for s in SCENARIOS if s.key == case.scenario_key)
        assert scenario.draft_body_template is not None
        erste_zeile = scenario.draft_body_template.splitlines()[0]
        assert erste_zeile in case.drafts[0].content


# --- Kanzlei-Mustertexte fuer den Dokumentengenerator (15.09.) ---


def test_generate_shared_document_templates_creates_entries(db_session: Session) -> None:
    """ECHTER FUND (15.09., UI-Sweep gegen die echte Instanz):
    `document_templates` war in der installierten Datenbank VOLLSTAENDIG
    LEER - der Dokumentengenerator ist real und funktionsfaehig, liess
    sich aber ohne eine einzige Vorlage weder vorfuehren noch testen."""
    generator = SyntheticDataGenerator(seed=9)

    templates = generator.generate_shared_document_templates(db_session)

    assert len(templates) > 0
    assert db_session.query(DocumentTemplate).count() == len(templates)
    assert all(t.name.startswith("Mustertext:") for t in templates)


def test_generate_shared_document_templates_is_idempotent(db_session: Session) -> None:
    generator = SyntheticDataGenerator(seed=9)
    first = generator.generate_shared_document_templates(db_session)

    second = generator.generate_shared_document_templates(db_session)

    assert [t.id for t in first] == [t.id for t in second]
    assert db_session.query(DocumentTemplate).count() == len(first)


def test_generated_document_templates_use_only_supported_placeholders(
    db_session: Session,
) -> None:
    """Bewusst KEINE `[Paragraf:GESETZ:§...]`-Platzhalter - diese Vorlagen
    muessen unabhaengig davon funktionieren, welche Gesetze gerade
    importiert sind."""
    from app.document_generator.placeholders import (
        SUPPORTED_PLACEHOLDERS,
        extract_placeholders,
    )

    generator = SyntheticDataGenerator(seed=9)
    templates = generator.generate_shared_document_templates(db_session)

    for template in templates:
        for placeholder in extract_placeholders(template.content):
            name = placeholder.strip("[]")
            assert name in SUPPORTED_PLACEHOLDERS, f"{template.name}: {placeholder}"


def test_generated_document_templates_resolve_cleanly_against_a_real_matter(
    db_session: Session,
) -> None:
    """Beweis, dass die Vorlagen mit der BESTEHENDEN Generierungslogik
    zusammenspielen, ohne Sonderbehandlung: kein unaufgeloester
    Platzhalter bleibt stehen fuer eine Akte mit vollstaendigen
    Stammdaten UND einem konfigurierten Kanzlei-Profil (wie bei einer
    echten Installation - `generate_from_template` behandelt einen LEEREN
    Kanzleinamen bewusst als "nicht aufloesbar", kein stiller
    Informationsverlust, siehe app/document_generator/service.py)."""
    from app.document_generator.service import generate_from_template
    from app.models import FirmProfile

    db_session.add(FirmProfile(firm_name="Musterkanzlei"))
    db_session.commit()

    generator = SyntheticDataGenerator(seed=9)
    templates = generator.generate_shared_document_templates(db_session)
    case = generator.generate_case(db_session, scenario_key="einspruch_steuerbescheid")

    for template in templates:
        result = generate_from_template(db_session, template, case.matter, actor="test@kanzlei.test")
        assert "[Mandantenname]" not in result.document.content
        assert "[Aktenzeichen]" not in result.document.content
        assert "[Datum]" not in result.document.content
        assert result.unresolved_placeholders == []


# --- generate_complex_case (20.09., Owner-Direktive "WORKSTREAM B —
# SYNTHETISCHE KANZLEI-WELT" §10: "NICHT einfach viele Mandanten mit
# jeweils einem Dokument anlegen. Stattdessen vollstaendige synthetische
# Faelle") ---


def test_generate_complex_case_creates_a_matter_with_several_connected_documents(
    db_session: Session,
) -> None:
    generator = SyntheticDataGenerator(seed=20)
    case = generator.generate_complex_case(db_session)

    assert case.matter.client_id == case.client.id
    assert len(case.documents) == 5
    for document in case.documents:
        assert document.matter_id == case.matter.id


def test_generate_complex_case_produces_a_real_deadline_from_the_opposing_letter(
    db_session: Session,
) -> None:
    generator = SyntheticDataGenerator(seed=21)
    case = generator.generate_complex_case(db_session)

    assert case.deadline is not None
    assert case.deadline.matter_id == case.matter.id
    assert case.deadline.document_id in [d.id for d in case.documents]


def test_generate_complex_case_produces_a_real_draft_reply(
    db_session: Session,
) -> None:
    generator = SyntheticDataGenerator(seed=22)
    case = generator.generate_complex_case(db_session)

    assert case.draft.matter_id == case.matter.id
    assert case.draft.status == "draft"
    assert len(case.draft.content) > 20


def test_generate_complex_case_stays_within_the_documented_target_audience(
    db_session: Session,
) -> None:
    """PROJECT_STATE.md, "Produktidentitaet": "Zielgruppe: Steuer-/
    Wirtschaftskanzleien (nicht primaer Arbeitsrecht)." - der komplexe
    Fall muss innerhalb dieser Zielgruppe bleiben (siehe DECISIONS.md fuer
    die volle Begruendung, warum Familien-/Straf-/reines Verkehrsrecht
    bewusst NICHT ergaenzt wurden)."""
    generator = SyntheticDataGenerator(seed=23)
    case = generator.generate_complex_case(db_session)

    assert case.matter.practice_area == "Gesellschaftsrecht"
    assert case.client.practice_area == "Gesellschaftsrecht"


def test_generate_complex_case_writes_real_readable_pdf_and_docx_files(
    db_session: Session, tmp_path
) -> None:
    """Beweist echte Dokument-Varianz (Direktive §13 "Dokument-Varianz:
    PDF, DOCX, ...") UND dass beide Formate durch die ECHTE
    Extraktionslogik der Anwendung lesbar sind - nicht nur, dass Dateien
    existieren."""
    from app.documents.extraction import extract_text

    generator = SyntheticDataGenerator(seed=24, document_storage_dir=tmp_path)
    case = generator.generate_complex_case(db_session)

    suffixes = {Path(d.file_path).suffix for d in case.documents}
    assert ".pdf" in suffixes
    assert ".docx" in suffixes, "Komplexer Fall muss auch ein echtes DOCX enthalten"

    for document in case.documents:
        path = Path(document.file_path)
        assert path.exists(), f"Datei fehlt: {path}"
        result = extract_text(path, min_text_length=10)
        assert not result.needs_ocr
        assert result.text is not None and len(result.text.strip()) > 0


def test_generate_complex_case_docx_filename_and_file_path_extension_match(
    db_session: Session, tmp_path
) -> None:
    """ECHTER FUND (20.09., beim Entwurf dieser Erweiterung selbst
    gefunden): `_write_document_file` schrieb VOR dieser Aenderung immer
    eine PDF, selbst wenn `filename` auf ".docx" endete - das haette zu
    einem irrefuehrenden ".docx.pdf"-Dateinamen gefuehrt und damit zu
    einem falschen Format-Badge (`icons.file_type_badge` leitet den Typ
    aus der echten Endung von `file_path` ab, siehe dortige Warnung)."""
    generator = SyntheticDataGenerator(seed=25, document_storage_dir=tmp_path)
    case = generator.generate_complex_case(db_session)

    docx_documents = [d for d in case.documents if d.original_filename.endswith(".docx")]
    assert len(docx_documents) == 1
    docx_document = docx_documents[0]
    assert docx_document.file_path.endswith(".docx")
    assert not docx_document.file_path.endswith(".docx.pdf")


def test_generate_complex_case_is_cleaned_up_by_reset_demo_data(
    db_session: Session,
) -> None:
    generator = SyntheticDataGenerator(seed=26)
    case = generator.generate_complex_case(db_session)
    matter_id = case.matter.id
    client_id = case.client.id

    removed = reset_demo_data(db_session)

    assert removed["clients"] >= 1
    assert db_session.get(Client, client_id) is None
    assert db_session.get(Matter, matter_id) is None


def test_generate_complex_case_client_carries_demo_prefix(db_session: Session) -> None:
    generator = SyntheticDataGenerator(seed=27)
    case = generator.generate_complex_case(db_session)

    assert case.client.client_number.startswith(DEMO_CLIENT_NUMBER_PREFIX)


def test_generate_complex_case_reference_number_uses_the_correct_kuerzel(
    db_session: Session,
) -> None:
    generator = SyntheticDataGenerator(seed=28)
    case = generator.generate_complex_case(db_session)

    assert case.matter.reference_number.endswith("-GesR")


# --- generate_complex_case_erbschaftsteuer (24.09., Owner-Direktive
# "ROADMAP-ALIGNED PRODUCT COMPLETION" §10 - zweiter vollstaendiger
# mehrseitiger Fall, Rechtsgebiet "Erbschaftsteuer" bereits am 20.09. als
# zulaessige Zielgruppen-Erweiterung entschieden, siehe DECISIONS.md, aber
# nie tatsaechlich als Fall umgesetzt) ---


def test_generate_complex_case_erbschaftsteuer_creates_a_matter_with_several_connected_documents(
    db_session: Session,
) -> None:
    generator = SyntheticDataGenerator(seed=40)
    case = generator.generate_complex_case_erbschaftsteuer(db_session)

    assert case.matter.client_id == case.client.id
    assert len(case.documents) == 5
    for document in case.documents:
        assert document.matter_id == case.matter.id


def test_generate_complex_case_erbschaftsteuer_produces_a_real_deadline_from_the_bescheid(
    db_session: Session,
) -> None:
    generator = SyntheticDataGenerator(seed=41)
    case = generator.generate_complex_case_erbschaftsteuer(db_session)

    assert case.deadline is not None
    assert case.deadline.matter_id == case.matter.id
    assert case.deadline.document_id in [d.id for d in case.documents]


def test_generate_complex_case_erbschaftsteuer_produces_a_real_draft_reply(
    db_session: Session,
) -> None:
    generator = SyntheticDataGenerator(seed=42)
    case = generator.generate_complex_case_erbschaftsteuer(db_session)

    assert case.draft.matter_id == case.matter.id
    assert case.draft.status == "draft"
    assert "E i n s p r u c h" in case.draft.content
    assert len(case.draft.content) > 20


def test_generate_complex_case_erbschaftsteuer_stays_within_the_documented_target_audience(
    db_session: Session,
) -> None:
    generator = SyntheticDataGenerator(seed=43)
    case = generator.generate_complex_case_erbschaftsteuer(db_session)

    assert case.matter.practice_area == "Erbschaftsteuer"
    assert case.client.practice_area == "Erbschaftsteuer"


def test_generate_complex_case_erbschaftsteuer_writes_real_readable_pdf_and_docx_files(
    db_session: Session, tmp_path
) -> None:
    from app.documents.extraction import extract_text

    generator = SyntheticDataGenerator(seed=44, document_storage_dir=tmp_path)
    case = generator.generate_complex_case_erbschaftsteuer(db_session)

    suffixes = {Path(d.file_path).suffix for d in case.documents}
    assert ".pdf" in suffixes
    assert ".docx" in suffixes, "Komplexer Fall muss auch ein echtes DOCX enthalten"

    for document in case.documents:
        path = Path(document.file_path)
        assert path.exists(), f"Datei fehlt: {path}"
        result = extract_text(path, min_text_length=10)
        assert not result.needs_ocr
        assert result.text is not None and len(result.text.strip()) > 0


def test_generate_complex_case_erbschaftsteuer_is_cleaned_up_by_reset_demo_data(
    db_session: Session,
) -> None:
    generator = SyntheticDataGenerator(seed=45)
    case = generator.generate_complex_case_erbschaftsteuer(db_session)
    matter_id = case.matter.id
    client_id = case.client.id

    removed = reset_demo_data(db_session)

    assert removed["clients"] >= 1
    assert db_session.get(Client, client_id) is None
    assert db_session.get(Matter, matter_id) is None


def test_generate_complex_case_erbschaftsteuer_client_carries_demo_prefix(
    db_session: Session,
) -> None:
    generator = SyntheticDataGenerator(seed=46)
    case = generator.generate_complex_case_erbschaftsteuer(db_session)

    assert case.client.client_number.startswith(DEMO_CLIENT_NUMBER_PREFIX)


def test_generate_complex_case_erbschaftsteuer_reference_number_uses_the_correct_kuerzel(
    db_session: Session,
) -> None:
    generator = SyntheticDataGenerator(seed=47)
    case = generator.generate_complex_case_erbschaftsteuer(db_session)

    assert case.matter.reference_number.endswith("-ErbSt")


def test_generate_complex_case_erbschaftsteuer_client_and_erblasser_are_different_people(
    db_session: Session,
) -> None:
    """Der Erblasser darf nicht zufaellig namensgleich mit dem Mandanten
    sein - sonst waeren Aktentitel/Entwurf ("Nachlass von X") verwirrend
    und faktisch falsch (eine Person kann nicht ihr eigener Erblasser
    sein)."""
    import re

    generator = SyntheticDataGenerator(seed=48)
    case = generator.generate_complex_case_erbschaftsteuer(db_session)

    match = re.search(r"Nachlass von ([^\n]+)", case.draft.content)
    assert match is not None
    erblasser_in_draft = match.group(1)
    assert erblasser_in_draft != case.client.name
