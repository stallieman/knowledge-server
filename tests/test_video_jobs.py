import sqlite3
from pathlib import Path

from knowledge_server.config import Settings
from knowledge_server.service import KnowledgeService
from knowledge_server.video_jobs import VideoJobManager


class FakeOllama:
    pass


def test_processing_job_is_marked_failed_after_restart(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "knowledge.db")
    service = KnowledgeService(settings, ollama=FakeOllama())
    service.initialize()
    manager = VideoJobManager(settings, service)
    now = "2026-08-16T00:00:00+00:00"
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            """
            INSERT INTO video_jobs(
                id, filename, input_path, status, progress, language,
                analysis_type, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "interrupted",
                "meeting.mp4",
                str(tmp_path / "meeting.mp4"),
                "processing",
                "Transcribing",
                "nl",
                "meeting",
                now,
                now,
            ),
        )

    restarted_manager = VideoJobManager(settings, service)
    job = restarted_manager.get_job("interrupted")

    assert job.status == "failed"
    assert job.error == "De server is herstart tijdens de verwerking."
    manager._executor.shutdown(wait=False)  # noqa: SLF001
    restarted_manager._executor.shutdown(wait=False)  # noqa: SLF001
