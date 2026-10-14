"""Q (§36): lokale Pruefung juristischer Fundstellen in Modellantworten. Unterscheidet aktenbelegt / lokal (amtlich oder
kuratiert) vorhanden / in freigegebener Quelle / nicht pruefbar / nicht gefunden - und prueft NIE die inhaltliche Stuetzung
(wird ausdruecklich als ungeprueft gekennzeichnet). Kein Modellaufruf: das Modell bestaetigt sich nicht selbst. Nur
synthetische Daten."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.drafting.citation_check import check_citations, citation_review_notes, extract_citations
from app.models import Client, Document, KnowledgeItem, Law, LawSection, Matter, Source
from app.models.base import Base


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def matter(db):
    client = Client(name="Beispielmandantin")
    matter = Matter(client=client, title="Beispielakte")
    db.add_all([client, matter])
    db.flush()
    db.add(Document(matter_id=matter.id, original_filename="klage.txt", file_path="x",
                    extracted_text="Die Klagefrist nach § 4 KSchG ist gewahrt; vgl. BAG, Urt. v. 01.02.2020 - 2 AZR 111/19."))
    # amtlich importiertes Gesetz (doknr gesetzt) und kuratierte Auswahl
    db.add(Law(code="BGB", title="Bürgerliches Gesetzbuch"))
    db.add(Law(code="ZPO", title="Zivilprozessordnung"))
    db.flush()
    for number in ("§ 535", "§ 536", "§ 543"):
        db.add(LawSection(law_code="BGB", section_number=number, title="t", text_content="Wortlaut", last_updated=date(2026, 1, 1),
                          source_name="Gesetze im Internet", doknr=f"BJNR{number[-3:]}"))
    db.add(LawSection(law_code="ZPO", section_number="§ 91", title="t", text_content="Wortlaut", last_updated=date(2026, 1, 1)))
    db.add(Source(title="BGH, Urt. v. 12.10.2022 - VIII ZR 1/21", source_type="urteil", reference="VIII ZR 1/21",
                  approval_level="freigegeben"))
    db.add(Source(title="BGH, Urt. v. 03.03.2021 - XII ZR 5/20", source_type="urteil", reference="XII ZR 5/20",
                  approval_level="entwurf"))
    db.commit()
    return matter


def test_extracts_norms_and_decisions() -> None:
    found = extract_citations("Gemäß § 535 Abs. 1 S. 2 BGB und §§ 91, 92 ZPO sowie Art. 3 GG; vgl. BGH, Urt. v. 12.10.2022 "
                              "- VIII ZR 1/21 und BVerfG, Beschl. v. 01.02.2021 - 1 BvR 123/20.")
    norms = {(c.law_code, c.section) for c in found if c.kind == "norm"}
    assert {("BGB", "535"), ("ZPO", "91"), ("ZPO", "92"), ("GG", "3")} <= norms
    assert {c.file_number for c in found if c.kind == "entscheidung"} == {"VIII ZR 1/21", "1 BvR 123/20"}


def test_classification_distinguishes_evidence_levels(db, matter) -> None:
    text = ("Der Anspruch folgt aus § 536 BGB und § 9999 BGB; Kosten nach § 91 ZPO, § 92 ZPO; Frist nach § 4 KSchG und "
            "§ 12 StVG. Vgl. BGH, Urt. v. 12.10.2022 - VIII ZR 1/21; BAG - 2 AZR 111/19; BGH - XII ZR 5/20; BGH - I ZR 999/19.")
    by_raw = {c.display: c.status for c in check_citations(text, matter.id, db)}
    assert by_raw["§ 536 BGB"] == "norm_amtlich_vorhanden"
    assert by_raw["§ 9999 BGB"] == "norm_amtlich_nicht_gefunden"
    assert by_raw["§ 91 ZPO"] == "norm_kuratiert_vorhanden"
    assert by_raw["§ 92 ZPO"] == "norm_nicht_pruefbar"  # nur kuratierte Auswahl - Fehlen beweist nichts
    assert by_raw["§ 4 KSchG"] == "aktenbelegt"
    assert by_raw["§ 12 StVG"] == "norm_nicht_pruefbar"  # Gesetz nicht installiert
    assert by_raw["VIII ZR 1/21"] == "freigegebene_quelle"
    assert by_raw["2 AZR 111/19"] == "aktenbelegt"
    assert by_raw["XII ZR 5/20"] == "nicht_verifiziert"  # Quelle nur im Entwurf, nicht freigegeben
    assert by_raw["I ZR 999/19"] == "nicht_verifiziert"


def test_review_notes_mark_unverified_and_never_claim_support(db, matter) -> None:
    notes = citation_review_notes(check_citations("Anspruch aus § 9999 BGB; vgl. BGH - I ZR 999/19; § 536 BGB.", matter.id, db))
    text = "\n".join(notes)
    assert "§ 9999 BGB" in text and "nicht gefunden" in text.lower()
    assert "I ZR 999/19" in text and "nicht verifiziert" in text.lower()
    assert "nicht geprüft" in text.lower()  # inhaltliche Stuetzung ausdruecklich ungeprueft
    assert "§ 536 BGB" not in text or "nicht" not in text.split("§ 536 BGB")[0][-40:]


def test_no_citations_no_notes(db, matter) -> None:
    assert citation_review_notes(check_citations("Sehr geehrte Damen und Herren, anbei die Unterlagen.", matter.id, db)) == []


def test_deactivated_law_is_not_used_as_evidence(db, matter) -> None:
    db.query(Law).filter_by(code="BGB").one().is_active = False
    db.commit()
    by_raw = {c.display: c.status for c in check_citations("§ 536 BGB", matter.id, db)}
    assert by_raw["§ 536 BGB"] == "norm_nicht_pruefbar"


def test_approved_knowledge_item_counts_as_approved_source(db, matter) -> None:
    db.add(KnowledgeItem(title="Kanzleiwissen Miete", content="Leitentscheidung: BGH, Urt. v. 05.05.2021 - VIII ZR 77/20.",
                         approval_status="approved"))
    db.commit()
    by_raw = {c.display: c.status for c in check_citations("vgl. BGH - VIII ZR 77/20", matter.id, db)}
    assert by_raw["VIII ZR 77/20"] == "freigegebene_quelle"



# --- Review §36 (unabhaengig): bestaetigte Befunde als Regressionstests ---------------------------------------------


def _matter_with_text(db, text: str):
    client = Client(name="Zweitmandant")
    matter = Matter(client=client, title="Zweitakte")
    db.add_all([client, matter])
    db.flush()
    db.add(Document(matter_id=matter.id, original_filename="d.txt", file_path="x", extracted_text=text))
    db.commit()
    return matter


def test_file_evidence_requires_the_same_citation_structure(db) -> None:
    matter = _matter_with_text(db, "Nach § 4 Abs. 1 der Satzung und das KSchG gilt ... § 3 der Rechnung über Kakao.")
    status = {c.display: c.status for c in check_citations("§ 4 KSchG; § 3 AO", matter.id, db)}
    assert status["§ 4 KSchG"] != "aktenbelegt" and status["§ 3 AO"] != "aktenbelegt"


def test_decision_numbers_match_with_boundaries(db) -> None:
    matter = _matter_with_text(db, "Vgl. BGH, Urt. v. 12.10.2022 - VIII ZR 1/21.")
    db.add(Source(title="BGH - VIII ZR 7/20", source_type="urteil", reference="VIII ZR 7/20", approval_level="freigegeben"))
    db.commit()
    status = {c.display: c.status for c in check_citations("BGH - I ZR 1/21; BGH - I ZR 7/20", matter.id, db)}
    assert status["I ZR 1/21"] == "nicht_verifiziert" and status["I ZR 7/20"] == "nicht_verifiziert"


def test_more_citation_forms_are_recognised() -> None:
    found = {c.display for c in extract_citations(
        "§§ 535 ff. BGB; Art. 6 Abs. 1 lit. f DSGVO; § 280 Abs. 1, 3 BGB; § 823 I BGB; BGH NJW 2020, 123; "
        "EuGH, Urt. v. 16.07.2020 - C-311/18; BAG - 2 AZR 12/2021; § 13 SGB 5"
    )}
    assert {"§ 535 BGB", "Art. 6 DSGVO", "§ 280 BGB", "§ 823 BGB", "NJW 2020, 123", "C-311/18", "2 AZR 12/2021"} <= found
    assert any(c.law_code == "SGBV" for c in extract_citations("§ 13 SGB 5"))


def test_company_form_is_not_a_court() -> None:
    assert not [c for c in extract_citations("Muster AG, Vertrag 2 K 33/24 vom 01.02.2024") if c.kind == "entscheidung"]
    assert [c for c in extract_citations("AG München, Urt. v. 01.02.2024 - 2 C 33/24") if c.kind == "entscheidung"]


def test_paragraph_is_not_matched_against_an_article(db, matter) -> None:
    db.add(Law(code="EGBGB", title="Einführungsgesetz"))
    db.flush()
    db.add(LawSection(law_code="EGBGB", section_number="Art 229 § 5", title="t", text_content="w", last_updated=date(2026, 1, 1),
                      source_name="Gesetze im Internet", doknr="BJNR0001"))
    db.commit()
    status = {c.display: c.status for c in check_citations("§ 229 EGBGB", matter.id, db)}
    assert status["§ 229 EGBGB"] == "norm_amtlich_nicht_gefunden"


def test_law_code_is_compared_case_insensitively(db, matter) -> None:
    db.add(Law(code="StGB", title="Strafgesetzbuch"))
    db.flush()
    db.add(LawSection(law_code="StGB", section_number="§ 263", title="Betrug", text_content="w", last_updated=date(2026, 1, 1)))
    db.commit()
    status = {c.display: c.status for c in check_citations("§ 263 StGB", matter.id, db)}
    assert status["§ 263 StGB"] == "norm_kuratiert_vorhanden"


def test_notes_say_found_not_proven_and_list_file_only_hits(db, matter) -> None:
    notes = "\n".join(citation_review_notes(check_citations("§ 4 KSchG und § 536 BGB", matter.id, db)))
    assert "nur in Aktendokumenten gefunden" in notes and "§ 4 KSchG" in notes
    assert "belegt" not in notes
    assert "nicht erkannte Zitierformen wurden nicht geprüft" in notes



def test_exported_draft_documents_never_count_as_file_evidence(db, matter) -> None:
    """§37 (Verifikation installierter Build): jeder Export legt das Schreiben als Aktendokument ab
    (`generated_from_draft_id`). Die erfundenen Fundstellen einer frueheren Modellantwort galten danach als
    "aktenbelegt" - das Modell haette sich selbst bestaetigt. Von Lexono erzeugte Dokumente zaehlen nie als Beleg."""
    from app.models import Draft

    draft = Draft(matter_id=matter.id, content="Anspruch aus § 9999 BGB; vgl. BGH - I ZR 999/19.")
    db.add(draft)
    db.flush()
    db.add(Document(matter_id=matter.id, original_filename="Entwurf_v1.docx", file_path="x", generated_from_draft_id=draft.id,
                    extracted_text="Anspruch aus § 9999 BGB; vgl. BGH - I ZR 999/19."))
    db.commit()
    status = {c.display: c.status for c in check_citations("§ 9999 BGB; BGH - I ZR 999/19", matter.id, db)}
    assert status["§ 9999 BGB"] != "aktenbelegt" and status["I ZR 999/19"] == "nicht_verifiziert"



def test_knowledge_promoted_from_a_draft_is_not_evidence(db, matter) -> None:
    """Review §37: ein aus einem Entwurf uebernommener und freigegebener Kanzleiwissen-Eintrag bestaetigte sonst die
    erfundenen Rechtsprechungszitate der frueheren Modellantwort."""
    db.add(KnowledgeItem(title="Aus Entwurf", content="Vgl. BGH - I ZR 999/19.", approval_status="approved",
                         source="Übernommen aus Entwurfs-Feedback (draft_feedback_id=x)"))
    db.commit()
    status = {c.display: c.status for c in check_citations("BGH - I ZR 999/19", matter.id, db)}
    assert status["I ZR 999/19"] == "nicht_verifiziert"
