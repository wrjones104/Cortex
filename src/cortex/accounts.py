"""User accounts, and the sessions that replace a typed-in token.

Two problems are solved in the same place. Getting a 43-character token onto a
phone is miserable, and one token for one vault means anyone holding it sees
everything. An account fixes both: you sign in with a password you can type
from memory, and what you sign in to is your own vault.

Isolation is by file, not by column. Each user's records, chunks, vectors,
full-text index, threads and settings live in their own SQLite file, so there
is no `WHERE user_id = ?` anywhere in the storage layer to forget - and no way
for one missed clause to leak someone's notes. It also sidesteps a real
correctness trap: vec0 applies its `k` before any join, so a shared vector
index filtered by user *after* the search would quietly return fewer results
for whoever was not in the global nearest-k. Nothing would raise; search would
just get worse for some people. See the over-fetch comment in retrieve.py for
the same problem in its smaller, survivable form.

The accounts themselves live in auth.db, beside the vaults but never inside
one. A vault is a thing you can copy, export or hand to somebody; it should
not carry anyone's password hash.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .db import transaction

# --- schema ---------------------------------------------------------------

AUTH_MIGRATION_1 = """
-- AUTOINCREMENT so a user id is never reused. The id is baked into the
-- vault filename, and deleting an account deliberately leaves its vault file
-- on disk - so a recycled id would hand the next person with that name
-- somebody else's notes, silently and with nothing raising.
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    is_owner      INTEGER NOT NULL DEFAULT 0,
    -- Which file under data_dir holds this person's vault. Fixed at creation
    -- and never rewritten, so renaming an account cannot strand its notes.
    vault_file    TEXT NOT NULL UNIQUE,
    created_at    TEXT NOT NULL
);

-- Only the hash of a session token is stored. A stolen auth.db then yields
-- no working sessions, the same reasoning that keeps plaintext passwords out
-- of the table above.
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE INDEX idx_sessions_expiry ON sessions(expires_at);
"""

AUTH_MIGRATION_2 = """
-- There is exactly one owner, and the database is what says so.
--
-- create_user() checks before inserting, but a check and an insert in two
-- statements is a race: two requests claiming a fresh Cortex at the same
-- instant can both see no owner and both become one. The window is small and
-- the situation is rare, which is precisely the kind of bug that survives.
-- A partial unique index closes it wherever it is attempted from.
CREATE UNIQUE INDEX idx_single_owner ON users(is_owner) WHERE is_owner = 1;
"""

AUTH_MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "users and sessions", AUTH_MIGRATION_1),
    (2, "one owner, enforced by the schema", AUTH_MIGRATION_2),
]

AUTH_SCHEMA_VERSION = AUTH_MIGRATIONS[-1][0]

# How long a session lasts without being used. Long on purpose: the whole
# point is that a phone stays signed in, and a session is revocable in a way
# that a token printed in a terminal never was.
SESSION_DAYS = 90

# Sessions slide forward as they are used, but writing on every request would
# turn every read into a write. An hour of granularity costs nothing and keeps
# a daily-used session alive indefinitely.
SESSION_TOUCH_SECONDS = 3600

MIN_PASSWORD_LENGTH = 8

# Lowercase, starts alphanumeric, no separators that mean anything to a path.
# Enforced rather than sanitised: a username is how someone is addressed, and
# quietly turning it into something else is worse than refusing it.
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")

OWNER_VAULT_FILE = "cortex.db"


class AuthError(RuntimeError):
    """Base for anything that stops a sign-in or an account change."""


class UnknownUserError(AuthError):
    pass


class BadCredentialsError(AuthError):
    pass


class UsernameTakenError(AuthError):
    pass


class InvalidUsernameError(AuthError):
    pass


class WeakPasswordError(AuthError):
    pass


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    is_owner: bool
    vault_file: str
    created_at: str

    @property
    def name(self) -> str:
        return self.display_name or self.username


# Stands in for the owner on an install that has not been given accounts yet.
#
# Upgrading should never lock somebody out of their own notes, and it should
# never break a cron job either. Until the first account exists, the API token
# still works and still opens cortex.db - exactly as it did before this module
# existed. Creating the owner adopts that same file, so nothing moves.
LEGACY_OWNER = User(
    id=0,
    username="owner",
    display_name="Owner",
    is_owner=True,
    vault_file=OWNER_VAULT_FILE,
    created_at="",
)


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


# --- password hashing -----------------------------------------------------

# scrypt at n=2^14, r=8, p=1 needs 16 MB and about 50 ms per attempt. That is
# a rounding error on a sign-in and a wall in front of offline guessing, which
# is the only threat that matters once the file is already stolen.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024

# Only used where the Python build has no scrypt, which needs OpenSSL 1.1.
# The stored string names its own algorithm, so both verify without guessing.
_PBKDF2_ROUNDS = 600_000


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def hash_password(password: str) -> str:
    """Hash a password into a self-describing string.

    The algorithm and its parameters travel with the hash, so the cost can be
    raised later without invalidating everybody's password: an old hash still
    says how to verify itself.
    """
    salt = secrets.token_bytes(16)
    try:
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            maxmem=_SCRYPT_MAXMEM,
        )
    except (ValueError, AttributeError):
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS
        )
        return f"pbkdf2${_PBKDF2_ROUNDS}${_b64(salt)}${_b64(digest)}"

    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash, in constant time.

    A malformed or unknown hash is a failed check rather than an exception:
    this runs on the sign-in path, and the difference between "no such user"
    and "corrupt row" is not something to tell whoever is knocking.
    """
    try:
        algorithm, *rest = stored.split("$")
        if algorithm == "scrypt":
            n, r, p, salt_b64, digest_b64 = rest
            candidate = hashlib.scrypt(
                password.encode("utf-8"),
                salt=_unb64(salt_b64),
                n=int(n),
                r=int(r),
                p=int(p),
                maxmem=_SCRYPT_MAXMEM,
            )
        elif algorithm == "pbkdf2":
            rounds, salt_b64, digest_b64 = rest
            candidate = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), _unb64(salt_b64), int(rounds)
            )
        else:
            return False
    except (ValueError, TypeError, AttributeError):
        return False

    return hmac.compare_digest(candidate, _unb64(digest_b64))


