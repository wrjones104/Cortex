# Cortex

**A local-first AI knowledge vault. Your notes, your machine, your models.**

Cortex takes raw, unstructured brain dumps — journals, meeting notes, worldbuilding, design docs, half-formed ideas — structures them with a local LLM, and makes them instantly retrievable by meaning and keyword. It runs entirely on [Ollama](https://ollama.com) and SQLite. Nothing leaves your computer.

---

## Key Features

- **100% Local & Private**: All embeddings, LLM inference, and search execute on your machine. Zero cloud dependencies, zero telemetry.
- **AI-Assisted Structuring**: An intelligent local "Librarian" reads incoming notes, assigns titles, categories, and tags, and organizes them into projects.
- **Hybrid Retrieval**: Combines semantic vector similarity search (`sqlite-vec`) with BM25 full-text keyword search (`FTS5`) via Reciprocal Rank Fusion (RRF).
- **Single-Transaction Integrity**: Notes, overlapping vector chunks, and full-text indexes are stored in a single SQLite database and committed atomically.
- **Installable Web App & Mobile PWA**: Modern responsive web client for desktop and mobile, with rich Markdown chat rendering, code highlighting, source citations, and dark/light themes.
- **Offline Capture & Background Sync**: Queue notes offline on mobile or desktop via IndexedDB; changes sync automatically and idempotently once reconnected.
- **Context-Managed Chat & Brainstorming**: Conversational retrieval with token budgeting and automatic history condensation, plus a creative brainstorming studio with selective idea banking.
- **Multi-User Vault Isolation**: Complete database file separation per user account (`cortex.db`, `cortex_<id>.db`). No cross-account leakage and no vector space dilution.
- **CLI & REST / SSE API**: Complete feature parity across the terminal CLI, Web UI, and a documented OpenAPI/Swagger server with streaming Server-Sent Events.

---

## How it works

One SQLite file holds everything: your records, their chunks, the vector index, and the full-text index.

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

Three design principles carry the architecture:

1. **One store, one transaction.** A record, its embeddings, and full-text index entries are written in a single SQLite `COMMIT`. They cannot drift out of sync, and backups require copying only a single database file.
2. **Chunked embedding.** Notes are split into ~400-token overlapping chunks before embedding and retrieved as whole records. Because embedding models have fixed context windows and reject larger inputs, chunking ensures notes of any length can be safely ingested.
3. **Hybrid retrieval.** Vector search captures conceptual similarity but can struggle with specific identifiers or proper nouns; keyword search finds exact terms but misses semantic paraphrasing. Cortex runs both retrieval arms simultaneously and fuses their rankings using Reciprocal Rank Fusion (RRF).

---

## Requirements

- **Python 3.11+**
- **Node.js 18+ & npm** (for building the web client)
- **[Ollama](https://ollama.com)** running locally
- Recommended Ollama models:

```bash
# Embedding model (default)
ollama pull embeddinggemma

# Chat & Librarian model (default)
ollama pull qwen2.5:14b

# Creative model (optional, used in split profile for brainstorming)
ollama pull gemma4:12b
```

> **Single-Model Setup:** If running on a GPU with limited VRAM, Cortex supports unified single-model mode (e.g. `smtek/Qwen3.8-27B:Q3_K_XL-16gb` or `qwen2.5:14b` for all chat and filing roles) to avoid model reload delays. See [Configuration](#configuration).

---

## Quickstart

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/wrjones104/Cortex.git
cd Cortex

# Create virtual environment and install Python package
uv venv && uv pip install -e ".[api,dev]"
# Or with standard pip:
# python -m venv .venv && source .venv/bin/activate && pip install -e ".[api,dev]"

# Install web dependencies and build the client
npm --prefix web install && npm run build --prefix web
```

### 2. Verify Environment & Setup

Run the interactive setup wizard to check vault storage, Ollama connectivity, model availability, and network safety:

```bash
cortex setup
```

`cortex setup` verifies the setup and offers to pull any missing models automatically. Run `cortex doctor` at any time to re-verify system health.

### 3. Start Cortex

```bash
cortex serve
```

The API and web interface are served together on a single port at `http://127.0.0.1:8765`.

Open `http://127.0.0.1:8765` in your browser. On first launch, you will be prompted to create the **Owner** account with a username and password.

---

## User Accounts & Vault Isolation

Cortex supports multiple user accounts while maintaining strict data separation. Each user receives their own isolated SQLite vault containing their records, chunks, vector indexes, conversations, and settings.

- **File-Level Isolation**: Each account's data lives in a separate SQLite file on disk. There is no shared table filtering (`WHERE user_id = ?`) in the core storage layer, preventing accidental cross-user leaks.
- **Isolated Vector Spaces**: Vector similarity searches run strictly within the user's individual database, preventing vector dilution from other users.
- **Account Management**: The initial owner account can create and manage additional accounts through the Web UI Settings screen or via the CLI:

```bash
cortex user add alex          # prompts for a password
cortex user list              # list accounts and vault file locations
cortex user passwd alex       # reset password and revoke active sessions
cortex user logout alex       # revoke all sessions for a user
cortex user remove alex       # remove account (use --purge to delete vault file)
```

CLI commands operate on the owner's vault by default. Pass `--user <name>` or set `CORTEX_USER=<name>` to target a different account.

---

## Web App & Mobile Access

### Features

- **Capture**: Quick brain dump input with real-time SSE progress indicators during AI classification and indexing.
- **Vault Explorer**: Filter, search, and view records with project tags, timestamps, and full Markdown rendering.
- **Chat**: Conversational interface with memory and grounding citations from your notes, rendered with Markdown tables, LaTeX math, and code syntax highlighting.
- **Brainstorming**: Generate alternatives and ideas, split rambles into discrete cards, and selectively bank chosen ideas directly to your vault.
- **Project Management**: Create and manage projects with custom grounding descriptions that frame future notes and conversations.

### Mobile & PWA Installation

The web interface is a Progressive Web App (PWA). Open it in Safari on iOS or Chrome on Android, and select **Add to Home Screen** to install it as a standalone app.

- Notes captured while offline are stored in browser IndexedDB.
- When network connectivity is restored, queued notes automatically synchronize to the server using idempotent batch processing.

### Remote Access over Tailscale

To securely access Cortex from phones or other devices on your private network, use [Tailscale](https://tailscale.com) to serve Cortex over HTTPS:

```bash
# 1. Start Cortex listening on localhost
cortex serve

# 2. Publish Cortex securely over your tailnet with automatic HTTPS
tailscale serve --bg 8765
```

Your vault is then accessible from your devices at `https://<machine-name>.<tailnet>.ts.net`.

**Why Tailscale HTTPS is recommended:**
- **PWA Capabilities**: Modern mobile browsers require a secure context (HTTPS or localhost) to enable service workers, offline caching, and PWA installation.
- **Zero Inbound Firewall Ports**: Cortex continues listening securely on loopback (`127.0.0.1`), avoiding open external ports.

> **Security Note for Ollama:** Ensure Ollama remains bound to `127.0.0.1` (`OLLAMA_HOST=127.0.0.1`). Cortex authenticates all inbound requests, whereas Ollama provides no authentication out of the box. Running `cortex doctor` will alert you if your Ollama instance is exposed to external interfaces.

---

## Command-Line Interface (CLI)

Cortex provides a rich CLI for automation, scripted capture, backups, and terminal workflows.

```bash
# Capture a quick note
cortex capture "The bell tower leans three degrees north." --project Echoes

# Hybrid search across records
cortex search "sailors navigating by a leaning tower"
```

### Command Reference

| Command | Description |
|---|---|
| `cortex capture` | File a note. Supports stdin, `--file`, `--project`, and `--verbatim` (skips AI rewrite) |
| `cortex search` | Perform hybrid semantic and keyword search with `--project` scoping and `--limit` |
| `cortex list` | List recent records with category, project, and timestamps |
| `cortex show ID` | Display a record in full |
| `cortex delete ID` | Delete a record, its vector embeddings, and full-text index entries |
| `cortex projects` | List all projects with record counts and descriptions |
| `cortex project NAME` | Manage a project (`--describe`, `--rename`, `--delete`) |
| `cortex export DIR` | Export vault records as Markdown files with YAML frontmatter |
| `cortex import DIR` | Ingest Markdown files from a directory into the vault |
| `cortex backup [DIR]` | Create a consistent point-in-time snapshot using SQLite's online backup API |
| `cortex reindex` | Recompute all chunks, embeddings, and full-text indexes |
| `cortex brainstorm` | Generate creative ideas or ramble mode with `--freeform` |
| `cortex ideas ID` | Inspect a generation, `--split` candidates, or `--bank` chosen ideas |
| `cortex ask` | Ask a question against your vault in a persistent conversation thread |
| `cortex threads` | List chat threads, inspect conversation history, or `--delete` a thread |
| `cortex setup` | Interactive first-run wizard: verify requirements and pull missing models |
| `cortex doctor` | Diagnostic health check of database, models, and network exposure |
| `cortex serve` | Start the HTTP API and integrated web UI server |
| `cortex token` | Output the machine API token for scripts and cron jobs |
| `cortex user` | Manage user accounts, passwords, and sessions |

### Data Portability: Export & Import

- **Markdown Export**: `cortex export <dir>` outputs one Markdown file per record, organized into project directories with standardized YAML frontmatter compatible with tools like Obsidian or Logseq.
- **Markdown Import**: `cortex import <dir>` recursively ingests folders of Markdown documents, parsing frontmatter or generating structure with the Librarian model.
- **Snapshot Backup**: `cortex backup` creates an atomic database snapshot using SQLite's online backup API without interrupting active readers or writers.

### Changing Embedding Models

Vector indexes record the embedding model that generated them and refuse queries from mismatched models to avoid silent search degradation. To switch embedding models:

```bash
CORTEX_EMBED_MODEL=nomic-embed-text cortex reindex
```

---

## HTTP & Streaming API

The FastAPI server provides full REST and Server-Sent Event (SSE) endpoints with interactive documentation available at `http://127.0.0.1:8765/docs`.

### Key Endpoints

| Route | Method | Description |
|---|---|---|
| `/health` | `GET` | Server health check (no credentials needed) |
| `/api/auth/state` | `GET` | Check whether an owner account exists |
| `/api/auth/setup` | `POST` | Create initial owner account |
| `/api/auth/login` | `POST` | Authenticate with username and password |
| `/api/auth/logout` | `POST` | Revoke current session token |
| `/api/auth/me` | `GET`, `PATCH` | View or update current user profile |
| `/api/auth/password` | `POST` | Change password |
| `/api/users` | `GET`, `POST` | List or create accounts (Owner only) |
| `/api/users/{id}` | `DELETE` | Delete account (Owner only) |
| `/api/status` | `GET` | Vault record counts, index integrity, and model availability |
| `/api/projects` | `GET` | List projects with note counts and descriptions |
| `/api/records` | `GET`, `POST` | List notes with pagination/filters, or create a note |
| `/api/records/stream` | `POST` | Stream note classification and embedding progress via SSE |
| `/api/records/{id}` | `GET`, `PATCH`, `DELETE` | Read, edit, or delete a note (supports optimistic concurrency) |
| `/api/search` | `GET` | Execute hybrid search queries |
| `/api/threads` | `GET`, `POST` | List or initiate conversational chat threads |
| `/api/threads/{id}/messages` | `POST` | Send message and stream AI response via SSE |
| `/api/generations` | `GET`, `POST` | Creative brainstorming history and streamed generation |
| `/api/generations/{id}/split` | `POST` | Split a freeform generation into candidate idea cards |
| `/api/generations/{id}/bank` | `POST` | Bank selected ideas as individual vault records |
| `/api/sync` | `POST` | Idempotently sync a batch of offline-queued captures |

### Authentication

All protected endpoints require an `Authorization: Bearer <token>` header. Two token types are supported:
- **Session Token**: Returned by `POST /api/auth/login`, scoped to the authenticated user.
- **Machine Token**: Generated on first run and printed with `cortex token`. Operates as the owner account for automated background scripts and cron jobs.

```bash
curl -H "Authorization: Bearer $(cortex token)" "http://127.0.0.1:8765/api/search?q=lighthouse"
```

---

## Configuration

All configuration settings are optional and can be set via environment variables.

| Variable | Default | Description |
|---|---|---|
| `CORTEX_DATA_DIR` | `%LOCALAPPDATA%\cortex` / `~/.local/share/cortex` | Directory storing vault databases and auth data |
| `CORTEX_OLLAMA_HOST` | `http://127.0.0.1:11434` | URL of the Ollama server |
| `CORTEX_EMBED_MODEL` | `embeddinggemma` | Model used for vector embeddings |
| `CORTEX_LIBRARIAN_MODEL` | `qwen2.5:14b` | Model used for note structuring and classification |
| `CORTEX_CREATIVE_MODEL` | `gemma4:12b` | Model used for brainstorming and creative generation |
| `CORTEX_UTILITY_MODEL` | `""` (falls back to Librarian) | Fast model for summarization and memory condensation |
| `CORTEX_MODEL_PROFILE` | `split` | Model routing profile: `split` or `single` |
| `CORTEX_SINGLE_MODEL` | `smtek/Qwen3.8-27B:Q3_K_XL-16gb` | Model used for all roles when `model_profile=single` |
| `CORTEX_MAX_CONTEXT` | `32768` | Context window size declared to Ollama for KV budgeting |
| `CORTEX_CHUNK_TARGET` | `400` | Target token count per chunk |
| `CORTEX_CHUNK_MAX` | `512` | Maximum token ceiling per chunk |
| `CORTEX_CHUNK_OVERLAP` | `60` | Overlap tokens between adjacent chunks |
| `CORTEX_MAX_DISTANCE` | per embedding model | Relevance distance ceiling for vector search filtering |
| `CORTEX_API_TOKEN` | generated on first run | Machine API token for system integrations |
| `CORTEX_WEB_DIR` | packaged client / `web/dist` | Directory serving the web application assets |

### Model Profiles: Split vs. Single

- **`split` (Default)**: Uses specialized models tailored for each task (e.g. structured extraction for filing, high-temperature models for brainstorming).
- **`single`**: Routes all filing, answering, and brainstorming tasks to `CORTEX_SINGLE_MODEL`. Recommended when running on GPUs where loading multiple models causes continuous VRAM eviction and reload overhead.

### Context Window Budgeting

`CORTEX_MAX_CONTEXT` specifies the exact context window passed as `num_ctx` to Ollama. This aligns the prompt budget with model execution and prevents silent server-side token truncation.

---

## Development

### Running Tests

```bash
# Run Python unit tests (runs offline with mock models)
pytest

# Run integration tests against a live Ollama instance
pytest -m ollama

# Run web test suite (IndexedDB queue and Markdown rendering tests)
npm test --prefix web
```

### Linting & Building

```bash
# Python linting and formatting
ruff check src tests

# Web linting and production build
npx oxlint web/src
npm run build --prefix web
```

### Codebase Structure

```
src/cortex/
  config.py       Configuration settings and environment variable parsing
  accounts.py     User authentication, password hashing, and vault file routing
  db.py           SQLite connections, extension loading (sqlite-vec), transactions
  migrations.py   Database schema versions and forward migrations
  chunk.py        Deterministic text chunker for vector embeddings
  embed.py        Embedder protocol and Ollama client implementation
  llm.py          Librarian structuring, categorization, and prompt formatting
  store.py        Record CRUD, chunking, FTS5 sync, and reindexing
  retrieve.py     Hybrid vector (sqlite-vec) and keyword (FTS5) search via RRF
  capture.py      End-to-end ingestion pipeline (raw text -> structured record)
  port.py         Markdown import/export and online SQLite database backup
  vault.py        Vault connection lifecycle and extension initialization
  cli.py          Typer-based command-line interface
  chat.py         Conversation threads, context budgeting, and ledger condensation
  creative.py     Brainstorming generations, ramble splitting, and idea banking
  tokens.py       Token estimation calibrated against real model prompts
  settings.py     Runtime model routing and persistent preferences
  setup_wizard.py First-run diagnostic checks, model puller, exposure detector
  webui.py        Static asset serving with Single Page Application fallback
  api/
    app.py        FastAPI application routes and SSE handlers
    deps.py       Authentication, rate-limiting, and request dependency injection
    schemas.py    Pydantic request and response wire contracts

web/
  src/
    components/   UI components, Markdown renderer, navigation, mascot illustrations
    lib/api.ts    Typed HTTP and SSE client
    lib/queue.ts  Offline capture queue backed by IndexedDB
    lib/sync.ts   Batched, idempotent offline sync engine
    screens/      Capture, Vault, Chat, Create, Pending, Settings, SignIn
    index.css     Responsive styles, CSS variables, and light/dark themes
```

### Packaging & Distribution

To build a standalone Python wheel that bundles the web frontend:

```bash
# 1. Build the web frontend
npm run build --prefix web

# 2. Stage the frontend assets for the Python package
cp -r web/dist src/cortex/webui

# 3. Build and install wheel via pipx
uv build && pipx install dist/cortex-*.whl

# 4. Clean up the staging directory
rm -rf src/cortex/webui
```

### Docker Deployment

For headless Linux servers or containerized deployments, a `docker-compose.yml` configuration is provided:

```bash
docker compose up -d
```

---

## Privacy

Cortex is entirely local. Your notes, vector embeddings, and search operations remain on your machine. The only network calls Cortex makes are local HTTP requests to your Ollama instance.

## License

[MIT](LICENSE)
