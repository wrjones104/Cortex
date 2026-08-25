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

```bash
docker compose up -d
```

Brings up Cortex and its own Ollama. Drop the `ollama` service from
`docker-compose.yml` if you already run one, and point `CORTEX_OLLAMA_HOST` at it. The
vault lives on a volume, so the container stays disposable and your notes do not.

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

```bash
cortex serve --tailscale
```

Binds to this machine's [Tailscale](https://tailscale.com) address, which it finds for
you. Your own devices can reach it; nothing else can. Open that address on your phone
and add it to your home screen.

> **Check Ollama too.** Cortex requires a token on every route. Ollama requires
> nothing — so an Ollama listening on `0.0.0.0` means anyone who can reach the machine
> can use your models and read what is sent through them, whatever Cortex does.
> `cortex doctor` tests this by actually calling Ollama on a non-loopback address and
> tells you if it answers. The fix is `OLLAMA_HOST=127.0.0.1` and a restart.

---

## Conversations

```bash
cortex ask "who tends the lighthouse?" --project Echoes
cortex ask --thread 1 "and his daughter?"
```

Threads are stored in the vault, so they survive a restart and are the same
conversations the web app shows. Four things make a long one work on a local model:

**Query condensation.** A follow-up like "how does she find her way?" contains no
noun at all. Before retrieving, the question is rewritten into a standalone one using
the last few turns, so the search sees "how does Mireille navigate" rather than the
word "she".

**A budgeted window.** The model's real context length is read from `/api/show` —
not assumed — and about 30% is held back for the answer. What is left is filled
newest-first with the conversation, with a cap on how much retrieved material can
crowd it out.

**A rolling summary.** When the thread no longer fits, the oldest turns are folded
into running prose instead of being dropped.

**A facts ledger.** Names, decisions and corrections are extracted from turns at the
moment they are summarised away, and are never summarised themselves. This is what
stops turn 30 forgetting the name you gave it on turn 4.

Token estimates calibrate themselves: every Ollama response reports
`prompt_eval_count`, the true size of the prompt just sent, so the characters-per-token
ratio is measured per model rather than guessed.

Changing a thread's search scope writes a visible marker into the transcript, so
scrolling back later shows which answers were scoped to what.

---

## Brainstorming

```bash
cortex brainstorm --project Echoes -n 4 "ways the harbour bell might work"
cortex ideas 1 --bank 0,3
```

Two modes, because brainstorming happens two ways:

**Alternatives** for when you know you want options. The count is part of the request
and the model returns a structured array, which is far more reliable than cutting
prose apart afterwards. You get a numbered list; you bank the ones you wanted.

**Ramble** for when the good idea turns up mid-thought. Generate as prose, then
`--split` it into candidates once you see something worth keeping.

Either way **each idea becomes its own record**, with its own title and its own
embedding — which is what makes it findable on its own later. Banking is **verbatim
by default**: the prototype sent every banked idea back through the Librarian, handing
a 27B model an unrequested rewrite of prose you had already decided you liked. Pass
`--clean` when you do want it tidied.

Generations are kept, so a second attempt never destroys a batch you had not finished
mining, and ideas you already banked are marked so you cannot file one twice.

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
- **Create** — brainstorm alternatives or ramble, then tick the ideas worth keeping.
  Earlier generations stay available.
- **Chat** — conversations down the side, transcript in the middle. Progress shows
  while the model works, each answer lists the notes it read, and scope changes appear
  inline.
- **Settings** — model routing, vault health, index integrity.

Model routing is stored in the vault rather than the environment, so changing your
Librarian is a click and takes effect immediately. Only chat-capable models are
offered — Ollama reports what each model can do, so an embedding model can no longer
be selected as a chat model.

### On the phone

Build once and serve the static files:

```bash
npm run build --prefix web
```

Open the app on your phone, point it at your Tailscale address, and use "Add to Home
Screen". The app shell is cached by a service worker, so it opens instantly.

**Capture works with no signal.** A note goes into a queue on the device and the screen
returns immediately — it never waits for a round trip, and never depends on one
succeeding. The queue drains itself when the network comes back, when you reopen the
app, or when you tap Sync. Every queued note carries an idempotency key, so a batch the
phone was unsure about can be re-sent without duplicating anything.

The model never runs on the handset. Queued notes are filed by the Librarian on your
desktop when they arrive, which is where the GPU is.

**Share to Cortex.** The app registers as a share target, so it appears in Android's
share sheet. Highlight anything, share it, and it lands in the capture box.

**Dictation.** The microphone button transcribes on the device via the Web Speech API,
so it costs no round trip and works offline like everything else on that screen.

> **Not included: true background sync.** A service worker draining the queue while the
> app is closed would need the connection token, which lives in localStorage and a
> service worker cannot read — so it would mean a second copy of the auth and sync
> logic that could drift from the first. The window it would cover is between capturing
> offline and next opening the app, and opening the app is how you capture the next
> thing anyway.

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
| **M7** | Packaging, Tailscale binding, first-run wizard | next |

## The prototype

`app.py` is the original single-file Streamlit app. It still runs
(`streamlit run app.py`, dependencies in `requirements.txt`) and reads its own separate
`memory_bank/` database.

The rebuild now covers everything the prototype did, and does it better. `app.py` is
kept only as a reference for the moment; nothing depends on it, and it can be deleted
whenever you like.

## Privacy

Fully local. Your notes, the inference and the search all happen on your machine. The
only network call Cortex makes is to your own Ollama server.

## Licence

MIT.