# --- the store ------------------------------------------------------------


def auth_db_path(data_dir: Path) -> Path:
    return data_dir / "auth.db"


def connect_auth(data_dir: Path) -> sqlite3.Connection:
    """Open auth.db, migrated and ready.

    Deliberately not cortex.db's connect(): the accounts database has no
    vectors in it, so making it depend on a loadable SQLite extension would
    mean a build that cannot load extensions locks people out of signing in
    as well as out of searching.

    check_same_thread=False for the reason given in db.connect() - FastAPI
    hands a sync dependency and its endpoint to different threadpool workers.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(auth_db_path(data_dir)), isolation_level=None, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    migrate_auth(conn)
    return conn


def migrate_auth(conn: sqlite3.Connection) -> int:
    """Bring auth.db up to AUTH_SCHEMA_VERSION.

    Its own numbered chain, tracked in its own file's user_version. The
    accounts schema and the vault schema move at different speeds and must be
    free to do so.
    """
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])

    if version > AUTH_SCHEMA_VERSION:
        raise AuthError(
            f"The accounts database is at version {version}, but this build of "
            f"Cortex understands up to {AUTH_SCHEMA_VERSION}. Upgrade Cortex."
        )

    applied = 0
    for number, _description, sql in AUTH_MIGRATIONS:
        if number <= version:
            continue
        try:
            conn.executescript(
                f"BEGIN;\n{sql}\nPRAGMA user_version = {int(number)};\nCOMMIT;"
            )
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied += 1

    return applied


def _user_of(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        display_name=row["display_name"],
        is_owner=bool(row["is_owner"]),
        vault_file=row["vault_file"],
        created_at=row["created_at"],
    )


def count_users(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])


def list_users(conn: sqlite3.Connection) -> list[User]:
    rows = conn.execute(
        "SELECT * FROM users ORDER BY is_owner DESC, username COLLATE NOCASE"
    ).fetchall()
    return [_user_of(row) for row in rows]


def get_user(conn: sqlite3.Connection, user_id: int) -> User:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise UnknownUserError(f"No account with id {user_id}.")
    return _user_of(row)


def find_user(conn: sqlite3.Connection, username: str) -> User | None:
    row = conn.execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)
    ).fetchone()
    return _user_of(row) if row else None


def owner(conn: sqlite3.Connection) -> User | None:
    row = conn.execute(
        "SELECT * FROM users WHERE is_owner = 1 ORDER BY id LIMIT 1"
    ).fetchone()
    return _user_of(row) if row else None


def validate_username(username: str) -> str:
    name = username.strip().lower()
    if not USERNAME_RE.match(name):
        raise InvalidUsernameError(
            "A username is 2 to 32 characters, lowercase, starting with a letter "
            "or digit, and may contain . _ -"
        )
    return name


def validate_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(
            f"Use at least {MIN_PASSWORD_LENGTH} characters. This is the only "
            "thing between your vault and anyone who can reach this server."
        )
    return password


def create_user(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    *,
    display_name: str = "",
    is_owner: bool | None = None,
) -> User:
    """Add an account and decide which file its vault lives in.

    The first account created is the owner, and it adopts cortex.db - the
    vault that was already there before accounts existed. Everyone after that
    gets a fresh file under vaults/, named from their username and their id so
    that deleting an account and recreating the name cannot land on the old
    file by accident.
    """
    name = validate_username(username)
    validate_password(password)

    with transaction(conn):
        if find_user(conn, name) is not None:
            raise UsernameTakenError(f"'{name}' is taken.")

        first = count_users(conn) == 0
        make_owner = first if is_owner is None else is_owner

        try:
            cursor = conn.execute(
                "INSERT INTO users (username, display_name, password_hash, is_owner, "
                "vault_file, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    name,
                    display_name.strip(),
                    hash_password(password),
                    1 if make_owner else 0,
                    # Unique placeholder: the real name needs the id, and the
                    # column is UNIQUE, so two rows cannot share a temporary one.
                    f"pending-{secrets.token_hex(8)}",
                    _stamp(_now()),
                ),
            )
        except sqlite3.IntegrityError as exc:
            # The single-owner index, or the username one under a race that
            # slipped past the check above.
            if make_owner:
                raise AuthError("This Cortex already has an owner.") from exc
            raise UsernameTakenError(f"'{name}' is taken.") from exc

        user_id = int(cursor.lastrowid)

        vault_file = (
            OWNER_VAULT_FILE
            if make_owner and first
            else f"vaults/{_slug(name)}-{user_id}.db"
        )
        conn.execute(
            "UPDATE users SET vault_file = ? WHERE id = ?", (vault_file, user_id)
        )

    return get_user(conn, user_id)


def _slug(username: str) -> str:
    """A filename fragment from a username.

    Belt and braces: validate_username has already refused anything with a
    path separator in it, and this refuses it again. A user-supplied string
    that reaches a filesystem path deserves two checks, not one.
    """
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", username.lower()).strip("-.")
    return cleaned or "user"


def set_password(conn: sqlite3.Connection, user_id: int, password: str) -> None:
    """Change a password and drop every session but the one changing it.

    Changing a password is what somebody does when they think it is known.
    Leaving the old sessions alive would make the act pointless; the caller
    re-issues for the device that did it.
    """
    validate_password(password)
    with transaction(conn):
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(password), user_id),
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def rename_user(conn: sqlite3.Connection, user_id: int, *, display_name: str) -> User:
    with transaction(conn):
        conn.execute(
            "UPDATE users SET display_name = ? WHERE id = ?",
            (display_name.strip(), user_id),
        )
    return get_user(conn, user_id)


def delete_user(conn: sqlite3.Connection, user_id: int) -> User:
    """Remove an account. The vault file is left alone.

    Deleting a row is recoverable from a backup; deleting somebody's whole
    knowledge vault on the same click is not. Callers that really mean it
    remove the file themselves, having been told the path.
    """
    user = get_user(conn, user_id)
    if user.is_owner:
        raise AuthError(
            "The owner account cannot be deleted. Hand ownership over first."
        )
    with transaction(conn):
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return user


# --- sessions -------------------------------------------------------------


def _hash_token(token: str) -> str:
    """SHA-256, not scrypt.

    A session token is 256 bits from secrets.token_urlsafe, so there is no
    guessing attack to slow down - only a stolen-database attack to blunt, and
    a plain digest does that. Passwords are the opposite case, which is why
    they get scrypt.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Session:
    token: str
    user: User
    expires_at: str


