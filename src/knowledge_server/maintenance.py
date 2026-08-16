from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from knowledge_server.config import Settings
from knowledge_server.ollama import OllamaClient


@dataclass(frozen=True)
class SystemHealth:
    status: str
    database: str
    ollama: str
    chat_model: str
    embedding_model: str
    ffmpeg: str
    transcriber: str
    disk_free_gb: float
    disk_used_percent: float


class MaintenanceService:
    def __init__(self, settings: Settings, ollama: OllamaClient) -> None:
        self.settings = settings
        self.ollama = ollama

    def health(self) -> SystemHealth:
        database = "ok"
        try:
            with sqlite3.connect(
                f"file:{self.settings.database_path}?mode=ro", uri=True
            ) as connection:
                connection.execute("SELECT 1").fetchone()
        except sqlite3.Error as error:
            database = f"fout: {error}"
        try:
            models = self.ollama.installed_models()
            ollama_status = "ok"
        except Exception as error:
            models = set()
            ollama_status = f"fout: {error}"
        disk = shutil.disk_usage(self.settings.database_path.parent)
        free_gb = disk.free / 1024**3
        used_percent = (disk.used / disk.total) * 100
        transcriber_script = (
            self.settings.transcriber_project_path
            / "scripts/video-transcriber-cuda12.fish"
        )
        statuses = (
            database,
            ollama_status,
            "ok" if shutil.which("ffmpeg") else "ontbreekt",
            "ok" if transcriber_script.is_file() else "ontbreekt",
        )
        overall = "ok" if all(value == "ok" for value in statuses) else "degraded"
        if free_gb < 10:
            overall = "degraded"
        return SystemHealth(
            status=overall,
            database=database,
            ollama=ollama_status,
            chat_model=(
                "ok" if self.settings.chat_model in models else "niet geïnstalleerd"
            ),
            embedding_model=(
                "ok"
                if self.settings.embedding_model in models
                else "niet geïnstalleerd"
            ),
            ffmpeg=statuses[2],
            transcriber=statuses[3],
            disk_free_gb=round(free_gb, 1),
            disk_used_percent=round(used_percent, 1),
        )

    def backup(self, keep: int = 14) -> Path:
        backup_dir = self.settings.database_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        destination = backup_dir / f"knowledge-{timestamp}.db"
        with (
            sqlite3.connect(self.settings.database_path) as source,
            sqlite3.connect(destination) as target,
        ):
            source.backup(target)
        backups = sorted(backup_dir.glob("knowledge-*.db"), reverse=True)
        for expired in backups[keep:]:
            expired.unlink(missing_ok=True)
        return destination
