from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from fleet_copilot.agent.identity import new_conversation_id, valid_conversation_id
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


def _frame(event: str, payload: str) -> bytes:
    return f"event: {event}\ndata: {payload}\n\n".encode()


@router.post("/conversations", status_code=201)
def create_conversation(request: Request) -> dict[str, str]:
    require_admin(request)
    return {"conversation_id": new_conversation_id()}


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, request: Request) -> Response:
    identity = require_admin(request)
    if not valid_conversation_id(conversation_id):
        raise HTTPException(404, "not found")
    await request.app.state.service.delete_conversation(identity, conversation_id)
    return Response(status_code=204)


@router.post("/chat")
async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
    identity = require_admin(request)
    if not valid_conversation_id(body.conversation_id):
        raise HTTPException(422, "invalid conversation id")
    question = clean_question(body.question)  # InvalidQuestion -> 422 before any stream starts
    service = request.app.state.service
    service.check_ready()  # DatasetUnavailable -> 503 before any stream starts

    async def frames() -> AsyncIterator[bytes]:
        async for item in pump(service.stream_turn(identity, body.conversation_id, question)):
            yield _frame(
                "trace" if isinstance(item, TraceEvent) else "answer", item.model_dump_json()
            )
        yield _frame("done", "{}")

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