def create_session(
    conn: sqlite3.Connection, user: User, *, label: str = ""
) -> Session:
    token = secrets.token_urlsafe(32)
    now = _now()
    expires = now + timedelta(days=SESSION_DAYS)

    with transaction(conn):
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, label, created_at, "
            "last_seen, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                _hash_token(token),
                user.id,
                label.strip()[:120],
                _stamp(now),
                _stamp(now),
                _stamp(expires),
            ),
        )
        # Opportunistic sweep. Expired rows are dead weight and there is no
        # daemon here to collect them.
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_stamp(now),))

    return Session(token=token, user=user, expires_at=_stamp(expires))


def resolve_session(conn: sqlite3.Connection, token: str) -> User | None:
    """The user behind a session token, or None.

    Sliding expiry: a session in daily use never expires, and one abandoned on
    a lost phone does. The write is throttled to once an hour per session so
    that reading does not turn into writing on every request.
    """
    row = conn.execute(
        "SELECT s.token_hash, s.expires_at, s.last_seen, u.* FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
        (_hash_token(token),),
    ).fetchone()
    if row is None:
        return None

    now = _now()
    if _parse(row["expires_at"]) <= now:
        with transaction(conn):
            conn.execute(
                "DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],)
            )
        return None

    if (now - _parse(row["last_seen"])).total_seconds() > SESSION_TOUCH_SECONDS:
        with transaction(conn):
            conn.execute(
                "UPDATE sessions SET last_seen = ?, expires_at = ? WHERE token_hash = ?",
                (
                    _stamp(now),
                    _stamp(now + timedelta(days=SESSION_DAYS)),
                    row["token_hash"],
                ),
            )

    return _user_of(row)


