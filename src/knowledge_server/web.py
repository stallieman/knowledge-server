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
from markdown_it import MarkdownIt
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

MARKDOWN = MarkdownIt(
    "gfm-like",
    {"html": False, "linkify": False, "typographer": False},
).disable("image")


def render_markdown(content: str) -> str:
    """Render model Markdown without allowing executable HTML or remote images."""
    rendered = MARKDOWN.render(content)
    return rendered.replace(
        "<a href=",
        '<a target="_blank" rel="noopener noreferrer" href=',
    )


def render_sources(sources: list[dict]) -> list[dict]:
    """Add safe rendered content to source payloads without changing storage."""
    return [
        {**source, "content_html": render_markdown(source.get("content", ""))}
        for source in sources
    ]


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
  <meta name="color-scheme" content="dark light">
  <title>Mijn kennisassistent</title>
  <script>
    (() => {
      const saved = localStorage.getItem('knowledge-theme');
      const preferred = matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
      document.documentElement.dataset.theme = saved || preferred;
    })();
  </script>
  <style>
    :root {
      color-scheme: dark;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --page: #0b1117;
      --surface: #111a23;
      --surface-strong: #17232e;
      --surface-soft: #0e161e;
      --text: #edf4fa;
      --muted: #9fb0bf;
      --border: #2a3b49;
      --accent: #63b3ff;
      --accent-strong: #1889ee;
      --accent-text: #06121d;
      --user: #143a59;
      --danger: #ff9d9d;
      --success: #67d59a;
      --shadow: 0 18px 50px rgba(0, 0, 0, .22);
    }
    :root[data-theme="light"] {
      color-scheme: light;
      --page: #f3f6f9;
      --surface: #ffffff;
      --surface-strong: #edf3f8;
      --surface-soft: #f8fafc;
      --text: #172431;
      --muted: #5c6b78;
      --border: #cfdae3;
      --accent: #096dc4;
      --accent-strong: #075da8;
      --accent-text: #ffffff;
      --user: #dceeff;
      --danger: #b42318;
      --success: #137b47;
      --shadow: 0 16px 44px rgba(34, 53, 70, .1);
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body { margin: 0; background: var(--page); color: var(--text); }
    body, button, input, select, textarea { font: inherit; }
    button, input, select, textarea { color: inherit; }
    .shell { width: min(1120px, calc(100% - 2rem)); margin: 0 auto; padding: 1.5rem 0 4rem; }
    .app-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }
    .eyebrow { margin: 0 0 .25rem; color: var(--accent); font-size: .78rem; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
    h1 { margin: 0; font-size: clamp(1.8rem, 5vw, 2.65rem); line-height: 1.08; }
    h2, h3 { margin-top: 0; }
    h2 { font-size: 1.35rem; }
    h3 { margin-top: 1.8rem; font-size: 1.05rem; }
    .subtitle, .helper, small { color: var(--muted); }
    .subtitle { margin: .45rem 0 0; line-height: 1.5; }
    .helper { margin: .45rem 0 0; font-size: .88rem; line-height: 1.45; }
    .theme-toggle, .secondary, nav a {
      border: 1px solid var(--border);
      background: var(--surface);
      color: var(--text);
    }
    .theme-toggle { flex: none; display: inline-flex; gap: .5rem; align-items: center; margin: 0; }
    nav {
      position: sticky;
      top: 0;
      z-index: 5;
      display: flex;
      gap: .5rem;
      overflow-x: auto;
      margin: 1.2rem -.3rem .8rem;
      padding: .65rem .3rem;
      background: color-mix(in srgb, var(--page) 88%, transparent);
      backdrop-filter: blur(12px);
    }
    nav a { padding: .5rem .75rem; border-radius: 999px; text-decoration: none; white-space: nowrap; }
    nav a:hover { border-color: var(--accent); color: var(--accent); }
    .panel {
      margin: 1rem 0;
      padding: clamp(1rem, 3vw, 1.5rem);
      border: 1px solid var(--border);
      border-radius: 1rem;
      background: var(--surface);
      box-shadow: var(--shadow);
      scroll-margin-top: 5rem;
    }
    .panel-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; margin-bottom: 1rem; }
    .panel-header h2, .panel-header p { margin-bottom: 0; }
    .local-badge, .badge, .selection-count {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      font-size: .78rem;
      white-space: nowrap;
    }
    .local-badge { gap: .4rem; padding: .35rem .65rem; color: var(--success); background: color-mix(in srgb, var(--success) 12%, transparent); }
    .local-badge::before { content: ""; width: .45rem; height: .45rem; border-radius: 50%; background: currentColor; }
    .row { display: flex; flex-wrap: wrap; gap: .75rem; align-items: end; }
    .row > label { flex: 1 1 13rem; }
    label { cursor: pointer; font-weight: 650; }
    input[type=file], select, textarea {
      width: 100%;
      border: 1px solid var(--border);
      border-radius: .65rem;
      background: var(--surface-soft);
      outline: none;
    }
    input[type=file], select { min-height: 2.8rem; margin-top: .35rem; padding: .62rem .72rem; }
    textarea { min-height: 7.5rem; resize: vertical; padding: .85rem 1rem; line-height: 1.5; }
    input:focus, select:focus, textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 20%, transparent); }
    button {
      min-height: 2.7rem;
      margin-top: .7rem;
      padding: .62rem 1rem;
      border: 0;
      border-radius: .65rem;
      background: var(--accent);
      color: var(--accent-text);
      font-weight: 800;
      cursor: pointer;
    }
    button:hover:not(:disabled) { filter: brightness(1.08); transform: translateY(-1px); }
    button:disabled { opacity: .55; cursor: wait; }
    .secondary { font-weight: 700; }
    .danger { background: color-mix(in srgb, var(--danger) 18%, var(--surface)); color: var(--danger); border: 1px solid color-mix(in srgb, var(--danger) 40%, var(--border)); }
    .conversation-bar { align-items: center; }
    .conversation-bar label { flex: 1 1 18rem; }
    .conversation-bar button { margin-top: 1.45rem; }
    #messages {
      min-height: 18rem;
      max-height: min(54vh, 38rem);
      overflow-y: auto;
      margin: 1rem 0;
      padding: .75rem;
      border: 1px solid var(--border);
      border-radius: .85rem;
      background: var(--surface-soft);
      overscroll-behavior: contain;
    }
    .empty-state { display: grid; min-height: 15rem; place-items: center; color: var(--muted); text-align: center; }
    .message { max-width: 88%; margin: .75rem 0; padding: .85rem 1rem; border: 1px solid var(--border); border-radius: .85rem; background: var(--surface); }
    .message.user { margin-left: auto; background: var(--user); border-bottom-right-radius: .2rem; }
    .message.assistant { margin-right: auto; border-bottom-left-radius: .2rem; }
    .message-role { margin-bottom: .35rem; color: var(--muted); font-size: .75rem; font-weight: 800; letter-spacing: .05em; text-transform: uppercase; }
    .message-body { overflow-x: auto; overflow-wrap: anywhere; line-height: 1.58; }
    .message.user .message-body { white-space: pre-wrap; }
    .message-body > :first-child, .source-content > :first-child { margin-top: 0; }
    .message-body > :last-child, .source-content > :last-child { margin-bottom: 0; }
    .message-body h1, .message-body h2, .message-body h3,
    .source-content h1, .source-content h2, .source-content h3 { margin: 1.15rem 0 .5rem; line-height: 1.25; }
    .message-body h1, .source-content h1 { font-size: 1.35rem; }
    .message-body h2, .source-content h2 { font-size: 1.18rem; }
    .message-body h3, .source-content h3 { font-size: 1.05rem; }
    .message-body p, .source-content p { margin: .65rem 0; }
    .message-body ul, .message-body ol, .source-content ul, .source-content ol { margin: .65rem 0; padding-left: 1.55rem; }
    .message-body li + li, .source-content li + li { margin-top: .25rem; }
    .message-body blockquote, .source-content blockquote { margin: .8rem 0; padding: .1rem 0 .1rem .85rem; border-left: 3px solid var(--accent); color: var(--muted); }
    .message-body code, .source-content code { padding: .1rem .32rem; border-radius: .3rem; background: var(--surface-strong); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .9em; }
    .message-body pre, .source-content pre { margin: .8rem 0; border: 1px solid var(--border); }
    .message-body pre code, .source-content pre code { padding: 0; background: transparent; font-size: inherit; }
    .message-body a, .source-content a { color: var(--accent); text-decoration-thickness: .08em; text-underline-offset: .18em; }
    .message-body table, .source-content table { width: 100%; margin: .8rem 0; border-collapse: collapse; font-size: .92rem; }
    .message-body th, .message-body td, .source-content th, .source-content td { padding: .45rem .6rem; border: 1px solid var(--border); text-align: left; vertical-align: top; }
    .message-body th, .source-content th { background: var(--surface-strong); }
    .message-body hr, .source-content hr { border: 0; border-top: 1px solid var(--border); margin: 1rem 0; }
    .message-notice { margin-top: .65rem; padding-top: .55rem; border-top: 1px solid var(--border); color: var(--muted); font-size: .8rem; }
    .message details { margin-top: .65rem; padding-top: .55rem; border-top: 1px solid var(--border); }
    .message summary, .manager summary { color: var(--accent); cursor: pointer; font-weight: 700; }
    pre { max-height: 24rem; overflow: auto; padding: .75rem; border-radius: .5rem; background: var(--page); white-space: pre; overflow-wrap: normal; }
    .source-content { max-height: 24rem; overflow: auto; margin-top: .55rem; padding: .75rem; border-radius: .55rem; background: var(--page); }
    .library-panel { margin: 1rem 0; border: 1px solid var(--border); border-radius: .8rem; background: var(--surface-soft); }
    .library-panel > summary { display: flex; justify-content: space-between; gap: 1rem; align-items: center; padding: .8rem 1rem; cursor: pointer; font-weight: 800; }
    .library-content { padding: 0 1rem 1rem; }
    .library-toolbar { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin-bottom: .75rem; }
    .library-toolbar button { min-height: 2.2rem; margin: 0; padding: .35rem .65rem; }
    .selection-count { margin-left: auto; padding: .25rem .55rem; background: var(--surface-strong); color: var(--muted); }
    #libraries { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: .45rem; max-height: 15rem; overflow-y: auto; }
    #libraries label { display: flex; gap: .45rem; align-items: flex-start; padding: .5rem .6rem; border-radius: .5rem; background: var(--surface); font-size: .9rem; font-weight: 600; }
    #libraries input { flex: none; margin-top: .15rem; accent-color: var(--accent); }
    .composer { padding: .85rem; border: 1px solid var(--border); border-radius: .85rem; background: var(--surface-soft); }
    .composer-actions { display: flex; align-items: center; justify-content: space-between; gap: .75rem; }
    .composer-actions .helper { margin: .7rem 0 0; }
    #status, #document-upload-status, #archive-upload-status, #upload-status, #backup-status { min-height: 1.25rem; color: var(--muted); }
    .upload-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; }
    .upload-card { padding: 1rem; border: 1px solid var(--border); border-radius: .85rem; background: var(--surface-soft); }
    .upload-card h3 { margin: 0 0 .4rem; }
    .job { padding: .8rem 0; border-top: 1px solid var(--border); }
    .job:first-child { border-top: 0; }
    .job > div { margin-top: .25rem; color: var(--muted); }
    .badge { margin-left: .3rem; padding: .18rem .5rem; background: var(--surface-strong); }
    .error { color: var(--danger); white-space: pre-wrap; }
    .actions { display: flex; flex-wrap: wrap; gap: .4rem; }
    .actions button { min-height: 2.25rem; margin-top: .45rem; padding: .4rem .65rem; }
    .manager { margin-top: 1.2rem; border: 1px solid var(--border); border-radius: .85rem; background: var(--surface-soft); }
    .manager > summary { display: flex; justify-content: space-between; gap: 1rem; padding: 1rem; list-style-position: inside; }
    .summary-meta { color: var(--muted); font-size: .85rem; font-weight: 500; }
    .manager-content { padding: 0 1rem 1rem; }
    #indexed-documents { max-height: 34rem; overflow-y: auto; margin-top: .8rem; padding-right: .35rem; }
    .document-summary { display: block; margin-top: .35rem; line-height: 1.45; }
    .health-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: .7rem; }
    .health-grid div { padding: .75rem; border: 1px solid var(--border); border-radius: .6rem; background: var(--surface-soft); overflow-wrap: anywhere; }
    dialog { width: min(800px, calc(100% - 2rem)); max-height: 85vh; padding: 0; border: 1px solid var(--border); border-radius: 1rem; background: var(--surface); color: var(--text); box-shadow: var(--shadow); }
    dialog::backdrop { background: rgba(3, 8, 13, .72); backdrop-filter: blur(3px); }
    .dialog-header { position: sticky; top: 0; display: flex; justify-content: space-between; gap: 1rem; align-items: center; padding: 1rem; border-bottom: 1px solid var(--border); background: var(--surface); }
    .dialog-header h2 { margin: 0; }
    .dialog-header button { min-height: 2.3rem; margin: 0; padding: .35rem .65rem; }
    .dialog-content { padding: 0 1rem 1rem; }
    [hidden] { display: none !important; }
    @media (max-width: 680px) {
      .shell { width: min(100% - 1rem, 1120px); padding-top: .75rem; }
      .app-header { align-items: center; }
      #theme-label { display: none; }
      .panel { border-radius: .8rem; }
      .panel-header { display: block; }
      .local-badge { margin-top: .75rem; }
      .conversation-bar button { flex: 1; margin-top: 0; }
      #messages { min-height: 15rem; max-height: 48vh; padding: .5rem; }
      .message { max-width: 96%; }
      .composer-actions { align-items: flex-end; }
      .composer-actions .helper { font-size: .75rem; }
      .manager > summary { display: block; }
      .summary-meta { display: block; margin: .35rem 0 0 1.2rem; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header class="app-header">
      <div>
        <p class="eyebrow">Persoonlijke AI-server</p>
        <h1>Mijn kennisassistent</h1>
        <p class="subtitle">Chat met je bibliotheken of gebruik de algemene kennis van het lokale model.</p>
      </div>
      <button id="theme-toggle" class="theme-toggle" type="button" aria-label="Wissel kleurthema">
        <span id="theme-icon" aria-hidden="true">☀</span><span id="theme-label">Licht</span>
      </button>
    </header>
    <nav aria-label="Hoofdnavigatie"><a href="#chat">Chat</a><a href="#documents">Documenten</a><a href="#videos">Video's</a><a href="#system">Systeem</a></nav>

    <main>
      <section class="panel" id="chat">
        <div class="panel-header">
          <div><h2>Chat</h2><p class="subtitle">Je gesprekken blijven lokaal op deze server bewaard.</p></div>
          <span class="local-badge">Lokale AI</span>
        </div>
        <div class="row conversation-bar">
          <label>Gesprek<select id="conversation"></select></label>
          <button id="new-conversation" class="secondary" type="button">Nieuw gesprek</button>
          <button id="export-conversation" class="secondary" type="button">Exporteren</button>
        </div>
        <div id="messages" aria-live="polite"><div class="empty-state">Gesprekken laden…</div></div>
        <details class="library-panel">
          <summary><span>Bibliotheken voor deze vraag</span><span id="selection-count" class="selection-count">0 geselecteerd</span></summary>
          <div class="library-content">
            <div class="library-toolbar">
              <button id="clear-libraries" class="secondary" type="button">Geen selectie</button>
              <button id="select-libraries" class="secondary" type="button">Alles selecteren</button>
            </div>
            <div id="libraries">Laden…</div>
            <p class="helper">Zonder selectie antwoordt het model vanuit zijn eigen kennis. Met een selectie zoekt het eerst in jouw documenten en valt het zo nodig transparant terug.</p>
          </div>
        </details>
        <div class="composer">
          <label for="question">Jouw vraag</label>
          <textarea id="question" placeholder="Stel een vraag aan je lokale kennisassistent…"></textarea>
          <div class="composer-actions">
            <p class="helper">Ctrl/⌘ + Enter om te versturen</p>
            <button id="ask" type="button">Versturen</button>
          </div>
          <p id="status" role="status"></p>
        </div>
      </section>

      <section class="panel" id="documents">
        <div class="panel-header"><div><h2>Documenten toevoegen</h2><p class="subtitle">Na de indexering verdwijnt de taak uit beeld. Het document blijft vindbaar in de gekozen bibliotheek.</p></div></div>
        <div class="upload-grid">
          <div class="upload-card">
            <h3>Los document</h3>
            <p class="helper">Kies zelf een bibliotheek of laat de server een voorstel doen.</p>
            <label>Bestand<input id="document" type="file"></label>
            <label>Bibliotheek<select id="document-library"><option value="">Automatische suggestie</option></select></label>
            <button id="upload-document" type="button">Upload en indexeer</button>
            <p id="document-upload-status" role="status"></p>
            <div id="document-jobs" hidden></div>
          </div>
          <div class="upload-card">
            <h3>ZIP-archief</h3>
            <p class="helper">Documenten worden per inhoud ingedeeld; MP4-video's gaan naar de videowachtrij.</p>
            <label>ZIP-bestand<input id="archive" type="file" accept=".zip,application/zip"></label>
            <label>Bestemming<select id="archive-library"><option value="">Automatisch per document</option></select></label>
            <button id="upload-archive" type="button">Upload en organiseer</button>
            <p id="archive-upload-status" role="status"></p>
            <div id="archive-jobs" hidden></div>
          </div>
        </div>
        <details id="document-management" class="manager">
          <summary><span>Geïndexeerde documenten beheren</span><span id="document-count" class="summary-meta">Klik om te openen</span></summary>
          <div class="manager-content">
            <p class="helper">Deze lijst wordt alleen geladen wanneer je hem nodig hebt en duwt de chat niet meer naar beneden.</p>
            <div class="row">
              <label>Bibliotheek<select id="browse-library"><option value="">Alle bibliotheken</option></select></label>
              <button id="upload-to-category" class="secondary" type="button">Document toevoegen aan selectie</button>
            </div>
            <div id="indexed-documents">Open dit onderdeel om documenten te laden.</div>
          </div>
        </details>
      </section>

      <section class="panel" id="videos">
        <div class="panel-header"><div><h2>Video transcriberen</h2><p class="subtitle">De server verwerkt MP4-video's één voor één en indexeert het transcript automatisch.</p></div></div>
        <div class="row">
          <label>Video<input id="video" type="file" accept="video/mp4,.mp4"></label>
          <label>Taal<select id="video-language"><option value="">Automatisch</option><option value="nl">Nederlands</option><option value="en">Engels</option></select></label>
          <label>Analyse<select id="analysis-type"><option value="meeting">Algemene meeting</option><option value="data-engineering">Data engineering</option></select></label>
        </div>
        <button id="upload-video" type="button">Upload en start</button>
        <p id="upload-status" role="status"></p>
        <div id="jobs"></div>
      </section>

      <section class="panel" id="system">
        <div class="panel-header"><div><h2>Systeemstatus</h2><p class="subtitle">Controleer de server en maak handmatig een back-up.</p></div></div>
        <div id="system-health" class="health-grid">Laden…</div>
        <div class="actions"><button id="create-backup" type="button">Maak back-up</button><button id="refresh-system" class="secondary" type="button">Vernieuwen</button></div>
        <p id="backup-status" role="status"></p>
      </section>
    </main>
  </div>

  <dialog id="preview-dialog">
    <div class="dialog-header"><h2 id="preview-title">Document</h2><button id="close-preview" class="secondary" type="button">Sluiten</button></div>
    <div class="dialog-content"><div id="preview-content" class="source-content"></div></div>
  </dialog>

  <script>
    const $ = selector => document.querySelector(selector);
    const librariesElement = $('#libraries');
    const questionElement = $('#question');
    const askButton = $('#ask');
    const statusElement = $('#status');
    const videoElement = $('#video');
    const uploadButton = $('#upload-video');
    const uploadStatus = $('#upload-status');
    const jobsElement = $('#jobs');
    const documentElement = $('#document');
    const documentLibrary = $('#document-library');
    const documentUploadButton = $('#upload-document');
    const documentUploadStatus = $('#document-upload-status');
    const documentJobsElement = $('#document-jobs');
    const indexedDocuments = $('#indexed-documents');
    const documentManagement = $('#document-management');
    const documentCount = $('#document-count');
    const conversationElement = $('#conversation');
    const messagesElement = $('#messages');
    const systemHealth = $('#system-health');
    const archiveElement = $('#archive');
    const archiveLibrary = $('#archive-library');
    const archiveJobsElement = $('#archive-jobs');
    const archiveUploadStatus = $('#archive-upload-status');
    const browseLibrary = $('#browse-library');
    const previewDialog = $('#preview-dialog');
    let indexedCache = null;
    let hadPendingDocumentJobs = false;
    let hadPendingArchiveJobs = false;

    function setTheme(theme) {
      document.documentElement.dataset.theme = theme;
      localStorage.setItem('knowledge-theme', theme);
      const dark = theme === 'dark';
      $('#theme-icon').textContent = dark ? '☀' : '☾';
      $('#theme-label').textContent = dark ? 'Licht' : 'Donker';
      $('#theme-toggle').setAttribute('aria-label', dark ? 'Schakel naar licht thema' : 'Schakel naar donker thema');
    }
    setTheme(document.documentElement.dataset.theme || 'dark');
    $('#theme-toggle').addEventListener('click', () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));

    function resetSelect(select, label) {
      const value = select.value;
      select.textContent = '';
      const first = document.createElement('option');
      first.value = '';
      first.textContent = label;
      select.append(first);
      return value;
    }

    function updateSelectionCount() {
      const count = document.querySelectorAll('#libraries input:checked').length;
      $('#selection-count').textContent = `${count} geselecteerd`;
    }

    async function loadLibraries() {
      const selected = new Set([...document.querySelectorAll('#libraries input:checked')].map(input => input.value));
      const response = await fetch('/api/libraries');
      if (!response.ok) throw new Error('Bibliotheken konden niet worden geladen.');
      const libraries = await response.json();
      const oldDocument = resetSelect(documentLibrary, 'Automatische suggestie');
      const oldArchive = resetSelect(archiveLibrary, 'Automatisch per document');
      const oldBrowse = resetSelect(browseLibrary, 'Alle bibliotheken');
      librariesElement.textContent = '';
      for (const library of libraries) {
        const label = document.createElement('label');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = library.slug;
        checkbox.checked = selected.has(library.slug);
        checkbox.addEventListener('change', updateSelectionCount);
        const name = document.createElement('span');
        name.textContent = `${library.name} (${library.document_count})`;
        label.append(checkbox, name);
        librariesElement.append(label);
        for (const target of [documentLibrary, archiveLibrary, browseLibrary]) {
          const option = document.createElement('option');
          option.value = library.slug;
          option.textContent = library.name;
          target.append(option);
        }
      }
      documentLibrary.value = oldDocument;
      archiveLibrary.value = oldArchive;
      browseLibrary.value = oldBrowse;
      const total = libraries.reduce((sum, library) => sum + library.document_count, 0);
      documentCount.textContent = `${total} documenten · klik om te openen`;
      updateSelectionCount();
    }

    $('#clear-libraries').addEventListener('click', () => {
      document.querySelectorAll('#libraries input').forEach(input => { input.checked = false; });
      updateSelectionCount();
    });
    $('#select-libraries').addEventListener('click', () => {
      document.querySelectorAll('#libraries input').forEach(input => { input.checked = true; });
      updateSelectionCount();
    });

    function actionButton(label, handler, danger=false) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.className = danger ? 'danger' : 'secondary';
      button.addEventListener('click', async () => {
        button.disabled = true;
        try { await handler(); }
        catch (error) { alert(`Actie mislukt: ${error.message}`); }
        finally { button.disabled = false; }
      });
      return button;
    }

    async function apiAction(url, method='POST') {
      const response = await fetch(url, {method});
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.detail || 'Actie mislukt');
      }
      indexedCache = null;
      const refreshes = [loadJobs(), loadDocumentJobs()];
      if (documentManagement.open) refreshes.push(loadIndexedDocuments(true));
      await Promise.all(refreshes);
      return response.status === 204 ? null : response.json();
    }

    function jobLabel(status) {
      return {queued: 'In wachtrij', processing: 'Bezig', completed: 'Klaar', failed: 'Mislukt', cancelled: 'Gestopt'}[status] || status;
    }

    async function loadDocumentJobs() {
      const response = await fetch('/api/document-jobs');
      const jobs = await response.json();
      const visibleJobs = jobs.filter(job => job.status !== 'completed');
      const hasPending = visibleJobs.some(job => ['queued', 'processing'].includes(job.status));
      if (hadPendingDocumentJobs && !hasPending && !visibleJobs.some(job => job.status === 'failed')) {
        documentUploadStatus.textContent = 'Indexering afgerond. Het document staat nu in je bibliotheek.';
        indexedCache = null;
        loadLibraries().catch(() => {});
      }
      hadPendingDocumentJobs = hasPending;
      documentJobsElement.textContent = '';
      documentJobsElement.hidden = visibleJobs.length === 0;
      for (const job of visibleJobs) {
        const article = document.createElement('article');
        article.className = 'job';
        const heading = document.createElement('strong');
        heading.textContent = job.filename;
        const badge = document.createElement('span');
        badge.className = 'badge';
        badge.textContent = jobLabel(job.status);
        const progress = document.createElement('div');
        progress.textContent = `${job.progress} · ${job.library_slug}`;
        article.append(heading, ' ', badge, progress);
        if (job.status === 'failed') {
          const actions = document.createElement('div');
          actions.className = 'actions';
          actions.append(actionButton('Opnieuw proberen', () => apiAction(`/api/document-jobs/${job.id}/retry`)));
          article.append(actions);
        }
        if (job.error) {
          const error = document.createElement('div');
          error.className = 'error';
          error.textContent = job.error;
          article.append(error);
        }
        documentJobsElement.append(article);
      }
    }

    function renderIndexedDocuments() {
      indexedDocuments.textContent = '';
      const documents = (indexedCache || []).filter(item => !browseLibrary.value || item.library_slug === browseLibrary.value);
      if (!documents.length) {
        indexedDocuments.textContent = 'Geen documenten gevonden in deze bibliotheek.';
        return;
      }
      for (const item of documents) {
        const article = document.createElement('article');
        article.className = 'job';
        const title = document.createElement('strong');
        title.textContent = item.title || item.source_name;
        const details = document.createElement('div');
        details.textContent = `${item.library_slug} · ${item.chunk_count} fragmenten`;
        const summary = document.createElement('small');
        summary.className = 'document-summary';
        summary.textContent = item.summary || '';
        const actions = document.createElement('div');
        actions.className = 'actions';
        actions.append(
          actionButton('Bekijken', async () => {
            const response = await fetch(`/api/documents/${item.id}/preview`);
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Voorbeeld kon niet worden geladen.');
            $('#preview-title').textContent = item.title || item.source_name;
            const preview = $('#preview-content');
            if (data.content_html) preview.innerHTML = data.content_html;
            else preview.textContent = data.content;
            previewDialog.showModal();
          }),
          actionButton('Herindexeren', () => apiAction(`/api/documents/${item.id}/reindex`)),
          actionButton('Slim voorstel', async () => {
            const suggestion = await apiAction(`/api/documents/${item.id}/metadata-suggestion`);
            const description = `${suggestion.title}\n\n${suggestion.summary}\n\nTags: ${suggestion.tags.join(', ')}\nBibliotheek: ${suggestion.library_slug}`;
            if (confirm(`${description}\n\nDit voorstel toepassen?`)) await apiAction(`/api/metadata-suggestions/${suggestion.id}/approve`);
          }),
          actionButton('Verplaatsen', async () => {
            const library = prompt('Nieuwe bibliotheekslug:');
            if (library) await apiAction(`/api/documents/${item.id}/move?library=${encodeURIComponent(library)}`);
          }),
          actionButton('Verwijderen', async () => {
            if (confirm('Document uit de index en importmap verwijderen?')) await apiAction(`/api/documents/${item.id}?delete_file=true`, 'DELETE');
          }, true),
        );
        article.append(title, details, summary, actions);
        indexedDocuments.append(article);
      }
    }

    async function loadIndexedDocuments(force=false) {
      if (!documentManagement.open) return;
      if (!indexedCache || force) {
        indexedDocuments.textContent = 'Documenten laden…';
        const response = await fetch('/api/documents');
        if (!response.ok) throw new Error('Documenten konden niet worden geladen.');
        indexedCache = await response.json();
        documentCount.textContent = `${indexedCache.length} documenten`;
      }
      renderIndexedDocuments();
    }
    documentManagement.addEventListener('toggle', () => {
      if (documentManagement.open) loadIndexedDocuments().catch(error => { indexedDocuments.textContent = error.message; });
    });
    browseLibrary.addEventListener('change', renderIndexedDocuments);
    $('#close-preview').addEventListener('click', () => previewDialog.close());
    previewDialog.addEventListener('click', event => { if (event.target === previewDialog) previewDialog.close(); });
    $('#upload-to-category').addEventListener('click', () => {
      if (!browseLibrary.value) { alert('Kies eerst een bibliotheek.'); return; }
      documentLibrary.value = browseLibrary.value;
      documentElement.click();
    });

    async function loadArchiveJobs() {
      const response = await fetch('/api/archive-jobs');
      const jobs = await response.json();
      const visibleJobs = jobs.filter(job => job.status !== 'completed');
      const hasPending = visibleJobs.some(job => ['queued', 'processing'].includes(job.status));
      if (hadPendingArchiveJobs && !hasPending && !visibleJobs.some(job => job.status === 'failed')) {
        archiveUploadStatus.textContent = 'ZIP verwerkt. De documenten worden nu afzonderlijk geïndexeerd.';
      }
      hadPendingArchiveJobs = hasPending;
      archiveJobsElement.textContent = '';
      archiveJobsElement.hidden = visibleJobs.length === 0;
      for (const job of visibleJobs) {
        const article = document.createElement('article');
        article.className = 'job';
        const title = document.createElement('strong'); title.textContent = job.filename;
        const badge = document.createElement('span'); badge.className = 'badge'; badge.textContent = jobLabel(job.status);
        const progress = document.createElement('div'); progress.textContent = job.progress;
        article.append(title, ' ', badge, progress);
        if (job.error) { const error = document.createElement('div'); error.className = 'error'; error.textContent = job.error; article.append(error); }
        archiveJobsElement.append(article);
      }
    }

    $('#upload-archive').addEventListener('click', async () => {
      const file = archiveElement.files[0];
      if (!file) { archiveUploadStatus.textContent = 'Kies eerst een ZIP-bestand.'; return; }
      const button = $('#upload-archive');
      button.disabled = true;
      archiveUploadStatus.textContent = 'ZIP uploaden…';
      const params = new URLSearchParams({filename: file.name, library: archiveLibrary.value});
      try {
        const response = await fetch(`/api/archive-jobs?${params}`, {method: 'POST', body: file});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'ZIP-upload mislukt');
        archiveUploadStatus.textContent = 'ZIP staat in de verwerkingswachtrij.';
        archiveElement.value = '';
        await loadArchiveJobs();
      } catch (error) { archiveUploadStatus.textContent = `Fout: ${error.message}`; }
      finally { button.disabled = false; }
    });

    documentUploadButton.addEventListener('click', async () => {
      const file = documentElement.files[0];
      if (!file) { documentUploadStatus.textContent = 'Kies eerst een document.'; return; }
      documentUploadButton.disabled = true;
      documentUploadStatus.textContent = 'Document uploaden…';
      const params = new URLSearchParams({filename: file.name, library: documentLibrary.value});
      try {
        const response = await fetch(`/api/document-jobs?${params}`, {method: 'POST', body: file});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Upload mislukt');
        documentUploadStatus.textContent = `Indexering gestart voor ${payload.library_slug}.`;
        documentElement.value = '';
        await loadDocumentJobs();
      } catch (error) { documentUploadStatus.textContent = `Fout: ${error.message}`; }
      finally { documentUploadButton.disabled = false; }
    });

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
        if (['queued', 'processing'].includes(job.status)) actions.append(actionButton('Stoppen', () => apiAction(`/api/video-jobs/${job.id}/cancel`)));
        if (['failed', 'cancelled'].includes(job.status)) actions.append(actionButton('Opnieuw', () => apiAction(`/api/video-jobs/${job.id}/retry`)));
        if (job.transcript_path) actions.append(actionButton('Transcript', () => { location.href=`/api/video-jobs/${job.id}/result/transcript`; }));
        if (job.summary_path) actions.append(actionButton('Samenvatting', () => { location.href=`/api/video-jobs/${job.id}/result/summary`; }));
        if (!['queued', 'processing'].includes(job.status)) actions.append(actionButton('Verwijderen', () => apiAction(`/api/video-jobs/${job.id}`, 'DELETE'), true));
        article.append(actions);
        if (job.error) { const error = document.createElement('div'); error.className = 'error'; error.textContent = job.error; article.append(error); }
        jobsElement.append(article);
      }
    }

    uploadButton.addEventListener('click', async () => {
      const file = videoElement.files[0];
      if (!file) { uploadStatus.textContent = 'Kies eerst een MP4-video.'; return; }
      uploadButton.disabled = true;
      uploadStatus.textContent = 'Video uploaden…';
      const params = new URLSearchParams({filename: file.name, language: $('#video-language').value, analysis_type: $('#analysis-type').value});
      try {
        const response = await fetch(`/api/video-jobs?${params}`, {method: 'POST', headers: {'Content-Type': 'video/mp4'}, body: file});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Upload mislukt');
        uploadStatus.textContent = 'Video staat in de wachtrij.';
        videoElement.value = '';
        await loadJobs();
      } catch (error) { uploadStatus.textContent = `Fout: ${error.message}`; }
      finally { uploadButton.disabled = false; }
    });

    function scrollMessages() { messagesElement.scrollTop = messagesElement.scrollHeight; }

    function addSources(article, sources=[]) {
      for (const source of sources) {
        const detail = document.createElement('details');
        const label = document.createElement('summary');
        label.textContent = `[${source.source_id}] ${source.source_name} · ${source.library_slug} · regels ${source.start_line}-${source.end_line}`;
        const fragment = document.createElement('div');
        fragment.className = 'source-content';
        if (source.content_html) fragment.innerHTML = source.content_html;
        else fragment.textContent = source.content || 'Geen fragment opgeslagen.';
        detail.append(label, fragment);
        article.append(detail);
      }
    }

    function appendMessage(role, content, sources=[], notice='', contentHtml='') {
      const article = document.createElement('article');
      article.className = `message ${role}`;
      const roleLabel = document.createElement('div');
      roleLabel.className = 'message-role';
      roleLabel.textContent = role === 'user' ? 'Jij' : 'Kennisassistent';
      const body = document.createElement('div');
      body.className = 'message-body';
      if (contentHtml) body.innerHTML = contentHtml;
      else body.textContent = content;
      article.append(roleLabel, body);
      if (notice) {
        const note = document.createElement('div');
        note.className = 'message-notice';
        note.textContent = notice;
        article.append(note);
      }
      addSources(article, sources);
      messagesElement.append(article);
      scrollMessages();
      return {article, body};
    }

    async function loadMessages() {
      if (!conversationElement.value) return;
      const response = await fetch(`/api/conversations/${conversationElement.value}/messages`);
      const messages = await response.json();
      messagesElement.textContent = '';
      if (!messages.length) {
        const empty = document.createElement('div');
        empty.className = 'empty-state';
        empty.textContent = 'Begin een nieuw gesprek met je eigen documenten of met de algemene kennis van het model.';
        messagesElement.append(empty);
        return;
      }
      for (const message of messages) appendMessage(message.role, message.content, message.sources, '', message.content_html);
      scrollMessages();
    }

    async function loadConversations(selectId) {
      const response = await fetch('/api/conversations');
      const conversations = await response.json();
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

    async function askQuestion() {
      const question = questionElement.value.trim();
      if (!question || askButton.disabled) {
        if (!question) statusElement.textContent = 'Vul eerst een vraag in.';
        return;
      }
      const libraries = [...document.querySelectorAll('#libraries input:checked')].map(input => input.value);
      const empty = messagesElement.querySelector('.empty-state');
      if (empty) empty.remove();
      appendMessage('user', question);
      const pending = appendMessage('assistant', 'Bronnen zoeken en lokaal antwoord genereren…');
      questionElement.value = '';
      askButton.disabled = true;
      statusElement.textContent = 'Bezig met antwoorden…';
      try {
        const response = await fetch(`/api/conversations/${conversationElement.value}/stream`, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({question, libraries, top_k: 6}),
        });
        if (!response.ok || !response.body) throw new Error('De server kon geen antwoord starten.');
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '', answerText = '', sources = [], notice = '';
        while (true) {
          const {value, done} = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, {stream: true});
          const events = buffer.split('\\n\\n');
          buffer = events.pop();
          for (const event of events) {
            const type = event.match(/^event: (.+)$/m)?.[1];
            const data = event.match(/^data: (.+)$/m)?.[1];
            if (type === 'metadata') {
              const metadata = JSON.parse(data);
              sources = metadata.sources;
              notice = metadata.notice || '';
            }
            if (type === 'chunk') {
              answerText += JSON.parse(data);
              pending.body.textContent = answerText;
              scrollMessages();
            }
            if (type === 'rendered') pending.body.innerHTML = JSON.parse(data);
            if (type === 'error') throw new Error(JSON.parse(data));
          }
        }
        if (notice) {
          const note = document.createElement('div');
          note.className = 'message-notice';
          note.textContent = notice;
          pending.article.append(note);
        }
        addSources(pending.article, sources);
        statusElement.textContent = '';
        await loadConversations(conversationElement.value);
      } catch (error) {
        pending.body.textContent = `Fout: ${error.message}`;
        statusElement.textContent = 'Antwoord mislukt. Probeer het opnieuw.';
      } finally { askButton.disabled = false; }
    }

    askButton.addEventListener('click', askQuestion);
    questionElement.addEventListener('keydown', event => {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        askQuestion();
      }
    });
    $('#new-conversation').addEventListener('click', async () => {
      const created = await (await fetch('/api/conversations', {method: 'POST'})).json();
      await loadConversations(created.id);
      questionElement.focus();
    });
    conversationElement.addEventListener('change', loadMessages);
    $('#export-conversation').addEventListener('click', () => {
      if (conversationElement.value) location.href = `/api/conversations/${conversationElement.value}/export`;
    });

    async function loadSystemHealth() {
      const health = await (await fetch('/api/system-health')).json();
      systemHealth.textContent = '';
      for (const [key, value] of Object.entries(health)) {
        const item = document.createElement('div');
        item.textContent = `${key}: ${value}`;
        systemHealth.append(item);
      }
    }
    $('#refresh-system').addEventListener('click', loadSystemHealth);
    $('#create-backup').addEventListener('click', async () => {
      const result = await apiAction('/api/backups');
      $('#backup-status').textContent = `Back-up gemaakt: ${result.filename}`;
    });

    loadLibraries().catch(error => { librariesElement.textContent = error.message; });
    loadJobs().catch(error => { jobsElement.textContent = `Taken konden niet worden geladen: ${error.message}`; });
    loadDocumentJobs().catch(error => { documentJobsElement.hidden = false; documentJobsElement.textContent = `Documenttaken konden niet worden geladen: ${error.message}`; });
    loadConversations().catch(error => { messagesElement.textContent = `Gesprekken konden niet worden geladen: ${error.message}`; });
    loadSystemHealth().catch(error => { systemHealth.textContent = error.message; });
    loadArchiveJobs().catch(error => { archiveJobsElement.hidden = false; archiveJobsElement.textContent = error.message; });
    setInterval(() => loadJobs().catch(() => {}), 4000);
    setInterval(() => loadDocumentJobs().catch(() => {}), 4000);
    setInterval(() => loadArchiveJobs().catch(() => {}), 4000);
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
        payloads = []
        for item in chat_store.messages(conversation_id):
            payload = asdict(item)
            payload["content_html"] = render_markdown(item.content)
            payload["sources"] = render_sources(payload["sources"])
            payloads.append(payload)
        return payloads

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
                    {
                        "notice": result.notice,
                        "sources": render_sources(sources),
                    },
                    ensure_ascii=False,
                )
                yield f"event: metadata\ndata: {metadata}\n\n"
                for offset in range(0, len(result.answer), 80):
                    chunk = json.dumps(
                        result.answer[offset : offset + 80], ensure_ascii=False
                    )
                    yield f"event: chunk\ndata: {chunk}\n\n"
                rendered = json.dumps(
                    render_markdown(result.answer), ensure_ascii=False
                )
                yield f"event: rendered\ndata: {rendered}\n\n"
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
        content = loaded.content[:100_000]
        return {
            "document": asdict(document),
            "content": content,
            "content_html": render_markdown(content),
        }

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
