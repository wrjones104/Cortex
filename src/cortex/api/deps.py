"""Auth and per-request resources.

Every request resolves to a person before it resolves to a vault. The bearer
token is either a session issued by signing in, or the machine token from
`cortex token` - and whichever it is, it names a user, and that user names the
SQLite file the rest of the request will talk to. Nothing downstream filters
by user, because nothing downstream can see another user's data to filter out.

Connections are opened per request rather than shared. Opening one costs
microseconds, and WAL mode lets readers run alongside a writer.

Per-request is necessary but not sufficient: FastAPI runs a sync dependency
and the sync endpoint it feeds on *different* threadpool workers, so even a
private connection is created on one thread and used on another. connect()
therefore opens with check_same_thread=False - see the reasoning there.

The embedder is the opposite case: it is a module-level singleton so that the
dimension probe — a real call to Ollama — happens once for the process rather
than once per request. It is shared across accounts deliberately. The
embedding model is fixed by the vector index rather than by a setting, so one
server means one embedding space; the models people *can* change are the chat
ones, and those live in each vault's own meta table.
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, status

from ..accounts import (
    LEGACY_OWNER,
    AuthError,
    User,
    connect_auth,
    count_users,
    owner,
    resolve_session,
    vault_path,
)
from ..config import Config, load_or_create_token
from ..db import StoreError, connect
from ..embed import EmbeddingError, OllamaEmbedder
from ..llm import OllamaChat, OllamaLibrarian
from ..migrations import ensure_vector_index, migrate
from ..settings import get_settings
from ..vault import Vault

_config: Config | None = None
_token: str | None = None
_embedder: OllamaEmbedder | None = None
# Tests inject fakes here; in normal use these come from settings.
_librarian_override: object | None = None
_chatter_override: object | None = None


def configure(config: Config, token: str | None = None) -> str:
    """Bind the process to one data directory. Returns the machine API token."""
    global _config, _token, _embedder, _librarian_override, _chatter_override
    _config = config
    _token = token or load_or_create_token(config.data_dir)
    _embedder = OllamaEmbedder(config.ollama_host, config.embed_model)
    _librarian_override = None
    _chatter_override = None
    return _token


def get_config() -> Config:
    """The server's configuration, pointed at no particular vault.

    Routes that touch a vault want config_for(user) instead. This is for the
    things that are properties of the server: the Ollama host, the models it
    has, the data directory the accounts live in.
    """
    if _config is None:
        raise RuntimeError("API not configured - call configure() before serving.")
    return _config


def config_for(user: User) -> Config:
    """The configuration for one person's vault."""
    config = get_config()
    # Raises if the stored path would resolve outside the data directory.
    # Checked here rather than trusted, because this is the one place a value
    # out of the database becomes a file that gets opened.
    vault_path(config.data_dir, user)
    return config.for_vault(user.vault_file)


# --- accounts -------------------------------------------------------------


def get_auth() -> Iterator[sqlite3.Connection]:
    """A migrated accounts database, for the routes that manage accounts."""
    conn = connect_auth(get_config().data_dir)
    try:
        yield conn
    finally:
        conn.close()


_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Sign in to Cortex, or send the machine token from `cortex token` "
    "as 'Authorization: Bearer <token>'.",
    headers={"WWW-Authenticate": "Bearer"},
)


def _presented(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _UNAUTHENTICATED
    presented = authorization.split(" ", 1)[1].strip()
    if not presented:
        raise _UNAUTHENTICATED
    return presented


def presented_token(authorization: str | None = Header(default=None)) -> str:
    """The raw bearer token, for the routes that act on the session itself."""
    return _presented(authorization)


def machine_token_matches(authorization: str | None) -> bool:
    """Whether a header carries the token from `cortex token`.

    Used by the one route that has to work before any account exists, and so
    cannot ask who is calling.
    """
    if _token is None:
        return False
    if not authorization or not authorization.lower().startswith("bearer "):
        return False
    return secrets.compare_digest(authorization.split(" ", 1)[1].strip(), _token)


def current_user(authorization: str | None = Header(default=None)) -> User:
    """Who is asking. Every route except /health and sign-in depends on this.

    Two kinds of bearer token are accepted, and they are tried in that order:

    A **session** token, issued by signing in. This is what the web and phone
    clients use, and it is why nobody has to copy a token onto a phone any
    more - you type a password you already know instead.

    The **machine** token from `cortex token`, which acts as the owner. Scripts,
    cron jobs and curl kept working across this change because of this branch,
    and it is compared with compare_digest so a wrong one cannot be recovered
    by timing the response.

    On an install that has no accounts yet, the machine token opens cortex.db
    exactly as it always did. That is what makes the upgrade non-breaking: the
    vault stays reachable until somebody creates the owner account, and
    creating it adopts the same file.
    """
    if _token is None:
        raise RuntimeError("API not configured - call configure() before serving.")

    presented = _presented(authorization)
    conn = connect_auth(get_config().data_dir)
    try:
        user = resolve_session(conn, presented)
        if user is not None:
            return user

        if secrets.compare_digest(presented, _token):
            existing = owner(conn)
            if existing is not None:
                return existing
            if count_users(conn) == 0:
                return LEGACY_OWNER
    finally:
        conn.close()

    raise _UNAUTHENTICATED


def require_owner(user: User = Depends(current_user)) -> User:
    """Gate the account-management routes.

    Only the owner adds and removes accounts. An open sign-up form on a
    machine reachable over a tailnet is a door, and this app is not the thing
    that should be deciding who walks through it.
    """
    if not user.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner of this Cortex can manage accounts.",
        )
    return user


