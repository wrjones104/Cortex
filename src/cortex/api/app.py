"""The HTTP API.

A thin layer: every route translates a request into a call into cortex.core
and an exception into a status code. No logic lives here, so the CLI, the web
client and the phone all get identical behaviour.
"""

from __future__ import annotations

import json
import queue
import sqlite3
import threading
from collections.abc import Iterator

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .. import __version__
from ..capture import capture as capture_note
from ..config import Config
from ..embed import EmbeddingError
from ..llm import LibrarianError
from ..retrieve import search as search_vault
from ..store import (
    DuplicateRecordError,
    RecordNotFoundError,
    count_records,
    delete_record,
    get_record,
    integrity_report,
    list_projects,
    list_records,
    update_record,
)
from ..vault import Vault
from . import deps
from .schemas import (
    CaptureIn,
    CaptureOut,
    ModelStatus,
    ProjectOut,
    RecordList,
    RecordOut,
    RecordPatch,
    SearchHitOut,
    SearchOut,
    StatusOut,
    SyncIn,
    SyncOut,
    SyncResultItem,
)


def create_app(config: Config | None = None, token: str | None = None) -> FastAPI:
    if config is not None:
        deps.configure(config, token)

    app = FastAPI(
        title="Cortex",
        version=__version__,
        description="Local-first AI knowledge vault.",
    )

    # The web client is served from a different port in development. Allowing
    # any origin is safe only because every route requires a bearer token that
    # a browser will not attach on its own - there is no cookie to ride on.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register(app)
    return app


