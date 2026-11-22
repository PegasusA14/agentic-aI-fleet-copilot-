from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import Send

from fleet_copilot.agent.identity import Identity, new_conversation_id, valid_conversation_id
from fleet_copilot.agent.service import clean_question
from fleet_copilot.api.security import require_admin
from fleet_copilot.api.stream import pump
from fleet_copilot.contracts import TraceEvent

router = APIRouter(prefix="/api")


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(max_length=64)
    question: str = Field(
        max_length=10_000
    )  # the exact limit and message come from `clean_question`


class ClosingStreamingResponse(StreamingResponse):
    """Close the body iterator ourselves: Starlette never does when `send` raises on a gone reader."""

    async def stream_response(self, send: Send) -> None:
        try:
            await super().stream_response(send)
        finally:
            await self.body_iterator.aclose()  # type: ignore[attr-defined]


def _frame(event: str, payload: str) -> bytes:
    return f"event: {event}\ndata: {payload}\n\n".encode()


@router.post("/conversations", status_code=201)
def create_conversation(request: Request) -> dict[str, str]:
    require_admin(request)
    return {"conversation_id": new_conversation_id()}


@router.get("/conversations")
def list_conversations(
    request: Request,
    identity: Annotated[Identity, Depends(require_admin)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    return request.app.state.conversations.list(identity.company_id, identity.actor_id, limit)


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str, request: Request, identity: Annotated[Identity, Depends(require_admin)]
) -> dict[str, Any]:
    stored = None
    if valid_conversation_id(conversation_id):
        stored = request.app.state.conversations.get(
            identity.company_id, identity.actor_id, conversation_id
        )
    if stored is None:  # unknown, malformed, another tenant's and another admin's all look the same
        raise HTTPException(404, "not found")
    return stored


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, request: Request) -> Response:
    identity = require_admin(request)
    if not valid_conversation_id(conversation_id):
        raise HTTPException(404, "not found")
    await request.app.state.service.delete_conversation(identity, conversation_id)
    request.app.state.conversations.delete(identity.company_id, identity.actor_id, conversation_id)
    return Response(status_code=204)


def _own_conversation(request: Request, identity: Identity, conversation_id: str) -> None:
    """404 unless this admin has this conversation: unknown, malformed, another tenant's and another
    admin's all look the same."""
    stored = None
    if valid_conversation_id(conversation_id):
        stored = request.app.state.conversations.get(
            identity.company_id, identity.actor_id, conversation_id
        )
    if stored is None:
        raise HTTPException(404, "not found")


@router.get("/conversations/{conversation_id}/notes")
async def list_notes(
    conversation_id: str, request: Request, identity: Annotated[Identity, Depends(require_admin)]
) -> list[dict[str, Any]]:
    _own_conversation(request, identity, conversation_id)
    return await request.app.state.service.notes(identity, conversation_id)


@router.delete("/conversations/{conversation_id}/notes/{index}", status_code=204)
async def delete_note(
    conversation_id: str,
    index: int,
    request: Request,
    identity: Annotated[Identity, Depends(require_admin)],
) -> Response:
    _own_conversation(request, identity, conversation_id)
    if not await request.app.state.service.forget_note(identity, conversation_id, index):
        raise HTTPException(404, "not found")
    return Response(status_code=204)


@router.post("/chat")
async def chat(
    body: ChatRequest, request: Request, identity: Annotated[Identity, Depends(require_admin)]
) -> StreamingResponse:
    if not valid_conversation_id(body.conversation_id):
        raise HTTPException(422, "invalid conversation id")
    question = clean_question(body.question)  # InvalidQuestion -> 422 before any stream starts
    service = request.app.state.service
    service.check_ready()  # DatasetUnavailable -> 503 before any stream starts

    async def frames() -> AsyncIterator[bytes]:
        async with contextlib.aclosing(
            pump(service.stream_turn(identity, body.conversation_id, question))
        ) as events:
            async for item in events:
                yield _frame(
                    "trace" if isinstance(item, TraceEvent) else "answer", item.model_dump_json()
                )
        yield _frame("done", "{}")

    return ClosingStreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
