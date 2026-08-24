"""HTTP API.

Runs against a real temp vault with fake models injected, so these are proper
end-to-end tests of the wire contract without needing a model server.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from cortex.api import deps
from cortex.api.app import create_app
from cortex.config import Config

TOKEN = "test-token-not-a-real-secret"


@pytest.fixture
def client(tmp_path, embedder, librarian, monkeypatch):
    config = Config(data_dir=tmp_path, embed_model="fake-embed")
    deps.configure(config, TOKEN)
    # The API builds its own model clients at configure() time; swap in the
    # deterministic fakes so the suite never touches Ollama.
    monkeypatch.setattr(deps, "_embedder", embedder)
    monkeypatch.setattr(deps, "_librarian", librarian)

    with TestClient(create_app()) as test_client:
        test_client.headers.update({"Authorization": f"Bearer {TOKEN}"})
        yield test_client


@pytest.fixture
def populated(client):
    for text, project in (
        ("Wexler tends the copper lantern on the northern cliffs.", "Echoes"),
        ("The harbour council meets weekly to argue about trade.", "Echoes"),
        ("Deployment pipeline keeps timing out on the integration suite.", "Work Notes"),
    ):
        response = client.post("/api/records", json={"text": text, "project": project})
        assert response.status_code == 201
    return client


# --- auth -----------------------------------------------------------------


def test_health_needs_no_token(client):
    client.headers.pop("Authorization")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize(
    "path", ["/api/status", "/api/records", "/api/projects", "/api/search?q=x"]
)
def test_every_other_route_needs_a_token(client, path):
    client.headers.pop("Authorization")
    response = client.get(path)
    assert response.status_code == 401
    assert "cortex token" in response.json()["detail"]


def test_a_wrong_token_is_rejected(client):
    client.headers.update({"Authorization": "Bearer wrong-token"})
    assert client.get("/api/records").status_code == 401


def test_a_malformed_authorization_header_is_rejected(client):
    for value in ("", "Basic abc", TOKEN, "Bearer"):
        client.headers.update({"Authorization": value})
        assert client.get("/api/records").status_code == 401


# --- capture --------------------------------------------------------------


def test_post_a_record(client):
    response = client.post(
        "/api/records", json={"text": "The bell tower leans north.", "project": "Echoes"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["record"]["project"] == "Echoes"
    assert body["record"]["category"] == "Test Category"
    assert body["chunks"] == 1


def test_verbatim_capture_is_stored_exactly(client):
    text = "# Option 3\n\nThe keeper's daughter inherits the lantern."
    response = client.post(
        "/api/records",
        json={"text": text, "project": "Echoes", "verbatim": True, "title": "Option 3"},
    )
    assert response.status_code == 201
    assert response.json()["record"]["body"] == text


def test_empty_text_is_a_422_from_validation(client):
    assert client.post("/api/records", json={"text": ""}).status_code == 422


def test_whitespace_only_text_is_a_400(client):
    response = client.post("/api/records", json={"text": "   \n  "})
    assert response.status_code == 400
    assert "empty" in response.json()["detail"]


def test_an_identical_note_is_a_409(client):
    payload = {"text": "Exactly the same words.", "project": "P"}
    assert client.post("/api/records", json=payload).status_code == 201

    conflict = client.post("/api/records", json=payload)
    assert conflict.status_code == 409
    assert "already in this project" in conflict.json()["detail"]


def test_a_duplicate_can_be_forced(client):
    payload = {"text": "Exactly the same words.", "project": "P"}
    client.post("/api/records", json=payload)
    forced = client.post("/api/records", json={**payload, "allow_duplicate": True})
    assert forced.status_code == 201


def test_a_replayed_capture_returns_the_original(client):
    payload = {"text": "Sent from the train.", "project": "P", "idempotency_key": "q-1"}
    first = client.post("/api/records", json=payload).json()
    second = client.post("/api/records", json=payload).json()

    assert first["record"]["id"] == second["record"]["id"]
    assert client.get("/api/records").json()["total"] == 1


# --- streaming capture ----------------------------------------------------


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        name, payload = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
        if name:
            events.append((name, payload))
    return events


def test_streaming_capture_reports_progress_then_the_record(client):
    response = client.post(
        "/api/records/stream", json={"text": "A streamed note.", "project": "Echoes"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.text)
    names = [name for name, _ in events]

    assert "progress" in names
    assert names[-1] == "record"
    assert names.count("record") == 1

    stages = [payload["stage"] for name, payload in events if name == "progress"]
    assert stages == ["context", "structuring", "indexing", "done"]

    record = events[-1][1]["record"]
    assert record["project"] == "Echoes"
    assert record["id"] > 0


def test_streaming_capture_ends_with_a_single_error_event_on_conflict(client):
    payload = {"text": "Duplicated note.", "project": "P"}
    client.post("/api/records", json=payload)

    events = _parse_sse(client.post("/api/records/stream", json=payload).text)
    names = [name for name, _ in events]

    # Progress legitimately precedes the conflict: the duplicate is only
    # detected on write, after the model has already structured the note.
    assert names[-1] == "error"
    assert names.count("error") == 1
    assert "record" not in names
    assert events[-1][1]["status"] == 409


def test_streaming_capture_emits_no_record_event_when_it_fails(client):
    events = _parse_sse(client.post("/api/records/stream", json={"text": "  "}).text)
    assert [name for name, _ in events] == ["error"]
    assert events[0][1]["status"] == 400


# --- read -----------------------------------------------------------------


def test_list_records_paginates_and_reports_the_total(populated):
    body = populated.get("/api/records?limit=2").json()
    assert len(body["records"]) == 2
    assert body["total"] == 3
    assert body["limit"] == 2

    page_two = populated.get("/api/records?limit=2&offset=2").json()
    assert len(page_two["records"]) == 1


def test_list_records_filters_by_project(populated):
    body = populated.get("/api/records?project=Echoes").json()
    assert body["total"] == 2
    assert {r["project"] for r in body["records"]} == {"Echoes"}


def test_pagination_bounds_are_enforced(client):
    assert client.get("/api/records?limit=0").status_code == 422
    assert client.get("/api/records?limit=9999").status_code == 422
    assert client.get("/api/records?offset=-1").status_code == 422


def test_get_one_record(populated):
    record_id = populated.get("/api/records").json()["records"][0]["id"]
    response = populated.get(f"/api/records/{record_id}")
    assert response.status_code == 200
    assert response.json()["id"] == record_id


def test_a_missing_record_is_a_404(client):
    assert client.get("/api/records/9999").status_code == 404


def test_projects_carry_their_counts(populated):
    projects = {p["name"]: p["record_count"] for p in populated.get("/api/projects").json()}
    assert projects == {"Echoes": 2, "Work Notes": 1}


# --- search ---------------------------------------------------------------


def test_search_finds_by_keyword(populated):
    body = populated.get("/api/search?q=Wexler").json()
    assert body["query"] == "Wexler"
    assert body["hits"]
    assert "Wexler" in body["hits"][0]["record"]["body"]
    assert body["hits"][0]["matched_by"] in ("keyword", "both")


def test_search_can_be_scoped_to_a_project(populated):
    body = populated.get("/api/search?q=deployment pipeline&project=Echoes").json()
    assert all(h["record"]["project"] == "Echoes" for h in body["hits"])


def test_search_for_something_absent_returns_no_hits(populated):
    body = populated.get("/api/search?q=quarterly amortisation leasehold").json()
    assert body["hits"] == []


def test_an_empty_query_is_rejected(client):
    assert client.get("/api/search?q=").status_code == 422


# --- update and delete ----------------------------------------------------


def test_patch_a_record(populated):
    record_id = populated.get("/api/records").json()["records"][0]["id"]
    response = populated.patch(
        f"/api/records/{record_id}", json={"title": "A New Title", "body": "Replaced body."}
    )

    assert response.status_code == 200
    assert response.json()["title"] == "A New Title"
    assert response.json()["body"] == "Replaced body."


def test_patching_the_body_makes_it_searchable_by_its_new_words(populated):
    record_id = populated.get("/api/records").json()["records"][0]["id"]
    populated.patch(f"/api/records/{record_id}", json={"body": "Brass bellows entirely."})

    hits = populated.get("/api/search?q=brass bellows").json()["hits"]
    assert any(h["record"]["id"] == record_id for h in hits)


def test_patching_a_missing_record_is_a_404(client):
    assert client.patch("/api/records/9999", json={"title": "x"}).status_code == 404


def test_delete_a_record(populated):
    record_id = populated.get("/api/records").json()["records"][0]["id"]

    assert populated.delete(f"/api/records/{record_id}").status_code == 204
    assert populated.get(f"/api/records/{record_id}").status_code == 404
    assert populated.get("/api/records").json()["total"] == 2


def test_deleting_a_missing_record_is_a_404(client):
    assert client.delete("/api/records/9999").status_code == 404


# --- sync -----------------------------------------------------------------


def test_sync_stores_a_batch(client):
    response = client.post(
        "/api/sync",
        json={
            "captures": [
                {"text": "Queued note one.", "project": "Phone", "idempotency_key": "a"},
                {"text": "Queued note two.", "project": "Phone", "idempotency_key": "b"},
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["stored"] == 2
    assert body["failed"] == 0
    assert all(r["record"] for r in body["results"])


def test_a_replayed_batch_stores_nothing_twice(client, librarian):
    payload = {
        "captures": [
            {"text": "Queued note one.", "project": "Phone", "idempotency_key": "a"},
            {"text": "Queued note two.", "project": "Phone", "idempotency_key": "b"},
        ]
    }
    first = client.post("/api/sync", json=payload).json()
    assert first["stored"] == 2

    calls_before = librarian.calls
    second = client.post("/api/sync", json=payload).json()

    # A replay must be reported as such, not as a fresh write - the phone
    # cannot otherwise tell whether its queue actually landed.
    assert second["stored"] == 0
    assert second["already_stored"] == 2
    assert all(r["record"] for r in second["results"])
    assert client.get("/api/records").json()["total"] == 2

    # And it must short-circuit before the model runs, or a replayed batch of
    # twenty notes re-runs a 14B model twenty times to discard every result.
    assert librarian.calls == calls_before


def test_one_bad_item_does_not_fail_the_batch(client):
    """The phone must be able to tell which notes landed, or it loses them."""
    response = client.post(
        "/api/sync",
        json={
            "captures": [
                {"text": "A good note.", "project": "Phone", "idempotency_key": "ok-1"},
                {"text": "   ", "project": "Phone", "idempotency_key": "bad-1"},
                {"text": "Another good note.", "project": "Phone", "idempotency_key": "ok-2"},
            ]
        },
    )

    body = response.json()
    assert body["stored"] == 2
    assert body["failed"] == 1

    by_key = {r["idempotency_key"]: r["status"] for r in body["results"]}
    assert by_key == {"ok-1": "stored", "bad-1": "failed", "ok-2": "stored"}


def test_sync_reports_duplicates_separately_from_failures(client):
    client.post("/api/records", json={"text": "Already here.", "project": "Phone"})

    body = client.post(
        "/api/sync", json={"captures": [{"text": "Already here.", "project": "Phone"}]}
    ).json()

    assert body["duplicates"] == 1
    assert body["failed"] == 0


def test_an_empty_batch_is_accepted(client):
    body = client.post("/api/sync", json={"captures": []}).json()
    assert body == {
        "results": [],
        "stored": 0,
        "already_stored": 0,
        "duplicates": 0,
        "failed": 0,
    }


# --- status ---------------------------------------------------------------


def test_status_reports_the_vault(populated):
    body = populated.get("/api/status").json()
    assert body["records"] == 3
    assert body["projects"] == 2
    assert body["vault_path"].endswith("cortex.db")
    assert set(body["integrity"]) == {
        "orphan_chunks",
        "chunks_without_vectors",
        "vectors_without_chunks",
        "records_without_chunks",
    }
    assert all(v == 0 for v in body["integrity"].values())


def test_status_works_on_a_fresh_vault(client):
    """It is the first call a new client makes, before anything exists."""
    body = client.get("/api/status").json()
    assert body["records"] == 0
    assert body["projects"] == 0


# --- cors -----------------------------------------------------------------


def test_cors_allows_the_dev_web_client(client):
    response = client.options(
        "/api/records",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_cors_does_not_allow_credentials(client):
    """Allowing any origin is only safe because no cookie can ride along."""
    response = client.get("/api/records", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-credentials" not in response.headers
