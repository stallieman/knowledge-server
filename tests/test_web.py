import asyncio
from pathlib import Path

import httpx

from knowledge_server.config import Settings
from knowledge_server.service import KnowledgeService
from knowledge_server.web import create_app


class FakeOllama:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def generate_answer(self, prompt: str) -> str:
        return "Lokaal antwoord."

    def unload_embedding_model(self) -> None:
        pass


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
