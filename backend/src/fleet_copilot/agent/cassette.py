"""Recorded model responses, keyed by what the model was asked.

The key hashes the call kind and the message types, contents and tool calls (names and arguments).
Message and tool-call ids are left out because providers make them up per call. A cassette line is
`{"key", "kind", "response"}`: the question text is only inside the hash, never stored.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

from fleet_copilot.agent.llm import ContentFiltered, Explanation, LLMGateway, Plan


class CassetteMiss(Exception):
    """A replayed turn asked something nobody recorded an answer for."""


def _message(message: BaseMessage) -> dict[str, Any]:
    body: dict[str, Any] = {"type": message.type, "content": str(message.content)}
    if isinstance(message, AIMessage):
        body["tool_calls"] = [{"name": c["name"], "args": c["args"]} for c in message.tool_calls]
    return body


def call_key(kind: str, messages: Sequence[BaseMessage]) -> str:
    text = json.dumps(
        [kind, [_message(m) for m in messages]], sort_keys=True, ensure_ascii=True, default=str
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _encode(kind: str, response: Any) -> dict[str, Any]:
    if kind == "act":
        return {
            "tool_calls": [
                {"name": c["name"], "args": c["args"], "id": c["id"]} for c in response.tool_calls
            ]
        }
    return response.model_dump(mode="json")


def _decode(kind: str, payload: dict[str, Any]) -> Any:
    if kind == "plan":
        return Plan.model_validate(payload)
    if kind == "explain":
        return Explanation.model_validate(payload)
    return AIMessage(
        content="",
        tool_calls=[
            {"name": c["name"], "args": c["args"], "id": c["id"], "type": "tool_call"}
            for c in payload["tool_calls"]
        ],
    )


def _load(path: Path) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return entries
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            entries[item["key"]] = item["response"]
        except (ValueError, KeyError, TypeError):
            raise ValueError(f"cassette line {number} is not a valid entry") from None
    return entries


class CassetteGateway:
    """Replays recorded responses. With an `inner` gateway it also records every miss (read-through)."""

    def __init__(self, path: Path, inner: LLMGateway | None = None) -> None:
        self._path = path
        self._inner = inner
        self._entries = _load(path)

    async def _serve(self, kind: str, messages: list[BaseMessage]) -> Any:
        key = call_key(kind, messages)
        hit = self._entries.get(key)
        if hit is not None:
            if "content_filtered" in hit:  # the provider refused this call when it was recorded
                raise ContentFiltered(hit["content_filtered"])
            return _decode(kind, hit)
        if self._inner is None:
            raise CassetteMiss("no recorded model response for this call")
        try:
            response = await getattr(self._inner, kind)(messages)
        except ContentFiltered as exc:
            self._append(key, kind, {"content_filtered": exc.category})
            raise
        payload = _encode(kind, response)
        self._append(key, kind, payload)
        return response

    def _append(self, key: str, kind: str, payload: dict[str, Any]) -> None:
        self._entries[key] = payload
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"key": key, "kind": kind, "response": payload}) + "\n")

    async def plan(self, messages: list[BaseMessage]) -> Plan:
        return await self._serve("plan", messages)

    async def act(self, messages: list[BaseMessage]) -> AIMessage:
        return await self._serve("act", messages)

    async def explain(self, messages: list[BaseMessage]) -> Explanation:
        return await self._serve("explain", messages)
