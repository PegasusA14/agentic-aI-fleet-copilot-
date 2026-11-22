"""Recorded model responses, keyed by what the model was asked.

The key hashes the call kind and the message types, contents and tool calls (names and arguments).
Message and tool-call ids are left out because providers make them up per call. A cassette line is
`{"key", "kind", "response"}`: the question text is only inside the hash, never stored. A line recorded from
a provider that reports token usage also has `"usage": {"prompt", "completion"}`; older lines have none and
replay the same way.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

from fleet_copilot.agent import usage as model_usage
from fleet_copilot.agent.llm import ContentFiltered, LLMGateway, Plan, Reply


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
        return Reply.model_validate(payload)
    return AIMessage(
        content="",
        tool_calls=[
            {"name": c["name"], "args": c["args"], "id": c["id"], "type": "tool_call"}
            for c in payload["tool_calls"]
        ],
    )


def _clean_usage(raw: Any) -> dict[str, int] | None:
    """The `{"prompt", "completion"}` of a cassette line, or None when it is absent or malformed."""
    if not isinstance(raw, dict):
        return None
    prompt, completion = raw.get("prompt"), raw.get("completion")
    for value in (prompt, completion):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
    return {"prompt": prompt, "completion": completion}


def _load(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, int]]]:
    entries: dict[str, dict[str, Any]] = {}
    usages: dict[str, dict[str, int]] = {}
    if not path.exists():
        return entries, usages
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            entries[item["key"]] = item["response"]
            seen = _clean_usage(item.get("usage"))
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ValueError(f"cassette line {number} is not a valid entry") from None
        if seen is not None:
            usages[item["key"]] = seen
    return entries, usages


class CassetteGateway:
    """Replays recorded responses. With an `inner` gateway it also records every miss (read-through)."""

    def __init__(self, path: Path, inner: LLMGateway | None = None) -> None:
        self._path = path
        self._inner = inner
        self._entries, self._usages = _load(path)
        self._calls = 0
        self._prompt = 0
        self._completion = 0
        self._with_usage = 0

    def usage_total(self) -> dict[str, int]:
        """What the calls served so far cost: `calls`, how many `calls_with_usage` recorded their tokens,
        and the `prompt` and `completion` tokens of those. Counts replayed hits and freshly recorded calls."""
        return {
            "calls": self._calls,
            "calls_with_usage": self._with_usage,
            "prompt": self._prompt,
            "completion": self._completion,
        }

    def _count(self, seen: dict[str, int] | None) -> None:
        self._calls += 1
        if seen is not None:
            self._with_usage += 1
            self._prompt += seen["prompt"]
            self._completion += seen["completion"]

    async def _serve(
        self, kind: str, messages: list[BaseMessage], tools: Sequence[str] | None = None
    ) -> Any:
        # The tools an act call is limited to are not part of the key: they follow from the recorded plan.
        key = call_key(kind, messages)
        hit = self._entries.get(key)
        if hit is not None:
            self._count(self._usages.get(key))
            if "content_filtered" in hit:  # the provider refused this call when it was recorded
                raise ContentFiltered(hit["content_filtered"])
            return _decode(kind, hit)
        if self._inner is None:
            raise CassetteMiss("no recorded model response for this call")
        model_usage.CURRENT.set(None)  # a gateway that reports usage sets it during the call
        try:
            if tools is None:
                response = await getattr(self._inner, kind)(messages)
            else:
                response = await getattr(self._inner, kind)(messages, tools=tools)
        except ContentFiltered as exc:
            self._append(key, kind, {"content_filtered": exc.category})
            raise
        seen = _clean_usage(model_usage.CURRENT.get())
        self._append(key, kind, _encode(kind, response), seen)
        return response

    def _append(
        self, key: str, kind: str, payload: dict[str, Any], seen: dict[str, int] | None = None
    ) -> None:
        self._entries[key] = payload
        line: dict[str, Any] = {"key": key, "kind": kind, "response": payload}
        if seen is not None:
            self._usages[key] = seen
            line["usage"] = seen
        self._count(seen)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")

    async def plan(self, messages: list[BaseMessage]) -> Plan:
        return await self._serve("plan", messages)

    async def act(
        self, messages: list[BaseMessage], tools: Sequence[str] | None = None
    ) -> AIMessage:
        return await self._serve("act", messages, tools)

    async def reply(self, messages: list[BaseMessage]) -> Reply:
        return await self._serve("explain", messages)

    explain = reply  # the earlier name of the same step
