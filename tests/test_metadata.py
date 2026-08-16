from pathlib import Path

from knowledge_server.config import Settings
from knowledge_server.service import KnowledgeService


class FakeOllama:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def unload_embedding_model(self) -> None:
        pass

    def generate_answer(self, prompt: str) -> str:
        return (
            '{"title":"Lokale RAG","summary":"Notities over lokale RAG.",'
            '"tags":["rag","ollama"],"library_slug":"ai-engineering"}'
        )


def test_model_metadata_requires_explicit_approval(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "knowledge.db")
    service = KnowledgeService(settings, ollama=FakeOllama())
    service.initialize()
    service.create_library("Persoonlijk", "persoonlijk")
    service.create_library("AI Engineering", "ai-engineering")
    source = tmp_path / "rag.md"
    source.write_text("# RAG\nLokale embeddings met Ollama.", encoding="utf-8")
    service.ingest("persoonlijk", source)
    document = service.list_documents()[0]

    suggestion = service.suggest_document_metadata(document.id)

    unchanged = service.list_documents()[0]
    assert suggestion.status == "pending"
    assert unchanged.library_slug == "persoonlijk"
    assert unchanged.title is None

    service.approve_metadata_suggestion(suggestion.id)
    approved = service.list_documents()[0]
    assert approved.library_slug == "ai-engineering"
    assert approved.title == "Lokale RAG"
    assert approved.tags == ["rag", "ollama"]
