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
) -> None:
    """Run the HTTP API."""
    try:
        import uvicorn
    except ImportError as exc:
        _fail('The API extra is not installed. Run: pip install -e ".[api]"')
        raise typer.Exit(1) from exc

    from .api import create_app
    from .config import load_or_create_token

    config = _config()
    api_token = load_or_create_token(config.data_dir)

    typer.secho(f"Cortex API on http://{host}:{port}", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  docs   http://{host}:{port}/docs")
    typer.echo(f"  vault  {config.db_path}")
    typer.echo(f"  token  {api_token}")

    if host not in ("127.0.0.1", "localhost", "::1"):
        typer.secho(
            f"\n  Binding to {host} exposes this API beyond your machine.\n"
            "  Put it on a Tailscale address rather than a public one, and never\n"
            "  expose Ollama itself.",
            fg=typer.colors.YELLOW,
        )

    uvicorn.run(create_app(config, api_token), host=host, port=port, reload=reload)
