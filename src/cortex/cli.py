"""Command line interface.

This is not scaffolding for the web app - it stays the right tool for scripted
capture, cron backups and bulk import long after the browser client exists.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import typer

from . import __version__
from .capture import capture as capture_note
from .config import Config
from .embed import EmbeddingError
from .llm import LibrarianError
from .port import backup as backup_vault
from .port import export_markdown, import_markdown
from .retrieve import search as search_vault
from .store import (
    DuplicateRecordError,
    RecordNotFoundError,
    count_records,
    delete_record,
    get_record,
    integrity_report,
    list_projects,
    list_records,
    reindex,
)
from .vault import open_readonly, open_vault

app = typer.Typer(
    add_completion=False,
    help="Cortex - a local-first AI knowledge vault.",
    no_args_is_help=True,
)


def _config() -> Config:
    return Config.from_env()


def _fail(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _local_time(iso: str) -> str:
    """Render a stored UTC timestamp in the reader's local timezone."""
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


@app.command()
def version() -> None:
    """Show the Cortex version and where the vault lives."""
    config = _config()
    typer.echo(f"cortex {__version__}")
    typer.echo(f"vault  {config.db_path}")


@app.command()
def capture(
    text: str | None = typer.Argument(None, help="Note text. Reads stdin when omitted."),
    project: str | None = typer.Option(None, "--project", "-p", help="Project to file under."),
    title: str | None = typer.Option(None, "--title", "-t", help="Override the title."),
    file: Path | None = typer.Option(None, "--file", "-f", help="Read the note from a file."),
    verbatim: bool = typer.Option(
        False, "--verbatim", help="Store exactly as written, with no model rewrite."
    ),
    allow_duplicate: bool = typer.Option(
        False, "--allow-duplicate", help="Store even if an identical note exists."
    ),
) -> None:
    """File a note into the vault."""
    if file is not None:
        raw = file.read_text(encoding="utf-8")
    elif text is not None:
        raw = text
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        _fail("Nothing to capture. Pass text, use --file, or pipe into stdin.")

    if not raw.strip():
        _fail("Nothing to capture - the note is empty.")

    config = _config()
    try:
        vault = open_vault(config)
    except EmbeddingError as exc:
        _fail(f"{exc}\nIs Ollama running, and is '{config.embed_model}' pulled?")

    try:
        result = capture_note(
            vault.conn,
            vault.embedder,
            raw,
            librarian=vault.librarian,
            project=project,
            title=title,
            verbatim=verbatim,
            allow_duplicate=allow_duplicate,
            chunk_options=vault.chunk_options,
        )
    except DuplicateRecordError as exc:
        typer.secho(str(exc), fg=typer.colors.YELLOW, err=True)
        typer.echo("Pass --allow-duplicate to store it anyway.", err=True)
        raise typer.Exit(code=1) from exc
    except (LibrarianError, EmbeddingError) as exc:
        _fail(str(exc))
    finally:
        pass

    record = result.record
    for warning in result.warnings:
        typer.secho(f"! {warning}", fg=typer.colors.YELLOW, err=True)

    typer.secho(f"#{record.id}  {record.title}", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  project  {record.project_name}")
    if record.category:
        typer.echo(f"  filed    {record.category} / {record.subcategory}")
    typer.echo(f"  indexed  {result.chunks} chunk{'s' if result.chunks != 1 else ''}")
    vault.close()


@app.command()
def search(
    query: str = typer.Argument(..., help="What to look for."),
    project: str | None = typer.Option(None, "--project", "-p", help="Limit to one project."),
    limit: int = typer.Option(10, "--limit", "-n", help="How many results."),
) -> None:
    """Search the vault by meaning and by keyword at once."""
    config = _config()
    try:
        vault = open_vault(config)
    except EmbeddingError as exc:
        _fail(f"{exc}\nIs Ollama running, and is '{config.embed_model}' pulled?")

    hits = search_vault(
        vault.conn,
        vault.embedder,
        query,
        project=project,
        limit=limit,
        rrf_k=config.rrf_k,
        max_distance=config.effective_max_distance,
    )

    if not hits:
        typer.echo("No matches.")
        vault.close()
        return

    for hit in hits:
        record = hit.record
        typer.secho(f"#{record.id}  {record.title}", fg=typer.colors.CYAN, bold=True)
        typer.echo(
            f"  {record.project_name} | {record.category or 'uncategorised'} | "
            f"matched by {hit.matched_by} | score {hit.score:.4f}"
        )
        snippet = " ".join(hit.snippet.split())
        typer.echo(f"  {snippet[:200]}{'...' if len(snippet) > 200 else ''}\n")

    vault.close()


@app.command("list")
def list_cmd(
    project: str | None = typer.Option(None, "--project", "-p", help="Limit to one project."),
    limit: int = typer.Option(20, "--limit", "-n", help="How many records."),
) -> None:
    """List records, newest first."""
    conn = open_readonly(_config())
    records = list_records(conn, project=project, limit=limit)
    total = count_records(conn, project=project)

    if not records:
        typer.echo("The vault is empty." if not project else f"Nothing in '{project}' yet.")
        conn.close()
        return

    for record in records:
        typer.secho(f"#{record.id:<5}", fg=typer.colors.BRIGHT_BLACK, nl=False)
        typer.echo(f"{record.title}")
        typer.secho(
            f"      {record.project_name} | {_local_time(record.created_at)}",
            fg=typer.colors.BRIGHT_BLACK,
        )

    typer.echo(f"\nShowing {len(records)} of {total}.")
    conn.close()


@app.command()
def show(record_id: int = typer.Argument(..., help="Record id.")) -> None:
    """Print one record in full."""
    conn = open_readonly(_config())
    try:
        record = get_record(conn, record_id)
    except RecordNotFoundError as exc:
        conn.close()
        _fail(str(exc))

    typer.secho(record.title, bold=True)
    typer.secho(
        f"{record.project_name} | {record.category or 'uncategorised'}"
        f"{' / ' + record.subcategory if record.subcategory else ''} | "
        f"created {_local_time(record.created_at)} | updated {_local_time(record.updated_at)}",
        fg=typer.colors.BRIGHT_BLACK,
    )
    typer.echo("")
    typer.echo(record.body)
    conn.close()


@app.command()
def delete(
    record_id: int = typer.Argument(..., help="Record id."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
) -> None:
    """Delete a record and everything indexed from it."""
    conn = open_readonly(_config())
    try:
        record = get_record(conn, record_id)
    except RecordNotFoundError as exc:
        conn.close()
        _fail(str(exc))

    if not yes:
        typer.echo(f"#{record.id}  {record.title}  ({record.project_name})")
        typer.confirm("Delete this permanently?", abort=True)

    delete_record(conn, record_id)
    typer.secho(f"Deleted #{record_id}.", fg=typer.colors.YELLOW)
    conn.close()


@app.command()
def projects() -> None:
    """List projects and how much is in each."""
    conn = open_readonly(_config())
    found = list_projects(conn)
    if not found:
        typer.echo("No projects yet.")
        conn.close()
        return

    width = max(len(p.name) for p in found)
    for project in found:
        count = count_records(conn, project=project.slug)
        typer.echo(f"{project.name.ljust(width)}   {count:>4}")
    conn.close()


@app.command("export")
def export_cmd(
    destination: Path = typer.Argument(..., help="Folder to write Markdown files into."),
) -> None:
    """Export the whole vault as Markdown with YAML frontmatter."""
    conn = open_readonly(_config())
    written = export_markdown(conn, destination)
    typer.secho(
        f"Exported {written} record{'s' if written != 1 else ''} to {destination}",
        fg=typer.colors.GREEN,
    )
    conn.close()


@app.command("import")
def import_cmd(
    source: Path = typer.Argument(..., help="Folder of Markdown files to ingest."),
    project: str = typer.Option("Imported", "--project", "-p", help="Fallback project name."),
) -> None:
    """Import a folder of Markdown files, recursively."""
    config = _config()
    try:
        vault = open_vault(config)
    except EmbeddingError as exc:
        _fail(f"{exc}\nIs Ollama running, and is '{config.embed_model}' pulled?")

    report = import_markdown(
        vault.conn,
        vault.embedder,
        source,
        default_project=project,
        chunk_options=vault.chunk_options,
    )

    typer.secho(f"Imported {report.imported}", fg=typer.colors.GREEN)
    if report.skipped_duplicates:
        typer.echo(f"Skipped {report.skipped_duplicates} already in the vault")
    for path, error in report.failed:
        typer.secho(f"Failed  {path}: {error}", fg=typer.colors.RED, err=True)
    vault.close()


@app.command()
def backup() -> None:
    """Take a consistent snapshot of the vault."""
    config = _config()
    conn = open_readonly(config)
    target = backup_vault(conn, config.backup_dir)
    size = target.stat().st_size / 1_048_576
    typer.secho(f"Backed up to {target} ({size:.1f} MB)", fg=typer.colors.GREEN)
    conn.close()


@app.command("reindex")
def reindex_cmd(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
) -> None:
    """Rebuild every chunk and embedding from the stored records."""
    config = _config()
    try:
        vault = open_vault(config)
    except EmbeddingError as exc:
        _fail(f"{exc}\nIs Ollama running, and is '{config.embed_model}' pulled?")

    total = count_records(vault.conn)
    if not yes:
        typer.echo(f"Re-embedding {total} record(s) with {config.embed_model}.")
        typer.confirm("Continue?", abort=True)

    def progress(done: int, of: int, record) -> None:
        typer.echo(f"  [{done}/{of}] {record.title[:60]}")

    count = reindex(
        vault.conn, vault.embedder, chunk_options=vault.chunk_options, progress=progress
    )
    typer.secho(f"Reindexed {count} record(s).", fg=typer.colors.GREEN)
    vault.close()


@app.command()
def doctor() -> None:
    """Check the vault and the model server, and report anything wrong."""
    config = _config()
    typer.secho("Vault", bold=True)
    typer.echo(f"  path     {config.db_path}")
    typer.echo(f"  exists   {'yes' if config.db_path.exists() else 'no (created on first use)'}")

    conn = open_readonly(config)
    typer.echo(f"  records  {count_records(conn)}")
    typer.echo(f"  projects {len(list_projects(conn))}")

    report = integrity_report(conn)
    problems = {k: v for k, v in report.items() if v}
    if problems:
        typer.secho("  integrity", fg=typer.colors.RED, bold=True)
        for key, value in problems.items():
            typer.secho(f"    {key.replace('_', ' ')}: {value}", fg=typer.colors.RED)
        typer.echo("    run `cortex reindex` to rebuild the index")
    else:
        typer.secho("  integrity ok", fg=typer.colors.GREEN)
    conn.close()

    typer.secho("\nOllama", bold=True)
    typer.echo(f"  host     {config.ollama_host}")
    try:
        import ollama

        installed = {m["model"] for m in ollama.Client(host=config.ollama_host).list()["models"]}
    except Exception as exc:  # noqa: BLE001 - reported to the user below
        typer.secho(f"  unreachable - {type(exc).__name__}: {exc}", fg=typer.colors.RED)
        typer.echo("  Cortex can still list, show, export and back up without it.")
        return

    typer.secho(f"  reachable, {len(installed)} model(s)", fg=typer.colors.GREEN)

    from .setup_wizard import ollama_exposure

    exposed = ollama_exposure(config)
    if exposed:
        typer.secho(
            f"  EXPOSED    Ollama answers on {exposed}, not just this machine.",
            fg=typer.colors.RED,
            bold=True,
        )
        typer.secho(
            "             It has no authentication, so anyone who can reach that\n"
            "             address can use your models and read what is sent to them.\n"
            "             Set OLLAMA_HOST=127.0.0.1 and restart Ollama.",
            fg=typer.colors.RED,
        )
    else:
        typer.secho("  private    Ollama answers on this machine only", fg=typer.colors.GREEN)
    for label, name in (
        ("embed", config.embed_model),
        ("librarian", config.librarian_model),
        ("creative", config.creative_model),
    ):
        ok = name in installed or f"{name}:latest" in installed
        colour = typer.colors.GREEN if ok else typer.colors.RED
        suffix = "" if ok else f"  - not installed, run: ollama pull {name}"
        typer.secho(f"  {label:<10} {name}{suffix}", fg=colour)


if __name__ == "__main__":
    app()


@app.command()
def token() -> None:
    """Print the API token, creating one if this is the first run."""
    from .config import load_or_create_token

    typer.echo(load_or_create_token(_config().data_dir))


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Interface to bind."),
    port: int = typer.Option(8765, "--port", help="Port to listen on."),
    reload: bool = typer.Option(False, "--reload", help="Restart on code changes."),
    tailscale: bool = typer.Option(
        False, "--tailscale", help="Bind to this machine's tailnet address."
    ),
    web: bool = typer.Option(True, "--web/--no-web", help="Also serve the browser client."),
) -> None:
    """Run Cortex: the API, and the web app with it."""
    try:
        import uvicorn
    except ImportError as exc:
        _fail('The API extra is not installed. Run: pip install -e ".[api]"')
        raise typer.Exit(1) from exc

    from .api import create_app
    from .config import load_or_create_token
    from .setup_wizard import tailscale_address
    from .webui import find_web_dir

    config = _config()

    if tailscale:
        address = tailscale_address()
        if not address:
            _fail("Could not find a Tailscale address. Is Tailscale installed and connected?")
        host = address

    api_token = load_or_create_token(config.data_dir)
    web_dir = find_web_dir() if web else None

    typer.secho(f"Cortex on http://{host}:{port}", fg=typer.colors.GREEN, bold=True)
    if web_dir is not None:
        typer.echo(f"  app    http://{host}:{port}")
    elif web:
        typer.secho(
            "  app    not built - run: npm run build --prefix web",
            fg=typer.colors.YELLOW,
        )
    typer.echo(f"  docs   http://{host}:{port}/docs")
    typer.echo(f"  vault  {config.db_path}")
    typer.echo(f"  token  {api_token}")

    if host in ("0.0.0.0", "::"):  # noqa: S104 - detecting it, not binding it
        typer.secho(
            "\n  Binding to every interface puts Cortex on any network this machine\n"
            "  is attached to. Prefer --tailscale, which reaches your own devices\n"
            "  without exposing anything.",
            fg=typer.colors.RED,
        )
    elif host not in ("127.0.0.1", "localhost", "::1"):
        # Plain HTTP on anything but localhost is not a secure context, and a
        # browser withholds exactly the things the phone client needs. Say it
        # here, because this is the moment someone is about to open it there.
        typer.secho(
            "\n  Serving plain HTTP on a non-local address. Browsers treat that as\n"
            "  insecure, so on a phone there is no service worker, no install to\n"
            "  the home screen, and no dictation.",
            fg=typer.colors.YELLOW,
        )
        typer.secho(
            "\n  For HTTPS without opening a firewall port, leave Cortex on\n"
            "  localhost and put Tailscale in front of it instead:\n"
            f"\n      cortex serve --port {port}\n"
            f"      tailscale serve --bg {port}\n"
            "\n  Cortex needs a token on every route; check the same is true of\n"
            "  Ollama with `cortex doctor`.",
            fg=typer.colors.BRIGHT_BLACK,
        )

    uvicorn.run(
        create_app(config, api_token, serve_web=web), host=host, port=port, reload=reload
    )


@app.command()
def threads(
    delete_id: int | None = typer.Option(None, "--delete", help="Delete a conversation."),
) -> None:
    """List conversations, or delete one."""
    from .chat import ThreadNotFoundError, delete_thread, list_threads

    conn = open_readonly(_config())

    if delete_id is not None:
        try:
            delete_thread(conn, delete_id)
        except ThreadNotFoundError as exc:
            conn.close()
            _fail(str(exc))
        typer.secho(f"Deleted conversation {delete_id}.", fg=typer.colors.YELLOW)
        conn.close()
        return

    found = list_threads(conn)
    if not found:
        typer.echo("No conversations yet. Start one with `cortex ask`.")
        conn.close()
        return

    for thread in found:
        typer.secho(f"#{thread.id:<5}", fg=typer.colors.BRIGHT_BLACK, nl=False)
        typer.echo(thread.title)
        bits = [thread.project or "all projects", f"{thread.message_count} messages"]
        if thread.summary:
            bits.append("summarised")
        typer.secho(
            f"      {' | '.join(bits)} | {_local_time(thread.updated_at)}",
            fg=typer.colors.BRIGHT_BLACK,
        )
    conn.close()


@app.command()
def ask(
    question: str = typer.Argument(..., help="What to ask."),
    thread_id: int | None = typer.Option(
        None, "--thread", "-t", help="Continue a conversation instead of starting one."
    ),
    project: str | None = typer.Option(None, "--project", "-p", help="Scope a new conversation."),
    show_sources: bool = typer.Option(True, "--sources/--no-sources", help="List what it read."),
) -> None:
    """Ask the vault a question, in a conversation that is kept."""
    from .chat import ThreadNotFoundError, answer, create_thread, get_thread
    from .llm import OllamaChat
    from .settings import get_settings

    config = _config()
    try:
        vault = open_vault(config)
    except EmbeddingError as exc:
        _fail(f"{exc}\nIs Ollama running, and is '{config.embed_model}' pulled?")

    settings = get_settings(vault.conn, config)
    chatter = OllamaChat(config.ollama_host, settings.librarian_model)
    utility = OllamaChat(
        config.ollama_host, settings.utility_model or settings.librarian_model
    )

    if thread_id is None:
        thread = create_thread(vault.conn, project=project)
        thread_id = thread.id
    else:
        try:
            get_thread(vault.conn, thread_id)
        except ThreadNotFoundError as exc:
            vault.close()
            _fail(str(exc))

    sources: list[str] = []
    try:
        for kind, payload in answer(
            vault.conn,
            vault.embedder,
            chatter,
            thread_id,
            question,
            utility=utility,
            max_distance=config.effective_max_distance,
            rrf_k=config.rrf_k,
        ):
            if kind == "status":
                typer.secho(f"  {payload}...", fg=typer.colors.BRIGHT_BLACK, err=True)
            elif kind == "sources":
                sources = list(payload)
            elif kind == "token":
                typer.echo(payload, nl=False)
        typer.echo("")
    except (LibrarianError, EmbeddingError) as exc:
        vault.close()
        _fail(str(exc))

    if show_sources and sources:
        typer.secho("\nRead:", fg=typer.colors.BRIGHT_BLACK)
        for source in sources:
            typer.secho(f"  - {source}", fg=typer.colors.BRIGHT_BLACK)

    typer.secho(f"\nConversation #{thread_id}", fg=typer.colors.BRIGHT_BLACK)
    typer.secho(
        f"Continue it with: cortex ask --thread {thread_id} \"...\"",
        fg=typer.colors.BRIGHT_BLACK,
    )
    vault.close()


@app.command()
def brainstorm(
    prompt: str = typer.Argument(..., help="What to brainstorm."),
    count: int = typer.Option(4, "--count", "-n", help="How many alternatives."),
    freeform: bool = typer.Option(
        False, "--freeform", help="Ramble as prose instead of producing options."
    ),
    project: str | None = typer.Option(None, "--project", "-p", help="Stay consistent with it."),
) -> None:
    """Brainstorm ideas, then bank the ones you want with `cortex ideas`."""
    from .creative import generate
    from .llm import OllamaChat
    from .settings import get_settings

    config = _config()
    try:
        vault = open_vault(config)
    except EmbeddingError as exc:
        _fail(f"{exc}\nIs Ollama running, and is '{config.embed_model}' pulled?")

    settings = get_settings(vault.conn, config)
    creative = OllamaChat(config.ollama_host, settings.creative_model)

    generation_id = None
    try:
        for kind, payload in generate(
            vault.conn,
            vault.embedder,
            creative,
            prompt,
            mode="freeform" if freeform else "options",
            count=count,
            project=project,
        ):
            if kind == "status":
                typer.secho(f"  {payload}...", fg=typer.colors.BRIGHT_BLACK, err=True)
            elif kind == "token" and freeform:
                typer.echo(payload, nl=False)
            elif kind == "done":
                generation_id = payload["generation_id"]
    except (LibrarianError, EmbeddingError) as exc:
        vault.close()
        _fail(str(exc))

    if freeform:
        typer.echo("")

    _show_ideas(vault.conn, generation_id)
    vault.close()


def _show_ideas(conn, generation_id: int) -> None:
    from .creative import get_generation

    generation = get_generation(conn, generation_id)

    if not generation.ideas:
        typer.secho(f"\nGeneration #{generation.id}", fg=typer.colors.BRIGHT_BLACK)
        typer.echo(f"Split it into ideas with: cortex ideas {generation.id} --split")
        return

    typer.echo("")
    for idea in generation.ideas:
        mark = "*" if idea.banked else " "
        typer.secho(f"{mark} [{idea.ordinal}] ", fg=typer.colors.BRIGHT_BLACK, nl=False)
        typer.secho(idea.title, bold=True, nl=False)
        typer.echo(f" - {idea.pitch}" if idea.pitch else "")
        typer.secho(f"      {idea.detail[:160]}", fg=typer.colors.BRIGHT_BLACK)

    typer.secho(f"\nGeneration #{generation.id}", fg=typer.colors.BRIGHT_BLACK)
    typer.secho(
        f"Bank the ones you want: cortex ideas {generation.id} --bank 0,2",
        fg=typer.colors.BRIGHT_BLACK,
    )


@app.command()
def ideas(
    generation_id: int | None = typer.Argument(None, help="Which generation to show."),
    bank_these: str | None = typer.Option(
        None, "--bank", help="Comma-separated ordinals to file, e.g. 0,2"
    ),
    do_split: bool = typer.Option(False, "--split", help="Cut a freeform ramble into ideas."),
    project: str | None = typer.Option(None, "--project", "-p", help="File them here."),
    clean: bool = typer.Option(
        False, "--clean", help="Let the Librarian retitle and categorise before filing."
    ),
) -> None:
    """Show a generation's ideas, split a ramble, or bank what you liked."""
    from .creative import GenerationNotFoundError, bank, list_generations, split
    from .llm import OllamaChat
    from .settings import get_settings

    config = _config()

    if generation_id is None:
        conn = open_readonly(config)
        found = list_generations(conn)
        if not found:
            typer.echo("Nothing brainstormed yet. Try `cortex brainstorm`.")
            conn.close()
            return
        for generation in found:
            taken = sum(1 for i in generation.ideas if i.banked)
            typer.secho(f"#{generation.id:<5}", fg=typer.colors.BRIGHT_BLACK, nl=False)
            typer.echo(generation.prompt[:70])
            typer.secho(
                f"      {generation.mode} | {len(generation.ideas)} ideas | "
                f"{taken} banked | {_local_time(generation.created_at)}",
                fg=typer.colors.BRIGHT_BLACK,
            )
        conn.close()
        return

    try:
        vault = open_vault(config)
    except EmbeddingError as exc:
        _fail(f"{exc}\nIs Ollama running, and is '{config.embed_model}' pulled?")

    settings = get_settings(vault.conn, config)

    try:
        if do_split:
            typer.secho("  Splitting...", fg=typer.colors.BRIGHT_BLACK, err=True)
            split(
                vault.conn,
                vault.librarian,
                OllamaChat(
                    config.ollama_host, settings.utility_model or settings.librarian_model
                ),
                generation_id,
            )

        if bank_these:
            try:
                ordinals = [int(part) for part in bank_these.split(",") if part.strip()]
            except ValueError:
                vault.close()
                _fail("--bank takes comma-separated numbers, e.g. --bank 0,2")

            result = bank(
                vault.conn,
                vault.embedder,
                generation_id,
                ordinals,
                librarian=vault.librarian,
                project=project,
                verbatim=not clean,
                chunk_options=vault.chunk_options,
            )
            for record in result.banked:
                typer.secho(
                    f"Filed #{record.id} {record.title} -> {record.project_name}",
                    fg=typer.colors.GREEN,
                )
            for ordinal, reason in result.skipped:
                typer.secho(f"Skipped [{ordinal}]: {reason}", fg=typer.colors.YELLOW, err=True)

        _show_ideas(vault.conn, generation_id)
    except GenerationNotFoundError as exc:
        vault.close()
        _fail(str(exc))
    except (LibrarianError, EmbeddingError) as exc:
        vault.close()
        _fail(str(exc))

    vault.close()


@app.command()
def setup(
    pull_missing: bool = typer.Option(
        True, "--pull/--no-pull", help="Offer to download any missing models."
    ),
) -> None:
    """Check everything Cortex needs, and set up what is missing."""
    from .setup_wizard import inspect, prepare_vault, pull, tailscale_address

    config = _config()

    typer.secho("Cortex setup", bold=True)
    typer.echo("")

    plan = inspect(config)
    for check in plan.checks:
        mark = "ok  " if check.ok else "--  "
        colour = typer.colors.GREEN if check.ok else typer.colors.YELLOW
        typer.secho(f"  {mark}", fg=colour, nl=False)
        typer.echo(f"{check.name.ljust(16)} {check.detail}")
        if check.fix and not check.ok:
            typer.secho(f"      {check.fix}", fg=typer.colors.BRIGHT_BLACK)

    if plan.missing_models and pull_missing:
        typer.echo("")
        names = ", ".join(model for _, model in plan.missing_models)
        if typer.confirm(f"Download {names} now?", default=True):
            for role, model in plan.missing_models:
                typer.secho(f"  pulling {model} ({role})", fg=typer.colors.BRIGHT_BLACK)
                last = ""
                try:
                    for status, percent in pull(config, model):
                        line = status if percent < 0 else f"{status} {percent}%"
                        if line != last:
                            typer.echo(f"\r    {line.ljust(48)}", nl=False)
                            last = line
                except Exception as exc:  # noqa: BLE001 - reported below
                    typer.echo("")
                    typer.secho(f"    failed: {exc}", fg=typer.colors.RED)
                    continue
                typer.echo("")
                typer.secho(f"    {model} ready", fg=typer.colors.GREEN)
            plan = inspect(config)

    token = prepare_vault(config)

    typer.echo("")
    if plan.ready:
        typer.secho("Everything is in place.", fg=typer.colors.GREEN, bold=True)
    else:
        typer.secho(
            "Some things still need attention - see above.", fg=typer.colors.YELLOW, bold=True
        )

    typer.echo("")
    typer.secho("Next", bold=True)
    typer.echo("  cortex capture \"a first thought\"     file something")
    typer.echo("  cortex serve                          open the web app")
    typer.echo("")
    typer.secho(f"  Your API token: {token}", fg=typer.colors.BRIGHT_BLACK)

    address = tailscale_address()
    if address:
        typer.echo("")
        typer.secho("Reaching it from your phone", bold=True)
        typer.echo(f"  cortex serve --tailscale       binds to {address}")
        typer.echo(
            f"  Open http://{address}:8765 on the phone and add it to your home screen."
        )
