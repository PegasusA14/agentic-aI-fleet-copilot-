"""A stand-in chat model for gateway tests: plays back a script, records what it was asked."""

from __future__ import annotations

from typing import Any


class FakeRunnable:
    def __init__(self, owner: FakeChatModel, label: str) -> None:
        self._owner = owner
        self._label = label

    async def ainvoke(self, messages: Any, config: Any = None) -> Any:
        self._owner.calls.append((self._label, messages))
        self._owner.configs.append(config)
        item = self._owner.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return await item()
        return item


class FakeChatModel:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, Any]] = []
        self.configs: list[Any] = []
        self.schemas: list[tuple[Any, str | None]] = []
        self.bound_tools: Any = None

    def with_structured_output(self, schema: Any, method: str | None = None) -> FakeRunnable:
        self.schemas.append((schema, method))
        return FakeRunnable(self, schema.__name__)

    def bind_tools(self, tools: Any) -> FakeRunnable:
        self.bound_tools = tools
        return FakeRunnable(self, "act")