def _register(app: FastAPI) -> None:  # noqa: C901 - a flat list of routes

    # --- unauthenticated ---------------------------------------------------

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        """Reachability check. The only route that does not need a token."""
        return {"status": "ok", "service": "cortex", "version": __version__}

    # --- system ------------------------------------------------------------

    @app.get("/api/status", response_model=StatusOut, tags=["system"], dependencies=[deps.Authed])
    def get_status(conn: sqlite3.Connection = Depends(deps.get_conn)) -> StatusOut:
        config = deps.get_config()

        reachable, detail, installed = True, None, set()
        try:
            import ollama

            listing = ollama.Client(host=config.ollama_host).list()
            installed = {m["model"] for m in listing["models"]}
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            reachable = False
            detail = f"{type(exc).__name__}: {exc}"

        models = [
            ModelStatus(
                role=role,
                name=name,
                installed=name in installed or f"{name}:latest" in installed,
            )
            for role, name in (
                ("embed", config.embed_model),
                ("librarian", config.librarian_model),
                ("creative", config.creative_model),
            )
        ]

        return StatusOut(
            version=__version__,
            records=count_records(conn),
            projects=len(list_projects(conn)),
            vault_path=str(config.db_path),
            integrity=integrity_report(conn),
            ollama_reachable=reachable,
            ollama_detail=detail,
            models=models if reachable else [],
        )

    # --- projects ----------------------------------------------------------

    @app.get(
        "/api/projects",
        response_model=list[ProjectOut],
        tags=["projects"],
        dependencies=[deps.Authed],
    )
    def get_projects(conn: sqlite3.Connection = Depends(deps.get_conn)) -> list[ProjectOut]:
        return [
            ProjectOut(
                id=p.id,
                name=p.name,
                slug=p.slug,
                record_count=count_records(conn, project=p.slug),
            )
            for p in list_projects(conn)
        ]

    # --- records -----------------------------------------------------------

    @app.get(
        "/api/records",
        response_model=RecordList,
        tags=["records"],
        dependencies=[deps.Authed],
    )
    def get_records(
        conn: sqlite3.Connection = Depends(deps.get_conn),
        project: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> RecordList:
        records = list_records(conn, project=project, limit=limit, offset=offset)
        return RecordList(
            records=[RecordOut.of(r) for r in records],
            total=count_records(conn, project=project),
            limit=limit,
            offset=offset,
        )

    @app.get(
        "/api/records/{record_id}",
        response_model=RecordOut,
        tags=["records"],
        dependencies=[deps.Authed],
    )
    def get_one(
        record_id: int, conn: sqlite3.Connection = Depends(deps.get_conn)
    ) -> RecordOut:
        try:
            return RecordOut.of(get_record(conn, record_id))
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/records",
        response_model=CaptureOut,
        status_code=status.HTTP_201_CREATED,
        tags=["records"],
        dependencies=[deps.Authed],
    )
    def post_record(body: CaptureIn, vault: Vault = Depends(deps.get_vault)) -> CaptureOut:
        """File a note. Blocks until the model has finished; use the streaming
        variant if you want progress while a local model thinks."""
        try:
            result = capture_note(
                vault.conn,
                vault.embedder,
                body.text,
                librarian=vault.librarian,
                project=body.project,
                title=body.title,
                category=body.category,
                subcategory=body.subcategory,
                verbatim=body.verbatim,
                idempotency_key=body.idempotency_key,
                allow_duplicate=body.allow_duplicate,
                chunk_options=vault.chunk_options,
            )
        except DuplicateRecordError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (EmbeddingError, LibrarianError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        return CaptureOut(
            record=RecordOut.of(result.record),
            chunks=result.chunks,
            warnings=result.warnings,
        )

    @app.post("/api/records/stream", tags=["records"], dependencies=[deps.Authed])
    def post_record_streaming(body: CaptureIn) -> StreamingResponse:
        """Same as POST /api/records, as server-sent events.

        Emits a `progress` event as each stage begins, then exactly one
        terminal event - `record` on success or `error` on failure. Clients
        should treat a stream that ends without a terminal event as a failure.

        The capture runs on a worker thread and events are handed back through
        a queue, so each stage reaches the client while the model is still
        working. Doing it inline would collect every event and flush them all
        at the end, which is the same as not streaming at all.

        The worker opens its own connection: SQLite objects belong to the
        thread that created them, so it cannot borrow the request's.
        """
        config = deps.get_config()

        def events() -> Iterator[str]:
            def sse(event: str, payload: dict) -> str:
                return f"event: {event}\ndata: {json.dumps(payload)}\n\n"

            outbox: queue.Queue[tuple[str, dict] | None] = queue.Queue()

            def work() -> None:
                vault = None
                try:
                    vault = deps.build_vault(config)
                    result = capture_note(
                        vault.conn,
                        vault.embedder,
                        body.text,
                        librarian=vault.librarian,
                        project=body.project,
                        title=body.title,
                        category=body.category,
                        subcategory=body.subcategory,
                        verbatim=body.verbatim,
                        idempotency_key=body.idempotency_key,
                        allow_duplicate=body.allow_duplicate,
                        chunk_options=vault.chunk_options,
                        progress=lambda stage, message: outbox.put(
                            ("progress", {"stage": stage, "message": message})
                        ),
                    )
                except DuplicateRecordError as exc:
                    outbox.put(("error", {"status": 409, "detail": str(exc)}))
                except ValueError as exc:
                    outbox.put(("error", {"status": 400, "detail": str(exc)}))
                except (EmbeddingError, LibrarianError) as exc:
                    outbox.put(("error", {"status": 503, "detail": str(exc)}))
                except Exception as exc:  # noqa: BLE001 - must reach the client
                    outbox.put(
                        ("error", {"status": 500, "detail": f"{type(exc).__name__}: {exc}"})
                    )
                else:
                    outbox.put(
                        (
                            "record",
                            CaptureOut(
                                record=RecordOut.of(result.record),
                                chunks=result.chunks,
                                warnings=result.warnings,
                            ).model_dump(),
                        )
                    )
                finally:
                    if vault is not None:
                        vault.close()
                    outbox.put(None)

            threading.Thread(target=work, daemon=True).start()

            while (item := outbox.get()) is not None:
                yield sse(*item)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.patch(
        "/api/records/{record_id}",
        response_model=RecordOut,
        tags=["records"],
        dependencies=[deps.Authed],
    )
    def patch_record(
        record_id: int, body: RecordPatch, vault: Vault = Depends(deps.get_vault)
    ) -> RecordOut:
        try:
            updated = update_record(
                vault.conn,
                vault.embedder,
                record_id,
                project=body.project,
                title=body.title,
                body=body.body,
                category=body.category,
                subcategory=body.subcategory,
                chunk_options=vault.chunk_options,
            )
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except EmbeddingError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return RecordOut.of(updated)

    @app.delete(
        "/api/records/{record_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["records"],
        dependencies=[deps.Authed],
    )
    def remove_record(record_id: int, conn: sqlite3.Connection = Depends(deps.get_conn)) -> None:
        try:
            delete_record(conn, record_id)
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # --- search ------------------------------------------------------------

    @app.get(
        "/api/search",
        response_model=SearchOut,
        tags=["search"],
        dependencies=[deps.Authed],
    )
    def get_search(
        q: str = Query(min_length=1, description="What to look for."),
        project: str | None = None,
        limit: int = Query(default=10, ge=1, le=100),
        vault: Vault = Depends(deps.get_vault),
    ) -> SearchOut:
        hits = search_vault(
            vault.conn,
            vault.embedder,
            q,
            project=project,
            limit=limit,
            rrf_k=vault.config.rrf_k,
            max_distance=vault.config.effective_max_distance,
        )
        return SearchOut(query=q, hits=[SearchHitOut.of(h) for h in hits])

    # --- sync --------------------------------------------------------------

    @app.post(
        "/api/sync",
        response_model=SyncOut,
        tags=["sync"],
        dependencies=[deps.Authed],
    )
    def post_sync(body: SyncIn, vault: Vault = Depends(deps.get_vault)) -> SyncOut:
        """Drain a batch of captures queued offline.

        One bad item never fails the batch: the phone would have no way to
        tell which notes landed, and would either lose them or send them all
        again. Each item reports its own outcome.
        """
        results: list[SyncResultItem] = []

        for item in body.captures:
            try:
                result = capture_note(
                    vault.conn,
                    vault.embedder,
                    item.text,
                    librarian=vault.librarian,
                    project=item.project,
                    title=item.title,
                    category=item.category,
                    subcategory=item.subcategory,
                    verbatim=item.verbatim,
                    idempotency_key=item.idempotency_key,
                    allow_duplicate=item.allow_duplicate,
                    chunk_options=vault.chunk_options,
                )
            except DuplicateRecordError as exc:
                results.append(
                    SyncResultItem(
                        idempotency_key=item.idempotency_key,
                        status="duplicate",
                        detail=str(exc),
                    )
                )
            except Exception as exc:  # noqa: BLE001 - reported per item
                results.append(
                    SyncResultItem(
                        idempotency_key=item.idempotency_key,
                        status="failed",
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                )
            else:
                results.append(
                    SyncResultItem(
                        idempotency_key=item.idempotency_key,
                        status="already_stored" if result.already_stored else "stored",
                        record=RecordOut.of(result.record),
                    )
                )

        return SyncOut(
            results=results,
            stored=sum(r.status == "stored" for r in results),
            already_stored=sum(r.status == "already_stored" for r in results),
            duplicates=sum(r.status == "duplicate" for r in results),
            failed=sum(r.status == "failed" for r in results),
        )
