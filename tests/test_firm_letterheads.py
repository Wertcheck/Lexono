"""Mehrere Briefkoepfe pro Kanzlei (app/firm_profile/letterheads.py)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.drafting.versioning import create_manual_edit_version, create_new_draft_version
from app.firm_profile import get_firm_profile
from app.firm_profile.letterheads import (
    FIRM_LETTERHEAD_REF,
    LetterheadError,
    create_letterhead,
    default_ref,
    delete_letterhead,
    list_letterheads,
    letterhead_for_draft,
    resolve_letterhead,
    resolve_ref_for_new_draft,
    set_default,
    update_letterhead,
)
from app.models import Client, Draft, Matter
from app.models.base import Base


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _matter(db: Session) -> Matter:
    client = Client(name="Test Mandant")
    db.add(client)
    db.commit()
    matter = Matter(client_id=client.id, title="Akte")
    db.add(matter)
    db.commit()
    return matter


def _profile(db: Session) -> None:
    profile = get_firm_profile(db)
    profile.firm_name = "Kanzlei Allgemein GbR"
    profile.street = "Allgemeinweg 1"
    profile.signatory_name = "RA Allgemein"
    db.commit()


def test_existing_profile_letterhead_is_the_first_and_default_letterhead(db: Session) -> None:
    _profile(db)
    choices = list_letterheads(db)
    assert [c.ref for c in choices] == [FIRM_LETTERHEAD_REF]
    assert choices[0].name == "Kanzlei allgemein" and choices[0].is_default
    assert resolve_letterhead(db, None).firm_name == "Kanzlei Allgemein GbR"


def test_more_than_two_letterheads_can_be_stored_and_are_independent(db: Session) -> None:
    _profile(db)
    a = create_letterhead(db, name="Kanzlei Immobilienrecht", actor="t", firm_name="Kanzlei Immo", street="Immoweg 2")
    b = create_letterhead(db, name="Kanzlei Arbeitsrecht", actor="t", firm_name="Kanzlei Arbeit", street="Arbeitsweg 3")
    names = [c.name for c in list_letterheads(db)]
    assert names == ["Kanzlei allgemein", "Kanzlei Immobilienrecht", "Kanzlei Arbeitsrecht"]
    assert resolve_letterhead(db, a.id).street == "Immoweg 2"
    assert resolve_letterhead(db, b.id).street == "Arbeitsweg 3"
    assert get_firm_profile(db).street == "Allgemeinweg 1", "Profil-Briefkopf bleibt unveraendert"


def test_default_applies_only_to_new_drafts_without_explicit_choice(db: Session) -> None:
    _profile(db)
    immo = create_letterhead(db, name="Immo", actor="t", firm_name="Kanzlei Immo")
    assert resolve_ref_for_new_draft(db, None) == FIRM_LETTERHEAD_REF
    set_default(db, immo.id)
    assert default_ref(db) == immo.id
    assert resolve_ref_for_new_draft(db, None) == immo.id
    assert resolve_ref_for_new_draft(db, FIRM_LETTERHEAD_REF) == FIRM_LETTERHEAD_REF
    assert resolve_ref_for_new_draft(db, "gibt-es-nicht") == immo.id
    set_default(db, FIRM_LETTERHEAD_REF)
    assert default_ref(db) == FIRM_LETTERHEAD_REF


def test_legacy_draft_without_ref_keeps_the_profile_letterhead_even_if_default_changes(db: Session) -> None:
    _profile(db)
    matter = _matter(db)
    legacy = Draft(matter_id=matter.id, content="Alt", version=1, status="draft")
    db.add(legacy)
    db.commit()
    immo = create_letterhead(db, name="Immo", actor="t", firm_name="Kanzlei Immo")
    set_default(db, immo.id)
    assert letterhead_for_draft(db, legacy).firm_name == "Kanzlei Allgemein GbR"


def test_follow_up_versions_inherit_the_letterhead_and_a_deliberate_change_is_possible(db: Session) -> None:
    _profile(db)
    matter = _matter(db)
    immo = create_letterhead(db, name="Immo", actor="t", firm_name="Kanzlei Immo")
    v1 = create_new_draft_version(
        db, matter_id=matter.id, content="v1", actor="t", event_type="draft_created", letterhead_ref=immo.id
    )
    v2 = create_new_draft_version(
        db, matter_id=matter.id, content="v2", previous_draft=v1, actor="t", event_type="draft_version_created"
    )
    v3 = create_manual_edit_version(db, previous_draft=v2, new_content="v3", actor="t")
    assert v1.letterhead_ref == v2.letterhead_ref == v3.letterhead_ref == immo.id
    v4 = create_new_draft_version(
        db, matter_id=matter.id, content="v3", previous_draft=v3, actor="t",
        event_type="draft_version_created", letterhead_ref=FIRM_LETTERHEAD_REF,
    )
    assert v4.letterhead_ref == FIRM_LETTERHEAD_REF
    assert v3.letterhead_ref == immo.id, "aeltere Version bleibt unveraendert"


def test_names_must_be_unique_and_non_empty(db: Session) -> None:
    _profile(db)
    create_letterhead(db, name="Immo", actor="t", firm_name="X")
    with pytest.raises(LetterheadError):
        create_letterhead(db, name="immo", actor="t", firm_name="Y")
    with pytest.raises(LetterheadError):
        create_letterhead(db, name="Kanzlei ALLGEMEIN", actor="t", firm_name="Y")
    with pytest.raises(LetterheadError):
        create_letterhead(db, name="  ", actor="t", firm_name="Y")
    with pytest.raises(LetterheadError):
        create_letterhead(db, name="Ohne Namen", actor="t", firm_name="")


def test_profile_letterhead_can_be_renamed_and_edited_through_the_same_function(db: Session) -> None:
    _profile(db)
    update_letterhead(db, FIRM_LETTERHEAD_REF, name="Hauptkanzlei", actor="t", firm_name="Neue Firma", street="Neu 5")
    profile = get_firm_profile(db)
    assert profile.letterhead_name == "Hauptkanzlei" and profile.firm_name == "Neue Firma" and profile.street == "Neu 5"


def test_deleting_is_blocked_for_the_profile_letterhead_and_for_used_letterheads(db: Session) -> None:
    _profile(db)
    matter = _matter(db)
    immo = create_letterhead(db, name="Immo", actor="t", firm_name="Kanzlei Immo")
    create_new_draft_version(
        db, matter_id=matter.id, content="x", actor="t", event_type="draft_created", letterhead_ref=immo.id
    )
    with pytest.raises(LetterheadError):
        delete_letterhead(db, FIRM_LETTERHEAD_REF)
    with pytest.raises(LetterheadError):
        delete_letterhead(db, immo.id)
    other = create_letterhead(db, name="Unbenutzt", actor="t", firm_name="Z")
    set_default(db, other.id)
    delete_letterhead(db, other.id)
    assert default_ref(db) == FIRM_LETTERHEAD_REF
