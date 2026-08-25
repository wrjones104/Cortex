"""Serving the built web client from the API.

An installed Cortex should be one command and one port. Making someone run a
separate static server next to the API — and then explain the resulting CORS
to them — is not a thing to ship.

The built assets are looked for in three places, in order:

1. CORTEX_WEB_DIR, for anyone serving a build from somewhere else entirely.
2. `web/dist` relative to the repository root, which is what `npm run build`
   writes and therefore always the freshest thing in a checkout.
3. `cortex/webui/` inside the installed package, which is where the release
   build copies them and the only one an installed Cortex has.

The order of the last two is load-bearing. The packaged copy is staged by
hand before building a wheel, so in a checkout it goes stale the moment the
client is rebuilt - and if it won, `cortex serve` would keep serving an old
build with nothing to say so. An installed package has no `web/dist` beside
it, so putting the checkout first costs it nothing.

If none exist the API still runs perfectly well; only the browser client is
missing, and `cortex doctor` says so.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Paths the SPA fallback must never swallow: they belong to the API, and
# returning index.html for them would turn a 404 into a confusing 200.
RESERVED = ("api", "health", "docs", "redoc", "openapi.json")


def find_web_dir() -> Path | None:
    override = os.environ.get("CORTEX_WEB_DIR")
    if override:
        candidate = Path(override).expanduser()
        return candidate if (candidate / "index.html").exists() else None

    # src/cortex/webui.py -> repository root -> web/dist
    checkout = Path(__file__).resolve().parents[2] / "web" / "dist"
    if (checkout / "index.html").exists():
        return checkout

    packaged = Path(__file__).parent / "webui"
    if (packaged / "index.html").exists():
        return packaged

    return None


def built_at(web_dir: Path) -> str | None:
    """When the served client was built, in local time.

    Worth surfacing: the assets are built separately from the Python side, so
    "I changed the UI and nothing happened" is almost always a stale build
    rather than a broken one.
    """
    from datetime import datetime

    index = web_dir / "index.html"
    if not index.exists():
        return None
    return datetime.fromtimestamp(index.stat().st_mtime).astimezone().strftime(
        "%Y-%m-%d %H:%M"
    )


def mount(app: FastAPI, web_dir: Path) -> None:
    """Serve the client, with a fallback so deep links work.

    A single-page app owns its own routes, so /vault/4 has to return
    index.html rather than a 404 — otherwise the app works until someone
    reloads the page or follows a link into it.

    Must be called after the API routes are registered: the catch-all matches
    everything, and whichever route was registered first wins.
    """
    assets = web_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index = web_dir / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        head = path.split("/", 1)[0]
        if head in RESERVED:
            raise HTTPException(status_code=404, detail="Not found")

        # Serve a real file when there is one (manifest, icons, service
        # worker), and hand everything else to the app.
        if path:
            candidate = (web_dir / path).resolve()
            if candidate.is_file() and candidate.is_relative_to(web_dir.resolve()):
                return FileResponse(candidate)

        return FileResponse(index)