def _parse(stamp: str) -> datetime:
    moment = datetime.fromisoformat(stamp)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def end_session(conn: sqlite3.Connection, token: str) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))


def end_all_sessions(conn: sqlite3.Connection, user_id: int) -> int:
    with transaction(conn):
        cursor = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return cursor.rowcount


def session_count(conn: sqlite3.Connection, user_id: int) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
    )


# --- sign-in --------------------------------------------------------------

# Consecutive failures per username, and the time the next attempt is allowed.
# In-process on purpose: a personal server does not get a Redis, and the
# thing being defended against is somebody on the tailnet trying a password
# list - which this makes uselessly slow. A restart clears it, which is a fair
# trade for having no moving parts.
_FAILURES: dict[str, tuple[int, float]] = {}
_FREE_ATTEMPTS = 5
_MAX_LOCKOUT_SECONDS = 300.0


def _lockout_remaining(name: str) -> float:
    _count, until = _FAILURES.get(name, (0, 0.0))
    return max(0.0, until - time.monotonic())


def _record_failure(name: str) -> None:
    count, _until = _FAILURES.get(name, (0, 0.0))
    count += 1
    delay = 0.0
    if count > _FREE_ATTEMPTS:
        delay = min(2.0 ** (count - _FREE_ATTEMPTS), _MAX_LOCKOUT_SECONDS)
    _FAILURES[name] = (count, time.monotonic() + delay)


def _clear_failures(name: str) -> None:
    _FAILURES.pop(name, None)


def reset_throttle() -> None:
    """Forget every recorded failure. For tests and for `cortex user unlock`."""
    _FAILURES.clear()


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> User:
    """Check a username and password, with backoff on repeated failure.

    An unknown username and a wrong password fail identically, and a wrong
    password is still hashed before the answer is given, so neither the
    message nor the timing says which of the two it was.
    """
    name = username.strip().lower()

    remaining = _lockout_remaining(name)
    if remaining > 0:
        raise BadCredentialsError(
            f"Too many failed attempts. Try again in {int(remaining) + 1} seconds."
        )

    user = find_user(conn, name)
    stored = (
        conn.execute(
            "SELECT password_hash FROM users WHERE id = ?", (user.id,)
        ).fetchone()["password_hash"]
        if user
        else None
    )

    # Hash against a throwaway even when there is no such user, so that a
    # missing account does not answer measurably faster than a wrong password.
    if stored is None:
        verify_password(password, hash_password(secrets.token_urlsafe(16)))
        _record_failure(name)
        raise BadCredentialsError("That username and password do not match.")

    if not verify_password(password, stored):
        _record_failure(name)
        raise BadCredentialsError("That username and password do not match.")

    _clear_failures(name)
    assert user is not None
    return user


# --- vault files ----------------------------------------------------------


def vault_path(data_dir: Path, user: User) -> Path:
    """Where a user's vault lives, refusing anything that escapes data_dir.

    vault_file comes out of the database, which is not the same as coming from
    a person - but it is one UPDATE away from being either, and a path that
    resolves outside the data directory should never be opened whatever wrote
    it there.
    """
    root = data_dir.resolve()
    candidate = (root / user.vault_file).resolve()
    if not candidate.is_relative_to(root):
        raise AuthError(
            f"The vault path for '{user.username}' points outside the data "
            f"directory. Refusing to open it."
        )
    return candidate


def remove_vault(data_dir: Path, user: User) -> list[Path]:
    """Delete a user's vault and its WAL sidecars. Returns what was removed."""
    base = vault_path(data_dir, user)
    removed = []
    for path in (base, Path(f"{base}-wal"), Path(f"{base}-shm")):
        if path.exists():
            os.remove(path)
            removed.append(path)
    return removed
