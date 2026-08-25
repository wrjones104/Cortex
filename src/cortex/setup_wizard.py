"""First run.

Everything Cortex needs is local, which is the point — but it does mean four
things have to line up before it works, and finding that out one error message
at a time is a poor introduction. This checks them, offers to fix what it can,
and says plainly what it cannot.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from .config import Config, load_or_create_token
from .db import connect
from .migrations import migrate
from .settings import installed_models, normalise_model


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str | None = None
    """A command the person can run, when we cannot do it for them."""


@dataclass
class Plan:
    checks: list[Check] = field(default_factory=list)
    missing_models: list[tuple[str, str]] = field(default_factory=list)
    """(role, model) pairs that are configured but not installed."""

    @property
    def ready(self) -> bool:
        return all(c.ok for c in self.checks)


def inspect(config: Config) -> Plan:
    """Work out what is in place and what is not. Changes nothing."""
    plan = Plan()

    plan.checks.append(
        Check(
            name="Vault location",
            ok=True,
            detail=str(config.db_path),
        )
    )

    try:
        conn = connect(config.db_path)
        try:
            migrate(conn)
            records = conn.execute("SELECT COUNT(*) AS n FROM records").fetchone()["n"]
        finally:
            conn.close()
        plan.checks.append(
            Check(
                name="Vault",
                ok=True,
                detail=f"ready, {records} record{'s' if records != 1 else ''}",
            )
        )
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        plan.checks.append(
            Check(
                name="Vault",
                ok=False,
                detail=f"{type(exc).__name__}: {exc}",
                fix="Check that the data directory is writable.",
            )
        )

    try:
        available = installed_models(config.ollama_host)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        plan.checks.append(
            Check(
                name="Ollama",
                ok=False,
                detail=f"not reachable at {config.ollama_host} ({type(exc).__name__})",
                fix="Start Ollama, then run `cortex setup` again.",
            )
        )
        return plan

    plan.checks.append(
        Check(name="Ollama", ok=True, detail=f"{config.ollama_host}, {len(available)} models")
    )

    exposed = ollama_exposure(config)
    plan.checks.append(
        Check(
            name="Ollama reach",
            ok=exposed is None,
            detail=(
                "this machine only"
                if exposed is None
                else f"answers on {exposed} with no authentication"
            ),
            fix=(
                None
                if exposed is None
                else "Set OLLAMA_HOST=127.0.0.1 and restart Ollama. Cortex talks to "
                "it locally and needs no more than that."
            ),
        )
    )

    by_name = {normalise_model(m["name"]): m for m in available}
    wanted = (
        ("embedding", config.embed_model, "can_embed"),
        ("librarian", config.librarian_model, "can_chat"),
        ("creative", config.creative_model, "can_chat"),
    )

    for role, model, capability in wanted:
        found = by_name.get(normalise_model(model))
        if found is None:
            plan.missing_models.append((role, model))
            plan.checks.append(
                Check(
                    name=f"{role.title()} model",
                    ok=False,
                    detail=f"{model} is not installed",
                    fix=f"ollama pull {model}",
                )
            )
        elif not found[capability]:
            # Installed, but wrong kind - the mistake the prototype allowed.
            plan.checks.append(
                Check(
                    name=f"{role.title()} model",
                    ok=False,
                    detail=f"{model} cannot be used for {role} "
                    f"(it does: {', '.join(found['capabilities']) or 'nothing'})",
                    fix=f"Set CORTEX_{role.upper()}_MODEL to a suitable model.",
                )
            )
        else:
            size = found.get("parameter_size")
            plan.checks.append(
                Check(
                    name=f"{role.title()} model",
                    ok=True,
                    detail=f"{model}{f' ({size})' if size else ''}",
                )
            )

    return plan


def pull(config: Config, model: str) -> Iterator[tuple[str, int]]:
    """Download a model, yielding (status, percent) as it goes.

    Percent is -1 while the server is doing something it cannot measure, such
    as verifying a digest.
    """
    from .ollama_client import client_for

    for progress in client_for(config.ollama_host).pull(model, stream=True):
        status = progress.get("status") or "working"
        total = progress.get("total") or 0
        completed = progress.get("completed") or 0
        percent = int(completed * 100 / total) if total else -1
        yield (status, percent)


def prepare_vault(config: Config) -> str:
    """Create the data directory, migrate the vault, and return the API token."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(config.db_path)
    try:
        migrate(conn)
    finally:
        conn.close()
    return load_or_create_token(config.data_dir)


def tailscale_address() -> str | None:
    """This machine's tailnet address, if Tailscale is running.

    Used to offer the right --host rather than making someone go and find it,
    because binding to a tailnet address is the difference between reaching
    Cortex from a phone and exposing it to the internet.
    """
    import shutil
    import subprocess

    binary = shutil.which("tailscale")
    if not binary:
        return None
    try:
        result = subprocess.run(  # noqa: S603 - fixed binary, no shell
            [binary, "ip", "-4"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None

    address = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""
    return address or None


def local_address() -> str | None:
    """A non-loopback address this machine answers on.

    Prefers the tailnet address, since that is the one Cortex is likely to be
    reached on, and falls back to whichever interface the OS would use to
    reach the outside world. No packets are actually sent.
    """
    import socket

    tailnet = tailscale_address()
    if tailnet:
        return tailnet

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 1))  # reserved for documentation; nothing is sent
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def ollama_exposure(config: Config, timeout: float = 3.0) -> str | None:
    """The address Ollama answers on beyond loopback, if any.

    Cortex authenticates every request; Ollama authenticates none. An Ollama
    listening on 0.0.0.0 means anyone who can reach the machine can use the
    models on it and read whatever is sent through them - which quietly undoes
    the point of running all of this locally.

    Returns the address it answered on, or None if it is loopback-only.
    """
    import urllib.parse

    address = local_address()
    if not address:
        return None

    parsed = urllib.parse.urlparse(config.ollama_host)
    port = parsed.port or 11434

    import httpx

    try:
        response = httpx.get(f"http://{address}:{port}/api/tags", timeout=timeout)
    except Exception:  # noqa: BLE001 - unreachable is the good outcome
        return None

    return address if response.status_code == 200 else None
