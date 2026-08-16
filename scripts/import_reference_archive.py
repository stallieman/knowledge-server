#!/usr/bin/env python3
"""Safely categorize a mixed reference ZIP into knowledge-server libraries."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, ZipInfo

from pypdf import PdfReader

FILE_TO_LIBRARY = {
    "Linux_Commands_one_note.pdf": "linux",
    "Linux_Switch naar Linux_one_note.pdf": "linux",
    "Tmux_one_note.pdf": "linux",
    "Neovim_one_note.pdf": "linux",
    "VSCode_one_note.pdf": "software-development",
    "Powershell_one_note.pdf": "software-development",
    "100+ python projects with source code.pdf": "software-development",
    "python_one_note.pdf": "software-development",
    "GIT_one_note.pdf": "software-development",
    "Docker commands_one_note.pdf": "devops-cloud",
    "elasticsearch_one_note.pdf": "elastic-stack",
    "Metricbeat_one_note.pdf": "elastic-stack",
    "SQL_Functions_one_note.pdf": "sql",
    "SQL_Window_functions_one_note.pdf": "sql",
    "SQL_Tabel en update_one_note.pdf": "sql",
    "Workplace_Billing_Insights_History_Model_Review.pdf": "wbih",
    "Workplace_Billing_Insights_WBIH_Actuele_Naslag_2026-08-07_v3.pdf": "wbih",
    "TDV_one_note.pdf": "wbih",
    "wbih_azure_devops_wiki_blauwdruk.md": "wbih",
    "wbih_azure_devops_wiki_blauwdruk(1).md": "wbih",
    "ai-engineering-basis-ece-tdv-chatbots-uitgebreid.pdf": "ai-engineering",
    "Configure a semantic model in Power.txt": "power-bi",
    "Clean, transform, and load data in.txt": "power-bi",
    "Create DAX calculations in semantic.txt": "power-bi",
    "Design Power BI reports.txt": "power-bi",
}

LIBRARY_NAMES = {
    "ai-engineering": "AI Engineering",
    "devops-cloud": "DevOps & Cloud",
    "elastic-stack": "Elastic Stack",
    "linux": "Linux",
    "power-bi": "Power BI",
    "project-tdv-view-exporter": "Project: TDV View Exporter",
    "software-development": "Softwareontwikkeling",
    "sql": "SQL",
    "wbih": "WBIH",
}

NESTED_ARCHIVE = "TDV View Exporter Project.zip"
NESTED_ROOT = PurePosixPath("tdv-view-exporter")
NESTED_ALLOWED_SUFFIXES = frozenset(
    {".bat", ".example", ".java", ".md", ".sh", ".txt", ".xml"}
)
SECRET_PATTERNS = (
    re.compile(r"BEGIN (?:RSA|OPENSSH|EC) PRIVATE KEY", re.IGNORECASE),
    re.compile(
        r"authorization\s*:\s*(?:Basic|Bearer)\s+\S+",
        re.IGNORECASE,
    ),
    re.compile(r"bearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(
        r"[?&](?:access_token|api_key|client_secret|sig|token)=[^&#\s]+",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:password|passwd|pwd|api[_ -]?key|client[_ -]?secret|"
        r"access[_ -]?token)\b\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{16,}",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class ArchiveRecord:
    source: str
    target: str | None
    library: str | None
    status: str
    size: int
    sha256: str


def safe_member(info: ZipInfo) -> bool:
    path = PurePosixPath(info.filename)
    return not (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in info.filename
        or info.is_dir()
    )


def extract_searchable_text(name: str, data: bytes) -> str:
    suffix = PurePosixPath(name).suffix.casefold()
    if suffix == ".pdf":
        from io import BytesIO

        return "\n".join(
            (page.extract_text() or "") for page in PdfReader(BytesIO(data)).pages
        )
    return data.decode("utf-8", errors="replace")


def contains_probable_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def write_knowledge_maps(
    records: list[ArchiveRecord], output_root: Path, archive_name: str
) -> None:
    grouped: dict[str, list[ArchiveRecord]] = defaultdict(list)
    for record in records:
        if record.status == "imported" and record.library:
            grouped[record.library].append(record)

    for library, items in sorted(grouped.items()):
        lines = [
            "---",
            f'title: "Kenniskaart {archive_name} — {LIBRARY_NAMES[library]}"',
            'source_type: "derived-routing-map"',
            f'source_archive: "{archive_name}"',
            f'library: "{library}"',
            'visibility: "tailnet-private"',
            "---",
            "",
            f"# Kenniskaart {archive_name} — {LIBRARY_NAMES[library]}",
            "",
            "Deze kaart routeert vragen naar de geïmporteerde naslagbronnen. "
            "De oorspronkelijke bestanden blijven de feitelijke bron.",
            "",
            "## Geïmporteerde bronnen",
            "",
        ]
        for item in sorted(items, key=lambda value: value.source.casefold()):
            lines.append(f"- {item.source}")
        lines.extend(
            [
                "",
                "## Gebruik",
                "",
                "Controleer bij operationele instructies altijd productversie, "
                "datum en omgevingsspecifieke aannames in de bron.",
            ]
        )
        target = output_root / library / "archief-2026-08-09" / "00-kenniskaart.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def import_nested_project(data: bytes, output_root: Path) -> list[ArchiveRecord]:
    from io import BytesIO

    records: list[ArchiveRecord] = []
    with ZipFile(BytesIO(data)) as nested:
        for info in nested.infolist():
            if not safe_member(info):
                continue
            member = PurePosixPath(info.filename)
            if member.parts[:1] != NESTED_ROOT.parts:
                continue
            if any(part.startswith("._") for part in member.parts):
                continue
            if member.name in {".gitkeep", "tdv-view-exporter.jar"}:
                continue
            if member.suffix.casefold() not in NESTED_ALLOWED_SUFFIXES:
                continue

            member_data = nested.read(info)
            digest = hashlib.sha256(member_data).hexdigest()
            text = member_data.decode("utf-8", errors="replace")
            if contains_probable_secret(text):
                records.append(
                    ArchiveRecord(
                        source=f"{NESTED_ARCHIVE}!/{info.filename}",
                        target=None,
                        library="project-tdv-view-exporter",
                        status="skipped-sensitive",
                        size=len(member_data),
                        sha256=digest,
                    )
                )
                continue

            relative = Path(*member.relative_to(NESTED_ROOT).parts)
            target = (
                output_root
                / "project-tdv-view-exporter"
                / "archief-2026-08-09"
                / "tdv-view-exporter"
                / relative
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(member_data)
            records.append(
                ArchiveRecord(
                    source=f"{NESTED_ARCHIVE}!/{info.filename}",
                    target=str(target),
                    library="project-tdv-view-exporter",
                    status="imported",
                    size=len(member_data),
                    sha256=digest,
                )
            )
    return records


def import_archive(archive: Path, output_root: Path) -> list[ArchiveRecord]:
    records: list[ArchiveRecord] = []
    seen_hashes: set[str] = set()
    with ZipFile(archive) as source_zip:
        infos = [info for info in source_zip.infolist() if safe_member(info)]
        infos.sort(
            key=lambda info: (
                bool(re.search(r"\(\d+\)(?=\.[^.]+$)", info.filename)),
                info.filename.casefold(),
            )
        )
        for info in infos:
            data = source_zip.read(info)
            if info.filename == NESTED_ARCHIVE:
                records.extend(import_nested_project(data, output_root))
                continue

            library = FILE_TO_LIBRARY.get(info.filename)
            digest = hashlib.sha256(data).hexdigest()
            if library is None:
                records.append(
                    ArchiveRecord(
                        source=info.filename,
                        target=None,
                        library=None,
                        status="unmapped",
                        size=len(data),
                        sha256=digest,
                    )
                )
                continue
            if digest in seen_hashes:
                records.append(
                    ArchiveRecord(
                        source=info.filename,
                        target=None,
                        library=library,
                        status="skipped-duplicate",
                        size=len(data),
                        sha256=digest,
                    )
                )
                continue
            if contains_probable_secret(extract_searchable_text(info.filename, data)):
                records.append(
                    ArchiveRecord(
                        source=info.filename,
                        target=None,
                        library=library,
                        status="skipped-sensitive",
                        size=len(data),
                        sha256=digest,
                    )
                )
                continue

            seen_hashes.add(digest)
            target = output_root / library / "archief-2026-08-09" / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            records.append(
                ArchiveRecord(
                    source=info.filename,
                    target=str(target),
                    library=library,
                    status="imported",
                    size=len(data),
                    sha256=digest,
                )
            )

    write_knowledge_maps(records, output_root, archive.name)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output-root", type=Path, default=Path("data/import"))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/import/reference-archive-manifest.json"),
    )
    args = parser.parse_args()
    archive = args.archive.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    if not archive.is_file():
        raise SystemExit(f"Archief bestaat niet: {archive}")

    records = import_archive(archive, output_root)
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_archive": archive.name,
        "records": [asdict(record) for record in records],
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    counts = Counter(record.status for record in records)
    libraries = Counter(
        record.library for record in records if record.status == "imported"
    )
    print(f"Archiefimport klaar: {dict(counts)}")
    print(f"Bibliotheken: {dict(libraries)}")
    print(f"Manifest: {args.manifest.resolve()}")


if __name__ == "__main__":
    main()
