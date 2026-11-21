"""The FastAPI app. Identity comes only from the signed session; nothing from the client names a tenant."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from fleet_copilot.actions.service import ActionService
from fleet_copilot.actions.store import ActionStore
from fleet_copilot.agent.gateways import build_gateway
from fleet_copilot.agent.llm import LLMGateway
from fleet_copilot.agent.service import (
    AgentService,
    DatasetUnavailable,
    InvalidQuestion,
    TurnRecord,
)
from fleet_copilot.api import routes_actions, routes_chat, routes_data, routes_session
from fleet_copilot.config import ApiSettings, ModelSettings, Settings, load_env
from fleet_copilot.evals.store import EvalStore
from fleet_copilot.observability.conversations import ConversationStore
from fleet_copilot.observability.store import TraceStore

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
log = logging.getLogger(__name__)
SESSION_COOKIE = "fleet_session"
SESSION_MAX_AGE_S = 8 * 3600


class OriginGuard:
    """Refuse state-changing requests whose `Origin` header names a site we did not allow."""

    def __init__(self, app, allowed: frozenset[str]) -> None:
        self.app = app
        self.allowed = allowed

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["method"] in _UNSAFE_METHODS:
            origin = dict(scope["headers"]).get(b"origin")
            if origin is not None and origin.decode("latin-1") not in self.allowed:
                await JSONResponse({"detail": "origin not allowed"}, status_code=403)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


def fan_out(*sinks: Callable[[TurnRecord], None]) -> Callable[[TurnRecord], None]:
    """One recorder that feeds every store. Each store is called on its own, so one that fails (a full disk,
    a locked file) never stops the others and never reaches the turn."""

    def record(turn: TurnRecord) -> None:
        for sink in sinks:
            try:
                sink(turn)
            except Exception as exc:  # observability must never break an answer
                log.warning("could not record a turn: %s", type(exc).__name__)

    return record


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serve the built single-page app from `dist`; nothing outside it is ever read."""
    root = dist.resolve()
    index = root / "index.html"
    if (root / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def single_page_app(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)  # unknown API paths stay a JSON 404
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)  # e.g. favicon.svg
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def create_app(
    *,
    settings: Settings,
    models: ModelSettings,
    api: ApiSettings,
    gateway: LLMGateway | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = settings.state_path()
        store = TraceStore(
            state / "traces.db", model_mode="scripted" if gateway is not None else models.mode
        )
        conversations = ConversationStore(state / "conversations.db")
        async with AgentService.open(
            db_path=settings.db_path,
            gateway=gateway if gateway is not None else build_gateway(models),
            checkpoint_path=state / "checkpoints.db",
            recorder=fan_out(store.record, conversations.record),
            actions_db_path=state / "actions.db",
        ) as service:
            app.state.service = service
            app.state.actions = ActionService(
                fleet_db=settings.db_path, store=ActionStore(state / "actions.db")
            )
            app.state.store = store
            app.state.conversations = conversations
            app.state.evals = EvalStore(state / "evals.db")  # where `make eval` writes
            yield

    app = FastAPI(
        title="Fleet Copilot", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.settings = settings
    app.state.api_settings = api
    app.add_middleware(
        SessionMiddleware,
        secret_key=api.session_secret.get_secret_value(),
        session_cookie=SESSION_COOKIE,
        max_age=SESSION_MAX_AGE_S,
        same_site="strict",
        https_only=False,  # served on 127.0.0.1 over http
    )
    app.add_middleware(OriginGuard, allowed=api.allowed_origins)  # added last, so it runs first

    @app.exception_handler(InvalidQuestion)
    async def _invalid_question(_request, exc: InvalidQuestion) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(DatasetUnavailable)
    async def _dataset_unavailable(_request, _exc: DatasetUnavailable) -> JSONResponse:
        return JSONResponse(
            {"detail": "The dataset is not available. Run `make load-data` and try again."},
            status_code=503,
        )

    app.include_router(routes_session.router)
    app.include_router(routes_chat.router)
    app.include_router(routes_data.router)
    app.include_router(routes_actions.router)
    if settings.static_dir is not None and (settings.static_dir / "index.html").is_file():
        _mount_frontend(app, settings.static_dir)  # last, so it never shadows an API route
    return app


def create_app_from_env() -> FastAPI:
    load_env()
    return create_app(
        settings=Settings.from_env(), models=ModelSettings.from_env(), api=ApiSettings.from_env()
    )
