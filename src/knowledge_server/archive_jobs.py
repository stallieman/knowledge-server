from __future__ import annotations

import hashlib
import shutil
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, ZipInfo, is_zipfile

from knowledge_server.document_jobs import DocumentJobBackend
from knowledge_server.documents import (
    MAX_BINARY_DOCUMENT_BYTES,
    MAX_TEXT_DOCUMENT_BYTES,
    SENSITIVE_FILENAMES,
    SENSITIVE_SUFFIXES,
    SUPPORTED_FILENAMES,
    SUPPORTED_SUFFIXES,
)
from knowledge_server.video_jobs import VideoJobBackend

MAX_ARCHIVE_MEMBERS = 2_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 20_000_000_000
MAX_VIDEO_BYTES = 20_000_000_000
MAX_COMPRESSION_RATIO = 200


@dataclass(frozen=True)
class ArchiveJob:
    id: str
    filename: str
    status: str
    progress: str
    library_slug: str | None
    document_count: int
    video_count: int
    skipped_count: int
    created_at: str
    updated_at: str
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class ArchiveJobBackend:
    def list_jobs(self) -> list[ArchiveJob]: ...
    def items(self, job_id: str) -> list[dict]: ...
    def create_job_from_path(
        self, filename: str, staged_path: Path, *, library_slug: str | None
    ) -> ArchiveJob: ...


def safe_member(info: ZipInfo) -> bool:
    member = PurePosixPath(info.filename)
    return not (
        info.is_dir()
        or info.flag_bits & 0x1
        or member.is_absolute()
        or ".." in member.parts
        or "\\" in info.filename
        or "\x00" in info.filename
    )


def member_kind(info: ZipInfo) -> str | None:
    name = PurePosixPath(info.filename).name.casefold()
    suffix = PurePosixPath(info.filename).suffix.casefold()
    if suffix == ".mp4":
        return "video"
    if name in SENSITIVE_FILENAMES or suffix in SENSITIVE_SUFFIXES:
        return None
    if suffix in SUPPORTED_SUFFIXES or name in SUPPORTED_FILENAMES:
        return "document"
    return None


def validate_archive(infos: list[ZipInfo]) -> None:
    files = [info for info in infos if not info.is_dir()]
    if len(files) > MAX_ARCHIVE_MEMBERS:
        raise ValueError(f"ZIP bevat meer dan {MAX_ARCHIVE_MEMBERS} bestanden.")
    total = sum(info.file_size for info in files)
    if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        raise ValueError("Uitgepakte ZIP is groter dan de limiet van 20 GB.")
    for info in files:
        if not safe_member(info):
            continue
        if (
            info.file_size > 10_000_000
            and info.compress_size > 0
            and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
        ):
            raise ValueError(f"Verdachte compressieverhouding: {info.filename}")


