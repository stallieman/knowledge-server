from __future__ import annotations

import shutil
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from knowledge_server.config import Settings
from knowledge_server.documents import discover_documents, load_document
from knowledge_server.service import KnowledgeService


@dataclass(frozen=True)
class DocumentJob:
    id: str
    filename: str
    status: str
    progress: str
    library_slug: str
    suggested_library_slug: str
    created_at: str
    updated_at: str
    stored_path: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class DocumentJobBackend(Protocol):
    def list_jobs(self) -> list[DocumentJob]: ...

    def create_job_from_path(
        self,
        filename: str,
        staged_path: Path,
        *,
        library_slug: str | None,
    ) -> DocumentJob: ...

    def retry(self, job_id: str) -> DocumentJob: ...
    def delete(self, job_id: str) -> None: ...


CATEGORY_KEYWORDS = {
    "ai-engineering": ("ai", "llm", "ollama", "embedding", "rag", "prompt"),
    "devops-cloud": ("azure", "aws", "cloud", "docker", "kubernetes", "devops"),
    "elastic-stack": ("elastic", "elasticsearch", "kibana", "logstash"),
    "home-energy": ("energy", "energie", "growatt", "solar", "zonnepanelen"),
    "linux": ("linux", "cachyos", "systemd", "pacman", "bash", "fish"),
    "power-bi": ("power bi", "powerbi", "dax", "measure", "dashboard"),
    "security-osint": ("security", "beveiliging", "osint", "cve", "vulnerability"),
    "software-development": ("python", "c#", "java", "typescript", "architecture"),
    "sql": ("sql", "database", "query", "table", "view", "stored procedure"),
    "wbih": ("wbih", "warehouse", "data platform"),
    "woii": ("woii", "world war", "tweede wereldoorlog"),
}


def suggest_library(path: Path, available_slugs: set[str]) -> str:
    """Suggest a library using explainable filename and content keywords."""
    document = load_document(path)
    haystack = f"{path.name}\n{document.content[:20000]}".casefold()
    scores = {
        slug: sum(haystack.count(keyword) for keyword in keywords)
        for slug, keywords in CATEGORY_KEYWORDS.items()
        if slug in available_slugs
    }
    if scores and max(scores.values()) > 0:
        return max(scores, key=lambda slug: scores[slug])
    if "persoonlijk" in available_slugs:
        return "persoonlijk"
    return sorted(available_slugs)[0]


