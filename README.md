# Cortex

**A local-first AI knowledge vault. Your notes, your machine, your models.**

Cortex takes raw, unstructured brain dumps — journals, meeting notes, worldbuilding,
design docs, half-formed ideas — files them with a local LLM, and makes them findable
by meaning and by keyword. It runs entirely on [Ollama](https://ollama.com) and SQLite.
Nothing leaves your computer.

> **Status: rebuild in progress.** Milestones 1–3 are complete: the storage core,
> the CLI, the HTTP API, and an installable web client that runs on the desktop and
> the phone. Chat and creative generation are next — see [Build order](#build-order).

---

## How it works

One SQLite file holds everything: your records, their chunks, the vector index, and the
full-text index.

```
raw text ──► Librarian (local LLM) ──► structured record
                                          │
                          ┌───────────────┼───────────────┐
                          ▼               ▼               ▼
                      records      chunks + vec0      records_fts
                          └───────── one COMMIT ──────────┘

query ──┬─► embed ──► vec0 kNN ────┐
        └─► tokenise ──► FTS5 bm25 ┴─► reciprocal rank fusion ──► results
```

Two design choices carry most of the weight:

**One store, one transaction.** A record and its embedding are written by the same
`COMMIT`, so they cannot drift apart. Backup is copying one file.

**Chunked embedding.** Notes are split into ~400-token overlapping chunks before
embedding, and retrieved as whole records. Every embedding model Cortex supports has a
2048-token window and returns an error rather than truncating past it — chunking is what
lets you save a note of any length.

**Hybrid retrieval.** Vector search cannot reliably find a proper noun you half-remember;
keyword search cannot find a concept you described in different words. Both arms run and
their rankings are fused.

---

## Requirements

- **Python 3.11+**
- **[Ollama](https://ollama.com)** running locally
- Three models — an embedder plus two chat models (they can be the same one):

```bash
ollama pull embeddinggemma
ollama pull qwen2.5:14b
```

## Install

```bash
uv venv && uv pip install -e ".[api,dev]"
```

Check everything is wired up:

```bash
cortex doctor
```

`doctor` reports where your vault lives, how many records it holds, whether the two
indexes agree, and whether Ollama has the models you've configured.

---

## Using it

```bash
cortex capture "The bell tower leans three degrees north." --project Echoes
```

The Librarian reads the note, gives it a title, category and subcategory, and files it.
Pipe from stdin or pass `--file` for longer notes.

```bash
cortex search "sailors navigating by a leaning tower"
```

Searches by meaning and by keyword at once. Each result says which arm matched it.

| Command | What it does |
|---|---|
| `cortex capture` | File a note. `--verbatim` skips the model rewrite, `--project` sets the project |
| `cortex search` | Hybrid search. `--project` scopes it, `--limit` caps results |
| `cortex list` | Records, newest first |
| `cortex show ID` | One record in full |
| `cortex delete ID` | Delete a record and everything indexed from it |
| `cortex projects` | Projects and their record counts |
| `cortex export DIR` | Every record as Markdown with YAML frontmatter |
| `cortex import DIR` | Ingest a folder of Markdown, recursively |
| `cortex backup` | Consistent snapshot via SQLite's online backup API |
| `cortex reindex` | Rebuild chunks and embeddings from the records |
| `cortex doctor` | Check the vault and the model server |
| `cortex serve` | Run the HTTP API |
| `cortex token` | Print the API token |

### Getting your writing out

`cortex export` writes one Markdown file per record, grouped into project folders, with
YAML frontmatter any other note tool can read. The round trip is tested: export, import
into a fresh vault, and every record comes back identical.

Do this on a schedule. `cortex backup` is the fast path; `cortex export` is the one you
can still read in ten years when nothing runs this code.

### Changing embedding model

The vector index records which model built it and refuses to open with a different one,
because mixing two vector spaces degrades search silently. Switch deliberately:

```bash
CORTEX_EMBED_MODEL=nomic-embed-text cortex reindex
```

---

## The API

```bash
cortex serve
```

Serves on `http://127.0.0.1:8765` with interactive docs at `/docs`. The first run
generates a bearer token and stores it next to the vault; `cortex token` prints it.

| Route | |
|---|---|
| `GET /health` | Reachability. The only route that needs no token |
| `GET /api/status` | Records, projects, index integrity, model availability |
| `GET /api/projects` | Projects with their counts |
| `GET /api/records` | List, filter by project, paginate |
| `POST /api/records` | File a note |
| `POST /api/records/stream` | Same, as server-sent events with progress |
| `GET /api/records/{id}` · `PATCH` · `DELETE` | One record |
| `GET /api/search` | Hybrid search |
| `POST /api/sync` | Drain a batch of captures queued offline |

Every route except `/health` needs `Authorization: Bearer <token>`.

```bash
curl -H "Authorization: Bearer $(cortex token)" "http://127.0.0.1:8765/api/search?q=lighthouse"
```

### Streaming captures

A local 14B model takes ten to twenty seconds to file a note, so
`POST /api/records/stream` reports each stage as it begins rather than leaving a
client with nothing to show:

```
event: progress
data: {"stage": "structuring", "message": "Reading and filing the note"}

event: record
data: {"record": {...}, "chunks": 1, "warnings": []}
```

Exactly one terminal event arrives — `record` on success, `error` on failure. Treat a
stream that ends without one as a failure.

### Syncing an offline queue

`POST /api/sync` takes a batch. One bad item never fails the batch, because a phone
that cannot tell which notes landed will either lose them or send them all again. Give
each capture an `idempotency_key` and a replayed batch is free: the key is checked
before any model runs, and each item comes back as `stored`, `already_stored`,
`duplicate` or `failed`.

### Reaching it from another device

Bind to a [Tailscale](https://tailscale.com) address rather than a public one:

```bash
cortex serve --host 100.x.y.z
```

Ollama itself stays on `127.0.0.1` and is never exposed — only Cortex talks to it.

---

## The web app

One client for every device: a browser tab on the desktop, an installed app on the
phone. Both talk to the same API.

```bash
cortex serve              # terminal 1
npm run dev --prefix web  # terminal 2, opens http://localhost:5173
```

On first run it asks for the server address and your token (`cortex token`), and
remembers them. Three screens:

- **Capture** — type, pick a project, save. Progress streams in while the model
  works. Drafts survive a browser restart, and Ctrl/Cmd+Enter saves.
- **Vault** — hybrid search across everything, filter by project, read, edit, delete.
  Each result says whether it matched by meaning, by keyword, or both.
- **Settings** — model routing, vault health, index integrity.

Model routing is stored in the vault rather than the environment, so changing your
Librarian is a click and takes effect immediately. Only chat-capable models are
offered — Ollama reports what each model can do, so an embedding model can no longer
be selected as a chat model.

### Installing it on a phone

Build once and serve the static files:

```bash
npm run build --prefix web
```

Open the app on your phone and use "Add to Home Screen". The app shell is cached by a
service worker, so it opens instantly. Point it at your Tailscale address, and Cortex
travels with you.

> Offline *capture* — queueing notes with no signal and syncing later — is M6. The
> API side is already built (`POST /api/sync`); the client queue is not.

---

## Configuration

All optional — the defaults work.

| Variable | Default |
|---|---|
| `CORTEX_DATA_DIR` | `%LOCALAPPDATA%\cortex` / `~/.local/share/cortex` |
| `CORTEX_OLLAMA_HOST` | `http://127.0.0.1:11434` |
| `CORTEX_EMBED_MODEL` | `embeddinggemma` |
| `CORTEX_LIBRARIAN_MODEL` | `qwen2.5:14b` |
| `CORTEX_CREATIVE_MODEL` | `gemma4:12b` |
| `CORTEX_CHUNK_TARGET` / `_MAX` / `_OVERLAP` | `400` / `512` / `60` |
| `CORTEX_MAX_DISTANCE` | per embedding model |
| `CORTEX_API_TOKEN` | generated on first run |

`CORTEX_MAX_DISTANCE` is the relevance floor for the vector arm — how far a vector hit
may be before it is dropped. Without one, a query about something you never wrote hands
back your whole vault, because nearest-neighbour search always returns its nearest
neighbours however far away they are.

Good values are model-dependent, and not by a little. Measured on a real vault, the
cosine distance of the best hit:

| Model | Genuine match | Nonsense query |
|---|---|---|
| `embeddinggemma` | 0.46 – 0.61 | 0.71 – 0.72 |
| `nomic-embed-text` | 0.47 – 0.51 | 0.57 – 0.61 |

Both separate cleanly, but in different places, so the default is chosen per model
rather than pretending one number fits. Set `CORTEX_MAX_DISTANCE` to override.

---

## Development

```bash
pytest                  # unit tests, no model server needed
pytest -m ollama        # integration tests against real models
ruff check src tests

npm run build --prefix web   # typechecks as part of the build
npx oxlint web/src
```

The suite runs offline: `Embedder` and `Librarian` are protocols, and the tests inject
deterministic fakes. Integration tests are marked `ollama` and skip themselves when the
server is unreachable.

### Layout

```
src/cortex/
  config.py       env-driven settings
  db.py           connections, extension loading, transactions
  migrations.py   schema, numbered forward migrations
  chunk.py        splitting note bodies for embedding
  embed.py        Embedder protocol + Ollama implementation
  llm.py          the Librarian
  store.py        records: create, read, update, delete, reindex
  retrieve.py     hybrid search
  capture.py      raw text in, filed record out
  port.py         import, export, backup
  vault.py        opening a vault, with or without Ollama
  cli.py          the command line
  settings.py     runtime model routing, stored in the vault
  api/
    app.py        routes
    deps.py       auth and per-request resources
    schemas.py    the wire contract

web/
  src/
    lib/api.ts    typed client, including the SSE reader
    screens/      Capture, Vault, RecordDetail, Settings, Setup
    index.css     design tokens, mobile-first, both themes
```

`core` knows nothing about HTTP or any UI. The API and web client will be thin layers
over it.

---

## Build order

| | | |
|---|---|---|
| **M1** | Storage core, hybrid search, CLI | ✅ done |
| **M2** | FastAPI: REST + SSE, bearer auth, batched sync | ✅ done |
| **M3** | Web client: capture, vault, search, settings | ✅ done |
| **M4** | Chat with persistent threads and managed context | next |
| **M5** | Creative generation with selective banking | |
| **M6** | Offline capture, installable PWA, share target, voice | |
| **M7** | Packaging, Tailscale binding, first-run wizard | |

## The prototype

`app.py` is the original single-file Streamlit app. It still runs
(`streamlit run app.py`, dependencies in `requirements.txt`) and reads its own separate
`memory_bank/` database.

The plan had it removed at M3, but the rebuild does not cover chat (M4) or creative
generation (M5) yet, so it is kept until those land rather than dropping working
features. Nothing depends on it.

## Privacy

Fully local. Your notes, the inference and the search all happen on your machine. The
only network call Cortex makes is to your own Ollama server.

## Licence

MIT.
