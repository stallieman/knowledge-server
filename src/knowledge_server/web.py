# ruff: noqa: E501
from __future__ import annotations

import threading
import uuid
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from knowledge_server.config import Settings
from knowledge_server.document_jobs import DocumentJobBackend, DocumentJobManager
from knowledge_server.service import KnowledgeService
from knowledge_server.video_jobs import VideoJobBackend, VideoJobManager


class QueryRequest(BaseModel):
    """Search or answer request from the local web client."""

    question: str = Field(min_length=1, max_length=4000)
    libraries: list[str] = Field(default_factory=list)
    top_k: int = Field(default=6, ge=1, le=20)


INDEX_HTML = """<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lokale kennisserver</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    body { max-width: 920px; margin: 0 auto; padding: 2rem 1rem 4rem; background: #101418; color: #e8edf2; }
    h1 { margin-bottom: .25rem; }
    .subtitle { color: #aab5c0; margin-top: 0; }
    fieldset { border: 1px solid #33404c; border-radius: .75rem; margin: 1.5rem 0; padding: 1rem; }
    .panel { border: 1px solid #33404c; border-radius: .85rem; margin: 1.5rem 0; padding: 1.2rem; }
    .row { display: flex; flex-wrap: wrap; gap: .8rem; align-items: end; }
    .row > label { flex: 1 1 12rem; }
    input[type=file], select { box-sizing: border-box; width: 100%; margin-top: .35rem; padding: .6rem; border: 1px solid #465563; border-radius: .5rem; background: #182027; color: inherit; }
    .job { border-top: 1px solid #33404c; padding: .8rem 0; }
    .job:first-child { border-top: 0; }
    .badge { display: inline-block; border-radius: 99px; padding: .15rem .55rem; background: #283746; font-size: .8rem; }
    .error { color: #ff9a9a; white-space: pre-wrap; }
    #libraries { display: flex; flex-wrap: wrap; gap: .6rem 1rem; }
    label { cursor: pointer; }
    textarea { box-sizing: border-box; width: 100%; min-height: 8rem; padding: .8rem; border-radius: .6rem; border: 1px solid #465563; background: #182027; color: inherit; }
    button { margin-top: .8rem; padding: .65rem 1.1rem; border: 0; border-radius: .5rem; background: #5da9ff; color: #07111c; font-weight: 700; cursor: pointer; }
    button:disabled { opacity: .55; cursor: wait; }
    #status { color: #aab5c0; min-height: 1.4rem; }
    #answer { white-space: pre-wrap; line-height: 1.55; background: #182027; border-radius: .75rem; padding: 1rem; }
    .source { border-left: 3px solid #5da9ff; padding: .5rem .8rem; margin: .8rem 0; background: #151c22; }
    .source small { color: #aab5c0; overflow-wrap: anywhere; }
  </style>
</head>
<body>
  <h1>Lokale kennisserver</h1>
  <p class="subtitle">Vraag alleen de bibliotheken die je bewust selecteert.</p>
  <section class="panel">
    <h2>Video transcriberen</h2>
    <p class="subtitle">Upload een MP4. De server verwerkt taken één voor één en indexeert het transcript automatisch.</p>
    <div class="row">
      <label>Video<input id="video" type="file" accept="video/mp4,.mp4"></label>
      <label>Taal<select id="video-language"><option value="">Automatisch</option><option value="nl">Nederlands</option><option value="en">Engels</option></select></label>
      <label>Analyse<select id="analysis-type"><option value="meeting">Algemene meeting</option><option value="data-engineering">Data engineering</option></select></label>
    </div>
    <button id="upload-video">Upload en start</button>
    <p id="upload-status"></p>
    <div id="jobs"></div>
  </section>
  <section class="panel">
    <h2>Document toevoegen</h2>
    <p class="subtitle">Kies zelf een bibliotheek of laat de server een transparante suggestie doen.</p>
    <div class="row">
      <label>Document<input id="document" type="file"></label>
      <label>Bibliotheek<select id="document-library"><option value="">Automatische suggestie</option></select></label>
    </div>
    <button id="upload-document">Upload en indexeer</button>
    <p id="document-upload-status"></p>
    <div id="document-jobs"></div>
  </section>
  <fieldset>
    <legend>Bibliotheken</legend>
    <div id="libraries">Laden…</div>
  </fieldset>
  <label for="question">Vraag</label>
  <textarea id="question" placeholder="Wat wil je in je eigen documenten opzoeken?"></textarea>
  <button id="ask">Vraag stellen</button>
  <p id="status"></p>
  <section id="output" hidden>
    <h2>Antwoord</h2>
    <div id="answer"></div>
    <h2>Gebruikte bronnen</h2>
    <div id="sources"></div>
  </section>
  <script>
    const librariesElement = document.querySelector('#libraries');
    const questionElement = document.querySelector('#question');
    const askButton = document.querySelector('#ask');
    const statusElement = document.querySelector('#status');
    const outputElement = document.querySelector('#output');
    const answerElement = document.querySelector('#answer');
    const sourcesElement = document.querySelector('#sources');
    const videoElement = document.querySelector('#video');
    const uploadButton = document.querySelector('#upload-video');
    const uploadStatus = document.querySelector('#upload-status');
    const jobsElement = document.querySelector('#jobs');
    const documentElement = document.querySelector('#document');
    const documentLibrary = document.querySelector('#document-library');
    const documentUploadButton = document.querySelector('#upload-document');
    const documentUploadStatus = document.querySelector('#document-upload-status');
    const documentJobsElement = document.querySelector('#document-jobs');

    async function loadLibraries() {
      const response = await fetch('/api/libraries');
      const libraries = await response.json();
      librariesElement.textContent = '';
      for (const library of libraries) {
        const label = document.createElement('label');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = library.slug;
        label.append(checkbox, ` ${library.name} (${library.document_count})`);
        librariesElement.append(label);
        const option = document.createElement('option');
        option.value = library.slug;
        option.textContent = library.name;
        documentLibrary.append(option);
      }
    }

    async function loadDocumentJobs() {
      const response = await fetch('/api/document-jobs');
      const jobs = await response.json();
      documentJobsElement.textContent = '';
      for (const job of jobs) {
        const article = document.createElement('article');
        article.className = 'job';
        const heading = document.createElement('strong');
        heading.textContent = job.filename;
        const badge = document.createElement('span');
        badge.className = 'badge';
        badge.textContent = jobLabel(job.status);
        const progress = document.createElement('div');
        progress.textContent = `${job.progress} · ${job.library_slug}`;
        const suggestion = document.createElement('small');
        suggestion.textContent = `Suggestie: ${job.suggested_library_slug}`;
        article.append(heading, ' ', badge, progress, suggestion);
        if (job.error) {
          const error = document.createElement('div');
          error.className = 'error';
          error.textContent = job.error;
          article.append(error);
        }
        documentJobsElement.append(article);
      }
    }

    documentUploadButton.addEventListener('click', async () => {
      const file = documentElement.files[0];
      if (!file) { documentUploadStatus.textContent = 'Kies eerst een document.'; return; }
      documentUploadButton.disabled = true;
      documentUploadStatus.textContent = 'Document uploaden…';
      const params = new URLSearchParams({
        filename: file.name, library: documentLibrary.value,
      });
      try {
        const response = await fetch(`/api/document-jobs?${params}`, {
          method: 'POST', body: file,
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Upload mislukt');
        documentUploadStatus.textContent = `In wachtrij voor ${payload.library_slug}.`;
        documentElement.value = '';
        await loadDocumentJobs();
      } catch (error) {
        documentUploadStatus.textContent = `Fout: ${error.message}`;
      } finally { documentUploadButton.disabled = false; }
    });

    function jobLabel(status) {
      return {queued: 'In wachtrij', processing: 'Bezig', completed: 'Klaar', failed: 'Mislukt'}[status] || status;
    }

    async function loadJobs() {
      const response = await fetch('/api/video-jobs');
      const jobs = await response.json();
      jobsElement.textContent = '';
      for (const job of jobs) {
        const article = document.createElement('article');
        article.className = 'job';
        const heading = document.createElement('strong');
        heading.textContent = job.filename;
        const badge = document.createElement('span');
        badge.className = 'badge';
        badge.textContent = jobLabel(job.status);
        const progress = document.createElement('div');
        progress.textContent = job.progress;
        article.append(heading, ' ', badge, progress);
        if (job.error) {
          const error = document.createElement('div');
          error.className = 'error';
          error.textContent = job.error;
          article.append(error);
        }
        jobsElement.append(article);
      }
    }

    uploadButton.addEventListener('click', async () => {
      const file = videoElement.files[0];
      if (!file) { uploadStatus.textContent = 'Kies eerst een MP4-video.'; return; }
      uploadButton.disabled = true;
      uploadStatus.textContent = 'Video uploaden…';
      const params = new URLSearchParams({
        filename: file.name,
        language: document.querySelector('#video-language').value,
        analysis_type: document.querySelector('#analysis-type').value,
      });
      try {
        const response = await fetch(`/api/video-jobs?${params}`, {
          method: 'POST', headers: {'Content-Type': 'video/mp4'}, body: file,
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Upload mislukt');
        uploadStatus.textContent = 'Video staat in de wachtrij.';
        videoElement.value = '';
        await loadJobs();
      } catch (error) {
        uploadStatus.textContent = `Fout: ${error.message}`;
      } finally { uploadButton.disabled = false; }
    });

    askButton.addEventListener('click', async () => {
      const libraries = [...document.querySelectorAll('#libraries input:checked')]
        .map(input => input.value);
      const question = questionElement.value.trim();
      if (!question) {
        statusElement.textContent = 'Vul eerst een vraag in.';
        return;
      }

      askButton.disabled = true;
      statusElement.textContent = 'Bronnen zoeken en lokaal antwoord genereren…';
      outputElement.hidden = true;
      try {
        const response = await fetch('/api/ask', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({question, libraries, top_k: 6}),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Onbekende fout');

        answerElement.textContent = `${payload.notice}\n\n${payload.answer}`;
        sourcesElement.textContent = '';
        for (const source of payload.sources) {
          const article = document.createElement('article');
          article.className = 'source';
          const title = document.createElement('strong');
          title.textContent = `[${source.source_id}] ${source.source_name}`;
          const details = document.createElement('small');
          details.textContent = `${source.library_slug} · regels ${source.start_line}-${source.end_line} · score ${source.score.toFixed(3)} · ${source.source_path}`;
          article.append(title, document.createElement('br'), details);
          sourcesElement.append(article);
        }
        outputElement.hidden = false;
        statusElement.textContent = '';
      } catch (error) {
        statusElement.textContent = `Fout: ${error.message}`;
      } finally {
        askButton.disabled = false;
      }
    });

    loadLibraries().catch(error => {
      librariesElement.textContent = `Bibliotheken konden niet worden geladen: ${error.message}`;
    });
    loadJobs().catch(error => { jobsElement.textContent = `Taken konden niet worden geladen: ${error.message}`; });
    loadDocumentJobs().catch(error => { documentJobsElement.textContent = `Documenttaken konden niet worden geladen: ${error.message}`; });
    setInterval(loadJobs, 4000);
    setInterval(loadDocumentJobs, 4000);
  </script>
</body>
</html>
"""