class DocumentJobManager:
    """Persist and serially index uploaded knowledge documents."""

    def __init__(
        self,
        settings: Settings,
        knowledge: KnowledgeService,
        *,
        workload_lock: threading.Lock | None = None,
    ) -> None:
        self.settings = settings
        self.knowledge = knowledge
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="document",
        )
        self._write_lock = threading.Lock()
        self._workload_lock = workload_lock or threading.Lock()
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.settings.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def initialize(self) -> None:
        self.settings.document_upload_path.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS document_jobs (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    staged_path TEXT NOT NULL,
                    stored_path TEXT,
                    status TEXT NOT NULL,
                    progress TEXT NOT NULL,
                    library_slug TEXT NOT NULL,
                    suggested_library_slug TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                UPDATE document_jobs SET status = 'failed',
                    progress = 'Onderbroken door een serverherstart.',
                    error = 'De server is herstart tijdens het indexeren.',
                    updated_at = ? WHERE status = 'processing'
                """,
                (self._now(),),
            )
            queued_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM document_jobs WHERE status = 'queued' "
                    "ORDER BY created_at"
                ).fetchall()
            ]
        for job_id in queued_ids:
            self._executor.submit(self._run, job_id)

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> DocumentJob:
        return DocumentJob(
            id=row["id"], filename=row["filename"], status=row["status"],
            progress=row["progress"], library_slug=row["library_slug"],
            suggested_library_slug=row["suggested_library_slug"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            stored_path=row["stored_path"], error=row["error"],
        )

    def list_jobs(self) -> list[DocumentJob]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM document_jobs ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def get_job(self, job_id: str) -> DocumentJob:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM document_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._row_to_job(row)

    def create_job_from_path(
        self,
        filename: str,
        staged_path: Path,
        *,
        library_slug: str | None,
    ) -> DocumentJob:
        safe_name = Path(filename).name
        if not safe_name or not staged_path.is_file():
            raise ValueError("Ongeldig document.")
        if not discover_documents(staged_path):
            raise ValueError("Dit bestandstype is niet ondersteund of is gevoelig.")
        available = {library.slug for library in self.knowledge.list_libraries()}
        if not available:
            raise ValueError("Er zijn nog geen kennisbibliotheken aangemaakt.")
        suggested = suggest_library(staged_path, available)
        selected = library_slug or suggested
        if selected not in available:
            raise ValueError(f"Onbekende bibliotheek: {selected}")

        job_id = uuid.uuid4().hex
        queued_path = self.settings.document_upload_path / job_id / safe_name
        queued_path.parent.mkdir(parents=True, exist_ok=False)
        shutil.move(str(staged_path), queued_path)
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO document_jobs(
                    id, filename, staged_path, status, progress, library_slug,
                    suggested_library_slug, created_at, updated_at
                ) VALUES (?, ?, ?, 'queued', 'Wacht op indexering.', ?, ?, ?, ?)
                """,
                (job_id, safe_name, str(queued_path), selected, suggested, now, now),
            )
        self._executor.submit(self._run, job_id)
        return self.get_job(job_id)

    def _update(self, job_id: str, **values: str | None) -> None:
        values["updated_at"] = self._now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._write_lock, self._connect() as connection:
            connection.execute(
                f"UPDATE document_jobs SET {assignments} WHERE id = ?",  # noqa: S608
                (*values.values(), job_id),
            )

    def retry(self, job_id: str) -> DocumentJob:
        job = self.get_job(job_id)
        if job.status != "failed":
            raise ValueError("Alleen mislukte documenttaken kunnen opnieuw.")
        source = Path(job.stored_path or "")
        if not source.is_file():
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT staged_path FROM document_jobs WHERE id = ?", (job_id,)
                ).fetchone()
            source = Path(row["staged_path"]) if row else Path()
        if not source.is_file():
            raise ValueError("Het oorspronkelijke document bestaat niet meer.")
        with self._connect() as connection:
            connection.execute(
                "UPDATE document_jobs SET staged_path = ? WHERE id = ?",
                (str(source), job_id),
            )
        self._update(
            job_id, status="queued", progress="Wacht op indexering.", error=None
        )
        self._executor.submit(self._run, job_id)
        return self.get_job(job_id)

    def delete(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if job.status in {"queued", "processing"}:
            raise ValueError("Wacht tot de indexering klaar is.")
        paths = [Path(value) for value in (job.stored_path,) if value]
        with self._connect() as connection:
            row = connection.execute(
                "SELECT staged_path FROM document_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            connection.execute("DELETE FROM document_jobs WHERE id = ?", (job_id,))
        if row:
            paths.append(Path(row["staged_path"]))
        for path in paths:
            for document in self.knowledge.list_documents():
                if Path(document.source_path) == path:
                    self.knowledge.delete_document(document.id)
            if path.is_relative_to(self.settings.database_path.parent):
                path.unlink(missing_ok=True)

    def _run(self, job_id: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM document_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            return
        try:
            self._update(job_id, progress="Wacht op beschikbare AI-capaciteit…")
            with self._workload_lock:
                self._process(job_id, row)
        except Exception as error:
            self._update(
                job_id, status="failed", progress="Indexering mislukt.",
                error=str(error) or type(error).__name__,
            )

    def _process(self, job_id: str, row: sqlite3.Row) -> None:
        source = Path(row["staged_path"])
        destination_dir = (
            self.settings.database_path.parent / "import" / row["library_slug"]
        )
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{job_id[:8]}-{row['filename']}"
        if source != destination:
            if destination.exists():
                source = destination
            else:
                shutil.move(str(source), destination)
                source = destination
        self._update(
            job_id,
            status="processing",
            progress="Document indexeren…",
            stored_path=str(destination),
        )
        report = self.knowledge.ingest(row["library_slug"], source)
        self._update(
            job_id,
            status="completed",
            progress=f"Klaar: {report.chunks_written} kennisfragmenten.",
            error=None,
        )
