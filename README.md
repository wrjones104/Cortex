# 🧠 Cortex

**A local-first, AI-powered "second brain" for capturing, organizing, and retrieving your ideas — powered entirely by models running on your own machine.**

Cortex takes raw, unstructured "brain dumps" (notes, journals, worldbuilding, design docs, therapy reflections, whatever), uses a local LLM to categorize and clean them up, and stores them in a searchable vault. Because it runs on [Ollama](https://ollama.com) + [ChromaDB](https://www.trychroma.com/) + SQLite locally, **nothing ever leaves your computer** — no API keys, no cloud, no subscription.

---

## ✨ Features

| Tab | What it does |
|-----|--------------|
| 💬 **Brain Dump** | Paste raw text; a "Librarian" model structures it into `project / category / subcategory / title / content` JSON, preserving your original words, then vaults it. |
| 🗄️ **The Vault** | Browse, filter (by project), edit, and delete all stored records in a table view. |
| ✨ **Creative Generation** | Brainstorm new ideas with a "Creative" model that stays consistent with your existing project canon (via semantic retrieval), then optionally vault the result. |
| 🤖 **Chat with Vault** | RAG-style Q&A — ask questions and get answers grounded *only* in your stored records. |
| ⚙️ **Settings** | Route which local Ollama model powers each function (Librarian vs. Creative). |

### How it works

Every record is written to **two** stores:
- **SQLite** (`master_vault.db`) — the structured source of truth (id, project, category, title, content, timestamp).
- **ChromaDB** (`universal_vectors` collection) — vector embeddings of the content for semantic search.

Retrieval (`get_project_context`) embeds your query with `nomic-embed-text`, pulls the most relevant records from Chroma, and injects them into the model's system prompt as grounding context.

```
Raw text ──► Librarian LLM (JSON) ──► SQLite (structured record)
                                  └──► Ollama embed ──► ChromaDB (vector)

Query ──► Ollama embed ──► ChromaDB search ──► context ──► LLM answer
```

---

## 🛠️ Requirements

- **Python 3.14** (a `venv` is checked in, built against 3.14.3)
- **[Ollama](https://ollama.com)** running locally on `http://127.0.0.1:11434`
- The following Ollama models pulled:
  - An embedding model: `nomic-embed-text` **(required)**
  - A "Librarian" chat model, e.g. `qwen2.5:14b` (handles JSON, archiving, chat)
  - A "Creative" chat model, e.g. `gemma`-family (handles brainstorming)

```bash
ollama pull nomic-embed-text
ollama pull qwen2.5:14b
```

> Model names are configurable at runtime in the **Settings** tab — the app auto-discovers whatever models you have installed.

### Python dependencies

```
streamlit
chromadb
ollama
pandas
```

Install into a virtual environment:

```bash
python -m venv venv
venv\Scripts\activate        # Windows
pip install streamlit chromadb ollama pandas
```

---

## 🚀 Running

Make sure Ollama is running, then:

```bash
streamlit run app.py
```

The app opens in your browser. Databases are created automatically under `./memory_bank/` on first run.

---

## 📁 Project structure

```
cortex/
├── app.py              # The entire application (Streamlit single-file app)
├── memory_bank/        # Auto-created data directory (git-ignored recommended)
│   ├── master_vault.db # SQLite structured store
│   └── chroma.sqlite3  # ChromaDB vector store
└── venv/               # Python virtual environment
```

---

## 🔒 Privacy

Cortex is **fully local**. Your notes, the LLM inference, and the vector search all happen on your machine. There are no outbound network calls except to your local Ollama server.

---

## 📌 Status

Early-stage personal project / working prototype. Functional across all five tabs. See the maintainer's notes for planned improvements (config extraction, error surfacing, dual-store consistency, and packaging).
