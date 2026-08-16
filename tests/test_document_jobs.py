from pathlib import Path

from knowledge_server.config import Settings
from knowledge_server.document_jobs import DocumentJobManager, suggest_library
from knowledge_server.service import KnowledgeService


class FakeOllama:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def unload_embedding_model(self) -> None:
        pass


def test_suggest_library_uses_explainable_keywords(tmp_path: Path) -> None:
    document = tmp_path / "ollama-rag-notes.md"
    document.write_text(
        "# RAG\nEmbeddings gebruiken met een lokaal LLM via Ollama.",
        encoding="utf-8",
    )

    suggestion = suggest_library(
        document,
        {"ai-engineering", "linux", "persoonlijk"},
    )

    assert suggestion == "ai-engineering"


def test_suggest_library_falls_back_to_personal(tmp_path: Path) -> None:
    document = tmp_path / "notities.md"
    document.write_text("Losse notities zonder vaktermen.", encoding="utf-8")

    suggestion = suggest_library(document, {"linux", "persoonlijk"})

    assert suggestion == "persoonlijk"


def test_document_job_is_stored_and_indexed(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "knowledge.db")
    service = KnowledgeService(settings, ollama=FakeOllama())
    service.initialize()
    service.create_library("Linux", "linux")
    staged = tmp_path / "incoming.md"
    staged.write_text("# Linux\nSystemd beheert services.", encoding="utf-8")
    manager = DocumentJobManager(settings, service)

    created = manager.create_job_from_path(
        "linux-notes.md",
        staged,
        library_slug=None,
    )
    manager._executor.shutdown(wait=True)  # noqa: SLF001
    completed = manager.get_job(created.id)

    assert completed.status == "completed"
    assert completed.library_slug == "linux"
    assert completed.suggested_library_slug == "linux"
    assert completed.stored_path is not None
    assert Path(completed.stored_path).is_file()
    assert service.list_libraries()[0].document_count == 1
