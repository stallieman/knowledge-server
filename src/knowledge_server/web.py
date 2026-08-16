# ruff: noqa: E501
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    StreamingResponse,
)
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from knowledge_server.archive_jobs import ArchiveJobBackend, ArchiveJobManager
from knowledge_server.chat import ChatStore
from knowledge_server.config import Settings
from knowledge_server.document_jobs import DocumentJobBackend, DocumentJobManager
from knowledge_server.documents import load_document
from knowledge_server.maintenance import MaintenanceService
from knowledge_server.security import AccessAudit, TailnetSecurityMiddleware
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
    nav { position: sticky; top: 0; z-index: 2; display: flex; gap: .5rem; overflow-x: auto; padding: .7rem 0; background: #101418ee; backdrop-filter: blur(8px); }
    nav a, .action { color: #9dccff; text-decoration: none; padding: .45rem .65rem; border-radius: .45rem; background: #182027; white-space: nowrap; }
    .actions { display: flex; flex-wrap: wrap; gap: .4rem; }
    .actions button { margin-top: .35rem; padding: .4rem .65rem; }
    .danger { background: #ff8f8f; }
    .message { padding: .8rem; border-radius: .6rem; margin: .6rem 0; white-space: pre-wrap; }
    .message.user { background: #203349; margin-left: 10%; }
    .message.assistant { background: #182027; margin-right: 10%; }
    .health-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: .6rem; }
    .health-grid div { background: #182027; padding: .7rem; border-radius: .5rem; }
    @media (max-width: 600px) { body { padding-top: .5rem; } .panel { padding: .8rem; } h1 { font-size: 1.6rem; } button { min-height: 44px; } }
  </style>
</head>
<body>
  <h1>Lokale kennisserver</h1>
  <p class="subtitle">Vraag alleen de bibliotheken die je bewust selecteert.</p>
  <nav><a href="#chat">Chat</a><a href="#documents">Documenten</a><a href="#videos">Video's</a><a href="#system">Systeem</a></nav>
  <section class="panel" id="videos">
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
  <section class="panel" id="documents">
    <h2>Document toevoegen</h2>
    <p class="subtitle">Kies zelf een bibliotheek of laat de server een transparante suggestie doen.</p>
    <div class="row">
      <label>Document<input id="document" type="file"></label>
      <label>Bibliotheek<select id="document-library"><option value="">Automatische suggestie</option></select></label>
    </div>
    <button id="upload-document">Upload en indexeer</button>
    <p id="document-upload-status"></p>
    <div id="document-jobs"></div>
    <h3>ZIP-archief importeren</h3>
    <p class="subtitle">Documenten worden per inhoud ingedeeld; MP4-video's gaan naar de videowachtrij.</p>
    <div class="row">
      <label>ZIP-bestand<input id="archive" type="file" accept=".zip,application/zip"></label>
      <label>Bestemming<select id="archive-library"><option value="">Automatisch per document</option></select></label>
    </div>
    <button id="upload-archive">Upload en organiseer</button>
    <p id="archive-upload-status"></p>
    <div id="archive-jobs"></div>
    <h3>Geïndexeerde documenten</h3>
    <div class="row"><label>Categorie<select id="browse-library"><option value="">Alle categorieën</option></select></label><button id="upload-to-category">Nieuw document in deze categorie</button></div>
    <div id="indexed-documents">Laden…</div>
  </section>
  <section class="panel" id="chat">
  <div class="row"><label>Gesprek<select id="conversation"></select></label><button id="new-conversation">Nieuw gesprek</button><button id="export-conversation">Exporteren</button></div>
  <div id="messages"></div>
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
  </section>
  <section class="panel" id="system">
    <h2>Systeemstatus</h2>
    <div id="system-health" class="health-grid">Laden…</div>
    <div class="actions"><button id="create-backup">Maak back-up</button><button id="refresh-system">Vernieuwen</button></div>
    <p id="backup-status"></p>
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
    const indexedDocuments = document.querySelector('#indexed-documents');
    const conversationElement = document.querySelector('#conversation');
    const messagesElement = document.querySelector('#messages');
    const systemHealth = document.querySelector('#system-health');
    const archiveElement = document.querySelector('#archive');
    const archiveLibrary = document.querySelector('#archive-library');
    const archiveJobsElement = document.querySelector('#archive-jobs');
    const archiveUploadStatus = document.querySelector('#archive-upload-status');
    const browseLibrary = document.querySelector('#browse-library');

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
        for (const target of [archiveLibrary, browseLibrary]) {
          const categoryOption = document.createElement('option');
          categoryOption.value = library.slug;
          categoryOption.textContent = library.name;
          target.append(categoryOption);
        }
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
        const actions = document.createElement('div');
        actions.className = 'actions';
        if (job.status === 'failed') actions.append(actionButton('Opnieuw', () => apiAction(`/api/document-jobs/${job.id}/retry`)));
        if (!['queued', 'processing'].includes(job.status)) actions.append(actionButton('Verwijder taak', () => apiAction(`/api/document-jobs/${job.id}`, 'DELETE')));
        article.append(actions);
        if (job.error) {
          const error = document.createElement('div');
          error.className = 'error';
          error.textContent = job.error;
          article.append(error);
        }
        documentJobsElement.append(article);
      }
    }

    function actionButton(label, handler, danger=false) {
      const button = document.createElement('button');
      button.textContent = label;
      if (danger) button.className = 'danger';
      button.addEventListener('click', handler);
      return button;
    }

    async function apiAction(url, method='POST') {
      const response = await fetch(url, {method});
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.detail || 'Actie mislukt');
      }
      await Promise.all([loadJobs(), loadDocumentJobs(), loadIndexedDocuments()]);
      return response.status === 204 ? null : response.json();
    }

    async function loadIndexedDocuments() {
      const response = await fetch('/api/documents');
      const documents = await response.json();
      indexedDocuments.textContent = '';
      for (const item of documents) {
        if (browseLibrary.value && item.library_slug !== browseLibrary.value) continue;
        const article = document.createElement('article');
        article.className = 'job';
        const title = document.createElement('strong');
        title.textContent = item.title || item.source_name;
        const details = document.createElement('div');
        details.textContent = `${item.library_slug} · ${item.chunk_count} fragmenten`;
        const summary = document.createElement('small');
        summary.textContent = item.summary || '';
        const actions = document.createElement('div');
        actions.className = 'actions';
        actions.append(
          actionButton('Bekijken', async () => {
            const data = await (await fetch(`/api/documents/${item.id}/preview`)).json();
            alert(data.content.slice(0, 12000));
          }),
          actionButton('Herindexeer', () => apiAction(`/api/documents/${item.id}/reindex`)),
          actionButton('Slim voorstel', async () => {
            const suggestion = await apiAction(`/api/documents/${item.id}/metadata-suggestion`);
            const description = `${suggestion.title}\n\n${suggestion.summary}\n\nTags: ${suggestion.tags.join(', ')}\nBibliotheek: ${suggestion.library_slug}`;
            if (confirm(`${description}\n\nDit voorstel toepassen?`)) await apiAction(`/api/metadata-suggestions/${suggestion.id}/approve`);
          }),
          actionButton('Verplaats', async () => {
            const library = prompt('Nieuwe bibliotheekslug:');
            if (library) await apiAction(`/api/documents/${item.id}/move?library=${encodeURIComponent(library)}`);
          }),
          actionButton('Verwijder', async () => {
            if (confirm('Document uit de index en importmap verwijderen?')) await apiAction(`/api/documents/${item.id}?delete_file=true`, 'DELETE');
          }, true),
        );
        article.append(title, details, summary, actions);
        indexedDocuments.append(article);
      }
    }

    browseLibrary.addEventListener('change', loadIndexedDocuments);
    document.querySelector('#upload-to-category').addEventListener('click', () => {
      if (!browseLibrary.value) {
        alert('Kies eerst een categorie.');
        return;
      }
      documentLibrary.value = browseLibrary.value;
      documentElement.click();
    });

    async function loadArchiveJobs() {
      const jobs = await (await fetch('/api/archive-jobs')).json();
      archiveJobsElement.textContent = '';
      for (const job of jobs) {
        const article = document.createElement('article');
        article.className = 'job';
        const title = document.createElement('strong'); title.textContent = job.filename;
        const badge = document.createElement('span'); badge.className = 'badge'; badge.textContent = jobLabel(job.status);
        const progress = document.createElement('div'); progress.textContent = job.progress;
        const actions = document.createElement('div'); actions.className = 'actions';
        actions.append(actionButton('Details', async () => {
          const items = await (await fetch(`/api/archive-jobs/${job.id}/items`)).json();
          alert(items.map(item => `${item.status}: ${item.source_name}${item.library_slug ? ` → ${item.library_slug}` : ''}${item.reason ? ` (${item.reason})` : ''}`).join('\\n') || 'Nog geen bestanden verwerkt.');
        }));
        article.append(title, ' ', badge, progress, actions);
        if (job.error) { const error = document.createElement('div'); error.className = 'error'; error.textContent = job.error; article.append(error); }
        archiveJobsElement.append(article);
      }
    }

    document.querySelector('#upload-archive').addEventListener('click', async () => {
      const file = archiveElement.files[0];
      if (!file) { archiveUploadStatus.textContent = 'Kies eerst een ZIP-bestand.'; return; }
      const button = document.querySelector('#upload-archive');
      button.disabled = true; archiveUploadStatus.textContent = 'ZIP uploaden…';
      const params = new URLSearchParams({filename: file.name, library: archiveLibrary.value});
      try {
        const response = await fetch(`/api/archive-jobs?${params}`, {method: 'POST', body: file});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'ZIP-upload mislukt');
        archiveUploadStatus.textContent = 'ZIP staat in de verwerkingswachtrij.';
        archiveElement.value = ''; await loadArchiveJobs();
      } catch (error) { archiveUploadStatus.textContent = `Fout: ${error.message}`; }
      finally { button.disabled = false; }
    });

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
      return {queued: 'In wachtrij', processing: 'Bezig', completed: 'Klaar', failed: 'Mislukt', cancelled: 'Gestopt'}[status] || status;
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
        const actions = document.createElement('div');
        actions.className = 'actions';
        if (['queued', 'processing'].includes(job.status)) actions.append(actionButton('Stop', () => apiAction(`/api/video-jobs/${job.id}/cancel`)));
        if (['failed', 'cancelled'].includes(job.status)) actions.append(actionButton('Opnieuw', () => apiAction(`/api/video-jobs/${job.id}/retry`)));
        if (job.transcript_path) actions.append(actionButton('Transcript', () => location.href=`/api/video-jobs/${job.id}/result/transcript`));
        if (job.summary_path) actions.append(actionButton('Samenvatting', () => location.href=`/api/video-jobs/${job.id}/result/summary`));
        if (!['queued', 'processing'].includes(job.status)) actions.append(actionButton('Verwijder', () => apiAction(`/api/video-jobs/${job.id}`, 'DELETE'), true));
        article.append(actions);
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
      if (window.useConversationChat) return;
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

    async function loadConversations(selectId) {
      const conversations = await (await fetch('/api/conversations')).json();
      conversationElement.textContent = '';
      for (const conversation of conversations) {
        const option = document.createElement('option');
        option.value = conversation.id;
        option.textContent = conversation.title;
        conversationElement.append(option);
      }
      if (selectId) conversationElement.value = selectId;
      if (!conversationElement.value) {
        const created = await (await fetch('/api/conversations', {method: 'POST'})).json();
        return loadConversations(created.id);
      }
      await loadMessages();
    }

    async function loadMessages() {
      if (!conversationElement.value) return;
      const messages = await (await fetch(`/api/conversations/${conversationElement.value}/messages`)).json();
      messagesElement.textContent = '';
      for (const message of messages) appendMessage(message.role, message.content, message.sources);
    }

    function appendMessage(role, content, sources=[]) {
      const article = document.createElement('article');
      article.className = `message ${role}`;
      article.textContent = content;
      for (const source of sources) {
        const detail = document.createElement('details');
        const label = document.createElement('summary');
        label.textContent = `[${source.source_id}] ${source.source_name} · ${source.library_slug} · regels ${source.start_line}-${source.end_line}`;
        const fragment = document.createElement('pre');
        fragment.textContent = source.content || 'Geen fragment opgeslagen.';
        detail.append(label, fragment);
        article.append(detail);
      }
      messagesElement.append(article);
      return article;
    }

    window.useConversationChat = true;
    askButton.addEventListener('click', async () => {
      const question = questionElement.value.trim();
      if (!question) return;
      const libraries = [...document.querySelectorAll('#libraries input:checked')].map(input => input.value);
      appendMessage('user', question);
      const answer = appendMessage('assistant', 'Bronnen zoeken…');
      questionElement.value = '';
      askButton.disabled = true;
      try {
        const response = await fetch(`/api/conversations/${conversationElement.value}/stream`, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({question, libraries, top_k: 6}),
        });
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '', text = '', sources = [];
        while (true) {
          const {value, done} = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, {stream: true});
          const events = buffer.split('\\n\\n'); buffer = events.pop();
          for (const event of events) {
            const type = event.match(/^event: (.+)$/m)?.[1];
            const data = event.match(/^data: (.+)$/m)?.[1];
            if (type === 'metadata') sources = JSON.parse(data).sources;
            if (type === 'chunk') { text += JSON.parse(data); answer.textContent = text; }
            if (type === 'error') throw new Error(JSON.parse(data));
          }
        }
        answer.remove();
        appendMessage('assistant', text, sources);
        await loadConversations(conversationElement.value);
      } catch (error) { answer.textContent = `Fout: ${error.message}`; }
      finally { askButton.disabled = false; }
    });

    document.querySelector('#new-conversation').addEventListener('click', async () => {
      const created = await (await fetch('/api/conversations', {method: 'POST'})).json();
      await loadConversations(created.id);
    });
    conversationElement.addEventListener('change', loadMessages);
    document.querySelector('#export-conversation').addEventListener('click', () => {
      if (conversationElement.value) location.href = `/api/conversations/${conversationElement.value}/export`;
    });

    async function loadSystemHealth() {
      const health = await (await fetch('/api/system-health')).json();
      systemHealth.textContent = '';
      for (const [key, value] of Object.entries(health)) {
        const item = document.createElement('div'); item.textContent = `${key}: ${value}`; systemHealth.append(item);
      }
    }
    document.querySelector('#refresh-system').addEventListener('click', loadSystemHealth);
    document.querySelector('#create-backup').addEventListener('click', async () => {
      const result = await apiAction('/api/backups');
      document.querySelector('#backup-status').textContent = `Back-up gemaakt: ${result.filename}`;
    });

    loadLibraries().catch(error => {
      librariesElement.textContent = `Bibliotheken konden niet worden geladen: ${error.message}`;
    });
    loadJobs().catch(error => { jobsElement.textContent = `Taken konden niet worden geladen: ${error.message}`; });
    loadDocumentJobs().catch(error => { documentJobsElement.textContent = `Documenttaken konden niet worden geladen: ${error.message}`; });
    loadIndexedDocuments().catch(error => { indexedDocuments.textContent = error.message; });
    loadConversations().catch(error => { messagesElement.textContent = error.message; });
    loadSystemHealth().catch(error => { systemHealth.textContent = error.message; });
    loadArchiveJobs().catch(error => { archiveJobsElement.textContent = error.message; });
    setInterval(loadJobs, 4000);
    setInterval(loadDocumentJobs, 4000);
    setInterval(loadArchiveJobs, 4000);
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
    archive_jobs: ArchiveJobBackend | None = None,
) -> FastAPI:
    """Create the local FastAPI application."""
    resolved_settings = settings or Settings()
    knowledge_service = service or KnowledgeService(resolved_settings)
    knowledge_service.initialize()
    workload_lock = threading.Lock()

    def locked_call(function, *args, **kwargs):
        with workload_lock:
            return function(*args, **kwargs)
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
    archive_job_manager = archive_jobs or ArchiveJobManager(
        resolved_settings.database_path,
        resolved_settings.archive_upload_path,
        document_job_manager,
        video_job_manager,
    )
    maintenance = MaintenanceService(resolved_settings, knowledge_service.ollama)
    chat_store = ChatStore(resolved_settings.database_path)

    app = FastAPI(
        title="Lokale kennisserver",
        version="0.3.0",
        docs_url="/api/docs",
        redoc_url=None,
    )
    access_audit = AccessAudit(resolved_settings.database_path)
    app.add_middleware(
        TailnetSecurityMiddleware,
        audit=access_audit,
        allowed_user=resolved_settings.allowed_tailscale_user,
    )

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return INDEX_HTML

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/system-health")
    async def system_health() -> dict:
        return asdict(maintenance.health())

    @app.post("/api/backups")
    async def create_backup() -> dict:
        path = maintenance.backup()
        return {"status": "ok", "filename": path.name}

    @app.get("/api/access-audit")
    async def access_audit_log() -> list[dict]:
        return access_audit.recent()

    @app.get("/api/libraries")
    async def libraries() -> list[dict]:
        return [asdict(library) for library in knowledge_service.list_libraries()]

    @app.post("/api/conversations", status_code=201)
    async def create_conversation() -> dict:
        return asdict(chat_store.create_conversation())

    @app.get("/api/conversations")
    async def conversations() -> list[dict]:
        return [asdict(item) for item in chat_store.list_conversations()]

    @app.get("/api/conversations/{conversation_id}/messages")
    async def conversation_messages(conversation_id: str) -> list[dict]:
        return [asdict(item) for item in chat_store.messages(conversation_id)]

    @app.get("/api/conversations/{conversation_id}/export")
    async def export_conversation(conversation_id: str) -> PlainTextResponse:
        try:
            content = chat_store.export_markdown(conversation_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return PlainTextResponse(
            content,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="gesprek-{conversation_id[:8]}.md"'
                )
            },
        )

    @app.post("/api/conversations/{conversation_id}/stream")
    async def stream_conversation_answer(
        conversation_id: str,
        request: QueryRequest,
    ) -> StreamingResponse:
        try:
            chat_store.add_message(conversation_id, "user", request.question)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

        async def events():
            yield "event: status\ndata: Bronnen zoeken…\n\n"
            try:
                result = await run_in_threadpool(
                    locked_call,
                    knowledge_service.answer,
                    request.question,
                    request.libraries,
                    top_k=request.top_k,
                )
                sources = [asdict(source) for source in result.sources]
                chat_store.add_message(
                    conversation_id, "assistant", result.answer, sources
                )
                metadata = json.dumps(
                    {"notice": result.notice, "sources": sources},
                    ensure_ascii=False,
                )
                yield f"event: metadata\ndata: {metadata}\n\n"
                for offset in range(0, len(result.answer), 80):
                    chunk = json.dumps(
                        result.answer[offset : offset + 80], ensure_ascii=False
                    )
                    yield f"event: chunk\ndata: {chunk}\n\n"
                yield "event: done\ndata: {}\n\n"
            except Exception as error:
                payload = json.dumps(str(error), ensure_ascii=False)
                yield f"event: error\ndata: {payload}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/api/video-jobs")
    async def list_video_jobs() -> list[dict]:
        return [job.to_dict() for job in video_job_manager.list_jobs()]

    @app.post("/api/video-jobs/{job_id}/cancel")
    async def cancel_video_job(job_id: str) -> dict:
        try:
            return video_job_manager.cancel(job_id).to_dict()
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/video-jobs/{job_id}/retry")
    async def retry_video_job(job_id: str) -> dict:
        try:
            return video_job_manager.retry(job_id).to_dict()
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.delete("/api/video-jobs/{job_id}", status_code=204)
    async def delete_video_job(job_id: str) -> None:
        try:
            video_job_manager.delete(job_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/video-jobs/{job_id}/result/{kind}")
    async def download_video_result(job_id: str, kind: str) -> FileResponse:
        jobs = {job.id: job for job in video_job_manager.list_jobs()}
        job = jobs.get(job_id)
        if job is None or kind not in {"transcript", "summary"}:
            raise HTTPException(status_code=404, detail="Resultaat niet gevonden.")
        value = job.transcript_path if kind == "transcript" else job.summary_path
        path = Path(value) if value else Path()
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Resultaat niet gevonden.")
        return FileResponse(path, filename=path.name, media_type="text/markdown")

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

    @app.get("/api/archive-jobs")
    async def list_archive_jobs() -> list[dict]:
        return [job.to_dict() for job in archive_job_manager.list_jobs()]

    @app.get("/api/archive-jobs/{job_id}/items")
    async def archive_job_items(job_id: str) -> list[dict]:
        return archive_job_manager.items(job_id)

    @app.post("/api/archive-jobs", status_code=202)
    async def create_archive_job(
        request: Request,
        filename: str = Query(min_length=1, max_length=255),
        library: str = Query(default="", max_length=100),
    ) -> dict:
        known_libraries = {
            item.slug for item in knowledge_service.list_libraries()
        }
        if library and library not in known_libraries:
            raise HTTPException(status_code=400, detail="Onbekende categorie.")
        incoming_dir = resolved_settings.archive_upload_path / ".incoming"
        incoming_dir.mkdir(parents=True, exist_ok=True)
        staged_path = incoming_dir / f"upload-{uuid.uuid4().hex}"
        size = 0
        try:
            with staged_path.open("xb") as target:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 2 * 1024 * 1024 * 1024:
                        raise ValueError("ZIP is groter dan de limiet van 2 GB.")
                    target.write(chunk)
            job = archive_job_manager.create_job_from_path(
                filename,
                staged_path,
                library_slug=library or None,
            )
            return job.to_dict()
        except (FileExistsError, OSError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        finally:
            staged_path.unlink(missing_ok=True)

    @app.get("/api/documents")
    async def indexed_documents() -> list[dict]:
        return [asdict(document) for document in knowledge_service.list_documents()]

    @app.get("/api/documents/{document_id}/preview")
    async def document_preview(document_id: int) -> dict:
        documents = {item.id: item for item in knowledge_service.list_documents()}
        document = documents.get(document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document niet gevonden.")
        try:
            loaded = load_document(Path(document.source_path))
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"document": asdict(document), "content": loaded.content[:100_000]}

    @app.post("/api/documents/{document_id}/reindex")
    async def reindex_document(document_id: int) -> dict:
        documents = {item.id: item for item in knowledge_service.list_documents()}
        document = documents.get(document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document niet gevonden.")
        report = locked_call(
            knowledge_service.reindex_document,
            document_id,
        )
        return asdict(report)

    @app.post("/api/documents/{document_id}/metadata-suggestion")
    async def suggest_document_metadata(document_id: int) -> dict:
        try:
            suggestion = locked_call(
                knowledge_service.suggest_document_metadata,
                document_id,
            )
            return asdict(suggestion)
        except (KeyError, ValueError, OSError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/metadata-suggestions/{suggestion_id}/approve")
    async def approve_document_metadata(suggestion_id: int) -> dict:
        try:
            return asdict(
                knowledge_service.approve_metadata_suggestion(suggestion_id)
            )
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/documents/{document_id}/move")
    async def move_document(document_id: int, library: str = Query()) -> dict:
        documents = {item.id: item for item in knowledge_service.list_documents()}
        document = documents.get(document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document niet gevonden.")
        if not knowledge_service.store.library_exists(library):
            raise HTTPException(status_code=400, detail="Onbekende bibliotheek.")
        locked_call(
            knowledge_service.ingest,
            library,
            Path(document.source_path),
        )
        knowledge_service.delete_document(document_id)
        moved = next(
            item
            for item in knowledge_service.list_documents()
            if item.library_slug == library
            and item.source_path == document.source_path
        )
        return asdict(moved)

    @app.delete("/api/documents/{document_id}", status_code=204)
    async def remove_document(document_id: int, delete_file: bool = False) -> None:
        try:
            document = knowledge_service.delete_document(document_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        path = Path(document.source_path)
        import_root = resolved_settings.database_path.parent / "import"
        if delete_file and path.is_relative_to(import_root):
            path.unlink(missing_ok=True)

    @app.post("/api/document-jobs/{job_id}/retry")
    async def retry_document_job(job_id: str) -> dict:
        try:
            return document_job_manager.retry(job_id).to_dict()
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.delete("/api/document-jobs/{job_id}", status_code=204)
    async def delete_document_job(job_id: str) -> None:
        try:
            document_job_manager.delete(job_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

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
            results = locked_call(
                knowledge_service.search,
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
            result = locked_call(
                knowledge_service.answer,
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
