from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class Conversation:
    id: str
    title: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ChatMessage:
    id: int
    conversation_id: str
    role: str
    content: str
    sources: list[dict]
    created_at: str


class ChatStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id)
                        ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sources TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                """
            )

    def create_conversation(self, title: str = "Nieuw gesprek") -> Conversation:
        conversation_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO conversations VALUES (?, ?, ?, ?)",
                (conversation_id, title[:120], now, now),
            )
        return Conversation(conversation_id, title[:120], now, now)

    def list_conversations(self) -> list[Conversation]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC"
            ).fetchall()
        return [Conversation(**dict(row)) for row in rows]

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        sources: list[dict] | None = None,
    ) -> ChatMessage:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            conversation = connection.execute(
                "SELECT title FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if conversation is None:
                raise KeyError(f"Unknown conversation: {conversation_id}")
            cursor = connection.execute(
                """
                INSERT INTO chat_messages(
                    conversation_id, role, content, sources, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, role, content, json.dumps(sources or []), now),
            )
            title = conversation["title"]
            if role == "user" and title == "Nieuw gesprek":
                title = content.strip().replace("\n", " ")[:80] or title
            connection.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, now, conversation_id),
            )
            message_id = int(cursor.lastrowid)
        return ChatMessage(
            message_id, conversation_id, role, content, sources or [], now
        )

    def messages(self, conversation_id: str) -> list[ChatMessage]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM chat_messages WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return [
            ChatMessage(
                id=row["id"], conversation_id=row["conversation_id"],
                role=row["role"], content=row["content"],
                sources=json.loads(row["sources"]), created_at=row["created_at"],
            )
            for row in rows
        ]

    def export_markdown(self, conversation_id: str) -> str:
        conversations = {item.id: item for item in self.list_conversations()}
        conversation = conversations.get(conversation_id)
        if conversation is None:
            raise KeyError(f"Unknown conversation: {conversation_id}")
        lines = [f"# {conversation.title}", ""]
        for message in self.messages(conversation_id):
            label = "Jij" if message.role == "user" else "Assistent"
            lines.extend([f"## {label}", "", message.content, ""])
            if message.sources:
                lines.append("Bronnen:")
                for source in message.sources:
                    lines.append(
                        f"- {source['source_name']} "
                        f"({source['library_slug']}, regels "
                        f"{source['start_line']}-{source['end_line']})"
                    )
                lines.append("")
        return "\n".join(lines)
