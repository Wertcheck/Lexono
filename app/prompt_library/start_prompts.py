"""Standard-Prompts der Chat-Startseite (28.10.2026, "Standard-Prompts verwalten").

Dieselbe Tabelle wie die Kanzlei-Prompt-Bibliothek (`PromptTemplate`) - kein zweites Datenmodell. Beliebig viele
Vorlagen, hoechstens `MAX_START_PROMPTS` gleichzeitig auf der Startseite. Die Verwaltung im Chat sammelt alle
Aenderungen (Anlegen, Bearbeiten, Loeschen, Aktivieren, Sortieren) und speichert sie mit EINEM Aufruf
(`apply_management_save`): alles wird zuerst vollstaendig geprueft und erst dann in einer Transaktion geschrieben -
ein Fehler laesst den gespeicherten Stand unveraendert.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import AuditEvent, PromptTemplate

MAX_START_PROMPTS = 4
NAME_MAX, DESCRIPTION_MAX, CONTENT_MAX = 100, 200, 2000

# Icon-Schluessel -> (Icon-Makro in _icons.html, Farbton der Karte)
ICONS: dict[str, tuple[str, str]] = {
    "document": ("document", "green"),
    "pencil": ("pencil", "blue"),
    "list": ("list_bulleted", "purple"),
    "folder": ("folder", "orange"),
    "scale": ("scale", "red"),
    "bulb": ("bulb", "neutral"),
    "book": ("book", "blue"),
    "envelope": ("envelope", "green"),
    "calendar": ("calendar_check", "orange"),
    "search": ("search", "neutral"),
    "sparkles": ("sparkles", "purple"),
    "chat": ("chat", "green"),
}
DEFAULT_ICON = "document"
START_ACTIONS = {"prefill", "prefill_attach", "open_matter"}

# Die bisher fest verdrahteten Startkarten (chat.html bis fde5c71) - identisch zur Migration schritt3_039.
DEFAULT_START_PROMPTS = [
    ("Dokument analysieren", "Inhalte verstehen und auswerten",
     "Bitte analysiere das angehängte Dokument und fasse die wichtigsten Punkte zusammen.", "document",
     "prefill_attach"),
    ("Schriftsatz erstellen", "z. B. an Mandanten, Gericht oder Behörde",
     "Bitte erstelle einen Schriftsatz-Entwurf zu folgendem Sachverhalt: ", "pencil", "prefill"),
    ("Zusammenfassen", "Wesentliche Inhalte auf den Punkt bringen",
     "Bitte fasse das angehängte Dokument in wenigen Sätzen zusammen.", "list", "prefill_attach"),
    ("Akte öffnen", "Vorhandene Dokumente und Kontext nutzen",
     "Öffnet die Aktenauswahl: Der Chat beginnt mit der gewählten Akte und kann deren Dokumente und Kontext nutzen.",
     "folder", "open_matter"),
]


class PromptManagementError(ValueError):
    """Verstaendliche Fehlermeldung fuer die Verwaltung; `key` benennt die betroffene Zeile (falls bekannt)."""

    def __init__(self, message: str, key: str | None = None, field: str | None = None) -> None:
        super().__init__(message)
        self.key = key
        self.field = field


@dataclass
class StartCard:
    id: str
    title: str
    description: str
    content: str
    icon_macro: str
    tint: str
    action: str


def icon_info(icon: str | None) -> tuple[str, str]:
    return ICONS.get(icon or DEFAULT_ICON, ICONS[DEFAULT_ICON])


def seed_default_start_prompts(db: Session, *, actor: str = "system") -> None:
    """Legt die bisherigen Startkarten an, falls es noch KEINE Vorlage gibt (Neuinstallation/Tests ohne Migration).
    Bestehende Daten werden nie veraendert."""
    if db.query(PromptTemplate).count():
        return
    for position, (name, description, content, icon, action) in enumerate(DEFAULT_START_PROMPTS):
        db.add(PromptTemplate(name=name, description=description, content=content, version=1,
                              created_by_actor=actor, updated_by_actor=actor, show_on_start=True,
                              sort_order=position, icon=icon, start_action=action))
    db.commit()


def ordered_templates(db: Session) -> list[PromptTemplate]:
    """Verwaltungsreihenfolge: aktivierte zuerst (in ihrer Reihenfolge), dann die uebrigen."""
    return (db.query(PromptTemplate)
            .order_by(PromptTemplate.show_on_start.desc(), PromptTemplate.sort_order, PromptTemplate.name)
            .all())


def start_cards(db: Session) -> list[StartCard]:
    """Ausschliesslich aktivierte Vorlagen, in gespeicherter Reihenfolge, hoechstens vier."""
    rows = (db.query(PromptTemplate).filter(PromptTemplate.show_on_start.is_(True))
            .order_by(PromptTemplate.sort_order, PromptTemplate.name).limit(MAX_START_PROMPTS).all())
    cards = []
    for row in rows:
        macro, tint = icon_info(row.icon)
        action = row.start_action if row.start_action in START_ACTIONS else "prefill"
        cards.append(StartCard(id=row.id, title=row.name, description=row.description or "", content=row.content,
                               icon_macro=macro, tint=tint, action=action))
    return cards


def serialize(row: PromptTemplate) -> dict:
    return {"id": row.id, "name": row.name, "description": row.description or "", "content": row.content,
            "icon": row.icon or DEFAULT_ICON, "show_on_start": bool(row.show_on_start), "action": row.start_action,
            "version": row.version}


def _text(item: dict, field: str) -> str:
    value = item.get(field)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PromptManagementError("Ungültige Eingabe.", item.get("key") or item.get("id"), field)
    return value


def apply_management_save(db: Session, payload: dict, *, actor: str) -> list[PromptTemplate]:
    """Speichert den kompletten Verwaltungsstand.

    payload = {"items": [{"id"|"key", "name", "description", "content", "icon", "show_on_start"}, ...] in der
    gewuenschten Reihenfolge, "deleted": [id, ...]}. Nicht genannte vorhandene Vorlagen (z. B. parallel von einer
    anderen Person angelegt) bleiben unveraendert und reihen sich hinten ein. Erst nach vollstaendiger Pruefung wird
    geschrieben; jeder Fehler laesst den gespeicherten Stand unveraendert."""
    items = payload.get("items")
    deleted = payload.get("deleted") or []
    if not isinstance(items, list) or not isinstance(deleted, list) or len(items) > 500:
        raise PromptManagementError("Die Änderungen konnten nicht gelesen werden.")
    existing = {row.id: row for row in db.query(PromptTemplate).all()}
    deleted_ids = set()
    for id_ in deleted:
        if not isinstance(id_, str) or id_ not in existing:
            raise PromptManagementError("Ein zu löschender Prompt existiert nicht mehr. Bitte neu laden.")
        deleted_ids.add(id_)

    planned: list[tuple[PromptTemplate | None, dict]] = []
    seen: set[str] = set()
    active = 0
    for item in items:
        if not isinstance(item, dict):
            raise PromptManagementError("Die Änderungen konnten nicht gelesen werden.")
        key = item.get("id") or item.get("key")
        row = None
        if item.get("id"):
            row = existing.get(item["id"])
            if row is None or item["id"] in deleted_ids or item["id"] in seen:
                raise PromptManagementError("Ein Prompt existiert nicht mehr. Bitte neu laden.", key)
            seen.add(item["id"])
        name, description, content = (_text(item, "name").strip(), _text(item, "description").strip(),
                                      _text(item, "content"))
        icon = item.get("icon") or DEFAULT_ICON
        if icon not in ICONS:
            raise PromptManagementError("Unbekanntes Icon.", key, "icon")
        changed = {"name": row is None or name != row.name,
                   "description": row is None or description != (row.description or ""),
                   "content": row is None or content != row.content}
        # Pflichtfelder und Laengen: neu angelegt oder geaendert (bestehende, laengere Texte bleiben unangetastet)
        if not name:
            raise PromptManagementError("Bitte einen Titel eingeben.", key, "name")
        if changed["name"] and len(name) > NAME_MAX:
            raise PromptManagementError(f"Der Titel darf höchstens {NAME_MAX} Zeichen haben.", key, "name")
        if changed["description"] and not description:
            raise PromptManagementError("Bitte eine Beschreibung eingeben.", key, "description")
        if changed["description"] and len(description) > DESCRIPTION_MAX:
            raise PromptManagementError(f"Die Beschreibung darf höchstens {DESCRIPTION_MAX} Zeichen haben.", key,
                                        "description")
        if not content.strip():
            raise PromptManagementError("Bitte eine Prompt-Anweisung eingeben.", key, "content")
        if changed["content"] and len(content) > CONTENT_MAX:
            raise PromptManagementError(f"Die Prompt-Anweisung darf höchstens {CONTENT_MAX} Zeichen haben.", key,
                                        "content")
        show = bool(item.get("show_on_start"))
        active += show
        planned.append((row, {"name": name, "description": description, "content": content, "icon": icon,
                              "show_on_start": show, "changed": changed}))
    untouched = [row for id_, row in existing.items() if id_ not in seen and id_ not in deleted_ids]
    active += sum(1 for row in untouched if row.show_on_start)
    if active > MAX_START_PROMPTS:
        raise PromptManagementError(
            f"Es können höchstens {MAX_START_PROMPTS} Prompts auf der Startseite angezeigt werden. Bitte zuerst "
            "einen anderen Prompt deaktivieren.", None, "show_on_start")

    # --- schreiben (alles geprueft) ---
    for id_ in deleted_ids:
        row = existing[id_]
        db.add(AuditEvent(entity_type="PromptTemplate", entity_id=row.id, event_type="prompt_template_deleted",
                          actor=actor, details=f"Name: {row.name}"))
        db.delete(row)
    position = 0
    for row, data in planned:
        if row is None:
            row = PromptTemplate(name=data["name"], description=data["description"], content=data["content"],
                                 version=1, created_by_actor=actor, updated_by_actor=actor, start_action="prefill")
            db.add(row)
            db.flush()
            db.add(AuditEvent(entity_type="PromptTemplate", entity_id=row.id, event_type="prompt_template_created",
                              actor=actor, details=f"Name: {data['name']}"))
        elif any(data["changed"].values()):
            row.name, row.description, row.content = data["name"], data["description"], data["content"]
            row.version += 1
            row.updated_by_actor = actor
            db.add(AuditEvent(entity_type="PromptTemplate", entity_id=row.id, event_type="prompt_template_updated",
                              actor=actor, details=f"Neue Version: {row.version}"))
        if row.show_on_start != data["show_on_start"] or row.sort_order != position or row.icon != data["icon"]:
            row.updated_by_actor = actor
        row.icon = data["icon"]
        row.show_on_start = data["show_on_start"]
        row.sort_order = position
        position += 1
    for row in sorted(untouched, key=lambda r: (r.sort_order, r.name)):
        row.sort_order = position
        position += 1
    active_names = [d["name"] for _r, d in planned if d["show_on_start"]]
    db.add(AuditEvent(entity_type="PromptTemplate", entity_id="start-page", event_type="prompt_start_page_updated",
                      actor=actor, details="Startseite: " + (", ".join(active_names) or "(keine)")))
    db.commit()
    return ordered_templates(db)
