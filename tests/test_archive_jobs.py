from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from knowledge_server.archive_jobs import ArchiveJobManager


@dataclass
class ChildJob:
    id: str
    library_slug: str | None = None


class FakeDocumentJobs:
    def __init__(self) -> None:
        self.files: list[str] = []

    def create_job_from_path(
        self,
        filename: str,
        staged_path: Path,
        *,
        library_slug: str | None,
    ) -> ChildJob:
        assert staged_path.is_file()
        self.files.append(filename)
        return ChildJob(f"document-{len(self.files)}", library_slug or "linux")


class FakeVideoJobs:
    def __init__(self) -> None:
        self.files: list[str] = []

    def create_job_from_path(
        self,
        filename: str,
        staged_path: Path,
        *,
        language: str | None,
        analysis_type: str,
    ) -> ChildJob:
        assert staged_path.is_file()
        assert language is None
        assert analysis_type == "meeting"
        self.files.append(filename)
        return ChildJob(f"video-{len(self.files)}")


def test_archive_routes_supported_files_and_skips_unsafe_members(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "kennis.zip"
    with ZipFile(archive, "w") as target:
        target.writestr("boeken/linux.pdf", b"%PDF-1.4\n")
        target.writestr("video/meeting.mp4", b"video")
        target.writestr("../escape.md", b"unsafe")
        target.writestr("image.jpg", b"unsupported")
    documents = FakeDocumentJobs()
    videos = FakeVideoJobs()
    manager = ArchiveJobManager(
        tmp_path / "knowledge.db",
        tmp_path / "archives",
        documents,
        videos,
    )

    created = manager.create_job_from_path(
        archive.name,
        archive,
        library_slug=None,
    )
    manager._executor.shutdown(wait=True)  # noqa: SLF001
    completed = next(job for job in manager.list_jobs() if job.id == created.id)

    assert completed.status == "completed"
    assert completed.document_count == 1
    assert completed.video_count == 1
    assert completed.skipped_count == 2
    assert documents.files == ["linux.pdf"]
    assert videos.files == ["meeting.mp4"]
    statuses = {
        item["source_name"]: item["status"]
        for item in manager.items(created.id)
    }
    assert statuses["../escape.md"] == "skipped"
    assert statuses["image.jpg"] == "skipped"


def test_archive_rejects_non_zip(tmp_path: Path) -> None:
    source = tmp_path / "fake.zip"
    source.write_text("not a zip", encoding="utf-8")
    manager = ArchiveJobManager(
        tmp_path / "knowledge.db",
        tmp_path / "archives",
        FakeDocumentJobs(),
        FakeVideoJobs(),
    )

    try:
        manager.create_job_from_path(source.name, source, library_slug=None)
    except ValueError as error:
        assert "geldige ZIP" in str(error)
    else:
        raise AssertionError("Invalid ZIP was accepted")
