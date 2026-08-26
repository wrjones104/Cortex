"""The Librarian: turns a raw brain dump into a structured record.

Two things the prototype got wrong are fixed here. It asked for JSON with
`format='json'` and hoped; this constrains the model with an actual schema, so
malformed output is rare rather than routine. And it let thinking models spend
their reasoning budget on a categorisation task and then threw the reasoning
away unread - `think=False` skips that work entirely.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

import ollama

from .ollama_client import client_for
from .tokens import DEFAULT_MAX_CONTEXT

LIBRARIAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "project": {"type": "string"},
        "category": {"type": "string"},
        "subcategory": {"type": "string"},
        "title": {"type": "string"},
        "content": {"type": "string"},
    },
    "required": ["project", "category", "subcategory", "title", "content"],
}

SYSTEM_PROMPT = """\
You are the archivist for a personal knowledge vault. You receive raw notes -
journals, meeting notes, worldbuilding, design docs, half-formed ideas - and
file them.

{project_directive}

Your job is to categorise and format. It is NOT to write.
- Preserve the author's own words and ideas exactly.
- Do not add lore, expand the text, invent details, or draw conclusions.
- You may apply clean Markdown formatting for readability. Nothing more.

Choose a taxonomy that fits the actual domain of the text. Read it first and
decide whether it is personal, professional, fictional or technical, then pick
a category that belongs to that domain - 'Team Management', 'Personal Health',
'Worldbuilding', 'Database Design'. Never default to fiction vocabulary like
'Lore' or 'Character' unless the text really is fiction.

The subcategory should be specific to this entry, not to the project.
The title should be short and concrete enough to recognise in a list.
{context}"""


class LibrarianError(RuntimeError):
    pass


@dataclass(frozen=True)
class StructuredNote:
    project: str
    category: str
    subcategory: str
    title: str
    content: str


class Librarian(Protocol):
    def structure(
        self, raw_text: str, *, project: str | None = None, context: str = ""
    ) -> StructuredNote: ...


def _extract_json(text: str) -> dict[str, Any]:
    """Parse the model's reply, repairing the common near-miss cases."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    braced = re.search(r"\{.*\}", text, re.DOTALL)
    if braced:
        try:
            return json.loads(braced.group(0))
        except json.JSONDecodeError:
            pass

    raise LibrarianError("The model did not return usable JSON.")


