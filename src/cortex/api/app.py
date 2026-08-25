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
from ..chat import (
    Thread,
    ThreadNotFoundError,
    answer,
    create_thread,
    delete_thread,
    get_thread,
    list_facts,
    list_messages,
    list_threads,
    rename_thread,
    set_thread_scope,
)
from ..config import Config
from ..creative import (
    GenerationNotFoundError,
    bank,
    delete_generation,
    generate,
    get_generation,
    list_generations,
    split,
)
from ..embed import EmbeddingError
from ..llm import LibrarianError
from ..retrieve import search as search_vault
from ..settings import get_settings, installed_models, normalise_model, set_settings
from ..store import (
    DuplicateRecordError,
    RecordNotFoundError,
    StaleEditError,
    count_records,
    delete_record,
    get_record,
    integrity_report,
    list_projects,
    list_records,
    update_record,
)
from ..vault import Vault
from ..webui import find_web_dir, mount
from . import deps
from .schemas import (
    AskIn,
    BankIn,
    BankOut,
    CaptureIn,
    CaptureOut,
    GenerateIn,
    GenerationOut,
    IdeaOut,
    MessageOut,
    ModelInfo,
    ModelStatus,
    ProjectOut,
    RecordList,
    RecordOut,
    RecordPatch,
    SearchHitOut,
    SearchOut,
    SettingsOut,
    SettingsPatch,
    StatusOut,
    SyncIn,
    SyncOut,
    SyncResultItem,
    ThreadCreate,
    ThreadDetail,
    ThreadOut,
    ThreadPatch,
)


