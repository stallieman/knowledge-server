from pathlib import Path

from knowledge_server.config import Settings
from knowledge_server.maintenance import MaintenanceService
from knowledge_server.service import KnowledgeService


class FakeOllama:
    def installed_models(self) -> set[str]:
        return {"qwen3:14b", "embeddinggemma:latest"}


def test_backup_creates_consistent_copy(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "knowledge.db")
    service = KnowledgeService(settings, ollama=FakeOllama())
    service.initialize()
    service.create_library("Linux", "linux")
    maintenance = MaintenanceService(settings, service.ollama)

    backup = maintenance.backup()

    assert backup.is_file()
    assert backup.parent == tmp_path / "backups"


def test_health_accepts_ollama_latest_alias(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "knowledge.db")
    service = KnowledgeService(settings, ollama=FakeOllama())
    service.initialize()

    health = MaintenanceService(settings, service.ollama).health()

    assert health.chat_model == "ok"
    assert health.embedding_model == "ok"
