# ruff: noqa: E501
from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from knowledge_server.config import Settings
from knowledge_server.service import KnowledgeService


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
      }
    }

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
  </script>
</body>
</html>
"""


def create_app(
    settings: Settings | None = None,
    *,
    service: KnowledgeService | None = None,
) -> FastAPI:
    """Create the local FastAPI application."""
    resolved_settings = settings or Settings()
    knowledge_service = service or KnowledgeService(resolved_settings)
    knowledge_service.initialize()

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
