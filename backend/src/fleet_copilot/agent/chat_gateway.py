"""The live model gateway: any LangChain chat model, with a timeout and bounded retries per call.

The gateway knows no provider. It builds the structured-output and tool-bound runnables the same way for
every model, and asks the provider's adapter (`agent.providers`) how to sort an error: retry it, report
a content-filter refusal, or give up.

Everything the model returns is validated by the graph afterwards. This module only moves messages, so it
never logs or re-raises request or response text: a failure is reported as an exception type name.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, BaseMessage

from fleet_copilot import policy
from fleet_copilot.agent import usage
from fleet_copilot.agent.llm import ContentFiltered, Explanation, Plan, PlanRejected
from fleet_copilot.agent.providers import ADAPTERS, DEFAULT_PROVIDER, ProviderAdapter
from fleet_copilot.agent.tools import TOOLS


class ModelUnavailable(Exception):
    """A model call kept failing. `reason` is an exception type name, never provider text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ChatGateway:
    """`LLMGateway` over any LangChain chat model."""

    def __init__(
        self,
        model: Any,
        *,
        adapter: ProviderAdapter | None = None,
        attempts: int = policy.MODEL_ATTEMPTS,
        call_timeout_s: float = policy.MODEL_CALL_TIMEOUT_S,
        backoff_s: Sequence[float] = policy.MODEL_BACKOFF_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        tools: Sequence[Any] = TOOLS,
    ) -> None:
        self._adapter = adapter or ADAPTERS[DEFAULT_PROVIDER]
        self._attempts = max(1, attempts)
        self._call_timeout_s = call_timeout_s
        self._backoff_s = tuple(backoff_s) or (0.0,)
        self._sleep = sleep
        self._plan = model.with_structured_output(Plan, method="function_calling")
        self._explain = model.with_structured_output(Explanation, method="function_calling")
        self._tools = tools
        self._model = model
        self._act = model.bind_tools(tools)
        self._act_bound: dict[tuple[str, ...], Any] = {}

    async def _call(self, runnable: Any, messages: list[BaseMessage]) -> Any:
        reason = "unavailable"
        for attempt in range(self._attempts):
            collector = usage.UsageCollector()
            try:
                async with asyncio.timeout(self._call_timeout_s):
                    result = await runnable.ainvoke(messages, config={"callbacks": [collector]})
            except OutputParserException:
                if collector.filtered:  # the answer was cut off by the provider's filter
                    raise ContentFiltered("content") from None
                raise
            except Exception as exc:
                kind = self._adapter.classify_error(exc)
                if kind == "content_filtered":
                    category = self._adapter.content_filter_category(exc) or "content"
                    raise ContentFiltered(category) from None
                if kind != "retry":  # a refusal of the request itself: asking again cannot help
                    raise ModelUnavailable(type(exc).__name__) from None
                reason = type(exc).__name__
            else:
                if collector.filtered:
                    raise ContentFiltered("content")
                usage.CURRENT.set(
                    collector.usage()
                )  # the cassette recorder reads it, if it wraps us
                return result
            if attempt + 1 < self._attempts:
                await self._sleep(self._backoff_s[min(attempt, len(self._backoff_s) - 1)])
        raise ModelUnavailable(reason) from None

    async def plan(self, messages: list[BaseMessage]) -> Plan:
        result = await self._call(self._plan, messages)
        if not isinstance(result, Plan):
            raise PlanRejected("no structured plan")
        return result

    def _act_for(self, names: Sequence[str] | None) -> Any:
        """The model bound to only these tools (every tool when `names` is None)."""
        if names is None:
            return self._act
        key = tuple(name for name in (t.name for t in self._tools) if name in set(names))
        if key not in self._act_bound:
            self._act_bound[key] = self._model.bind_tools([t for t in self._tools if t.name in key])
        return self._act_bound[key]

    async def act(
        self, messages: list[BaseMessage], tools: Sequence[str] | None = None
    ) -> AIMessage:
        result = await self._call(self._act_for(tools), messages)
        if not isinstance(result, AIMessage):
            raise ModelUnavailable("bad_model_message")
        return result

    async def explain(self, messages: list[BaseMessage]) -> Explanation:
        result = await self._call(self._explain, messages)
        if not isinstance(result, Explanation):
            raise ModelUnavailable("bad_structured_output")
        return result