def require_real_account(user: User = Depends(require_owner)) -> User:
    """The owner, as an actual row rather than the pre-accounts stand-in.

    Managing accounts from the machine token before any account exists is a
    chicken-and-egg problem with a one-line answer: make the first one.
    """
    if user.id == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This Cortex has no accounts yet. Create the owner account "
            "first, in the app or with `cortex user add <name>`.",
        )
    return user


# --- vault resources ------------------------------------------------------


def get_conn(user: User = Depends(current_user)) -> Iterator[sqlite3.Connection]:
    """A migrated connection to the caller's vault. Does not need Ollama."""
    try:
        config = config_for(user)
    except AuthError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    conn = connect(config.db_path)
    try:
        migrate(conn)
        yield conn
    finally:
        conn.close()


def build_vault(config: Config) -> Vault:
    """Open a ready-to-index vault, raising core exceptions rather than HTTP ones.

    Takes a Config rather than a User because the caller may be a worker
    thread that has no request to resolve: SQLite objects belong to the thread
    that created them, so the streaming endpoints resolve the user on the
    request thread and hand the resulting config across.

    Call this from whichever thread will use the connection.
    """
    assert _embedder is not None

    conn = connect(config.db_path)
    try:
        migrate(conn)
        ensure_vector_index(conn, _embedder.model, _embedder.dim)
        # Model routing is a runtime setting, so the Librarian is resolved per
        # request. Ollama clients are shared per host, so this is nearly free.
        settings = get_settings(conn, config)
    except BaseException:
        conn.close()
        raise

    librarian = _librarian_override or OllamaLibrarian(
        config.ollama_host, settings.librarian_model
    )
    return Vault(conn=conn, config=config, embedder=_embedder, librarian=librarian)


def get_vault(user: User = Depends(current_user)) -> Iterator[Vault]:
    """The caller's vault, with the vector index ready. Needs Ollama."""
    try:
        vault = build_vault(config_for(user))
    except EmbeddingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"The embedding model is unavailable. {exc}",
        ) from exc
    except StoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except AuthError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        yield vault
    finally:
        vault.close()


def build_chatter(vault: Vault) -> OllamaChat:
    """The model that answers, resolved from the thread's settings."""
    if _chatter_override is not None:
        return _chatter_override
    settings = get_settings(vault.conn, vault.config)
    return OllamaChat(vault.config.ollama_host, settings.librarian_model)


def build_utility(vault: Vault) -> OllamaChat:
    """The model that condenses queries and writes summaries.

    Kept separate from the answering model because these are small, frequent
    calls where speed matters more than quality - a 9B does them fine while
    a 27B answers. Falls back to the answering model when unset.
    """
    if _chatter_override is not None:
        return _chatter_override
    settings = get_settings(vault.conn, vault.config)
    return OllamaChat(
        vault.config.ollama_host, settings.utility_model or settings.librarian_model
    )


def build_creative(vault: Vault) -> OllamaChat:
    """The model that brainstorms. Deliberately separate from the Librarian:
    filing wants precision, brainstorming wants range."""
    if _chatter_override is not None:
        return _chatter_override
    settings = get_settings(vault.conn, vault.config)
    return OllamaChat(vault.config.ollama_host, settings.creative_model)


Authed = Depends(current_user)
CurrentUser = Depends(current_user)
Owner = Depends(require_real_account)
