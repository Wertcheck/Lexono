"""DocumentSearchService – kombiniert Metadatenfilter, Volltext- und
semantische Suche.

WICHTIGSTE REGEL (siehe Moduldocstring in __init__.py): Es gibt bewusst
KEINE Methode für eine aktenübergreifende Dokumentensuche. Jede
Dokumentensuche verlangt zwingend eine `matter_id` und filtert auf
Datenbankebene (`Document.matter_id == matter_id`) - nicht erst
nachträglich in Python. Die Wissensbasis-Suche (`search_knowledge_base`)
ist bewusst getrennt und liefert ausschließlich freigegebene
KnowledgeItems, nie Mandantendokumente.

Indizierung (`index_document`/`index_knowledge_item`) und Suche sind
getrennte Methoden - Indizierung geschieht, sobald Text verfügbar ist
(nach Prompt 06), Suche kann jederzeit danach erfolgen.
"""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy.orm import Session

from app.models import Document, Embedding, KnowledgeItem, Source
from app.search.embeddings import EmbeddingProvider
from app.search.schema import SearchResult
from app.search.utils import build_snippet, cosine_similarity

# Ab welcher Cosine-Similarity ein semantischer Treffer ueberhaupt als
# relevant gilt (verhindert, dass praktisch jedes Dokument mit Score>0
# zurueckgegeben wird).
_SEMANTIC_RELEVANCE_THRESHOLD = 0.3


