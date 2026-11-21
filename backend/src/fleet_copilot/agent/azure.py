"""The live model gateway: Azure OpenAI through LangChain, with a timeout and bounded retries per call.

Everything the model returns is validated by the graph afterwards. This module only moves messages, so it
never logs or re-raises request or response text: a failure is reported as an exception type name.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import openai
from langchain_core.messages import AIMessage, BaseMessage
from langchain_openai import AzureChatOpenAI

from fleet_copilot import policy
from fleet_copilot.agent.llm import ContentFiltered, Explanation, Plan, PlanRejected
from fleet_copilot.agent.tools import TOOLS
from fleet_copilot.config import AzureSettings

_RETRYABLE = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
    TimeoutError,
)


class ModelUnavailable(Exception):
    """A model call kept failing. `reason` is an exception type name, never provider text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _filter_category(exc: openai.BadRequestError) -> str | None:
    """ "jailbreak" or "content" when the provider's content filter refused the call, else None."""
    body = exc.body if isinstance(exc.body, dict) else {}
    inner = body.get("innererror") if isinstance(body.get("innererror"), dict) else {}
    if (
        getattr(exc, "code", None) != "content_filter"
        and inner.get("code") != "ResponsibleAIPolicyViolation"
    ):
        return None
    flags = (
        inner.get("content_filter_result")
        if isinstance(inner.get("content_filter_result"), dict)
        else {}
    )
    jailbreak = flags.get("jailbreak") if isinstance(flags.get("jailbreak"), dict) else {}
    return "jailbreak" if jailbreak.get("filtered") or jailbreak.get("detected") else "content"


def build_azure_model(settings: AzureSettings, call_timeout_s: float) -> AzureChatOpenAI:
    return AzureChatOpenAI(
        azure_deployment=settings.deployment,
        azure_endpoint=settings.endpoint,
        api_version=settings.api_version,
        api_key=settings.api_key,
        temperature=0,
        timeout=call_timeout_s,
        max_retries=0,  # retries are ours, so they are bounded, counted and covered by the turn deadline
    )


class ChatGateway:
    """`LLMGateway` over any LangChain chat model."""

    def __init__(
        self,
        model: Any,
        *,
        attempts: int = policy.MODEL_ATTEMPTS,
        call_timeout_s: float = policy.MODEL_CALL_TIMEOUT_S,
        backoff_s: Sequence[float] = policy.MODEL_BACKOFF_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        tools: Sequence[Any] = TOOLS,
    ) -> None:
        self._attempts = max(1, attempts)
        self._call_timeout_s = call_timeout_s
        self._backoff_s = tuple(backoff_s) or (0.0,)
        self._sleep = sleep
        self._plan = model.with_structured_output(Plan, method="function_calling")
        self._explain = model.with_structured_output(Explanation, method="function_calling")
        self._act = model.bind_tools(tools)

    async def _call(self, runnable: Any, messages: list[BaseMessage]) -> Any:
        reason = "unavailable"
        for attempt in range(self._attempts):
            try:
                async with asyncio.timeout(self._call_timeout_s):
                    return await runnable.ainvoke(messages)
            except _RETRYABLE as exc:
                reason = type(exc).__name__
            except openai.BadRequestError as exc:
                category = _filter_category(exc)
                if category is None:
                    raise
                raise ContentFiltered(category) from None  # type: ignore[arg-type]
            if attempt + 1 < self._attempts:
                await self._sleep(self._backoff_s[min(attempt, len(self._backoff_s) - 1)])
        raise ModelUnavailable(reason) from None

    async def plan(self, messages: list[BaseMessage]) -> Plan:
        result = await self._call(self._plan, messages)
        if not isinstance(result, Plan):
            raise PlanRejected("no structured plan")
        return result

    async def act(self, messages: list[BaseMessage]) -> AIMessage:
        result = await self._call(self._act, messages)
        if not isinstance(result, AIMessage):
            raise ModelUnavailable("bad_model_message")
        return result

    async def explain(self, messages: list[BaseMessage]) -> Explanation:
        result = await self._call(self._explain, messages)
        if not isinstance(result, Explanation):
            raise ModelUnavailable("bad_structured_output")
        return result
