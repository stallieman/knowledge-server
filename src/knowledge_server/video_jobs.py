from __future__ import annotations

import shutil
import sqlite3
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from knowledge_server.config import Settings
from knowledge_server.service import KnowledgeService


@dataclass(frozen=True)
class VideoJob:
    id: str
    filename: str
    status: str
    progress: str
    language: str | None
    analysis_type: str
    created_at: str
    updated_at: str
    transcript_path: str | None = None
    summary_path: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class VideoJobBackend(Protocol):
    def list_jobs(self) -> list[VideoJob]: ...

    def create_job(
        self,
        filename: str,
        content: bytes,
        *,
        language: str | None,
        analysis_type: str,
    ) -> VideoJob: ...

    def cancel(self, job_id: str) -> VideoJob: ...
    def retry(self, job_id: str) -> VideoJob: ...
    def delete(self, job_id: str) -> None: ...

    def create_job_from_path(
        self,
        filename: str,
        staged_path: Path,
        *,
        language: str | None,
        analysis_type: str,
    ) -> VideoJob: ...


class VideoJobManager:
    """Persist and serially execute GPU-heavy video processing jobs."""

    def __init__(
        self,
        settings: Settings,
        knowledge: KnowledgeService,
        *,
        workload_lock: threading.Lock | None = None,
    ) -> None:
        self.settings = settings
        self.knowledge = knowledge
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video")
        self._write_lock = threading.Lock()
        self._workload_lock = workload_lock or threading.Lock()
        self._processes: dict[str, subprocess.Popen] = {}
        self._cancelled: set[str] = set()
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.settings.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        self.settings.video_upload_path.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS video_jobs (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress TEXT NOT NULL,
                    language TEXT,
                    analysis_type TEXT NOT NULL,
                    transcript_path TEXT,
                    summary_path TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                UPDATE video_jobs
                SET status = 'failed',
                    progress = 'Onderbroken door een serverherstart.',
                    error = 'De server is herstart tijdens de verwerking.',
                    updated_at = ?
                WHERE status = 'processing'
                """,
                (self._now(),),
            )
            queued_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM video_jobs WHERE status = 'queued' "
                    "ORDER BY created_at"
                ).fetchall()
            ]
        for job_id in queued_ids:
            self._executor.submit(self._run, job_id)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> VideoJob:
        return VideoJob(
            id=row["id"],
            filename=row["filename"],
            status=row["status"],
            progress=row["progress"],
            language=row["language"],
            analysis_type=row["analysis_type"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            transcript_path=row["transcript_path"],
            summary_path=row["summary_path"],
            error=row["error"],
        )

    def list_jobs(self) -> list[VideoJob]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM video_jobs ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def create_job(
        self,
        filename: str,
        content: bytes,
        *,
        language: str | None,
        analysis_type: str,
    ) -> VideoJob:
        staged_path = self.settings.video_upload_path / ".incoming" / uuid.uuid4().hex
        staged_path.parent.mkdir(parents=True, exist_ok=True)
        staged_path.write_bytes(content)
        try:
            return self.create_job_from_path(
                filename,
                staged_path,
                language=language,
                analysis_type=analysis_type,
            )
        finally:
            staged_path.unlink(missing_ok=True)

    def create_job_from_path(
        self,
        filename: str,
        staged_path: Path,
        *,
        language: str | None,
        analysis_type: str,
    ) -> VideoJob:
        safe_name = Path(filename).name
        if not safe_name or Path(safe_name).suffix.casefold() != ".mp4":
            raise ValueError("Alleen MP4-video's worden ondersteund.")
        if not staged_path.is_file() or staged_path.stat().st_size == 0:
            raise ValueError("Het geüploade videobestand is leeg.")
        if language not in {None, "nl", "en"}:
            raise ValueError("Taal moet automatisch, Nederlands of Engels zijn.")
        if analysis_type not in {"meeting", "data-engineering"}:
            raise ValueError("Onbekend analysetype.")

        job_id = uuid.uuid4().hex
        input_path = (
            self.settings.video_upload_path
            / job_id
            / f"{job_id[:8]}-{safe_name}"
        )
        input_path.parent.mkdir(parents=True, exist_ok=False)
        shutil.move(str(staged_path), input_path)
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO video_jobs(
                    id, filename, input_path, status, progress, language,
                    analysis_type, created_at, updated_at
                ) VALUES (?, ?, ?, 'queued', 'Wacht op verwerking.', ?, ?, ?, ?)
                """,
                (job_id, safe_name, str(input_path), language, analysis_type, now, now),
            )
        self._executor.submit(self._run, job_id)
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> VideoJob:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM video_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._row_to_job(row)

    def _update(self, job_id: str, **values: str | None) -> None:
        values["updated_at"] = self._now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._write_lock, self._connect() as connection:
            connection.execute(
                f"UPDATE video_jobs SET {assignments} WHERE id = ?",  # noqa: S608
                (*values.values(), job_id),
            )

    def cancel(self, job_id: str) -> VideoJob:
        job = self.get_job(job_id)
        if job.status not in {"queued", "processing"}:
            raise ValueError("Alleen wachtende of actieve taken kunnen worden gestopt.")
        self._cancelled.add(job_id)
        process = self._processes.get(job_id)
        if process is not None:
            process.terminate()
        self._update(
            job_id,
            status="cancelled",
            progress="Geannuleerd.",
            error=None,
        )
        return self.get_job(job_id)

    def retry(self, job_id: str) -> VideoJob:
        job = self.get_job(job_id)
        if job.status not in {"failed", "cancelled"}:
            raise ValueError("Alleen mislukte of geannuleerde taken kunnen opnieuw.")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT input_path FROM video_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None or not Path(row["input_path"]).is_file():
            raise ValueError("Het oorspronkelijke videobestand bestaat niet meer.")
        self._cancelled.discard(job_id)
        self._update(
            job_id,
            status="queued",
            progress="Wacht op verwerking.",
            error=None,
        )
        self._executor.submit(self._run, job_id)
        return self.get_job(job_id)

    def delete(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if job.status in {"queued", "processing"}:
            raise ValueError("Stop de actieve taak voordat je deze verwijdert.")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT input_path FROM video_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            connection.execute("DELETE FROM video_jobs WHERE id = ?", (job_id,))
        if row is not None:
            input_path = Path(row["input_path"])
            if input_path.is_relative_to(self.settings.video_upload_path):
                shutil.rmtree(input_path.parent, ignore_errors=True)
            output_dir = (
                self.settings.transcriber_project_path
                / "data/output/meetings"
                / input_path.stem
            )
            output_root = (
                self.settings.transcriber_project_path / "data/output/meetings"
            )
            if output_dir.is_relative_to(output_root):
                shutil.rmtree(output_dir, ignore_errors=True)

    def _run(self, job_id: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM video_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            return
        if job_id in self._cancelled:
            return
        input_path = Path(row["input_path"])
        try:
            self._update(job_id, progress="Wacht op beschikbare AI-capaciteit…")
            with self._workload_lock:
                self._process(job_id, row, input_path)
        except Exception as error:  # the durable job record must capture all failures
            self._processes.pop(job_id, None)
            if job_id in self._cancelled:
                return
            details = str(error)
            self._update(
                job_id,
                status="failed",
                progress="Verwerking mislukt.",
                error=details or type(error).__name__,
            )

    def _process(
        self,
        job_id: str,
        row: sqlite3.Row,
        input_path: Path,
    ) -> None:
        try:
            self._update(job_id, status="processing", progress="Video verwerken…")
            command = [
                str(
                    self.settings.transcriber_project_path
                    / "scripts/video-transcriber-cuda12.fish"
                ),
                "process-long-video",
                str(input_path),
                "--model",
                "large-v3",
                "--chunk-seconds",
                "300",
                "--analysis-type",
                row["analysis_type"],
            ]
            if row["language"]:
                command.extend(["--language", row["language"]])
            process = subprocess.Popen(
                command,
                cwd=self.settings.transcriber_project_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self._processes[job_id] = process
            output_lines: list[str] = []
            if process.stdout is not None:
                for line in process.stdout:
                    message = line.strip()
                    if not message:
                        continue
                    output_lines.append(message)
                    output_lines = output_lines[-30:]
                    self._update(job_id, progress=message[:500])
            return_code = process.wait()
            self._processes.pop(job_id, None)
            if job_id in self._cancelled:
                return
            if return_code != 0:
                raise RuntimeError("\n".join(output_lines[-12:]))
            meeting_dir = (
                self.settings.transcriber_project_path
                / "data/output/meetings"
                / input_path.stem
            )
            transcript = meeting_dir / "full_transcript_with_timestamps.md"
            summary = meeting_dir / "final_meeting_summary.md"
            if not transcript.is_file():
                raise RuntimeError(
                    "De transcriber heeft geen leesbaar transcript gemaakt."
                )
            self._update(job_id, progress="Transcript in kennisbank indexeren…")
            self.knowledge.ingest("persoonlijke-videos", transcript)
            self._update(
                job_id,
                status="completed",
                progress="Klaar en geïndexeerd.",
                transcript_path=str(transcript),
                summary_path=str(summary) if summary.is_file() else None,
                error=None,
            )
        finally:
            pass
