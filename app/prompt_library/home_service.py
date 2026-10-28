"""Verwaltung der Standard-Prompts für die Chat-Startseite (Modal "Standard-
Prompts verwalten").

Der Client hält beim Bearbeiten eine Arbeitskopie (neu anlegen, ändern,
löschen, aktivieren, sortieren) und sendet sie mit "Speichern" als EINEN
Zustand. `save_state` validiert den gesamten Zustand und schreibt ihn in einer
Transaktion - bei einem Fehler wird nichts geändert. Auf der Startseite
erscheinen ausschließlich Prompts mit `show_on_home`, in der Reihenfolge von
`sort_order`; höchstens `HOME_PROMPT_LIMIT` dürfen aktiviert sein."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import AuditEvent, PromptTemplate
from app.prompt_library.home import (
    CONTENT_MAX,
    DESCRIPTION_MAX,
    HOME_PROMPT_LIMIT,
    NAME_MAX,
)
from app.prompt_library.icons import DEFAULT_ICON, PROMPT_ICONS


@dataclass
class PromptStateError(Exception):
    """Validierungsfehler; `errors` = Liste von {index, field, message} (index None = global)."""

    errors: list[dict[str, object]] = field(default_factory=list)


def _ordered(db: Session) -> list[PromptTemplate]:
    return (
        db.query(PromptTemplate)
        .order_by(PromptTemplate.sort_order, PromptTemplate.name)
        .all()
    )


def list_prompts(db: Session) -> list[PromptTemplate]:
    return _ordered(db)


def home_prompts(db: Session) -> list[PromptTemplate]:
    """Genau die aktivierten Prompts in gespeicherter Reihenfolge (nie automatisch die obersten vier)."""
    return [p for p in _ordered(db) if p.show_on_home][:HOME_PROMPT_LIMIT]


def serialize(prompt: PromptTemplate) -> dict[str, object]:
    return {
        "id": prompt.id,
        "name": prompt.name,
        "description": prompt.description or "",
        "content": prompt.content,
        "icon": prompt.icon or DEFAULT_ICON,
        "show_on_home": bool(prompt.show_on_home),
        "attach_on_use": bool(prompt.attach_on_use),
        "version": prompt.version,
    }


def _clean(entry: dict[str, object], index: int, errors: list[dict[str, object]]) -> dict[str, object]:
    name = str(entry.get("name") or "").strip()
    description = str(entry.get("description") or "").strip()
    content = str(entry.get("content") or "")
    icon = str(entry.get("icon") or DEFAULT_ICON)

    def err(field_name: str, message: str) -> None:
        errors.append({"index": index, "field": field_name, "message": message})

    if not name:
        err("name", "Bitte geben Sie einen Titel ein.")
    elif len(name) > NAME_MAX:
        err("name", f"Der Titel darf höchstens {NAME_MAX} Zeichen lang sein.")
    if not description:
        err("description", "Bitte geben Sie eine Beschreibung ein.")
    elif len(description) > DESCRIPTION_MAX:
        err("description", f"Die Beschreibung darf höchstens {DESCRIPTION_MAX} Zeichen lang sein.")
    if not content.strip():
        err("content", "Bitte geben Sie eine Prompt-Anweisung ein.")
    elif len(content) > CONTENT_MAX:
        err("content", f"Die Anweisung darf höchstens {CONTENT_MAX} Zeichen lang sein.")
    if icon not in PROMPT_ICONS:
        icon = DEFAULT_ICON
    return {
        "name": name,
        "description": description,
        "content": content,
        "icon": icon,
        "show_on_home": bool(entry.get("show_on_home")),
        "attach_on_use": bool(entry.get("attach_on_use")),
    }


def save_state(
    db: Session, payload: dict[str, object], *, actor: str
) -> list[PromptTemplate]:
    raw_entries = payload.get("prompts")
    raw_deleted = payload.get("deleted") or []
    if not isinstance(raw_entries, list) or not isinstance(raw_deleted, list):
        raise PromptStateError([{"index": None, "field": "prompts", "message": "Ungültige Daten."}])

    errors: list[dict[str, object]] = []
    existing = {p.id: p for p in _ordered(db)}
    deleted_ids = {str(i) for i in raw_deleted if str(i) in existing}

    cleaned: list[tuple[PromptTemplate | None, dict[str, object]]] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw_entries):
        if not isinstance(entry, dict):
            errors.append({"index": index, "field": "prompts", "message": "Ungültiger Eintrag."})
            continue
        entry_id = entry.get("id")
        prompt = None
        if entry_id:
            prompt = existing.get(str(entry_id))
            if prompt is None or str(entry_id) in deleted_ids or str(entry_id) in seen:
                errors.append({"index": index, "field": "id", "message": "Der Prompt existiert nicht mehr. Bitte laden Sie die Liste neu."})
                continue
            seen.add(str(entry_id))
        cleaned.append((prompt, _clean(entry, index, errors)))

    active = sum(1 for _, data in cleaned if data["show_on_home"])
    if active > HOME_PROMPT_LIMIT:
        errors.append({
            "index": None,
            "field": "show_on_home",
            "message": (
                f"Es können höchstens {HOME_PROMPT_LIMIT} Prompts auf der Startseite angezeigt werden. "
                "Bitte deaktivieren Sie zuerst einen anderen Prompt."
            ),
        })
    if errors:
        raise PromptStateError(errors)

    for prompt_id in deleted_ids:
        prompt = existing[prompt_id]
        db.add(AuditEvent(
            entity_type="PromptTemplate", entity_id=prompt.id, event_type="prompt_template_deleted",
            actor=actor, details=f"Name: {prompt.name}",
        ))
        db.delete(prompt)

    layout_changed = bool(deleted_ids)
    ordered: list[PromptTemplate] = []
    for prompt, data in cleaned:
        if prompt is None:
            prompt = PromptTemplate(
                name=data["name"], description=data["description"], content=data["content"],
                icon=data["icon"], show_on_home=data["show_on_home"], attach_on_use=data["attach_on_use"],
                version=1, created_by_actor=actor, updated_by_actor=actor, sort_order=0,
            )
            db.add(prompt)
            db.flush()
            db.add(AuditEvent(
                entity_type="PromptTemplate", entity_id=prompt.id, event_type="prompt_template_created",
                actor=actor, details=f"Name: {prompt.name}",
            ))
            layout_changed = True
        else:
            content_changed = (
                prompt.name != data["name"]
                or (prompt.description or "") != data["description"]
                or prompt.content != data["content"]
            )
            if content_changed:
                prompt.name = data["name"]
                prompt.description = data["description"]
                prompt.content = data["content"]
                prompt.version += 1
                prompt.updated_by_actor = actor
                db.add(AuditEvent(
                    entity_type="PromptTemplate", entity_id=prompt.id, event_type="prompt_template_updated",
                    actor=actor, details=f"Neue Version: {prompt.version}",
                ))
            if (prompt.icon != data["icon"] or bool(prompt.show_on_home) != data["show_on_home"]
                    or bool(prompt.attach_on_use) != data["attach_on_use"]):
                layout_changed = True
            prompt.icon = data["icon"]
            prompt.show_on_home = data["show_on_home"]
            prompt.attach_on_use = data["attach_on_use"]
        ordered.append(prompt)

    # nicht mitgesendete (z. B. parallel angelegte) Prompts bleiben erhalten, hinter der gesendeten Liste
    sent = {p.id for p in ordered}
    rest = [p for p in existing.values() if p.id not in sent and p.id not in deleted_ids]
    for position, prompt in enumerate([*ordered, *rest]):
        if prompt.sort_order != position:
            layout_changed = True
        prompt.sort_order = position
    if layout_changed:
        db.add(AuditEvent(
            entity_type="PromptTemplate", entity_id="home-layout", event_type="prompt_home_layout_updated",
            actor=actor,
            details="Startseite: " + (", ".join(p.name for p in ordered if p.show_on_home) or "keine"),
        ))
    db.commit()
    return _ordered(db)
