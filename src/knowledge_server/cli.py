from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer

from knowledge_server.config import Settings
from knowledge_server.maintenance import MaintenanceService
from knowledge_server.service import KnowledgeService
from knowledge_server.web import create_app

DEFAULT_LIBRARIES = (
    ("wbih", "WBIH"),
    ("sql", "SQL"),
    ("linux", "Linux"),
    ("elastic-stack", "Elastic Stack"),
    ("devops-cloud", "DevOps & Cloud"),
    ("software-development", "Softwareontwikkeling"),
    ("security-osint", "Security & OSINT"),
    ("persoonlijk", "Persoonlijk"),
    ("power-bi", "Power BI"),
    ("ai-engineering", "AI Engineering"),
    ("home-energy", "Home Energy"),
    ("woii", "WOII"),
    ("persoonlijke-videos", "Persoonlijke video's"),
    ("chatgpt-archief", "ChatGPT-archief"),
)

app = typer.Typer(no_args_is_help=True)
library_app = typer.Typer(no_args_is_help=True)
app.add_typer(library_app, name="library")


def _service() -> KnowledgeService:
    service = KnowledgeService(Settings())
    service.initialize()
    return service


@app.command("init")
def initialize() -> None:
    """Initialize storage and the agreed default libraries."""
    service = _service()
    for slug, name in DEFAULT_LIBRARIES:
        service.create_library(name, slug)
        (Path("data/import") / slug).mkdir(parents=True, exist_ok=True)
    typer.echo(f"Database initialized: {service.settings.database_path}")
    typer.echo(f"Libraries available: {len(DEFAULT_LIBRARIES)}")


@library_app.command("create")
def create_library(
    name: Annotated[str, typer.Argument(help="Display name of the library.")],
    slug: Annotated[
        str | None,
        typer.Option(help="Optional stable library slug."),
    ] = None,
) -> None:
    """Create or rename a logical knowledge library."""
    library = _service().create_library(name, slug)
    typer.echo(f"Library ready: {library.slug} ({library.name})")


@library_app.command("list")
def list_libraries() -> None:
    """List libraries and their indexed content counts."""
    libraries = _service().list_libraries()
    if not libraries:
        typer.echo("No libraries found. Run: knowledge-server init")
        return

    for library in libraries:
        typer.echo(
            f"{library.slug:<22} {library.name:<24} "
            f"documents={library.document_count} chunks={library.chunk_count}"
        )


