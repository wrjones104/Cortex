"""Packaging and first run.

The parts that decide whether someone who is not me can get this working.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cortex.api import deps
from cortex.api.app import create_app
from cortex.config import Config
from cortex.setup_wizard import inspect, prepare_vault
from cortex.webui import RESERVED, find_web_dir

TOKEN = "packaging-token"


# --- serving the web client -----------------------------------------------


@pytest.fixture
def built_web(tmp_path, monkeypatch):
    """A stand-in for `npm run build`, so these tests need no node."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Cortex</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('hi')", encoding="utf-8")
    (dist / "manifest.webmanifest").write_text('{"name":"Cortex"}', encoding="utf-8")
    monkeypatch.setenv("CORTEX_WEB_DIR", str(dist))
    return dist


@pytest.fixture
def web_client(tmp_path, embedder, built_web, monkeypatch):
    config = Config(data_dir=tmp_path, embed_model="fake-embed")
    deps.configure(config, TOKEN)
    monkeypatch.setattr(deps, "_embedder", embedder)
    with TestClient(create_app()) as client:
        yield client


def test_the_web_directory_is_found_from_the_environment(built_web):
    assert find_web_dir() == built_web


def test_a_missing_web_directory_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEX_WEB_DIR", str(tmp_path / "nowhere"))
    assert find_web_dir() is None


def test_the_app_is_served_at_the_root(web_client):
    response = web_client.get("/")
    assert response.status_code == 200
    assert "Cortex" in response.text


def test_static_assets_are_served(web_client):
    assert web_client.get("/assets/app.js").status_code == 200
    assert web_client.get("/manifest.webmanifest").status_code == 200


def test_a_deep_link_returns_the_app_not_a_404(web_client):
    """A single-page app owns its own routes: /vault/4 has to work on a
    reload, or the app breaks the moment anyone shares a link."""
    for path in ("/vault/4", "/chat/2", "/create", "/pending"):
        response = web_client.get(path)
        assert response.status_code == 200, path
        assert "Cortex" in response.text


@pytest.mark.parametrize("path", ["/api/records", "/api/status", "/health"])
def test_api_routes_still_win_over_the_fallback(web_client, path):
    """The catch-all must not swallow the API, or a 401 becomes a page."""
    response = web_client.get(path)
    assert response.status_code != 200 or path == "/health"
    assert "<!doctype html>" not in response.text.lower()


def test_an_unknown_api_route_is_a_404_not_the_app(web_client):
    response = web_client.get("/api/nonsense")
    assert response.status_code == 404
    assert "<!doctype html>" not in response.text.lower()


def test_docs_are_not_swallowed(web_client):
    assert web_client.get("/openapi.json").status_code == 200
    assert web_client.get("/openapi.json").json()["info"]["title"] == "Cortex"


def test_the_reserved_prefixes_cover_every_api_surface():
    for prefix in ("api", "health", "docs", "openapi.json"):
        assert prefix in RESERVED


def test_a_path_outside_the_web_directory_cannot_be_read(web_client, tmp_path):
    """Path traversal through the fallback would serve anything on disk."""
    secret = tmp_path / "secret.txt"
    secret.write_text("not for the browser", encoding="utf-8")

    response = web_client.get("/../secret.txt")
    assert "not for the browser" not in response.text


def test_serving_the_web_client_can_be_switched_off(tmp_path, embedder, built_web, monkeypatch):
    config = Config(data_dir=tmp_path, embed_model="fake-embed")
    deps.configure(config, TOKEN)
    monkeypatch.setattr(deps, "_embedder", embedder)

    with TestClient(create_app(serve_web=False)) as client:
        assert client.get("/").status_code == 404


# --- the first-run wizard -------------------------------------------------


def test_inspect_reports_an_unreachable_ollama_without_raising(tmp_path):
    config = Config(data_dir=tmp_path, ollama_host="http://127.0.0.1:1")
    plan = inspect(config)

    assert plan.ready is False
    ollama_check = next(c for c in plan.checks if c.name == "Ollama")
    assert ollama_check.ok is False
    assert "Start Ollama" in ollama_check.fix


def test_inspect_lists_models_that_need_pulling(tmp_path, monkeypatch):
    import cortex.setup_wizard as wizard

    monkeypatch.setattr(
        wizard,
        "installed_models",
        lambda host: [
            {
                "name": "qwen2.5:14b",
                "parameter_size": "14.8B",
                "capabilities": ["completion"],
                "can_chat": True,
                "can_embed": False,
                "can_think": False,
            }
        ],
    )

    config = Config(
        data_dir=tmp_path,
        embed_model="embeddinggemma",
        librarian_model="qwen2.5:14b",
        creative_model="gemma4:12b",
    )
    plan = inspect(config)

    missing = {model for _, model in plan.missing_models}
    assert missing == {"embeddinggemma", "gemma4:12b"}
    assert plan.ready is False
    embed_check = next(c for c in plan.checks if c.name == "Embedding model")
    assert embed_check.fix == "ollama pull embeddinggemma"


