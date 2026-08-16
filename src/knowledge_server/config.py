from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _database_path() -> Path:
    configured_path = os.getenv("KNOWLEDGE_SERVER_DB", "data/knowledge.db")
    return Path(configured_path).expanduser().resolve()


def _video_upload_path() -> Path:
    configured_path = os.getenv("KNOWLEDGE_SERVER_VIDEO_UPLOADS")
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    return _database_path().parent / "video-uploads"


def _document_upload_path() -> Path:
    configured_path = os.getenv("KNOWLEDGE_SERVER_DOCUMENT_UPLOADS")
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    return _database_path().parent / "document-uploads"


def _archive_upload_path() -> Path:
    configured_path = os.getenv("KNOWLEDGE_SERVER_ARCHIVE_UPLOADS")
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    return _database_path().parent / "archive-uploads"


@dataclass(frozen=True)
class Settings:
    """Runtime configuration, overridable through environment variables."""

    database_path: Path = field(default_factory=_database_path)
    ollama_base_url: str = field(
        default_factory=lambda: os.getenv(
            "KNOWLEDGE_SERVER_OLLAMA_URL",
            "http://127.0.0.1:11434",
        )
    )
    chat_model: str = field(
        default_factory=lambda: os.getenv(
            "KNOWLEDGE_SERVER_CHAT_MODEL",
            "qwen3:14b",
        )
    )
    embedding_model: str = field(
        default_factory=lambda: os.getenv(
            "KNOWLEDGE_SERVER_EMBEDDING_MODEL",
            "embeddinggemma",
        )
    )
    request_timeout_seconds: float = 1200.0
    chunk_size_chars: int = 1800
    chunk_overlap_chars: int = 250
    transcriber_project_path: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "KNOWLEDGE_SERVER_TRANSCRIBER_PATH",
                "../local-meeting-transcriber",
            )
        )
        .expanduser()
        .resolve()
    )
    video_upload_path: Path = field(
        default_factory=_video_upload_path
    )
    document_upload_path: Path = field(default_factory=_document_upload_path)
    archive_upload_path: Path = field(default_factory=_archive_upload_path)
    allowed_tailscale_user: str = field(
        default_factory=lambda: os.getenv(
            "KNOWLEDGE_SERVER_TAILSCALE_USER",
            "stallieman@github",
        )
    )

    def __post_init__(self) -> None:
        if not os.getenv("KNOWLEDGE_SERVER_VIDEO_UPLOADS"):
            object.__setattr__(
                self,
                "video_upload_path",
                self.database_path.parent / "video-uploads",
            )
        if not os.getenv("KNOWLEDGE_SERVER_DOCUMENT_UPLOADS"):
            object.__setattr__(
                self,
                "document_upload_path",
                self.database_path.parent / "document-uploads",
            )
        if not os.getenv("KNOWLEDGE_SERVER_ARCHIVE_UPLOADS"):
            object.__setattr__(
                self,
                "archive_upload_path",
                self.database_path.parent / "archive-uploads",
            )
