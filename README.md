# Cortex

**A local-first AI knowledge vault. Your notes, your machine, your models.**

Cortex takes raw, unstructured brain dumps — journals, meeting notes, worldbuilding,
design docs, half-formed ideas — files them with a local LLM, and makes them findable
by meaning and by keyword. It runs entirely on [Ollama](https://ollama.com) and SQLite.
Nothing leaves your computer.

> **Status: the rebuild is done.** Storage core, CLI, HTTP API, an installable web
> client, conversations with managed context, brainstorming with selective banking,
> offline capture, and a first run that checks its own footing.

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
npm --prefix web install && npm run build --prefix web
cortex setup
```

`cortex setup` checks everything Cortex needs — the vault, Ollama, each configured
model, and whether Ollama is exposed beyond your machine — offers to pull anything
missing, and prints your API token.

```bash
cortex serve
```

One command, one port. The API and the web app are served together at
`http://127.0.0.1:8765`, so there is no second server and no CORS to think about.

`cortex doctor` re-runs the checks any time.

### As a package

```bash
npm run build --prefix web && cp -r web/dist src/cortex/webui
uv build
pipx install dist/cortex-*.whl
```

The web client is copied into the package before building, so an installed Cortex
serves the app itself.

### With Docker

**You probably don't want this.** If Ollama already runs on your machine — especially
on Windows or a Mac with a GPU — installing Cortex natively is simpler and faster, and
Docker buys you nothing. The compose file is for a Linux box you want to hand the
whole stack to.

```bash
docker compose up -d
```

It brings up two services: Cortex, and an Ollama of its own. The `ollama` service
exists for a machine that hasn't got one. **If you already run Ollama, delete that
service** and point Cortex at the one you have:

```yaml
services:
  cortex:
    environment:
      # Docker Desktop resolves this to the host. On Linux, add
      # `extra_hosts: ["host.docker.internal:host-gateway"]` as well.
      CORTEX_OLLAMA_HOST: http://host.docker.internal:11434
```

There is a real catch here, and it is worth understanding before you choose:

> A container cannot reach an Ollama bound to `127.0.0.1`, because inside the container
> that address is the container itself. So running Cortex in Docker against a host
> Ollama means binding Ollama to something wider — which is exactly the exposure
> `cortex doctor` warns about. Running Cortex natively lets you keep Ollama on
> `127.0.0.1`, which is the safer arrangement and the reason to prefer it.

The vault lives on a volume, so the container stays disposable and your notes do not.
The bundled `ollama` service reserves an NVIDIA GPU; remove that `deploy` block to run
on CPU.

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
| `cortex projects` | Projects, their descriptions and record counts |
| `cortex project NAME` | Rename it, describe it, or remove it |
| `cortex export DIR` | Every record as Markdown with YAML frontmatter |
| `cortex import DIR` | Ingest a folder of Markdown, recursively |
| `cortex backup` | Consistent snapshot via SQLite's online backup API |
| `cortex reindex` | Rebuild chunks and embeddings from the records |
| `cortex brainstorm` | Generate alternatives, or ramble with `--freeform` |
| `cortex ideas` | Show a generation, `--split` a ramble, `--bank 0,2` what you liked |
| `cortex ask` | Ask a question, in a conversation that is kept |
| `cortex threads` | List conversations, `--delete N` to remove one |
| `cortex setup` | First run: check everything, pull what's missing |
| `cortex doctor` | Check the vault, the model server, and its exposure |
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
| `GET /api/records/{id}` · `PATCH` · `DELETE` | One record. PATCH takes `expected_updated_at` and 409s if it changed elsewhere |
| `GET /api/search` | Hybrid search |
| `GET/POST /api/threads` | List and start conversations |
| `GET/PATCH/DELETE /api/threads/{id}` | One conversation |
| `POST /api/threads/{id}/messages` | Ask, streamed as server-sent events |
| `GET/POST /api/generations` | History, and brainstorming as server-sent events |
| `POST /api/generations/{id}/split` | Cut a ramble into candidates |
| `POST /api/generations/{id}/bank` | File the chosen ideas, one record each |
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

Put Tailscale in front of Cortex rather than binding Cortex to the tailnet:

```bash
cortex serve                  # stays on 127.0.0.1
tailscale serve --bg 8765     # publishes it over HTTPS on your tailnet
```

Your machine is then reachable at `https://<machine>.<tailnet>.ts.net` from your own
devices and nothing else. `tailscale serve status` shows what is published;
`tailscale serve reset` takes it down.

**Do it this way, not by binding to the tailnet address.** Two reasons:

*HTTPS is not optional for the phone client.* Browsers only grant service workers,
home-screen install and dictation to a *secure context* — HTTPS, or localhost. Over
plain `http://100.x.y.z` the app still loads, but it will not install, will not cache
its shell, and the microphone button disappears. Tailscale issues a real certificate,
so all of that works.

*No firewall hole.* Cortex keeps listening on localhost, where nothing needs to be
allowed through. Binding to the tailnet address instead means adding an inbound rule
for whichever `python.exe` is running it — easy to get wrong, and easy to leave behind.

If you would rather bind directly anyway, `cortex serve --tailscale` finds the address
for you, and you will need an inbound rule scoped to that interface:

```powershell
# Run as Administrator. Narrow on purpose: this port, that adapter, tailnet only.
New-NetFirewallRule -DisplayName "Cortex on Tailscale" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 `
  -InterfaceAlias "Tailscale" -RemoteAddress 100.64.0.0/10 -Profile Private
```

> **Check Ollama too.** Cortex requires a token on every route. Ollama requires
> nothing — so an Ollama listening on `0.0.0.0` means anyone who can reach the machine
> can use your models and read what is sent through them, whatever Cortex does.
> `cortex doctor` tests this by actually calling Ollama on a non-loopback address and
> tells you if it answers. The fix is `OLLAMA_HOST=127.0.0.1` and a restart.

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
| `CORTEX_UTILITY_MODEL` | falls back to the Librarian |
| `CORTEX_CHUNK_TARGET` / `_MAX` / `_OVERLAP` | `400` / `512` / `60` |
| `CORTEX_MAX_DISTANCE` | per embedding model |
| `CORTEX_API_TOKEN` | generated on first run |
| `CORTEX_WEB_DIR` | the packaged client, then `web/dist` |

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

npm test --prefix web        # the offline queue and its sync
npm run build --prefix web   # typechecks as part of the build
npx oxlint web/src
```

The queue tests run against a real IndexedDB implementation rather than a mock,
because that is the code a lost note would be lost in.

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
  chat.py         threads, condensation, budgeting, compaction, the ledger
  creative.py     brainstorming, splitting, selective banking
  tokens.py       token estimation that calibrates against real prompts
  settings.py     runtime model routing, stored in the vault
  setup_wizard.py first-run checks, model pulling, exposure detection
  webui.py        serving the built client, with an SPA fallback
  api/
    app.py        routes
    deps.py       auth and per-request resources
    schemas.py    the wire contract

web/
  src/
    lib/api.ts    typed client, including the SSE reader
    lib/queue.ts  the offline capture queue (IndexedDB)
    lib/sync.ts   draining it, batched and idempotent
    lib/voice.ts  on-device dictation
    screens/      Capture, Vault, Chat, Create, Pending, Settings, Setup
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
| **M4** | Chat with persistent threads and managed context | ✅ done |
| **M5** | Creative generation with selective banking | ✅ done |
| **M6** | Offline capture, installable PWA, share target, voice | ✅ done |
| **M7** | Packaging, Tailscale binding, first-run wizard | ✅ done |

## Privacy

Fully local. Your notes, the inference and the search all happen on your machine. The
only network call Cortex makes is to your own Ollama server.

## Licence

MIT.