def test_inspect_rejects_a_model_that_cannot_do_the_job(tmp_path, monkeypatch):
    """Installed is not the same as usable - the prototype's mistake."""
    import cortex.setup_wizard as wizard

    monkeypatch.setattr(
        wizard,
        "installed_models",
        lambda host: [
            {
                "name": "embeddinggemma:latest",
                "parameter_size": "307M",
                "capabilities": ["embedding"],
                "can_chat": False,
                "can_embed": True,
                "can_think": False,
            }
        ],
    )

    config = Config(data_dir=tmp_path, librarian_model="embeddinggemma")
    plan = inspect(config)

    librarian = next(c for c in plan.checks if c.name == "Librarian model")
    assert librarian.ok is False
    assert "cannot be used for librarian" in librarian.detail
    # It is installed, so pulling it again would not help.
    assert "pull" not in (librarian.fix or "")


def test_inspect_matches_a_bare_name_against_a_tagged_one(tmp_path, monkeypatch):
    import cortex.setup_wizard as wizard

    monkeypatch.setattr(
        wizard,
        "installed_models",
        lambda host: [
            {
                "name": "embeddinggemma:latest",
                "parameter_size": "307M",
                "capabilities": ["embedding"],
                "can_chat": False,
                "can_embed": True,
                "can_think": False,
            }
        ],
    )

    plan = inspect(Config(data_dir=tmp_path, embed_model="embeddinggemma"))
    assert next(c for c in plan.checks if c.name == "Embedding model").ok is True


def test_preparing_the_vault_is_idempotent(tmp_path):
    config = Config(data_dir=tmp_path / "fresh")

    first = prepare_vault(config)
    second = prepare_vault(config)

    assert first == second
    assert config.db_path.exists()


def test_tailscale_address_is_none_when_it_is_not_installed(monkeypatch):
    import shutil

    import cortex.setup_wizard as wizard

    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert wizard.tailscale_address() is None


# --- Ollama exposure -------------------------------------------------------


def test_ollama_answering_beyond_loopback_is_reported(tmp_path, monkeypatch):
    """Cortex authenticates every request; Ollama authenticates none.

    An Ollama on 0.0.0.0 means anyone who can reach the machine can use the
    models on it and read whatever is sent through them - which quietly undoes
    the point of running all of this locally.
    """
    import httpx

    import cortex.setup_wizard as wizard

    monkeypatch.setattr(wizard, "local_address", lambda: "100.64.0.1")

    class Answered:
        status_code = 200

    monkeypatch.setattr(httpx, "get", lambda url, timeout=None: Answered())

    config = Config(data_dir=tmp_path)
    assert wizard.ollama_exposure(config) == "100.64.0.1"


def test_a_loopback_only_ollama_reports_nothing(tmp_path, monkeypatch):
    import httpx

    import cortex.setup_wizard as wizard

    monkeypatch.setattr(wizard, "local_address", lambda: "100.64.0.1")

    def refused(url, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(httpx, "get", refused)

    assert wizard.ollama_exposure(Config(data_dir=tmp_path)) is None


def test_exposure_is_not_guessed_when_there_is_no_local_address(tmp_path, monkeypatch):
    import cortex.setup_wizard as wizard

    monkeypatch.setattr(wizard, "local_address", lambda: None)
    assert wizard.ollama_exposure(Config(data_dir=tmp_path)) is None


def test_setup_reports_the_exposure_as_a_failed_check(tmp_path, monkeypatch):
    import cortex.setup_wizard as wizard

    monkeypatch.setattr(
        wizard,
        "installed_models",
        lambda host: [
            {
                "name": "embeddinggemma:latest",
                "parameter_size": "307M",
                "capabilities": ["embedding"],
                "can_chat": False,
                "can_embed": True,
                "can_think": False,
            },
            {
                "name": "qwen2.5:14b",
                "parameter_size": "14.8B",
                "capabilities": ["completion"],
                "can_chat": True,
                "can_embed": False,
                "can_think": False,
            },
        ],
    )
    monkeypatch.setattr(wizard, "ollama_exposure", lambda config: "100.64.0.1")

    config = Config(
        data_dir=tmp_path,
        embed_model="embeddinggemma",
        librarian_model="qwen2.5:14b",
        creative_model="qwen2.5:14b",
    )
    plan = wizard.inspect(config)

    reach = next(c for c in plan.checks if c.name == "Ollama reach")
    assert reach.ok is False
    assert "100.64.0.1" in reach.detail
    assert "OLLAMA_HOST=127.0.0.1" in reach.fix
    assert plan.ready is False


def test_a_private_ollama_passes_the_check(tmp_path, monkeypatch):
    import cortex.setup_wizard as wizard

    monkeypatch.setattr(
        wizard,
        "installed_models",
        lambda host: [
            {
                "name": "embeddinggemma:latest",
                "parameter_size": "307M",
                "capabilities": ["embedding"],
                "can_chat": False,
                "can_embed": True,
                "can_think": False,
            },
            {
                "name": "qwen2.5:14b",
                "parameter_size": "14.8B",
                "capabilities": ["completion"],
                "can_chat": True,
                "can_embed": False,
                "can_think": False,
            },
        ],
    )
    monkeypatch.setattr(wizard, "ollama_exposure", lambda config: None)

    plan = wizard.inspect(
        Config(
            data_dir=tmp_path,
            embed_model="embeddinggemma",
            librarian_model="qwen2.5:14b",
            creative_model="qwen2.5:14b",
        )
    )

    assert next(c for c in plan.checks if c.name == "Ollama reach").ok is True
    assert plan.ready is True