def create_app(
    config: Config | None = None, token: str | None = None, *, serve_web: bool = True
) -> FastAPI:
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

    # After the API routes: the SPA fallback matches everything, and the
    # first matching route wins.
    if serve_web:
        web_dir = find_web_dir()
        if web_dir is not None:
            mount(app, web_dir)

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

    @app.get(
        "/api/models",
        response_model=list[ModelInfo],
        tags=["system"],
        dependencies=[deps.Authed],
    )
    def get_models() -> list[ModelInfo]:
        """Every installed model, with what it can actually do.

        Clients filter on can_chat so an embedding model can never be picked
        as a chat model - the prototype allowed exactly that, and it failed
        with a 400 the next time you tried to generate anything.
        """
        config = deps.get_config()
        try:
            return [ModelInfo(**m) for m in installed_models(config.ollama_host)]
        except Exception as exc:  # noqa: BLE001 - reported as unavailable
            raise HTTPException(
                status_code=503, detail=f"Could not reach Ollama: {exc}"
            ) from exc

    @app.get(
        "/api/settings",
        response_model=SettingsOut,
        tags=["system"],
        dependencies=[deps.Authed],
    )
    def get_current_settings(
        conn: sqlite3.Connection = Depends(deps.get_conn),
    ) -> SettingsOut:
        settings = get_settings(conn, deps.get_config())
        return SettingsOut(
            librarian_model=settings.librarian_model,
            creative_model=settings.creative_model,
            utility_model=settings.utility_model,
            embed_model=settings.embed_model,
        )

    @app.patch(
        "/api/settings",
        response_model=SettingsOut,
        tags=["system"],
        dependencies=[deps.Authed],
    )
    def patch_settings(
        body: SettingsPatch, conn: sqlite3.Connection = Depends(deps.get_conn)
    ) -> SettingsOut:
        config = deps.get_config()
        requested = body.model_dump(exclude_none=True)

        if requested:
            try:
                chat_capable = {
                    normalise_model(m["name"])
                    for m in installed_models(config.ollama_host)
                    if m["can_chat"]
                }
            except Exception:  # noqa: BLE001 - cannot validate, so do not guess
                chat_capable = None

            if chat_capable is not None:
                for field, name in requested.items():
                    if normalise_model(name) not in chat_capable:
                        raise HTTPException(
                            status_code=422,
                            detail=f"'{name}' cannot be used as the {field.split('_')[0]} "
                            "model - it is not installed, or does not support chat.",
                        )
            set_settings(conn, **requested)

        settings = get_settings(conn, config)
        return SettingsOut(
            librarian_model=settings.librarian_model,
            creative_model=settings.creative_model,
            utility_model=settings.utility_model,
            embed_model=settings.embed_model,
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
                expected_updated_at=body.expected_updated_at,
            )
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StaleEditError as exc:
            # Hand back the current version so the client can show both and
            # let the person choose, rather than dropping either edit.
            raise HTTPException(
                status_code=409,
                detail=str(exc),
                headers={"X-Cortex-Current": str(exc.record.updated_at)},
            ) from exc
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


    # --- conversations ------------------------------------------------------

    def _thread_out(conn: sqlite3.Connection, thread: Thread) -> ThreadOut:
        return ThreadOut(
            id=thread.id,
            title=thread.title,
            project=thread.project,
            message_count=thread.message_count,
            has_summary=bool(thread.summary),
            fact_count=len(list_facts(conn, thread.id)),
            created_at=thread.created_at,
            updated_at=thread.updated_at,
        )

    @app.get(
        "/api/threads",
        response_model=list[ThreadOut],
        tags=["chat"],
        dependencies=[deps.Authed],
    )
    def get_threads(conn: sqlite3.Connection = Depends(deps.get_conn)) -> list[ThreadOut]:
        return [_thread_out(conn, t) for t in list_threads(conn)]

    @app.post(
        "/api/threads",
        response_model=ThreadOut,
        status_code=status.HTTP_201_CREATED,
        tags=["chat"],
        dependencies=[deps.Authed],
    )
    def post_thread(
        body: ThreadCreate, conn: sqlite3.Connection = Depends(deps.get_conn)
    ) -> ThreadOut:
        thread = create_thread(
            conn, title=body.title or "New conversation", project=body.project
        )
        return _thread_out(conn, thread)

    @app.get(
        "/api/threads/{thread_id}",
        response_model=ThreadDetail,
        tags=["chat"],
        dependencies=[deps.Authed],
    )
    def get_one_thread(
        thread_id: int, conn: sqlite3.Connection = Depends(deps.get_conn)
    ) -> ThreadDetail:
        try:
            thread = get_thread(conn, thread_id)
        except ThreadNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        return ThreadDetail(
            thread=_thread_out(conn, thread),
            messages=[
                MessageOut(
                    id=m.id,
                    role=m.role,
                    content=m.content,
                    sources=m.sources,
                    created_at=m.created_at,
                )
                for m in list_messages(conn, thread_id)
            ],
            facts=list_facts(conn, thread_id),
        )

    @app.patch(
        "/api/threads/{thread_id}",
        response_model=ThreadOut,
        tags=["chat"],
        dependencies=[deps.Authed],
    )
    def patch_thread(
        thread_id: int, body: ThreadPatch, conn: sqlite3.Connection = Depends(deps.get_conn)
    ) -> ThreadOut:
        try:
            if body.title is not None:
                rename_thread(conn, thread_id, body.title)
            if body.clear_project:
                set_thread_scope(conn, thread_id, None)
            elif body.project is not None:
                set_thread_scope(conn, thread_id, body.project)
            return _thread_out(conn, get_thread(conn, thread_id))
        except ThreadNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete(
        "/api/threads/{thread_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["chat"],
        dependencies=[deps.Authed],
    )
    def remove_thread(thread_id: int, conn: sqlite3.Connection = Depends(deps.get_conn)) -> None:
        try:
            delete_thread(conn, thread_id)
        except ThreadNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/threads/{thread_id}/messages", tags=["chat"], dependencies=[deps.Authed])
    def post_message(thread_id: int, body: AskIn) -> StreamingResponse:
        """Ask a question in a thread, as server-sent events.

        Emits `status` while it condenses, retrieves and compacts, then
        `sources`, then `token` per piece of the answer, then exactly one
        terminal `done` or `error`.

        Like the streaming capture, the work runs on a thread of its own
        feeding a queue - a local model takes long enough that collecting the
        events and flushing them at the end would defeat the point.
        """
        config = deps.get_config()

        def events() -> Iterator[str]:
            def sse(event: str, payload: object) -> str:
                return f"event: {event}\ndata: {json.dumps(payload)}\n\n"

            outbox: queue.Queue[tuple[str, object] | None] = queue.Queue()

            def work() -> None:
                vault = None
                try:
                    vault = deps.build_vault(config)
                    chatter = deps.build_chatter(vault)
                    for kind, payload in answer(
                        vault.conn,
                        vault.embedder,
                        chatter,
                        thread_id,
                        body.message,
                        utility=deps.build_utility(vault),
                        max_distance=config.effective_max_distance,
                        rrf_k=config.rrf_k,
                    ):
                        outbox.put((kind, payload))
                except ThreadNotFoundError as exc:
                    outbox.put(("error", {"status": 404, "detail": str(exc)}))
                except ValueError as exc:
                    outbox.put(("error", {"status": 400, "detail": str(exc)}))
                except (EmbeddingError, LibrarianError) as exc:
                    outbox.put(("error", {"status": 503, "detail": str(exc)}))
                except Exception as exc:  # noqa: BLE001 - must reach the client
                    outbox.put(
                        ("error", {"status": 500, "detail": f"{type(exc).__name__}: {exc}"})
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

    # --- brainstorming ------------------------------------------------------

    def _generation_out(generation) -> GenerationOut:
        return GenerationOut(
            id=generation.id,
            prompt=generation.prompt,
            project=generation.project,
            model=generation.model,
            mode=generation.mode,
            output=generation.output,
            created_at=generation.created_at,
            ideas=[
                IdeaOut(
                    ordinal=i.ordinal,
                    title=i.title,
                    pitch=i.pitch,
                    detail=i.detail,
                    banked=i.banked,
                    banked_record_id=i.banked_record_id,
                )
                for i in generation.ideas
            ],
        )

    @app.get(
        "/api/generations",
        response_model=list[GenerationOut],
        tags=["creative"],
        dependencies=[deps.Authed],
    )
    def get_generations(
        conn: sqlite3.Connection = Depends(deps.get_conn),
    ) -> list[GenerationOut]:
        return [_generation_out(g) for g in list_generations(conn)]

    @app.get(
        "/api/generations/{generation_id}",
        response_model=GenerationOut,
        tags=["creative"],
        dependencies=[deps.Authed],
    )
    def get_one_generation(
        generation_id: int, conn: sqlite3.Connection = Depends(deps.get_conn)
    ) -> GenerationOut:
        try:
            return _generation_out(get_generation(conn, generation_id))
        except GenerationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete(
        "/api/generations/{generation_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["creative"],
        dependencies=[deps.Authed],
    )
    def remove_generation(
        generation_id: int, conn: sqlite3.Connection = Depends(deps.get_conn)
    ) -> None:
        try:
            delete_generation(conn, generation_id)
        except GenerationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/generations", tags=["creative"], dependencies=[deps.Authed])
    def post_generation(body: GenerateIn) -> StreamingResponse:
        """Brainstorm, streamed as server-sent events.

        A 27B model takes around a minute to work up five developed
        alternatives. In options mode the tokens are JSON rather than prose, so
        a client should show progress rather than the text - but showing
        nothing at all for a minute is worse.
        """
        config = deps.get_config()

        def events() -> Iterator[str]:
            def sse(event: str, payload: object) -> str:
                return f"event: {event}\ndata: {json.dumps(payload)}\n\n"

            outbox: queue.Queue[tuple[str, object] | None] = queue.Queue()

            def work() -> None:
                vault = None
                try:
                    vault = deps.build_vault(config)
                    for kind, payload in generate(
                        vault.conn,
                        vault.embedder,
                        deps.build_creative(vault),
                        body.prompt,
                        mode=body.mode,
                        count=body.count,
                        project=body.project,
                        use_context=body.use_context,
                    ):
                        outbox.put((kind, payload))
                except ValueError as exc:
                    outbox.put(("error", {"status": 400, "detail": str(exc)}))
                except (EmbeddingError, LibrarianError) as exc:
                    outbox.put(("error", {"status": 503, "detail": str(exc)}))
                except Exception as exc:  # noqa: BLE001 - must reach the client
                    outbox.put(
                        ("error", {"status": 500, "detail": f"{type(exc).__name__}: {exc}"})
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

    @app.post(
        "/api/generations/{generation_id}/split",
        response_model=GenerationOut,
        tags=["creative"],
        dependencies=[deps.Authed],
    )
    def post_split(generation_id: int, vault: Vault = Depends(deps.get_vault)) -> GenerationOut:
        """Cut a freeform generation into bankable candidates."""
        try:
            split(vault.conn, vault.librarian, deps.build_utility(vault), generation_id)
        except GenerationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except LibrarianError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return _generation_out(get_generation(vault.conn, generation_id))

    @app.post(
        "/api/generations/{generation_id}/bank",
        response_model=BankOut,
        tags=["creative"],
        dependencies=[deps.Authed],
    )
    def post_bank(
        generation_id: int, body: BankIn, vault: Vault = Depends(deps.get_vault)
    ) -> BankOut:
        """File the chosen ideas, one record each."""
        try:
            result = bank(
                vault.conn,
                vault.embedder,
                generation_id,
                body.ordinals,
                librarian=vault.librarian,
                project=body.project,
                verbatim=body.verbatim,
                chunk_options=vault.chunk_options,
            )
        except GenerationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except EmbeddingError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        return BankOut(
            banked=[RecordOut.of(r) for r in result.banked],
            skipped=[{"ordinal": o, "reason": r} for o, r in result.skipped],
        )

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
