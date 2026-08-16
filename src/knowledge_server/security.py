from __future__ import annotations

import ipaddress
import json
import sqlite3
import subprocess
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

from starlette.responses import JSONResponse

TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def is_trusted_client(value: str | None) -> bool:
    if not value:
        return False
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_loopback or address in TAILNET_V4 or address in TAILNET_V6


class AccessAudit:
    def __init__(self, database_path) -> None:
        self.database_path = database_path
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS access_audit (
                    id INTEGER PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    client_ip TEXT NOT NULL,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    outcome TEXT NOT NULL
                )
                """
            )

    def record(
        self,
        client_ip: str,
        method: str,
        path: str,
        outcome: str,
    ) -> None:
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO access_audit(
                    occurred_at, client_ip, method, path, outcome
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(UTC).isoformat(),
                    client_ip,
                    method,
                    path[:500],
                    outcome,
                ),
            )
            connection.execute(
                """
                DELETE FROM access_audit WHERE id NOT IN (
                    SELECT id FROM access_audit ORDER BY id DESC LIMIT 5000
                )
                """
            )

    def recent(self, limit: int = 50) -> list[dict]:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM access_audit ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


class TailnetSecurityMiddleware:
    """Enforce localhost/tailnet clients and same-origin browser mutations."""

    def __init__(self, app, *, audit: AccessAudit, allowed_user: str) -> None:
        self.app = app
        self.audit = audit
        self.allowed_user = allowed_user
        self._identity_cache: dict[str, tuple[float, str | None]] = {}

    def _tailscale_identity(self, client_ip: str) -> str | None:
        cached = self._identity_cache.get(client_ip)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        try:
            result = subprocess.run(
                ["tailscale", "whois", "--json", client_ip],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            identity = str(json.loads(result.stdout)["UserProfile"]["LoginName"])
        except (OSError, subprocess.SubprocessError, KeyError, json.JSONDecodeError):
            identity = None
        self._identity_cache[client_ip] = (time.monotonic() + 60, identity)
        return identity

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        client_ip = scope.get("client", ("", 0))[0]
        method = scope["method"]
        path = scope["path"]
        headers = {
            key.decode("latin-1").casefold(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        if not is_trusted_client(client_ip):
            self.audit.record(
                client_ip or "unknown", method, path, "denied-ip"
            )
            response = JSONResponse(
                {"detail": "Alleen tailnet-toegang is toegestaan."},
                403,
            )
            await response(scope, receive, send)
            return
        address = ipaddress.ip_address(client_ip)
        if not address.is_loopback:
            identity = self._tailscale_identity(client_ip)
            if identity != self.allowed_user:
                self.audit.record(client_ip, method, path, "denied-identity")
                response = JSONResponse(
                    {"detail": "Dit Tailscale-account heeft geen toegang."},
                    403,
                )
                await response(scope, receive, send)
                return

        if method in MUTATING_METHODS:
            origin = headers.get("origin")
            if origin and urlparse(origin).netloc != headers.get("host"):
                self.audit.record(client_ip, method, path, "denied-origin")
                response = JSONResponse(
                    {"detail": "Ongeldige request-herkomst."}, 403
                )
                await response(scope, receive, send)
                return

        status_code = 500

        async def secure_send(message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                security_headers = (
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (
                        b"content-security-policy",
                        b"default-src 'self'; style-src 'self' 'unsafe-inline'; "
                        b"script-src 'self' 'unsafe-inline'; connect-src 'self'; "
                        b"img-src 'self' data:; frame-ancestors 'none'",
                    ),
                )
                message["headers"] = [*message.get("headers", []), *security_headers]
            await send(message)

        await self.app(scope, receive, secure_send)
        if method in MUTATING_METHODS:
            self.audit.record(client_ip, method, path, str(status_code))
