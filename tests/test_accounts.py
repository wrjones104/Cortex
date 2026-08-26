"""Accounts, sessions, and the isolation between one person's vault and another.

The isolation tests are the important ones. Separation is by file rather than
by a user_id column, so the thing to prove is not that a filter is correct but
that two accounts opening the same API never see each other's rows at all.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cortex import accounts
from cortex.accounts import (
    InvalidUsernameError,
    UsernameTakenError,
    WeakPasswordError,
    authenticate,
    connect_auth,
    create_session,
    create_user,
    hash_password,
    resolve_session,
    verify_password,
)
from cortex.api import deps
from cortex.api.app import create_app
from cortex.config import Config

TOKEN = "test-token-not-a-real-secret"
PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def no_throttle():
    """The sign-in backoff is process state; do not let it leak between tests."""
    accounts.reset_throttle()
    yield
    accounts.reset_throttle()


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path


@pytest.fixture
def auth(data_dir):
    conn = connect_auth(data_dir)
    yield conn
    conn.close()


@pytest.fixture
def client(data_dir, embedder, librarian, monkeypatch):
    """An API with no accounts yet, and no Authorization header set."""
    config = Config(data_dir=data_dir, embed_model="fake-embed")
    deps.configure(config, TOKEN)
    monkeypatch.setattr(deps, "_embedder", embedder)
    monkeypatch.setattr(deps, "_librarian_override", librarian)

    with TestClient(create_app()) as test_client:
        yield test_client


def sign_up(client, username="owner", password=PASSWORD, **extra):
    response = client.post(
        "/api/auth/setup",
        json={"username": username, "password": password, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def sign_in(client, username, password=PASSWORD):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def as_user(token):
    return {"Authorization": f"Bearer {token}"}


# --- password hashing -----------------------------------------------------


def test_a_password_verifies_against_its_own_hash():
    stored = hash_password(PASSWORD)
    assert verify_password(PASSWORD, stored)
    assert not verify_password(PASSWORD + "!", stored)


def test_the_same_password_hashes_differently_every_time():
    """Distinct salts, so equal hashes cannot reveal equal passwords."""
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_the_plaintext_never_appears_in_the_hash():
    assert PASSWORD not in hash_password(PASSWORD)


@pytest.mark.parametrize(
    "stored", ["", "nonsense", "scrypt$bad", "md5$1$2$3", "$$$$"]
)
def test_a_malformed_hash_fails_rather_than_raising(stored):
    """This runs on the sign-in path; a corrupt row is a failed check."""
    assert verify_password(PASSWORD, stored) is False


# --- account rules --------------------------------------------------------


def test_the_first_account_is_the_owner_and_adopts_the_existing_vault(auth):
    user = create_user(auth, "wren", PASSWORD)
    assert user.is_owner
    assert user.vault_file == "cortex.db"


def test_later_accounts_get_their_own_vault_file(auth):
    create_user(auth, "wren", PASSWORD)
    second = create_user(auth, "alex", PASSWORD)
    assert not second.is_owner
    assert second.vault_file != "cortex.db"
    assert second.vault_file.startswith("vaults/")


def test_two_accounts_never_share_a_vault_file(auth):
    files = {create_user(auth, f"person{n}", PASSWORD).vault_file for n in range(5)}
    assert len(files) == 5


def test_a_recreated_username_does_not_inherit_the_old_vault(auth):
    """Ids are in the filename, so deleting and remaking a name is a fresh start."""
    create_user(auth, "wren", PASSWORD)
    first = create_user(auth, "alex", PASSWORD)
    accounts.delete_user(auth, first.id)
    again = create_user(auth, "alex", PASSWORD)
    assert again.vault_file != first.vault_file


@pytest.mark.parametrize(
    "username",
    ["a", "", "Has Capitals", "with space", "../escape", "sla/sh", "x" * 33, ".dot"],
)
def test_bad_usernames_are_refused(auth, username):
    with pytest.raises(InvalidUsernameError):
        create_user(auth, username, PASSWORD)


def test_a_short_password_is_refused(auth):
    with pytest.raises(WeakPasswordError):
        create_user(auth, "wren", "short")


def test_a_username_is_taken_case_insensitively(auth):
    create_user(auth, "wren", PASSWORD)
    with pytest.raises(UsernameTakenError):
        create_user(auth, "WREN", PASSWORD)


def test_the_owner_cannot_be_deleted(auth):
    user = create_user(auth, "wren", PASSWORD)
    with pytest.raises(accounts.AuthError):
        accounts.delete_user(auth, user.id)


# --- sign-in --------------------------------------------------------------


def test_the_right_password_authenticates(auth):
    create_user(auth, "wren", PASSWORD)
    assert authenticate(auth, "wren", PASSWORD).username == "wren"


def test_a_username_is_case_insensitive_at_sign_in(auth):
    create_user(auth, "wren", PASSWORD)
    assert authenticate(auth, "WREN", PASSWORD).username == "wren"


def test_a_wrong_password_and_a_missing_account_fail_identically(auth):
    create_user(auth, "wren", PASSWORD)

    with pytest.raises(accounts.BadCredentialsError) as wrong:
        authenticate(auth, "wren", "not the password")
    accounts.reset_throttle()
    with pytest.raises(accounts.BadCredentialsError) as missing:
        authenticate(auth, "nobody", PASSWORD)

    assert str(wrong.value) == str(missing.value)


def test_repeated_failures_start_refusing_to_try(auth):
    create_user(auth, "wren", PASSWORD)
    for _ in range(accounts._FREE_ATTEMPTS + 1):
        with pytest.raises(accounts.BadCredentialsError):
            authenticate(auth, "wren", "wrong")

    # Now even the correct password is turned away, rather than being checked.
    with pytest.raises(accounts.BadCredentialsError) as locked:
        authenticate(auth, "wren", PASSWORD)
    assert "Too many failed attempts" in str(locked.value)


def test_a_success_clears_the_backoff(auth):
    create_user(auth, "wren", PASSWORD)
    with pytest.raises(accounts.BadCredentialsError):
        authenticate(auth, "wren", "wrong")
    assert authenticate(auth, "wren", PASSWORD).username == "wren"
    assert accounts._lockout_remaining("wren") == 0


# --- sessions -------------------------------------------------------------


def test_a_session_resolves_back_to_its_user(auth):
    user = create_user(auth, "wren", PASSWORD)
    session = create_session(auth, user)
    assert resolve_session(auth, session.token).id == user.id


def test_an_unknown_session_token_resolves_to_nobody(auth):
    create_user(auth, "wren", PASSWORD)
    assert resolve_session(auth, "not-a-real-token") is None


def test_the_session_token_itself_is_not_stored(auth):
    """A stolen auth.db should yield no working sessions."""
    user = create_user(auth, "wren", PASSWORD)
    session = create_session(auth, user)
    rows = auth.execute("SELECT token_hash FROM sessions").fetchall()
    assert session.token not in {row["token_hash"] for row in rows}


def test_an_expired_session_stops_working(auth):
    user = create_user(auth, "wren", PASSWORD)
    session = create_session(auth, user)
    auth.execute(
        "UPDATE sessions SET expires_at = '2001-01-01T00:00:00+00:00'"
    )
    assert resolve_session(auth, session.token) is None


def test_changing_a_password_ends_every_session(auth):
    user = create_user(auth, "wren", PASSWORD)
    one = create_session(auth, user)
    two = create_session(auth, user)
    accounts.set_password(auth, user.id, "a whole new password")
    assert resolve_session(auth, one.token) is None
    assert resolve_session(auth, two.token) is None


def test_deleting_an_account_ends_its_sessions(auth):
    create_user(auth, "wren", PASSWORD)
    alex = create_user(auth, "alex", PASSWORD)
    session = create_session(auth, alex)
    accounts.delete_user(auth, alex.id)
    assert resolve_session(auth, session.token) is None


# --- vault paths ----------------------------------------------------------


def test_a_vault_path_that_escapes_the_data_directory_is_refused(auth, data_dir):
    user = create_user(auth, "wren", PASSWORD)
    escaped = accounts.User(
        id=user.id,
        username=user.username,
        display_name="",
        is_owner=False,
        vault_file="../../elsewhere.db",
        created_at="",
    )
    with pytest.raises(accounts.AuthError):
        accounts.vault_path(data_dir, escaped)


# --- the first run --------------------------------------------------------


def test_a_fresh_install_reports_that_it_needs_an_account(client):
    state = client.get("/api/auth/state").json()
    assert state["configured"] is False
    assert state["requires_token"] is False


def test_the_first_account_can_be_made_without_credentials(client):
    """Nothing to protect yet, and needing a token to replace the token is a circle."""
    body = sign_up(client)
    assert body["user"]["is_owner"] is True
    assert body["token"]


def test_a_second_owner_cannot_be_created(client):
    sign_up(client)
    response = client.post(
        "/api/auth/setup", json={"username": "usurper", "password": PASSWORD}
    )
    assert response.status_code == 409


def test_once_configured_the_state_says_so(client):
    sign_up(client)
    assert client.get("/api/auth/state").json()["configured"] is True


def test_claiming_a_vault_that_already_holds_notes_needs_the_machine_token(client):
    """The upgrade path. Anyone reaching the port must not be able to claim it."""
    client.post(
        "/api/records",
        json={"text": "A note written before accounts existed."},
        headers=as_user(TOKEN),
    )

    state = client.get("/api/auth/state").json()
    assert state["adopting_existing_vault"] is True
    assert state["requires_token"] is True

    refused = client.post(
        "/api/auth/setup", json={"username": "stranger", "password": PASSWORD}
    )
    assert refused.status_code == 401

    allowed = client.post(
        "/api/auth/setup",
        json={"username": "wren", "password": PASSWORD},
        headers=as_user(TOKEN),
    )
    assert allowed.status_code == 201


def test_the_owner_inherits_the_notes_that_were_there_before(client):
    client.post(
        "/api/records",
        json={"text": "Wexler tends the copper lantern."},
        headers=as_user(TOKEN),
    )
    body = client.post(
        "/api/auth/setup",
        json={"username": "wren", "password": PASSWORD},
        headers=as_user(TOKEN),
    ).json()

    listing = client.get("/api/records", headers=as_user(body["token"])).json()
    assert listing["total"] == 1
    assert "copper lantern" in listing["records"][0]["body"]


# --- signing in over HTTP -------------------------------------------------


def test_signing_in_returns_a_working_token(client):
    sign_up(client, "wren")
    token = sign_in(client, "wren")
    me = client.get("/api/auth/me", headers=as_user(token))
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "wren"


def test_a_wrong_password_is_rejected(client):
    sign_up(client, "wren")
    response = client.post(
        "/api/auth/login", json={"username": "wren", "password": "wrong"}
    )
    assert response.status_code == 401


def test_no_password_is_ever_echoed_back(client):
    body = sign_up(client, "wren")
    assert PASSWORD not in str(body)


@pytest.mark.parametrize(
    "path", ["/api/status", "/api/records", "/api/projects", "/api/search?q=x"]
)
def test_routes_still_refuse_an_unknown_token(client, path):
    sign_up(client, "wren")
    response = client.get(path, headers=as_user("not-a-real-token"))
    assert response.status_code == 401


def test_logging_out_kills_that_token_only(client):
    sign_up(client, "wren")
    phone = sign_in(client, "wren")
    laptop = sign_in(client, "wren")

    assert client.post("/api/auth/logout", headers=as_user(phone)).status_code == 204
    assert client.get("/api/auth/me", headers=as_user(phone)).status_code == 401
    assert client.get("/api/auth/me", headers=as_user(laptop)).status_code == 200


def test_revoking_sessions_signs_every_device_out(client):
    sign_up(client, "wren")
    phone = sign_in(client, "wren")
    laptop = sign_in(client, "wren")

    assert (
        client.post("/api/auth/sessions/revoke", headers=as_user(phone)).status_code
        == 204
    )
    assert client.get("/api/auth/me", headers=as_user(phone)).status_code == 401
    assert client.get("/api/auth/me", headers=as_user(laptop)).status_code == 401


def test_changing_a_password_hands_back_a_fresh_token(client):
    sign_up(client, "wren")
    old = sign_in(client, "wren")

    response = client.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": "a longer new one"},
        headers=as_user(old),
    )
    assert response.status_code == 200
    new = response.json()["token"]

    assert client.get("/api/auth/me", headers=as_user(old)).status_code == 401
    assert client.get("/api/auth/me", headers=as_user(new)).status_code == 200
    assert sign_in(client, "wren", "a longer new one")


def test_changing_a_password_needs_the_current_one(client):
    sign_up(client, "wren")
    token = sign_in(client, "wren")
    response = client.post(
        "/api/auth/password",
        json={"current_password": "not it", "new_password": "a longer new one"},
        headers=as_user(token),
    )
    assert response.status_code == 403


# --- the machine token ----------------------------------------------------


def test_the_machine_token_works_before_any_account_exists(client):
    """The upgrade must not break a cron job the moment Cortex is updated."""
    assert client.get("/api/status", headers=as_user(TOKEN)).status_code == 200


def test_the_machine_token_acts_as_the_owner(client):
    sign_up(client, "wren")
    me = client.get("/api/auth/me", headers=as_user(TOKEN)).json()
    assert me["user"]["username"] == "wren"


def test_the_machine_token_reaches_the_owners_vault_and_not_anothers(client):
    sign_up(client, "wren")
    owner_token = sign_in(client, "wren")
    client.post("/api/users", json={"username": "alex", "password": PASSWORD},
                headers=as_user(owner_token))
    alex = sign_in(client, "alex")

    client.post("/api/records", json={"text": "Alex writes about harbours."},
                headers=as_user(alex))
    client.post("/api/records", json={"text": "Wren writes about lighthouses."},
                headers=as_user(owner_token))

    by_machine = client.get("/api/records", headers=as_user(TOKEN)).json()
    assert by_machine["total"] == 1
    assert "lighthouses" in by_machine["records"][0]["body"]


# --- account management ---------------------------------------------------


def test_the_owner_can_add_an_account(client):
    sign_up(client, "wren")
    token = sign_in(client, "wren")
    response = client.post(
        "/api/users",
        json={"username": "alex", "password": PASSWORD},
        headers=as_user(token),
    )
    assert response.status_code == 201
    assert response.json()["is_owner"] is False


def test_an_ordinary_account_cannot_add_another(client):
    sign_up(client, "wren")
    owner_token = sign_in(client, "wren")
    client.post("/api/users", json={"username": "alex", "password": PASSWORD},
                headers=as_user(owner_token))
    alex = sign_in(client, "alex")

    response = client.post(
        "/api/users",
        json={"username": "intruder", "password": PASSWORD},
        headers=as_user(alex),
    )
    assert response.status_code == 403


def test_an_ordinary_account_cannot_list_or_remove_accounts(client):
    sign_up(client, "wren")
    owner_token = sign_in(client, "wren")
    created = client.post(
        "/api/users",
        json={"username": "alex", "password": PASSWORD},
        headers=as_user(owner_token),
    ).json()
    alex = sign_in(client, "alex")

    assert client.get("/api/users", headers=as_user(alex)).status_code == 403
    assert (
        client.delete(f"/api/users/{created['id']}", headers=as_user(alex)).status_code
        == 403
    )


def test_removing_an_account_keeps_its_vault_by_default(client, data_dir):
    sign_up(client, "wren")
    owner_token = sign_in(client, "wren")
    created = client.post(
        "/api/users",
        json={"username": "alex", "password": PASSWORD},
        headers=as_user(owner_token),
    ).json()
    alex = sign_in(client, "alex")
    client.post("/api/records", json={"text": "Something Alex wrote."},
                headers=as_user(alex))

    vaults = list((data_dir / "vaults").glob("*.db"))
    assert vaults

    assert (
        client.delete(f"/api/users/{created['id']}", headers=as_user(owner_token))
        .status_code
        == 204
    )
    assert all(path.exists() for path in vaults)
    assert client.get("/api/auth/me", headers=as_user(alex)).status_code == 401


def test_purging_an_account_deletes_its_vault(client, data_dir):
    sign_up(client, "wren")
    owner_token = sign_in(client, "wren")
    created = client.post(
        "/api/users",
        json={"username": "alex", "password": PASSWORD},
        headers=as_user(owner_token),
    ).json()
    alex = sign_in(client, "alex")
    client.post("/api/records", json={"text": "Something Alex wrote."},
                headers=as_user(alex))

    vaults = list((data_dir / "vaults").glob("*.db"))
    assert vaults

    client.delete(
        f"/api/users/{created['id']}?purge=true", headers=as_user(owner_token)
    )
    assert not any(path.exists() for path in vaults)


# --- isolation ------------------------------------------------------------


@pytest.fixture
def two_people(client):
    """An owner and an ordinary account, each with a note the other must not see."""
    sign_up(client, "wren")
    wren = sign_in(client, "wren")
    client.post("/api/users", json={"username": "alex", "password": PASSWORD},
                headers=as_user(wren))
    alex = sign_in(client, "alex")

    client.post(
        "/api/records",
        json={"text": "Wexler tends the copper lantern.", "project": "Echoes"},
        headers=as_user(wren),
    )
    client.post(
        "/api/records",
        json={"text": "The deployment pipeline times out.", "project": "Work"},
        headers=as_user(alex),
    )
    return wren, alex


def test_records_are_not_shared(client, two_people):
    wren, alex = two_people

    hers = client.get("/api/records", headers=as_user(wren)).json()
    theirs = client.get("/api/records", headers=as_user(alex)).json()

    assert hers["total"] == 1
    assert theirs["total"] == 1
    assert "copper lantern" in hers["records"][0]["body"]
    assert "deployment pipeline" in theirs["records"][0]["body"]


def test_a_record_id_from_one_account_is_not_readable_by_another(client, two_people):
    """Ids restart per vault, so the same number means a different note - or none."""
    wren, alex = two_people
    hers = client.get("/api/records", headers=as_user(wren)).json()["records"][0]

    fetched = client.get(f"/api/records/{hers['id']}", headers=as_user(alex))
    if fetched.status_code == 200:
        assert fetched.json()["body"] != hers["body"]


def test_search_never_crosses_accounts(client, two_people):
    wren, alex = two_people

    found = client.get(
        "/api/search", params={"q": "copper lantern"}, headers=as_user(alex)
    ).json()
    assert all("copper lantern" not in hit["record"]["body"] for hit in found["hits"])


def test_projects_are_not_shared(client, two_people):
    wren, alex = two_people

    assert [p["name"] for p in client.get("/api/projects", headers=as_user(wren)).json()] == [
        "Echoes"
    ]
    assert [p["name"] for p in client.get("/api/projects", headers=as_user(alex)).json()] == [
        "Work"
    ]


def test_conversations_are_not_shared(client, two_people):
    wren, alex = two_people
    client.post("/api/threads", json={"title": "Wren's thinking"}, headers=as_user(wren))

    assert len(client.get("/api/threads", headers=as_user(wren)).json()) == 1
    assert client.get("/api/threads", headers=as_user(alex)).json() == []


def test_each_account_has_its_own_vault_file(client, two_people):
    wren, alex = two_people
    hers = client.get("/api/status", headers=as_user(wren)).json()["vault_path"]
    theirs = client.get("/api/status", headers=as_user(alex)).json()["vault_path"]
    assert hers != theirs


def test_model_settings_are_per_account(client, two_people, monkeypatch):
    """The other half of the ask: your models are yours."""
    wren, alex = two_people

    # PATCH /api/settings refuses a model Ollama does not have, so on a machine
    # that happens to be running Ollama these names would be rejected on their
    # merits rather than tested. Stand in for the model list.
    monkeypatch.setattr(
        "cortex.api.app.installed_models",
        lambda host, timeout=10.0: [
            {"name": f"{name}:latest", "can_chat": True}
            for name in ("wren-model", "alex-model")
        ],
    )

    client.patch(
        "/api/settings", json={"librarian_model": "wren-model"}, headers=as_user(wren)
    )
    client.patch(
        "/api/settings", json={"librarian_model": "alex-model"}, headers=as_user(alex)
    )

    assert (
        client.get("/api/settings", headers=as_user(wren)).json()["librarian_model"]
        == "wren-model"
    )
    assert (
        client.get("/api/settings", headers=as_user(alex)).json()["librarian_model"]
        == "alex-model"
    )


def test_streaming_capture_lands_in_the_callers_own_vault(client, two_people):
    """The streaming routes resolve the vault on the request thread and hand it
    to a worker. Getting that wrong would file one person's note in another's."""
    _wren, alex = two_people

    response = client.post(
        "/api/records/stream",
        json={"text": "A streamed note belonging to Alex."},
        headers=as_user(alex),
    )
    assert response.status_code == 200
    assert "record" in response.text

    theirs = client.get("/api/records", headers=as_user(alex)).json()
    assert theirs["total"] == 2
    assert client.get("/api/records", headers=as_user(_wren)).json()["total"] == 1


def test_offline_sync_lands_in_the_callers_own_vault(client, two_people):
    _wren, alex = two_people

    response = client.post(
        "/api/sync",
        json={"captures": [{"text": "Queued on the phone while offline."}]},
        headers=as_user(alex),
    )
    assert response.status_code == 200
    assert response.json()["stored"] == 1
    assert client.get("/api/records", headers=as_user(_wren)).json()["total"] == 1


def test_a_second_owner_is_impossible_even_asked_for_directly(auth):
    """Enforced by the schema, not only by the check before the insert."""
    create_user(auth, "wren", PASSWORD)
    with pytest.raises(accounts.AuthError):
        create_user(auth, "usurper", PASSWORD, is_owner=True)


def test_the_auth_schema_migrates_from_nothing_to_current(data_dir):
    conn = connect_auth(data_dir)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == accounts.AUTH_SCHEMA_VERSION
        # Running it again applies nothing and breaks nothing.
        assert accounts.migrate_auth(conn) == 0
    finally:
        conn.close()