def create_app(
    settings: Settings | None = None,
    *,
    service: KnowledgeService | None = None,
    video_jobs: VideoJobBackend | None = None,
    document_jobs: DocumentJobBackend | None = None,
) -> FastAPI:
    """Create the local FastAPI application."""
    resolved_settings = settings or Settings()
    knowledge_service = service or KnowledgeService(resolved_settings)
    knowledge_service.initialize()
    workload_lock = threading.Lock()
    video_job_manager = video_jobs or VideoJobManager(
        resolved_settings,
        knowledge_service,
        workload_lock=workload_lock,
    )
    document_job_manager = document_jobs or DocumentJobManager(
        resolved_settings,
        knowledge_service,
        workload_lock=workload_lock,
    )

    app = FastAPI(
        title="Lokale kennisserver",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url=None,
    )

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return INDEX_HTML

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/libraries")
    async def libraries() -> list[dict]:
        return [asdict(library) for library in knowledge_service.list_libraries()]

    @app.get("/api/video-jobs")
    async def list_video_jobs() -> list[dict]:
        return [job.to_dict() for job in video_job_manager.list_jobs()]

    @app.post("/api/video-jobs", status_code=202)
    async def create_video_job(
        request: Request,
        filename: str = Query(min_length=1, max_length=255),
        language: str = Query(default="", pattern="^(|nl|en)$"),
        analysis_type: str = Query(
            default="meeting", pattern="^(meeting|data-engineering)$"
        ),
    ) -> dict:
        incoming_dir = resolved_settings.video_upload_path / ".incoming"
        incoming_dir.mkdir(parents=True, exist_ok=True)
        staged_path = incoming_dir / f"upload-{uuid.uuid4().hex}"
        size = 0
        try:
            with staged_path.open("xb") as target:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 20 * 1024 * 1024 * 1024:
                        raise ValueError("Video is groter dan de limiet van 20 GB.")
                    target.write(chunk)
            job = video_job_manager.create_job_from_path(
                filename,
                staged_path,
                language=language or None,
                analysis_type=analysis_type,
            )
            return job.to_dict()
        except (FileExistsError, OSError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        finally:
            staged_path.unlink(missing_ok=True)

    @app.get("/api/document-jobs")
    async def list_document_jobs() -> list[dict]:
        return [job.to_dict() for job in document_job_manager.list_jobs()]

    @app.post("/api/document-jobs", status_code=202)
    async def create_document_job(
        request: Request,
        filename: str = Query(min_length=1, max_length=255),
        library: str = Query(default="", max_length=100),
    ) -> dict:
        incoming_dir = resolved_settings.document_upload_path / ".incoming"
        incoming_dir.mkdir(parents=True, exist_ok=True)
        staged_path = incoming_dir / f"upload-{uuid.uuid4().hex}"
        size = 0
        try:
            with staged_path.open("xb") as target:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 200 * 1024 * 1024:
                        raise ValueError("Document is groter dan de limiet van 200 MB.")
                    target.write(chunk)
            job = document_job_manager.create_job_from_path(
                filename,
                staged_path,
                library_slug=library or None,
            )
            return job.to_dict()
        except (FileExistsError, OSError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        finally:
            staged_path.unlink(missing_ok=True)

    @app.post("/api/search")
    async def search(request: QueryRequest) -> dict:
        try:
            results = knowledge_service.search(
                request.question,
                request.libraries,
                top_k=request.top_k,
            )
            return {"results": [asdict(result) for result in results]}
        except (KeyError, ValueError, RuntimeError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/ask")
    async def ask(request: QueryRequest) -> dict:
        try:
            result = knowledge_service.answer(
                request.question,
                request.libraries,
                top_k=request.top_k,
            )
            return {
                "answer": result.answer,
                "mode": result.mode,
                "notice": result.notice,
                "sources": [asdict(source) for source in result.sources],
            }
        except (KeyError, ValueError, RuntimeError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    return app
