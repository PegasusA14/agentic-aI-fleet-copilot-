"""Token usage of one model call, carried from the live gateway to the cassette recorder.

LangChain reports usage on the provider's message, and the structured-output wrappers (`plan`, `explain`)
return a parsed model that no longer has it. A callback sees the message before it is parsed, so the gateway
attaches a `UsageCollector` to each call and publishes what it saw in `CURRENT`. The cassette recorder reads
`CURRENT` right after the call. A context variable keeps concurrent conversations from seeing each other's
numbers, because the gateway and the recorder run in the same task.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

CURRENT: ContextVar[dict[str, int] | None] = ContextVar("fleet_model_usage", default=None)


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


class UsageCollector(BaseCallbackHandler):
    """Adds up the prompt and completion tokens of every generation of one model call."""

    run_inline = True

    def __init__(self) -> None:
        self.prompt = 0
        self.completion = 0
        self.seen = False

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        for generations in response.generations:
            for generation in generations:
                meta = getattr(getattr(generation, "message", None), "usage_metadata", None)
                if isinstance(meta, dict) and meta:
                    self.prompt += _count(meta.get("input_tokens"))
                    self.completion += _count(meta.get("output_tokens"))
                    self.seen = True
        if not self.seen:  # older providers report it on the result rather than the message
            token_usage = (response.llm_output or {}).get("token_usage")
            if isinstance(token_usage, dict) and token_usage:
                self.prompt += _count(token_usage.get("prompt_tokens"))
                self.completion += _count(token_usage.get("completion_tokens"))
                self.seen = True

    def usage(self) -> dict[str, int] | None:
        """`{"prompt": n, "completion": n}`, or None when the provider reported nothing."""
        return {"prompt": self.prompt, "completion": self.completion} if self.seen else None