class ArchiveJobManager:
    """Safely expand ZIP uploads into document and video job queues."""

    def __init__(
        self,
        database_path: Path,
        upload_root: Path,
        document_jobs: DocumentJobBackend,
        video_jobs: VideoJobBackend,
    ) -> None:
        self.database_path = database_path
        self.upload_root = upload_root
        self.document_jobs = document_jobs
        self.video_jobs = video_jobs
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="archive")
        self._write_lock = threading.Lock()
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def initialize(self) -> None:
        self.upload_root.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS archive_jobs (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL,
                    archive_path TEXT NOT NULL, status TEXT NOT NULL,
                    progress TEXT NOT NULL, library_slug TEXT,
                    document_count INTEGER NOT NULL DEFAULT 0,
                    video_count INTEGER NOT NULL DEFAULT 0,
                    skipped_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS archive_items (
                    id INTEGER PRIMARY KEY, archive_job_id TEXT NOT NULL
                        REFERENCES archive_jobs(id) ON DELETE CASCADE,
                    source_name TEXT NOT NULL, kind TEXT,
                    status TEXT NOT NULL, target_job_id TEXT,
                    library_slug TEXT, reason TEXT
                );
                """
            )
            connection.execute(
                """
                UPDATE archive_jobs SET status = 'failed',
                    progress = 'Onderbroken door een serverherstart.',
                    error = 'De server is herstart tijdens het uitpakken.',
                    updated_at = ? WHERE status = 'processing'
                """,
                (self._now(),),
            )

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> ArchiveJob:
        return ArchiveJob(
            id=row["id"], filename=row["filename"], status=row["status"],
            progress=row["progress"], library_slug=row["library_slug"],
            document_count=row["document_count"], video_count=row["video_count"],
            skipped_count=row["skipped_count"], created_at=row["created_at"],
            updated_at=row["updated_at"], error=row["error"],
        )

    def list_jobs(self) -> list[ArchiveJob]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM archive_jobs ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def items(self, job_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM archive_items WHERE archive_job_id = ? ORDER BY id",
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_job_from_path(
        self,
        filename: str,
        staged_path: Path,
        *,
        library_slug: str | None,
    ) -> ArchiveJob:
        safe_name = Path(filename).name
        if Path(safe_name).suffix.casefold() != ".zip" or not is_zipfile(staged_path):
            raise ValueError("Het bestand is geen geldige ZIP.")
        job_id = uuid.uuid4().hex
        archive_path = self.upload_root / job_id / safe_name
        archive_path.parent.mkdir(parents=True, exist_ok=False)
        shutil.move(str(staged_path), archive_path)
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO archive_jobs(
                    id, filename, archive_path, status, progress,
                    library_slug, created_at, updated_at
                ) VALUES (?, ?, ?, 'queued', 'Wacht op uitpakken.', ?, ?, ?)
                """,
                (job_id, safe_name, str(archive_path), library_slug, now, now),
            )
        self._executor.submit(self._run, job_id)
        return next(job for job in self.list_jobs() if job.id == job_id)

    def _update(self, job_id: str, **values) -> None:
        values["updated_at"] = self._now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._write_lock, self._connect() as connection:
            connection.execute(
                f"UPDATE archive_jobs SET {assignments} WHERE id = ?",  # noqa: S608
                (*values.values(), job_id),
            )

    def _record(self, job_id: str, source_name: str, **values) -> None:
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO archive_items(
                    archive_job_id, source_name, kind, status,
                    target_job_id, library_slug, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, source_name, values.get("kind"), values["status"],
                    values.get("target_job_id"), values.get("library_slug"),
                    values.get("reason"),
                ),
            )

    def _extract(self, archive: ZipFile, info: ZipInfo, target: Path) -> str:
        digest = hashlib.sha256()
        written = 0
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, target.open("xb") as output:
            while chunk := source.read(1024 * 1024):
                written += len(chunk)
                if written > info.file_size + 1:
                    raise ValueError(f"Ongeldige bestandsgrootte: {info.filename}")
                digest.update(chunk)
                output.write(chunk)
        return digest.hexdigest()

    def _run(self, job_id: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM archive_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            return
        archive_path = Path(row["archive_path"])
        work_dir = archive_path.parent / "extracted"
        documents = videos = skipped = 0
        seen_hashes: set[str] = set()
        try:
            self._update(job_id, status="processing", progress="ZIP controleren…")
            with ZipFile(archive_path) as archive:
                infos = archive.infolist()
                validate_archive(infos)
                for index, info in enumerate(infos, start=1):
                    if info.is_dir():
                        continue
                    self._update(
                        job_id,
                        progress=f"Bestand {index}/{len(infos)} controleren…",
                    )
                    kind = member_kind(info) if safe_member(info) else None
                    if kind is None:
                        skipped += 1
                        self._record(
                            job_id, info.filename, status="skipped",
                            reason="Onveilig, gevoelig of niet ondersteund.",
                        )
                        continue
                    limit = (
                        MAX_VIDEO_BYTES
                        if kind == "video"
                        else MAX_BINARY_DOCUMENT_BYTES
                        if PurePosixPath(info.filename).suffix.casefold()
                        in {".pdf", ".docx"}
                        else MAX_TEXT_DOCUMENT_BYTES
                    )
                    if info.file_size <= 0 or info.file_size > limit:
                        skipped += 1
                        self._record(
                            job_id, info.filename, kind=kind, status="skipped",
                            reason="Bestand is leeg of te groot.",
                        )
                        continue
                    member_name = PurePosixPath(info.filename).name
                    target = work_dir / f"{index:04d}-{member_name}"
                    digest = self._extract(archive, info, target)
                    if digest in seen_hashes:
                        target.unlink(missing_ok=True)
                        skipped += 1
                        self._record(
                            job_id, info.filename, kind=kind, status="duplicate",
                            reason="Exact dubbel bestand in dezelfde ZIP.",
                        )
                        continue
                    seen_hashes.add(digest)
                    if kind == "video":
                        child = self.video_jobs.create_job_from_path(
                            PurePosixPath(info.filename).name,
                            target,
                            language=None,
                            analysis_type="meeting",
                        )
                        videos += 1
                    else:
                        child = self.document_jobs.create_job_from_path(
                            PurePosixPath(info.filename).name,
                            target,
                            library_slug=row["library_slug"],
                        )
                        documents += 1
                    self._record(
                        job_id, info.filename, kind=kind, status="queued",
                        target_job_id=child.id,
                        library_slug=getattr(child, "library_slug", None),
                    )
            self._update(
                job_id, status="completed",
                progress=(
                    f"Klaar: {documents} documenten en {videos} video's in wachtrij; "
                    f"{skipped} overgeslagen."
                ),
                document_count=documents, video_count=videos,
                skipped_count=skipped, error=None,
            )
        except Exception as error:  # Keep asynchronous failures visible to the user.
            self._update(
                job_id, status="failed", progress="ZIP-import mislukt.",
                document_count=documents, video_count=videos,
                skipped_count=skipped, error=str(error),
            )
        finally:
            archive_path.unlink(missing_ok=True)
            shutil.rmtree(work_dir, ignore_errors=True)