class DocumentSearchService:
    def __init__(self, embedding_provider: EmbeddingProvider) -> None:
        self.embedding_provider = embedding_provider

    # --- Indizierung ---------------------------------------------------

    def index_document(self, document: Document, db: Session) -> None:
        if not document.extracted_text or not document.extracted_text.strip():
            return
        self._upsert_embedding(
            entity_type="Document",
            entity_id=document.id,
            text=document.extracted_text,
            db=db,
        )

    def index_knowledge_item(self, item: KnowledgeItem, db: Session) -> None:
        # Nur freigegebenes Wissen wird ueberhaupt indiziert - sicherer
        # Default, konsistent mit Konzept §5/§12/§13 (Freigabepflicht).
        if item.approval_status != "approved":
            return
        self._upsert_embedding(
            entity_type="KnowledgeItem", entity_id=item.id, text=item.content, db=db
        )

    def index_source(self, source: Source, db: Session) -> None:
        # Nur freigegebene Rechtsquellen werden indiziert (Prompt 14/15) -
        # analog zur Freigabepflicht bei KnowledgeItem.
        if source.approval_level != "freigegeben":
            return
        searchable_text = "\n".join(
            part for part in (source.title, source.reference, source.notes) if part
        )
        if not searchable_text.strip():
            return
        self._upsert_embedding(
            entity_type="Source", entity_id=source.id, text=searchable_text, db=db
        )

    def _upsert_embedding(
        self, *, entity_type: str, entity_id: str, text: str, db: Session
    ) -> None:
        vector = self.embedding_provider.embed(text)
        vector_json = json.dumps(vector)
        existing = (
            db.query(Embedding)
            .filter_by(entity_type=entity_type, entity_id=entity_id)
            .first()
        )
        if existing:
            existing.vector_json = vector_json
            existing.model_name = self.embedding_provider.model_name
        else:
            db.add(
                Embedding(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    model_name=self.embedding_provider.model_name,
                    vector_json=vector_json,
                )
            )
        db.commit()

    # --- Suche -----------------------------------------------------------

    def search_within_matter(
        self,
        matter_id: str,
        query: str,
        db: Session,
        *,
        document_type: str | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """Durchsucht AUSSCHLIESSLICH Dokumente der angegebenen Akte.

        `document_type` ist ein optionaler exakter Metadatenfilter
        (`Document.classified_type`, siehe Prompt 08).
        """
        db_query = db.query(Document).filter(Document.matter_id == matter_id)
        if document_type is not None:
            db_query = db_query.filter(Document.classified_type == document_type)
        candidate_documents = db_query.all()

        # Nur einbetten, wenn es ueberhaupt Kandidaten gibt, gegen die
        # bewertet werden koennte - bei einer leeren Kandidatenliste bleibt
        # das Ergebnis so oder so leer (siehe Schleife unten), das reale
        # Modell-Laden von FastEmbed (~1,7 GB, P1-Speicherdruck-Befund,
        # OPEN_ISSUES.md) waere sonst reine Verschwendung, u. a. bei jeder
        # neu angelegten Akte ohne vorhandene Dokumente.
        query_vector = (
            self.embedding_provider.embed(query)
            if query.strip() and candidate_documents
            else None
        )

        results: list[SearchResult] = []
        for document in candidate_documents:
            result = self._score_document(document, query, query_vector, db)
            if result is not None:
                results.append(result)

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def search_knowledge_base(
        self, query: str, db: Session, *, limit: int = 10
    ) -> list[SearchResult]:
        """Durchsucht AUSSCHLIESSLICH freigegebenes Kanzleiwissen
        (`approval_status == "approved"`) - nie Mandantendokumente.

        Erweiterung Prompt 12: berücksichtigt zusätzlich den
        Gültigkeitsbereich (`valid_from`/`valid_until`) - ein abgelaufener
        oder noch nicht gültiger Textbaustein wird trotz Freigabe nicht
        zurückgegeben."""
        today = date.today()
        items = (
            db.query(KnowledgeItem)
            .filter(KnowledgeItem.approval_status == "approved")
            .filter(
                (KnowledgeItem.valid_from.is_(None))
                | (KnowledgeItem.valid_from <= today)
            )
            .filter(
                (KnowledgeItem.valid_until.is_(None))
                | (KnowledgeItem.valid_until >= today)
            )
            .all()
        )
        # Siehe Kommentar in search_within_matter - dieselbe Begruendung.
        query_vector = (
            self.embedding_provider.embed(query) if query.strip() and items else None
        )

        results: list[SearchResult] = []
        for item in items:
            result = self._score_knowledge_item(item, query, query_vector, db)
            if result is not None:
                results.append(result)

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def search_sources(
        self,
        query: str,
        db: Session,
        *,
        source_type: str | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """Durchsucht AUSSCHLIESSLICH freigegebene Rechtsquellen
        (`approval_level == "freigegeben"`) - berücksichtigt wie bei
        `search_knowledge_base` den Gültigkeitsbereich."""
        today = date.today()
        db_query = db.query(Source).filter(Source.approval_level == "freigegeben")
        if source_type is not None:
            db_query = db_query.filter(Source.source_type == source_type)
        db_query = db_query.filter(
            (Source.valid_from.is_(None)) | (Source.valid_from <= today)
        ).filter((Source.valid_until.is_(None)) | (Source.valid_until >= today))
        sources = db_query.all()

        # Siehe Kommentar in search_within_matter - dieselbe Begruendung.
        query_vector = (
            self.embedding_provider.embed(query) if query.strip() and sources else None
        )

        results: list[SearchResult] = []
        for source in sources:
            result = self._score_source(source, query, query_vector, db)
            if result is not None:
                results.append(result)

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    # --- interne Bewertungslogik -----------------------------------------

    def _score_document(
        self,
        document: Document,
        query: str,
        query_vector: list[float] | None,
        db: Session,
    ) -> SearchResult | None:
        if not document.extracted_text:
            return None

        fulltext_match = bool(query.strip()) and (
            query.lower() in document.extracted_text.lower()
        )
        semantic_score = self._semantic_score(
            "Document", document.id, query_vector, db
        )

        if not fulltext_match and semantic_score < _SEMANTIC_RELEVANCE_THRESHOLD:
            return None

        match_type, score = self._combine(fulltext_match, semantic_score)
        return SearchResult(
            entity_type="Document",
            entity_id=document.id,
            matter_id=document.matter_id,
            snippet=build_snippet(document.extracted_text, query),
            score=score,
            match_type=match_type,
        )

    def _score_knowledge_item(
        self,
        item: KnowledgeItem,
        query: str,
        query_vector: list[float] | None,
        db: Session,
    ) -> SearchResult | None:
        fulltext_match = bool(query.strip()) and (query.lower() in item.content.lower())
        semantic_score = self._semantic_score("KnowledgeItem", item.id, query_vector, db)

        if not fulltext_match and semantic_score < _SEMANTIC_RELEVANCE_THRESHOLD:
            return None

        match_type, score = self._combine(fulltext_match, semantic_score)
        return SearchResult(
            entity_type="KnowledgeItem",
            entity_id=item.id,
            matter_id=None,
            snippet=build_snippet(item.content, query),
            score=score,
            match_type=match_type,
        )

    def _score_source(
        self,
        source: Source,
        query: str,
        query_vector: list[float] | None,
        db: Session,
    ) -> SearchResult | None:
        searchable_text = "\n".join(
            part for part in (source.title, source.reference, source.notes) if part
        )
        if not searchable_text.strip():
            return None

        fulltext_match = bool(query.strip()) and (
            query.lower() in searchable_text.lower()
        )
        semantic_score = self._semantic_score("Source", source.id, query_vector, db)

        if not fulltext_match and semantic_score < _SEMANTIC_RELEVANCE_THRESHOLD:
            return None

        match_type, score = self._combine(fulltext_match, semantic_score)
        return SearchResult(
            entity_type="Source",
            entity_id=source.id,
            matter_id=None,
            snippet=build_snippet(searchable_text, query),
            score=score,
            match_type=match_type,
        )

    def _semantic_score(
        self,
        entity_type: str,
        entity_id: str,
        query_vector: list[float] | None,
        db: Session,
    ) -> float:
        if query_vector is None:
            return 0.0
        embedding_row = (
            db.query(Embedding)
            .filter_by(entity_type=entity_type, entity_id=entity_id)
            .first()
        )
        if embedding_row is None:
            return 0.0
        stored_vector = json.loads(embedding_row.vector_json)
        return cosine_similarity(query_vector, stored_vector)

    @staticmethod
    def _combine(fulltext_match: bool, semantic_score: float) -> tuple[str, float]:
        is_semantic_relevant = semantic_score >= _SEMANTIC_RELEVANCE_THRESHOLD
        if fulltext_match and is_semantic_relevant:
            return "hybrid", max(semantic_score, 0.9)
        if fulltext_match:
            return "fulltext", 0.8
        return "semantic", semantic_score
