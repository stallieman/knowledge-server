import asyncio
import json
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from knowledge_server.security import (
    AccessAudit,
    TailnetSecurityMiddleware,
    is_trusted_client,
)


def test_trusted_clients_are_limited_to_loopback_and_tailnet() -> None:
    assert is_trusted_client("127.0.0.1")
    assert is_trusted_client("::1")
    assert is_trusted_client("100.80.32.88")
    assert is_trusted_client("fd7a:115c:a1e0::1")
    assert not is_trusted_client("192.168.1.10")
    assert not is_trusted_client("8.8.8.8")
    assert not is_trusted_client("invalid")


def test_tailnet_identity_must_match_allowed_user(tmp_path: Path) -> None:
    app = FastAPI()
    app.add_middleware(
        TailnetSecurityMiddleware,
        audit=AccessAudit(tmp_path / "audit.db"),
        allowed_user="stallieman@github",
    )

    @app.get("/")
    async def index() -> JSONResponse:
        return JSONResponse({"ok": True})

    whois = Mock(
        returncode=0,
        stdout=json.dumps({"UserProfile": {"LoginName": "stallieman@github"}}),
    )
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(
            app=app,
            client=("100.80.32.88", 50000),
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="https://server.test"
        ) as client:
            return await client.get("/")

    with patch("knowledge_server.security.subprocess.run", return_value=whois):
        response = asyncio.run(request())

    assert response.status_code == 200


def test_tailnet_identity_rejects_different_user(tmp_path: Path) -> None:
    app = FastAPI()
    app.add_middleware(
        TailnetSecurityMiddleware,
        audit=AccessAudit(tmp_path / "audit.db"),
        allowed_user="stallieman@github",
    )

    @app.get("/")
    async def index() -> JSONResponse:
        return JSONResponse({"ok": True})

    whois = Mock(
        returncode=0,
        stdout=json.dumps({"UserProfile": {"LoginName": "someone@example.com"}}),
    )
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(
            app=app,
            client=("100.80.32.88", 50000),
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="https://server.test"
        ) as client:
            return await client.get("/")

    with patch("knowledge_server.security.subprocess.run", return_value=whois):
        response = asyncio.run(request())

    assert response.status_code == 403
