"""Draft – Antwortentwurf.

Versionierung ist hier zentral: JEDE neue Version ist eine EIGENE Zeile,
verkettet über `previous_version_id` - eine bestehende Draft-Zeile wird
NIE überschrieben (weder Inhalt noch Version), egal ob die neue Version
durch KI-Neugenerierung (`app/drafting/service.py`, ggf. angestoßen durch
eine `AttorneyInstruction`) oder durch manuelle Bearbeitung entsteht
(`app/feedback/service.py` bei "approved_with_edits", oder eine
zukünftige eigenständige Bearbeitungsaktion im Dashboard). Die zentrale,
einzige Stelle, die tatsächlich neue Draft-Zeilen anlegt, ist
`app/drafting/versioning.py: create_new_draft_version` - siehe dort für
die Begründung dieser Zentralisierung.

`version` bleibt eine fortlaufende Ganzzahl innerhalb einer Versionskette
(1, 2, 3, ...) - `previous_version_id` ist die eigentliche, verlässliche
Verkettung; `version` ist die für Menschen lesbare Nummer entlang dieser
Kette. `status` bildet den Freigabeweg ab, ersetzt aber nicht die
vollständige Workflow-State-Machine aus Prompt 20/ARCHITECTURE.md §6.

WICHTIG zur Historie: eine ÄLTERE Version wird nach dem Entstehen einer
neueren NICHT nachträglich verändert (auch ihr `status` bleibt
eingefroren) - nur die jeweils aktuelle/neueste Zeile einer Kette erhält
Status-Updates ohne Versionssprung (z. B. eine reine Freigabe ohne
inhaltliche Änderung, siehe DraftFeedbackService).

ERWEITERUNG (04.10., Owner-Direktive "Dokumenten-Editor produktionsnah
implementieren" - siehe .agentic/DECISIONS.md fuer die volle Herleitung,
INSBESONDERE die bewusste Abweichung von der fruaheren Entscheidung
"Briefkopf-/Signatur-Vorschau statt Rich-Text-Editor" vom 20.09.):

- `subject`/`recipient`: strukturierte Felder (Betreff/Empfaenger) fuer
  den neuen Editor - NULL bei jeder Zeile, die (noch) ueber den alten
  Weg (Schriftsatz-Generator/manuelle Bearbeitung ohne Editor) entstand.
- `content_format`: "text" (Standard - `content` ist Klartext, siehe
  app/export/letterhead.py und die PDF-/DOCX-Export-Services, UNVERAENDERT
  fuer jede bestehende Zeile) oder "html" (nur vom neuen Rich-Text-Editor
  erzeugt, serverseitig sanitisiert - siehe app/drafting/editor_service.py).
- `last_autosaved_at`: Zeitstempel DERSELBEN aktuellen Zeile (siehe "ohne
  Versionssprung" oben) - Autosave ueberschreibt `content`/`subject`/
  `recipient` dieser einen Zeile nur, solange `status == "draft"` ist
  (noch keine Freigabe-/Ablehnungsentscheidung getroffen wurde).
- `status == "ai_suggestion_discarded"`: ein KI-Bearbeitungsvorschlag
  (siehe app/drafting/editor_service.py) wird IMMER ueber die bestehende
  `create_new_draft_version`-Kette als neue, eingefrorene Zeile erzeugt
  (NIE eine zweite, parallele Persistenz-Logik) - "Verwerfen" aendert
  NUR noch diesen einen Status-Wert auf der bereits erzeugten Zeile
  (ohne Versionssprung, siehe oben), damit `_load_version_chain`
  (app/web/drafts_router.py) sie nicht als aktive Kettenspitze
  auswaehlt. Die Zeile selbst bleibt unveraendert in der Historie
  erhalten (Nachvollziehbarkeit, CLAUDE.md) - nichts wird geloescht.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Draft(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "drafts"

    matter_id: Mapped[str] = mapped_column(
        ForeignKey("matters.id"), nullable=False, index=True
    )
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # draft / legal_review / approved / rejected - siehe Hinweis oben.
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    # Self-FK: bildet die Versionskette. None = allererste Version (v1)
    # einer Entwurfslinie. Siehe Moduldocstring.
    previous_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("drafts.id"), nullable=True, index=True
    )

    # Siehe Moduldocstring "ERWEITERUNG (04.10.)".
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_format: Mapped[str] = mapped_column(String(16), default="text", nullable=False)
    last_autosaved_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    # Briefkopf dieses Entwurfs: "firm" (Briefkopf des Kanzlei-Profils) oder `Letterhead.id`; wird
    # beim Erstellen gesetzt und von Folgeversionen uebernommen, damit eine Ueberarbeitung nie
    # einen anderen Briefkopf einsetzt. NULL (aeltere Entwuerfe) = "firm".
    letterhead_ref: Mapped[str | None] = mapped_column(String(40), nullable=True)

    matter: Mapped["Matter"] = relationship(back_populates="drafts")
    previous_version: Mapped["Draft | None"] = relationship(
        remote_side="Draft.id", foreign_keys=[previous_version_id]
    )
    quality_ratings: Mapped[list["DraftQualityRating"]] = relationship(
        back_populates="draft", cascade="all, delete-orphan"
    )
    # 26.09., Owner-Direktive "DOCUMENT WORKSPACE / SCHRIFTSATZ
    # PRODUCT-COMPLETION": Dokumente, die aus einem Export DIESER
    # Entwurfsversion in der Akte gespeichert wurden - siehe
    # app/models/document.py::generated_from_draft_id.
    generated_documents: Mapped[list["Document"]] = relationship(
        back_populates="generated_from_draft"
    )
