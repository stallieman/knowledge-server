from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from knowledge_server.chunking import TextChunk


@dataclass(frozen=True)
class Library:
    """A logical knowledge library."""

    slug: str
    name: str
    document_count: int = 0
    chunk_count: int = 0


@dataclass(frozen=True)
class StoredChunk:
    """A stored chunk and its embedding."""

    chunk_id: int
    library_slug: str
    source_path: str
    source_name: str
    chunk_index: int
    content: str
    start_line: int
    end_line: int
    embedding: list[float]


@dataclass(frozen=True)
class StoredDocument:
    id: int
    library_slug: str
    source_path: str
    source_name: str
    indexed_at: str
    chunk_count: int
    title: str | None = None
    summary: str | None = None
    tags: list[str] | None = None


@dataclass(frozen=True)
class MetadataSuggestion:
    id: int
    document_id: int
    title: str
    summary: str
    tags: list[str]
    library_slug: str
    status: str
    created_at: str


class KnowledgeStore:
    """SQLite persistence for libraries, documents, chunks, and embeddings."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        """Create the database schema when needed."""
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS libraries (
                    id INTEGER PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY,
                    library_id INTEGER NOT NULL REFERENCES libraries(id)
                        ON DELETE CASCADE,
                    source_path TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    embedding_model TEXT NOT NULL,
                    indexed_at TEXT NOT NULL,
                    UNIQUE(library_id, source_path)
                );

                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY,
                    document_id INTEGER NOT NULL REFERENCES documents(id)
                        ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    embedding TEXT NOT NULL,
                    UNIQUE(document_id, chunk_index)
                );

                CREATE INDEX IF NOT EXISTS idx_documents_library
                    ON documents(library_id);
                CREATE INDEX IF NOT EXISTS idx_chunks_document
                    ON chunks(document_id);

                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    content,
                    source_name,
                    library_slug UNINDEXED,
                    tokenize = "unicode61 remove_diacritics 2 tokenchars '-_.'"
                );
                """
            )
            chunk_count = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[
                0
            ]
            fts_count = connection.execute(
                "SELECT COUNT(*) FROM chunks_fts"
            ).fetchone()[0]
            if chunk_count != fts_count:
                connection.execute("DELETE FROM chunks_fts")
                connection.execute(
                    """
                    INSERT INTO chunks_fts(rowid, content, source_name, library_slug)
                    SELECT chunks.id, chunks.content, documents.source_name,
                           libraries.slug
                    FROM chunks
                    JOIN documents ON documents.id = chunks.document_id
                    JOIN libraries ON libraries.id = documents.library_id
                    """
                )
            document_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(documents)")
            }
            for column, definition in {
                "title": "TEXT",
                "summary": "TEXT",
                "tags": "TEXT NOT NULL DEFAULT '[]'",
            }.items():
                if column not in document_columns:
                    connection.execute(
                        f"ALTER TABLE documents ADD COLUMN {column} {definition}"
                    )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata_suggestions (
                    id INTEGER PRIMARY KEY,
                    document_id INTEGER NOT NULL REFERENCES documents(id)
                        ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    library_slug TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def create_library(self, slug: str, name: str) -> Library:
        """Create a library or return the existing one."""
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO libraries(slug, name, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET name = excluded.name
                """,
                (slug, name, now),
            )
        return Library(slug=slug, name=name)

    def list_libraries(self) -> list[Library]:
        """List libraries with document and chunk counts."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    libraries.slug,
                    libraries.name,
                    COUNT(DISTINCT documents.id) AS document_count,
                    COUNT(chunks.id) AS chunk_count
                FROM libraries
                LEFT JOIN documents ON documents.library_id = libraries.id
                LEFT JOIN chunks ON chunks.document_id = documents.id
                GROUP BY libraries.id
                ORDER BY libraries.name COLLATE NOCASE
                """
            ).fetchall()

        return [
            Library(
                slug=row["slug"],
                name=row["name"],
                document_count=row["document_count"],
                chunk_count=row["chunk_count"],
            )
            for row in rows
        ]

    def library_exists(self, slug: str) -> bool:
        """Return whether a library slug exists."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM libraries WHERE slug = ?",
                (slug,),
            ).fetchone()
        return row is not None

    def list_documents(self) -> list[StoredDocument]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT documents.id, libraries.slug AS library_slug,
                       documents.source_path, documents.source_name,
                       documents.indexed_at, COUNT(chunks.id) AS chunk_count,
                       documents.title, documents.summary, documents.tags
                FROM documents
                JOIN libraries ON libraries.id = documents.library_id
                LEFT JOIN chunks ON chunks.document_id = documents.id
                GROUP BY documents.id
                ORDER BY documents.indexed_at DESC
                """
            ).fetchall()
        return [
            StoredDocument(
                id=row["id"],
                library_slug=row["library_slug"],
                source_path=row["source_path"],
                source_name=row["source_name"],
                indexed_at=row["indexed_at"],
                chunk_count=row["chunk_count"],
                title=row["title"],
                summary=row["summary"],
                tags=json.loads(row["tags"] or "[]"),
            )
            for row in rows
        ]

    def save_metadata_suggestion(
        self,
        *,
        document_id: int,
        title: str,
        summary: str,
        tags: list[str],
        library_slug: str,
    ) -> MetadataSuggestion:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO metadata_suggestions(
                    document_id, title, summary, tags, library_slug,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (document_id, title, summary, json.dumps(tags), library_slug, now),
            )
            suggestion_id = int(cursor.lastrowid)
        return MetadataSuggestion(
            id=suggestion_id,
            document_id=document_id,
            title=title,
            summary=summary,
            tags=tags,
            library_slug=library_slug,
            status="pending",
            created_at=now,
        )

    def approve_metadata_suggestion(self, suggestion_id: int) -> MetadataSuggestion:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM metadata_suggestions WHERE id = ?",
                (suggestion_id,),
            ).fetchone()
            if row is None or row["status"] != "pending":
                raise KeyError(f"Unknown pending suggestion: {suggestion_id}")
            library = connection.execute(
                "SELECT id FROM libraries WHERE slug = ?", (row["library_slug"],)
            ).fetchone()
            if library is None:
                raise KeyError(f"Unknown library: {row['library_slug']}")
            connection.execute(
                """
                UPDATE documents SET title = ?, summary = ?, tags = ?, library_id = ?
                WHERE id = ?
                """,
                (
                    row["title"], row["summary"], row["tags"],
                    library["id"], row["document_id"],
                ),
            )
            chunk_ids = connection.execute(
                "SELECT id FROM chunks WHERE document_id = ?", (row["document_id"],)
            ).fetchall()
            connection.executemany(
                "UPDATE chunks_fts SET library_slug = ? WHERE rowid = ?",
                ((row["library_slug"], item["id"]) for item in chunk_ids),
            )
            connection.execute(
                "UPDATE metadata_suggestions SET status = 'approved' WHERE id = ?",
                (suggestion_id,),
            )
        return MetadataSuggestion(
            id=row["id"], document_id=row["document_id"], title=row["title"],
            summary=row["summary"], tags=json.loads(row["tags"]),
            library_slug=row["library_slug"], status="approved",
            created_at=row["created_at"],
        )

    def delete_document(self, document_id: int) -> StoredDocument:
        documents = {item.id: item for item in self.list_documents()}
        document = documents.get(document_id)
        if document is None:
            raise KeyError(f"Unknown document: {document_id}")
        with self._connect() as connection:
            chunk_ids = connection.execute(
                "SELECT id FROM chunks WHERE document_id = ?", (document_id,)
            ).fetchall()
            connection.executemany(
                "DELETE FROM chunks_fts WHERE rowid = ?",
                ((row["id"],) for row in chunk_ids),
            )
            connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        return document

    def invalidate_document(self, document_id: int) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE documents SET content_hash = '' WHERE id = ?",
                (document_id,),
            )
        if cursor.rowcount == 0:
            raise KeyError(f"Unknown document: {document_id}")

    def document_is_current(
        self,
        library_slug: str,
        source_path: str,
        content_hash: str,
        embedding_model: str,
    ) -> bool:
        """Return whether this exact document version is already indexed."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT documents.content_hash, documents.embedding_model
                FROM documents
                JOIN libraries ON libraries.id = documents.library_id
                WHERE libraries.slug = ? AND documents.source_path = ?
                """,
                (library_slug, source_path),
            ).fetchone()

        return bool(
            row
            and row["content_hash"] == content_hash
            and row["embedding_model"] == embedding_model
        )

    def replace_document(
        self,
        *,
        library_slug: str,
        source_path: str,
        source_name: str,
        content_hash: str,
        embedding_model: str,
        chunks: list[TextChunk],
        embeddings: list[list[float]],
    ) -> None:
        """Atomically replace one document and all its chunks."""
        if len(chunks) != len(embeddings):
            raise ValueError("Every chunk must have exactly one embedding")

        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            library_row = connection.execute(
                "SELECT id FROM libraries WHERE slug = ?",
                (library_slug,),
            ).fetchone()
            if library_row is None:
                raise KeyError(f"Unknown library: {library_slug}")

            connection.execute(
                """
                INSERT INTO documents(
                    library_id,
                    source_path,
                    source_name,
                    content_hash,
                    embedding_model,
                    indexed_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(library_id, source_path) DO UPDATE SET
                    source_name = excluded.source_name,
                    content_hash = excluded.content_hash,
                    embedding_model = excluded.embedding_model,
                    indexed_at = excluded.indexed_at
                """,
                (
                    library_row["id"],
                    source_path,
                    source_name,
                    content_hash,
                    embedding_model,
                    now,
                ),
            )
            document_row = connection.execute(
                """
                SELECT id FROM documents
                WHERE library_id = ? AND source_path = ?
                """,
                (library_row["id"], source_path),
            ).fetchone()
            document_id = int(document_row["id"])

            old_chunk_ids = connection.execute(
                "SELECT id FROM chunks WHERE document_id = ?",
                (document_id,),
            ).fetchall()
            connection.executemany(
                "DELETE FROM chunks_fts WHERE rowid = ?",
                ((row["id"],) for row in old_chunk_ids),
            )
            connection.execute(
                "DELETE FROM chunks WHERE document_id = ?",
                (document_id,),
            )
            connection.executemany(
                """
                INSERT INTO chunks(
                    document_id,
                    chunk_index,
                    content,
                    start_line,
                    end_line,
                    embedding
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        document_id,
                        chunk.index,
                        chunk.text,
                        chunk.start_line,
                        chunk.end_line,
                        json.dumps(embedding),
                    )
                    for chunk, embedding in zip(chunks, embeddings, strict=True)
                ],
            )
            stored_rows = connection.execute(
                """
                SELECT id, chunk_index, content
                FROM chunks
                WHERE document_id = ?
                ORDER BY chunk_index
                """,
                (document_id,),
            ).fetchall()
            connection.executemany(
                """
                INSERT INTO chunks_fts(rowid, content, source_name, library_slug)
                VALUES (?, ?, ?, ?)
                """,
                (
                    (
                        row["id"],
                        row["content"],
                        source_name,
                        library_slug,
                    )
                    for row in stored_rows
                ),
            )

    def load_chunks(self, library_slugs: Iterable[str]) -> list[StoredChunk]:
        """Load all searchable chunks for the selected libraries."""
        slugs = list(dict.fromkeys(library_slugs))
        if not slugs:
            return []

        placeholders = ", ".join("?" for _ in slugs)
        query = f"""
            SELECT
                chunks.id AS chunk_id,
                libraries.slug AS library_slug,
                documents.source_path,
                documents.source_name,
                chunks.chunk_index,
                chunks.content,
                chunks.start_line,
                chunks.end_line,
                chunks.embedding
            FROM chunks
            JOIN documents ON documents.id = chunks.document_id
            JOIN libraries ON libraries.id = documents.library_id
            WHERE libraries.slug IN ({placeholders})
            ORDER BY libraries.slug, documents.source_path, chunks.chunk_index
        """

        with self._connect() as connection:
            rows = connection.execute(query, slugs).fetchall()

        return [
            StoredChunk(
                chunk_id=row["chunk_id"],
                library_slug=row["library_slug"],
                source_path=row["source_path"],
                source_name=row["source_name"],
                chunk_index=row["chunk_index"],
                content=row["content"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                embedding=json.loads(row["embedding"]),
            )
            for row in rows
        ]

    def search_lexical_chunk_ids(
        self,
        library_slugs: Iterable[str],
        fts_query: str,
        *,
        limit: int,
    ) -> list[int]:
        """Return chunk ids ranked by exact/lexical FTS5 relevance."""
        slugs = list(dict.fromkeys(library_slugs))
        if not slugs or not fts_query or limit < 1:
            return []

        placeholders = ", ".join("?" for _ in slugs)
        query = f"""
            SELECT chunks_fts.rowid AS chunk_id
            FROM chunks_fts
            JOIN chunks ON chunks.id = chunks_fts.rowid
            JOIN documents ON documents.id = chunks.document_id
            JOIN libraries ON libraries.id = documents.library_id
            WHERE chunks_fts MATCH ?
              AND libraries.slug IN ({placeholders})
            ORDER BY bm25(chunks_fts, 1.0, 5.0, 0.0)
            LIMIT ?
        """
        parameters = [fts_query, *slugs, limit]
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [int(row["chunk_id"]) for row in rows]

    def prune_missing_documents(
        self,
        library_slug: str,
        source_root: Path,
        current_paths: Iterable[Path],
    ) -> int:
        """Remove stale index rows for deleted files below one source root."""
        root = source_root.expanduser().resolve()
        current = {str(path.expanduser().resolve()) for path in current_paths}
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT documents.id, documents.source_path
                FROM documents
                JOIN libraries ON libraries.id = documents.library_id
                WHERE libraries.slug = ?
                """,
                (library_slug,),
            ).fetchall()
            stale_document_ids = [
                int(row["id"])
                for row in rows
                if Path(row["source_path"]).is_relative_to(root)
                and row["source_path"] not in current
            ]
            if not stale_document_ids:
                return 0

            placeholders = ", ".join("?" for _ in stale_document_ids)
            chunk_rows = connection.execute(
                f"SELECT id FROM chunks WHERE document_id IN ({placeholders})",
                stale_document_ids,
            ).fetchall()
            connection.executemany(
                "DELETE FROM chunks_fts WHERE rowid = ?",
                ((row["id"],) for row in chunk_rows),
            )
            connection.execute(
                f"DELETE FROM documents WHERE id IN ({placeholders})",
                stale_document_ids,
            )
        return len(stale_document_ids)
