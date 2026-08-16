import asyncio
from pathlib import Path

import httpx

from knowledge_server.config import Settings
from knowledge_server.document_jobs import DocumentJob
from knowledge_server.service import KnowledgeService
from knowledge_server.video_jobs import VideoJob
from knowledge_server.web import create_app


class FakeOllama:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def generate_answer(self, prompt: str) -> str:
        return "Lokaal antwoord."

    def unload_embedding_model(self) -> None:
        pass


class FakeVideoJobs:
    def __init__(self) -> None:
        self.jobs: list[VideoJob] = []

    def list_jobs(self) -> list[VideoJob]:
        return self.jobs

    def create_job_from_path(
        self,
        filename: str,
        staged_path: Path,
        *,
        language: str | None,
        analysis_type: str,
    ) -> VideoJob:
        assert staged_path.read_bytes() == b"video"
        job = VideoJob(
            id="job-1",
            filename=filename,
            status="queued",
            progress="Wacht op verwerking.",
            language=language,
            analysis_type=analysis_type,
            created_at="2026-08-16T00:00:00+00:00",
            updated_at="2026-08-16T00:00:00+00:00",
        )
        self.jobs.append(job)
        return job


class FakeDocumentJobs:
    def __init__(self) -> None:
        self.jobs: list[DocumentJob] = []

    def list_jobs(self) -> list[DocumentJob]:
        return self.jobs

    def create_job_from_path(
        self,
        filename: str,
        staged_path: Path,
        *,
        library_slug: str | None,
    ) -> DocumentJob:
        assert staged_path.read_bytes() == b"document"
        job = DocumentJob(
            id="document-1",
            filename=filename,
            status="queued",
            progress="Wacht op indexering.",
            library_slug=library_slug or "linux",
            suggested_library_slug="linux",
            created_at="2026-08-16T00:00:00+00:00",
            updated_at="2026-08-16T00:00:00+00:00",
        )
        self.jobs.append(job)
        return job


async def get_json(app, path: str) -> tuple[int, object]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.get(path)
    return response.status_code, response.json()


async def post_json(app, path: str, payload: dict) -> tuple[int, object]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(path, json=payload)
    return response.status_code, response.json()


def test_web_health_and_library_endpoint(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "web.db")
    service = KnowledgeService(settings, ollama=FakeOllama())
    service.initialize()
    service.create_library("Linux")
    app = create_app(settings, service=service)

    health_status, health_payload = asyncio.run(get_json(app, "/api/health"))
    libraries_status, libraries_payload = asyncio.run(get_json(app, "/api/libraries"))

    assert health_status == 200
    assert health_payload == {"status": "ok"}
    assert libraries_status == 200
    assert libraries_payload == [
        {
            "slug": "linux",
            "name": "Linux",
            "document_count": 0,
            "chunk_count": 0,
        }
    ]


def test_web_uses_model_fallback_without_libraries(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "web.db")
    service = KnowledgeService(settings, ollama=FakeOllama())
    service.initialize()
    app = create_app(settings, service=service)

    status_code, _ = asyncio.run(
        post_json(
            app,
            "/api/ask",
            {"question": "Test?", "libraries": []},
        )
    )

    assert status_code == 200


def test_web_uploads_and_lists_video_jobs(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "web.db")
    service = KnowledgeService(settings, ollama=FakeOllama())
    service.initialize()
    jobs = FakeVideoJobs()
    app = create_app(settings, service=service, video_jobs=jobs)

    async def upload() -> tuple[int, dict]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/api/video-jobs",
                params={
                    "filename": "meeting.mp4",
                    "language": "nl",
                    "analysis_type": "meeting",
                },
                content=b"video",
                headers={"Content-Type": "video/mp4"},
            )
        return response.status_code, response.json()

    status_code, payload = asyncio.run(upload())
    list_status, listed = asyncio.run(get_json(app, "/api/video-jobs"))

    assert status_code == 202
    assert payload["filename"] == "meeting.mp4"
    assert payload["language"] == "nl"
    assert list_status == 200
    assert listed == [payload]


def test_web_uploads_and_lists_document_jobs(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "web.db")
    service = KnowledgeService(settings, ollama=FakeOllama())
    service.initialize()
    jobs = FakeDocumentJobs()
    app = create_app(settings, service=service, document_jobs=jobs)

    async def upload() -> tuple[int, dict]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/api/document-jobs",
                params={"filename": "linux-notes.md", "library": "linux"},
                content=b"document",
            )
        return response.status_code, response.json()

    status_code, payload = asyncio.run(upload())
    list_status, listed = asyncio.run(get_json(app, "/api/document-jobs"))

    assert status_code == 202
    assert payload["library_slug"] == "linux"
    assert payload["suggested_library_slug"] == "linux"
    assert list_status == 200
    assert listed == [payload]
