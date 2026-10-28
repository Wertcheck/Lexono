"""PromptTemplateService – CRUD für editierbare Kanzlei-Prompt-Vorlagen
(Schritt 3, Teil 2)."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import AuditEvent, PromptTemplate
from app.prompt_library.schema import PromptTemplateInput


class PromptTemplateService:
    def list_templates(self, db: Session) -> list[PromptTemplate]:
        return db.query(PromptTemplate).order_by(PromptTemplate.name).all()

    def create_template(
        self, db: Session, data: PromptTemplateInput, *, actor: str
    ) -> PromptTemplate:
        next_order = (db.query(func.max(PromptTemplate.sort_order)).scalar() or 0) + 1
        template = PromptTemplate(
            sort_order=next_order,
            name=data.name,
            description=data.description,
            content=data.content,
            version=1,
            created_by_actor=actor,
            updated_by_actor=actor,
        )
        db.add(template)
        db.flush()
        db.add(
            AuditEvent(
                entity_type="PromptTemplate",
                entity_id=template.id,
                event_type="prompt_template_created",
                actor=actor,
                details=f"Name: {data.name}",
            )
        )
        db.commit()
        db.refresh(template)
        return template

    def update_template(
        self, db: Session, template: PromptTemplate, data: PromptTemplateInput, *, actor: str
    ) -> PromptTemplate:
        template.name = data.name
        template.description = data.description
        template.content = data.content
        template.version += 1
        template.updated_by_actor = actor
        db.add(
            AuditEvent(
                entity_type="PromptTemplate",
                entity_id=template.id,
                event_type="prompt_template_updated",
                actor=actor,
                details=f"Neue Version: {template.version}",
            )
        )
        db.commit()
        db.refresh(template)
        return template

    def delete_template(self, db: Session, template: PromptTemplate, *, actor: str) -> None:
        db.add(
            AuditEvent(
                entity_type="PromptTemplate",
                entity_id=template.id,
                event_type="prompt_template_deleted",
                actor=actor,
                details=f"Name: {template.name}",
            )
        )
        db.delete(template)
        db.commit()
