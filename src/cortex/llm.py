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
    def __init__(self, host: str, model: str) -> None:
        self.model = model
        self.host = host
        self._cached_client: ollama.Client | None = None

    @property
    def _client(self) -> ollama.Client:
        """Built on first use - see the note in embed.OllamaEmbedder."""
        if self._cached_client is None:
            self._cached_client = ollama.Client(host=self.host)
        return self._cached_client

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
                model=self.model, messages=messages, format=LIBRARIAN_SCHEMA, think=False
            )
        except ollama.ResponseError as exc:
            if "think" in str(exc).lower():
                response = self._client.chat(
                    model=self.model, messages=messages, format=LIBRARIAN_SCHEMA
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
