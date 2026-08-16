# knowledge-server

Lokale RAG-kennisserver met strikt gescheiden bibliotheken, Ollama en zichtbare
bronverwijzingen.

## Ontwerpgrens

`local-meeting-transcriber` blijft verantwoordelijk voor video, audio en
transcriptie. Dit project indexeert de geproduceerde transcripties en andere
tekstdocumenten:

```text
local-meeting-transcriber
        └── full_transcript_with_timestamps.md
                         ↓
knowledge-server / Persoonlijke video's
```

Er worden geen documenten naar een externe dienst gestuurd. De SQLite-index,
documentchunks, embeddings en antwoorden blijven lokaal.

## Huidige mogelijkheden

- aparte bibliotheken met harde filters;
- import van documenten en gangbare broncode- en configuratieformaten;
- veilige Git-snapshotimport van alleen getrackte bestanden;
- incrementeel opnieuw indexeren op basis van SHA-256;
- embeddings via Ollama `embeddinggemma`;
- hybride zoeken: semantische cosine similarity plus SQLite FTS5/BM25;
- Reciprocal Rank Fusion (RRF) voor één ranglijst met zowel betekenis als exacte
  vaktermen, bestandsnamen en code-identifiers;
- titel-, sectie-, onderwerp- en headingcontext in iedere embedding;
- antwoorden via `qwen3:14b` met broncodes en bestandsregels;
- zoeken in één of bewust meerdere bibliotheken;
- transparante modelkennis-fallback als bronnen ontbreken of niets is geselecteerd;
- lokale CLI en webinterface;
- LLM en embeddingmodel worden direct na gebruik uit VRAM verwijderd;
- bescherming tegen instructies die in bronbestanden staan.

Een directe Microsoft Graph-sync voor OneNote en specifieke ChatGPT-exportimport
volgen in een volgende uitbreiding. PDF-, Word-, HTML- en Markdownexports kunnen
nu al worden geïndexeerd.

## Installatie

Vereisten: CachyOS, Ollama, `uv`, Python 3.12 en de al aanwezige `qwen3:14b`.

```fish
cd ~/projects/knowledge-server
uv sync
ollama pull embeddinggemma
uv run knowledge-server init
```

`init` maakt deze bibliotheken aan:

- WBIH
- SQL
- Linux
- Elastic Stack
- DevOps & Cloud
- Softwareontwikkeling
- Security & OSINT
- Persoonlijk
- Power BI
- AI Engineering
- Home Energy
- WOII
- Persoonlijke video's
- ChatGPT-archief

## Een transcript indexeren

```fish
uv run knowledge-server ingest persoonlijke-videos \
  "../local-meeting-transcriber/data/output/meetings/Recording 2026-07-03 115427/full_transcript_with_timestamps.md"
```

Ongewijzigde bestanden worden bij een volgende ingest overgeslagen.

## Zoeken en vragen

Alleen bronnen zoeken:

```fish
uv run knowledge-server search \
  "Wat is besloten over de kalenderdimensie?" \
  --library persoonlijke-videos
```

Een lokaal antwoord genereren:

```fish
uv run knowledge-server ask \
  "Wat is besloten over de kalenderdimensie?" \
  --library persoonlijke-videos
```

Zonder bibliotheekselectie antwoordt Qwen vanuit zijn algemene modelkennis. De CLI
en webinterface markeren dit expliciet en tonen dan geen bibliotheekcitaten:

```fish
uv run knowledge-server ask "Wat is een stermodel?"
```

Meerdere bibliotheken worden alleen door herhaalde, expliciete selectie bevraagd:

```fish
uv run knowledge-server ask "Leg deze query uit" \
  --library sql \
  --library wbih
```

## Lokale webinterface

```fish
uv run knowledge-server serve
```

Open daarna `http://127.0.0.1:8000`. De server bindt bewust alleen aan localhost.
Gebruik nog niet `--host 0.0.0.0`: voordat de interface op het thuisnetwerk of een
werklaptop bereikbaar wordt, voegen we authenticatie en een veilige verbinding toe.

## Automatisch starten na een reboot

De actieve configuratie staat in `deploy/knowledge-server-user.service`. Dit is een
user service die alleen op localhost luistert, één kleine webworker gebruikt en een
lage CPU- en I/O-prioriteit heeft. Met systemd-lingering start deze ook bij het
opstarten, zonder interactieve login. Modellen worden alleen op verzoek geladen en
daarna direct weer uit RAM en VRAM verwijderd.

`deploy/knowledge-server.service` is daarnaast beschikbaar als volledig geharde
system service wanneer beheer onder `/etc/systemd/system` later gewenst is.

Status en logs:

```fish
systemctl --user status knowledge-server
journalctl --user -u knowledge-server -f
```

Stoppen en opnieuw starten:

```fish
systemctl --user stop knowledge-server
systemctl --user restart knowledge-server
```

## Automatische onderwerpimport

`data/import` bevat één map per bibliotheekslug. Plaats documenten bijvoorbeeld in:

```text
data/import/
├── home-energy/
├── linux/
├── persoonlijke-videos/
├── sql/
├── wbih/
└── woii/
```

De timer `knowledge-server-index.timer` controleert deze mappen elke dertig minuten.
Alleen nieuwe of gewijzigde documenten krijgen embeddings; ongewijzigde bestanden
worden na een snelle hashcontrole overgeslagen. De indexer draait met de laagste
I/O-prioriteit en sluit direct weer af.

Handmatig controleren kan met:

```fish
uv run knowledge-server ingest-all
systemctl --user list-timers knowledge-server-index.timer
```

### OneNote

Een OneNote-deellink is geschikt om een notebook in de browser te bekijken, maar
niet als stabiele machine-interface voor automatische indexering. De directe route
wordt daarom Microsoft Graph met alleen-lezen OAuth-toegang. Tot die koppeling is
gebouwd kun je pagina's uit OneNote naar PDF, Word of HTML exporteren en ze in de
juiste onderwerpmap plaatsen. De periodieke indexer verwerkt ze daarna automatisch.
De volledige sectiemapping en geplande OAuth-route staan in
[`docs/onenote-sync.md`](docs/onenote-sync.md).

Een binaire OneNote-back-up kan lokaal en herhaalbaar worden verwerkt met de
afzonderlijke importstraat in [`docs/onenote-import.md`](docs/onenote-import.md).

Een gemengd ZIP-archief met PDF's, tekst, Markdown en projectcode kan gecontroleerd
worden gecategoriseerd via
[`docs/reference-archive-import.md`](docs/reference-archive-import.md).

Lokale Git-checkouts en later read-only gesynchroniseerde GitHub-projecten kunnen
per projectbibliotheek worden geïndexeerd. De veiligheidsgrenzen en commando's staan
in [`docs/github-integration.md`](docs/github-integration.md).

## Configuratie

Optionele omgevingsvariabelen:

```text
KNOWLEDGE_SERVER_DB
KNOWLEDGE_SERVER_OLLAMA_URL
KNOWLEDGE_SERVER_CHAT_MODEL
KNOWLEDGE_SERVER_EMBEDDING_MODEL
```

De standaarddatabase is `data/knowledge.db` en valt onder `.gitignore`.

## Ontwikkelchecks

```fish
uv run ruff check .
uv run pytest -q
```