@app.command()
def ingest(
    library: Annotated[str, typer.Argument(help="Target library slug.")],
    source: Annotated[Path, typer.Argument(help="File or directory to index.")],
) -> None:
    """Index Markdown, text, and SQL files into one library."""
    try:
        report = _service().ingest(library, source)
    except (FileNotFoundError, KeyError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error

    typer.echo(f"Discovered: {report.discovered}")
    typer.echo(f"Indexed: {report.indexed}")
    typer.echo(f"Unchanged: {report.unchanged}")
    typer.echo(f"Chunks written: {report.chunks_written}")


@app.command("ingest-git")
def ingest_git(
    library: Annotated[str, typer.Argument(help="Target project library slug.")],
    repository: Annotated[
        Path,
        typer.Argument(help="Local Git checkout; only tracked safe files are indexed."),
    ],
) -> None:
    """Index a local Git snapshot without reading ignored or secret files."""
    try:
        report = _service().ingest_git(library, repository)
    except (FileNotFoundError, KeyError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error

    typer.echo(f"Tracked and supported: {report.discovered}")
    typer.echo(f"Indexed: {report.indexed}")
    typer.echo(f"Unchanged: {report.unchanged}")
    typer.echo(f"Removed from index: {report.removed}")
    typer.echo(f"Chunks written: {report.chunks_written}")


@app.command("ingest-root")
def ingest_root(
    source_root: Annotated[
        Path,
        typer.Argument(help="Root with one subdirectory per library slug."),
    ] = Path("data/import"),
) -> None:
    """Incrementally index every known library folder below one root."""
    resolved_root = source_root.expanduser().resolve()
    if not resolved_root.exists():
        raise typer.BadParameter(f"Import root does not exist: {resolved_root}")

    service = _service()
    totals = {
        "discovered": 0,
        "indexed": 0,
        "unchanged": 0,
        "chunks": 0,
        "removed": 0,
    }

    for library in service.list_libraries():
        library_dir = resolved_root / library.slug
        if not library_dir.is_dir():
            continue
        report = service.ingest(library.slug, library_dir)
        totals["discovered"] += report.discovered
        totals["indexed"] += report.indexed
        totals["unchanged"] += report.unchanged
        totals["chunks"] += report.chunks_written
        totals["removed"] += report.removed

    typer.echo(
        "Import complete: "
        f"discovered={totals['discovered']} "
        f"indexed={totals['indexed']} "
        f"unchanged={totals['unchanged']} "
        f"removed={totals['removed']} "
        f"chunks={totals['chunks']}"
    )


@app.command("ingest-all")
def ingest_all(
    source_root: Annotated[
        Path,
        typer.Option(help="Root with one subdirectory per library slug."),
    ] = Path("data/import"),
    git_sources: Annotated[
        Path,
        typer.Option(help="JSON configuration with local Git project sources."),
    ] = Path("config/git-sources.json"),
) -> None:
    """Index document libraries and configured local Git snapshots."""
    resolved_root = source_root.expanduser().resolve()
    resolved_git_sources = git_sources.expanduser().resolve()
    if not resolved_root.is_dir():
        raise typer.BadParameter(f"Import root does not exist: {resolved_root}")
    if not resolved_git_sources.is_file():
        raise typer.BadParameter(
            f"Git source configuration does not exist: {resolved_git_sources}"
        )

    try:
        configured_sources = json.loads(
            resolved_git_sources.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError) as error:
        raise typer.BadParameter(
            f"Invalid Git source configuration: {error}"
        ) from error
    if not isinstance(configured_sources, list):
        raise typer.BadParameter("Git source configuration must be a JSON list")

    service = _service()
    totals = {
        "discovered": 0,
        "indexed": 0,
        "unchanged": 0,
        "removed": 0,
        "chunks": 0,
    }
    for library in service.list_libraries():
        library_dir = resolved_root / library.slug
        if not library_dir.is_dir():
            continue
        report = service.ingest(library.slug, library_dir)
        totals["discovered"] += report.discovered
        totals["indexed"] += report.indexed
        totals["unchanged"] += report.unchanged
        totals["removed"] += report.removed
        totals["chunks"] += report.chunks_written

    for source in configured_sources:
        if not isinstance(source, dict):
            raise typer.BadParameter("Every Git source must be a JSON object")
        library_slug = str(source.get("library", "")).strip()
        repository_value = str(source.get("repository", "")).strip()
        if not library_slug or not repository_value:
            raise typer.BadParameter(
                "Every Git source needs library and repository fields"
            )
        report = service.ingest_git(library_slug, Path(repository_value))
        totals["discovered"] += report.discovered
        totals["indexed"] += report.indexed
        totals["unchanged"] += report.unchanged
        totals["removed"] += report.removed
        totals["chunks"] += report.chunks_written

    typer.echo(
        "All imports complete: "
        f"discovered={totals['discovered']} "
        f"indexed={totals['indexed']} "
        f"unchanged={totals['unchanged']} "
        f"removed={totals['removed']} "
        f"chunks={totals['chunks']}"
    )


@app.command()
def search(
    question: Annotated[str, typer.Argument(help="Semantic search query.")],
    library: Annotated[
        list[str],
        typer.Option(
            "--library",
            "-l",
            help="Library slug; repeat to search multiple libraries.",
        ),
    ],
    top_k: Annotated[int, typer.Option(min=1, max=20)] = 6,
    brief: Annotated[
        bool,
        typer.Option(help="Omit source content and print ranking metadata only."),
    ] = False,
) -> None:
    """Search selected libraries without generating an answer."""
    try:
        results = _service().search(question, library, top_k=top_k)
    except (KeyError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error

    payload = [asdict(result) for result in results]
    if brief:
        for item in payload:
            item.pop("content", None)
    typer.echo(json.dumps(payload, indent=2))


@app.command()
def ask(
    question: Annotated[
        str,
        typer.Argument(help="Question for the knowledge base."),
    ],
    library: Annotated[
        list[str] | None,
        typer.Option(
            "--library",
            "-l",
            help="Optional library slug; repeat to search multiple libraries.",
        ),
    ] = None,
    top_k: Annotated[int, typer.Option(min=1, max=20)] = 6,
) -> None:
    """Answer from selected libraries and show exact source locations."""
    try:
        result = _service().answer(question, library or [], top_k=top_k)
    except (KeyError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error

    typer.echo(f"{result.notice}\n")
    typer.echo(result.answer)
    if result.sources:
        typer.echo("\nBronnen:")
        for source in result.sources:
            typer.echo(
                f"[{source.source_id}] {source.source_path}:"
                f"{source.start_line}-{source.end_line} (score {source.score:.3f})"
            )


@app.command()
def serve(
    host: Annotated[
        str,
        typer.Option(help="Keep localhost until authentication is configured."),
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8000,
) -> None:
    """Start the local browser interface."""
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port)


@app.command()
def backup() -> None:
    """Create and rotate a consistent SQLite backup."""
    settings = Settings()
    service = KnowledgeService(settings)
    maintenance = MaintenanceService(settings, service.ollama)
    typer.echo(f"Backup created: {maintenance.backup()}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
