"""service_factory – baut die "echten" Drafting-/Feedback-/AttorneyInstruction-
Services aus der zentralen Konfiguration, für die Dashboard-Routen
(app/web/drafts_router.py).

Bisher (vor Prompt 23) wurden diese Services ausschließlich in Tests
zusammengebaut - keine Anwendungsschicht hat sie tatsächlich für echte
Nutzung instanziiert. Diese Factory schließt die Lücke, DAMIT die neue
"Änderungen übernehmen & neu formulieren"-Aktion im Dashboard funktioniert.

Der teure Teil (Embedding-Modell) wird über `lru_cache` genau EINMAL pro
Prozess geladen, nicht bei jeder Anfrage neu.

Seit Prompt 34: die eigentliche Provider-AUSWAHL (welcher Claude-Provider
gebaut wird) liegt nicht mehr hier, sondern in
`app/ai_providers/factory.py` (ModelProvider-Abstraktion) - diese Datei
ruft nur noch `build_writing_provider`/`build_review_provider` auf.
"""

from __future__ import annotations

from functools import lru_cache

from app.ai_providers.factory import (
    ProviderNotConfiguredError,
    build_local_llm_provider,
    build_review_provider,
    build_writing_provider,
)
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.attorney_instructions.service import AttorneyInstructionService
from app.config import get_settings
from app.drafting.editor_service import EditorService
from app.drafting.service import DraftingService
from app.feedback.service import DraftFeedbackService
from app.privacy.gateway import ClaudePrivacyGateway
from app.research.service import LegalResearchService
from app.review.engine import ReviewEngine
from app.search.embeddings import FastEmbedProvider
from app.search.global_search_service import GlobalSearchService
from app.search.service import DocumentSearchService

# Rückwärtskompatibler Alias (Prompt 34 verallgemeinert die vorher hier
# lokal definierte `WritingProviderNotConfiguredError` zu
# `ProviderNotConfiguredError` in app/ai_providers/factory.py - der alte
# Name bleibt als Alias importierbar, damit bestehender Code
# (app/web/drafts_router.py, mehrere Testdateien) unverändert funktioniert).
WritingProviderNotConfiguredError = ProviderNotConfiguredError


@lru_cache(maxsize=1)
def get_document_search_service() -> DocumentSearchService:
    """Singleton pro Prozess - das Embedding-Modell wird beim ersten
    tatsächlichen `embed()`-Aufruf lazy geladen (siehe FastEmbedProvider),
    aber die Provider-Instanz selbst muss nicht wiederholt neu gebaut
    werden."""
    settings = get_settings()
    return DocumentSearchService(FastEmbedProvider(settings.embedding_model_name))


def get_drafting_service() -> DraftingService:
    """Baut einen funktionsfähigen `DraftingService` mit dem konfigurierten
    Schreib-Provider (Prompt 34, siehe app/ai_providers/factory.py). Wirft
    `ProviderNotConfiguredError`, wenn kein `ANTHROPIC_API_KEY` hinterlegt
    ist - der Router zeigt dann eine freundliche Meldung statt eines
    Serverfehlers.

    §65: `local_llm_provider` wird NUR gebaut, wenn
    `settings.local_ai_enabled=True` (Standard: aus) - siehe
    `app/ai_providers/local_llm_provider.py` für die Begründung, warum das
    dann ein PFLICHT-, nicht ein Best-Effort-Schritt ist."""
    settings = get_settings()
    search_service = get_document_search_service()
    research_service = LegalResearchService(
        search_service,
        min_score_for_sufficient=settings.research_min_score_for_sufficient,
    )

    writing_provider = build_writing_provider(settings)
    local_llm_provider = build_local_llm_provider(settings)

    return DraftingService(
        RuleBasedLocalAIProvider(search_service),
        research_service,
        search_service,
        ClaudePrivacyGateway(),
        writing_provider,
        model_name=settings.claude_model_name,
        local_llm_provider=local_llm_provider,
    )


def get_feedback_service() -> DraftFeedbackService:
    return DraftFeedbackService()


def get_global_search_service() -> GlobalSearchService:
    """Universal Command Bar (Strg+K/⌘K, siehe app/web/global_search_router.py) -
    nutzt fuer die "Extern"-Kategorie (Rechtsquellen) denselben bereits
    gecachten `DocumentSearchService`-Singleton wie die uebrige
    Rechtsrecherche (kein zweites Embedding-Modell im Speicher)."""
    return GlobalSearchService(get_document_search_service())


def get_review_engine() -> ReviewEngine:
    """Baut eine funktionsfähige `ReviewEngine` mit dem konfigurierten
    Review-Provider (Prompt 34) - wirft `ProviderNotConfiguredError`
    (derselbe Fehlertyp wie bei der Entwurfsproduktion), wenn kein
    `ANTHROPIC_API_KEY` hinterlegt ist."""
    settings = get_settings()
    search_service = get_document_search_service()
    research_service = LegalResearchService(
        search_service,
        min_score_for_sufficient=settings.research_min_score_for_sufficient,
    )
    review_provider = build_review_provider(settings)

    return ReviewEngine(
        RuleBasedLocalAIProvider(search_service),
        research_service,
        ClaudePrivacyGateway(),
        review_provider,
        model_name=settings.claude_model_name,
    )


def get_attorney_instruction_service() -> AttorneyInstructionService:
    """Für Aktionen, die tatsächlich eine Neugenerierung auslösen können
    (`apply_instruction`) - baut den vollen `DraftingService` inkl.
    Prüfung auf konfigurierten Provider."""
    return AttorneyInstructionService(get_drafting_service())


def get_attorney_instruction_service_for_saving_only() -> AttorneyInstructionService:
    """Für die reine "Anmerkung speichern"-Aktion (`create_instruction`) -
    baut BEWUSST keinen `DraftingService` (kein Embedding-Modell, keine
    Provider-Konfigurationsprüfung). Speichern einer Anmerkung darf nicht
    daran scheitern, dass (noch) kein Provider konfiguriert ist - das
    wird erst relevant, wenn tatsächlich neu generiert werden soll."""
    return AttorneyInstructionService(drafting_service=None)


def get_editor_service() -> EditorService:
    """Für Autosave/Discard/"Als Vorlage speichern" im Dokumenten-Editor
    (04.10.) - baut BEWUSST analog zu
    `get_attorney_instruction_service_for_saving_only` KEINEN vollen
    `DraftingService` (kein Embedding-Modell, keine Provider-Prüfung).
    Autosave darf nicht daran scheitern, dass (noch) kein Claude-API-Key
    konfiguriert ist - siehe `get_editor_service_for_ai_edit` für den
    einzigen Pfad, der tatsächlich einen Claude-Aufruf auslöst."""
    return EditorService(AttorneyInstructionService(drafting_service=None))


def get_editor_service_for_ai_edit() -> EditorService:
    """Für `EditorService.apply_ai_suggestion` (löst einen
    kostenpflichtigen Claude-Aufruf aus) - baut den vollen
    `AttorneyInstructionService` inkl. Provider-Prüfung, identisch zu
    `get_attorney_instruction_service`."""
    return EditorService(get_attorney_instruction_service())
