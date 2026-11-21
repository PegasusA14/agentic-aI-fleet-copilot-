"""The FastAPI app. Identity comes only from the signed session; nothing from the client names a tenant."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from fleet_copilot.actions.service import ActionService
from fleet_copilot.actions.store import ActionStore
from fleet_copilot.agent.gateways import build_gateway
from fleet_copilot.agent.llm import LLMGateway
from fleet_copilot.agent.service import AgentService, DatasetUnavailable, InvalidQuestion
from fleet_copilot.api import routes_actions, routes_chat, routes_data, routes_session
from fleet_copilot.config import ApiSettings, ModelSettings, Settings, load_env
from fleet_copilot.observability.store import TraceStore

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
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
        async with AgentService.open(
            db_path=settings.db_path,
            gateway=gateway if gateway is not None else build_gateway(models),
            checkpoint_path=state / "checkpoints.db",
            recorder=store.record,
            actions_db_path=state / "actions.db",
        ) as service:
            app.state.service = service
            app.state.actions = ActionService(
                fleet_db=settings.db_path, store=ActionStore(state / "actions.db")
            )
            app.state.store = store
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
    return app


def create_app_from_env() -> FastAPI:
    load_env()
    return create_app(
        settings=Settings.from_env(), models=ModelSettings.from_env(), api=ApiSettings.from_env()
    )
