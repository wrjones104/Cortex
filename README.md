# Cortex

**A local-first AI knowledge vault. Your notes, your machine, your models.**

Cortex takes raw, unstructured brain dumps — journals, meeting notes, worldbuilding,
design docs, half-formed ideas — files them with a local LLM, and makes them findable
by meaning and by keyword. It runs entirely on [Ollama](https://ollama.com) and SQLite.
Nothing leaves your computer.

> **Status: rebuild in progress.** Milestone 1 (storage core + CLI) is complete and
> tested. The HTTP API and the web client are next — see [Build order](#build-order).

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
uv venv && uv pip install -e ".[dev]"
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
| `CORTEX_MAX_DISTANCE` | `0.75` |

`CORTEX_MAX_DISTANCE` is the relevance floor for the vector arm. Good values are
model-dependent — see the note in `src/cortex/retrieve.py` before changing it.

---

## Development

```bash
pytest                  # unit tests, no model server needed
pytest -m ollama        # integration tests against real models
ruff check src tests
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
```

`core` knows nothing about HTTP or any UI. The API and web client will be thin layers
over it.

---

## Build order

| | | |
|---|---|---|
| **M1** | Storage core, hybrid search, CLI | ✅ done |
| **M2** | FastAPI: REST + SSE, bearer auth, batched sync | next |
| **M3** | Web client: capture, vault, search — retires the Streamlit prototype | |
| **M4** | Chat with persistent threads and managed context | |
| **M5** | Creative generation with selective banking | |
| **M6** | Offline capture, installable PWA, share target, voice | |
| **M7** | Packaging, Tailscale binding, first-run wizard | |

## The prototype

`app.py` is the original single-file Streamlit app. It still runs
(`streamlit run app.py`, dependencies in `requirements.txt`) and reads its own separate
`memory_bank/` database. It is kept for reference and comes out at M3.

## Privacy

Fully local. Your notes, the inference and the search all happen on your machine. The
only network call Cortex makes is to your own Ollama server.

## Licence

MIT.
