from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from pydantic import SecretStr

from fleet_copilot.api.app import create_app
from fleet_copilot.config import ApiSettings, ModelSettings, Settings

ORIGIN = "http://127.0.0.1:8000"


@asynccontextmanager
async def client_for(db_path, state_dir: Path, llm, *, operator_passcode: str | None = "op-pass"):
    settings = Settings(data_path=Path("unused"), db_path=db_path, state_dir=state_dir)
    api = ApiSettings(
        session_secret=SecretStr("test-secret"),
        operator_passcode=SecretStr(operator_passcode) if operator_passcode else None,
        allowed_origins=frozenset({ORIGIN}),
        port=8000,
        failure_delay_s=0.0,
    )
    models = ModelSettings(
        mode="replay",
        azure=None,
        cassette_path=state_dir / "c.jsonl",
        record=False,
        call_timeout_s=5.0,
    )
    app = create_app(settings=settings, models=models, api=api, gateway=llm)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
            yield client


async def sign_in(client, user="admin-acme", passcode=None):
    body = {"user": user} if passcode is None else {"user": user, "passcode": passcode}
    return await client.post("/api/session", json=body)


def parse_sse(text: str) -> list[tuple[str, dict]]:
    frames = []
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        event = next(line[len("event: ") :] for line in lines if line.startswith("event: "))
        data = next(line[len("data: ") :] for line in lines if line.startswith("data: "))
        frames.append((event, json.loads(data)))
    return frames