class OllamaLibrarian:
    def __init__(self, host: str, model: str, max_context: int = DEFAULT_MAX_CONTEXT) -> None:
        self.model = model
        self.host = host
        self.max_context = max_context

    @property
    def _client(self) -> ollama.Client:
        """Shared per host - see the note in embed.OllamaEmbedder."""
        return client_for(self.host)

    def structure(
        self, raw_text: str, *, project: str | None = None, context: str = ""
    ) -> StructuredNote:
        if not raw_text.strip():
            raise LibrarianError("Nothing to file - the note is empty.")

        directive = (
            f"This note belongs to the project '{project}'. Use that name verbatim."
            if project
            else "No project was given. Choose a short, concrete project name from the content."
        )
        system = SYSTEM_PROMPT.format(
            project_directive=directive,
            context=f"\n\nExisting notes from this project, for consistency only:\n{context}"
            if context
            else "",
        )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": raw_text},
        ]

        try:
            response = self._client.chat(
                model=self.model,
                messages=messages,
                format=LIBRARIAN_SCHEMA,
                think=False,
                options={"num_ctx": self.max_context},
            )
        except ollama.ResponseError as exc:
            if "think" in str(exc).lower():
                response = self._client.chat(
                    model=self.model,
                    messages=messages,
                    format=LIBRARIAN_SCHEMA,
                    options={"num_ctx": self.max_context},
                )
            else:
                raise LibrarianError(f"{self.model} failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - surfaced with context
            raise LibrarianError(f"{self.model} failed: {type(exc).__name__}: {exc}") from exc

        data = _extract_json(response["message"]["content"])

        content = str(data.get("content") or "").strip()
        if not content:
            # Never let a model's empty field silently discard the note.
            content = raw_text.strip()

        return StructuredNote(
            project=str(data.get("project") or project or "").strip(),
            category=str(data.get("category") or "").strip(),
            subcategory=str(data.get("subcategory") or "").strip(),
            title=str(data.get("title") or "").strip() or _fallback_title(raw_text),
            content=content,
        )


def _fallback_title(raw_text: str) -> str:
    first = next((ln.strip() for ln in raw_text.splitlines() if ln.strip()), "Untitled")
    first = first.lstrip("#").strip()
    return first[:80] if len(first) <= 80 else first[:77] + "..."


class Chatter(Protocol):
    """A model that can hold a conversation.

    Separate from Librarian: filing a note is a one-shot structured call,
    while chat streams and needs the model's real context window.
    """

    model: str

    @property
    def context_length(self) -> int: ...

    def complete(
        self, messages: list[dict], *, think: bool = False, format: Any = None
    ) -> tuple[str, int]:
        """Return (text, prompt_tokens). `format` may be a JSON schema."""
        ...

    def stream(self, messages: list[dict], *, think: bool = False, format: Any = None): ...


class OllamaChat:
    """Chat through a local Ollama server."""

    def __init__(self, host: str, model: str, max_context: int = DEFAULT_MAX_CONTEXT) -> None:
        self.model = model
        self.host = host
        self.max_context = max_context
        self._context: int | None = None

    @property
    def _client(self) -> ollama.Client:
        return client_for(self.host)

    @property
    def context_length(self) -> int:
        """The window in force: the smaller of what the model can do and what
        we are willing to load.

        This number does double duty, and that is the point. It is what
        `build_window` budgets against, and it is what gets sent to Ollama as
        `num_ctx`, so the two can never disagree.

        They used to. /api/show reports the *architecture's* maximum, not the
        window the model was actually loaded with - Ollama loads its own
        default unless told otherwise, and silently truncates anything past
        it. Believing the probe meant building a prompt several times larger
        than the server would accept and never being told: retrieved notes
        dropped out of the middle of the prompt, and the overflow check that
        should have triggered compaction was measuring against a window that
        did not exist. A model advertising 262k made it a 60x error.
        """
        if self._context is None:
            self._context = max(512, min(self._probe_context(), self.max_context))
        return self._context

    def _options(self) -> dict[str, int]:
        """Sent on every call, identically, so the window is declared rather
        than inferred. Keeping it constant across roles also stops Ollama
        reloading the weights just because a number changed."""
        return {"num_ctx": self.context_length}

    def _probe_context(self) -> int:
        from .tokens import FALLBACK_CONTEXT

        try:
            import httpx

            response = httpx.post(
                f"{self.host.rstrip('/')}/api/show",
                json={"model": self.model},
                timeout=30.0,
            )
            response.raise_for_status()
            info = response.json().get("model_info") or {}
        except Exception:  # noqa: BLE001 - a missing window is not fatal
            return FALLBACK_CONTEXT

        for key, value in info.items():
            if key.endswith("context_length") and isinstance(value, int) and value > 0:
                return value
        return FALLBACK_CONTEXT

    def complete(
        self, messages: list[dict], *, think: bool = False, format: Any = None
    ) -> tuple[str, int]:
        try:
            response = self._client.chat(
                model=self.model,
                messages=messages,
                stream=False,
                think=think,
                options=self._options(),
                **({"format": format} if format is not None else {}),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced with context
            raise LibrarianError(f"{self.model} failed: {type(exc).__name__}: {exc}") from exc

        text = (response["message"]["content"] or "").strip()
        return text, int(response.get("prompt_eval_count") or 0)

    def stream(self, messages: list[dict], *, think: bool = False, format: Any = None):
        """Yield ('token', text) as it arrives, then ('done', prompt_tokens).

        Reasoning tokens are yielded separately as ('thinking', text) so a
        client can show them without them landing in the stored answer. The
        prototype dropped them on the floor after paying for them.

        `format` may carry a JSON schema. The tokens are then JSON rather than
        prose, but they are still streamed: a 27B model takes a minute to
        produce five developed ideas, and showing nothing for that long is
        worse than showing progress a client can choose not to render.
        """
        try:
            chunks = self._client.chat(
                model=self.model,
                messages=messages,
                stream=True,
                think=think,
                options=self._options(),
                **({"format": format} if format is not None else {}),
            )
            prompt_tokens = 0
            for chunk in chunks:
                message = chunk.get("message") or {}
                thinking = message.get("thinking")
                if thinking:
                    yield ("thinking", thinking)
                content = message.get("content")
                if content:
                    yield ("token", content)
                if chunk.get("prompt_eval_count"):
                    prompt_tokens = int(chunk["prompt_eval_count"])
            yield ("done", prompt_tokens)
        except Exception as exc:  # noqa: BLE001 - surfaced with context
            raise LibrarianError(f"{self.model} failed: {type(exc).__name__}: {exc}") from exc
