"""Pick the model gateway from settings.

The cassette key does not include the provider, so a cassette recorded with one provider replays with any.
"""

from __future__ import annotations

from fleet_copilot.agent.cassette import CassetteGateway
from fleet_copilot.agent.chat_gateway import ChatGateway
from fleet_copilot.agent.llm import LLMGateway
from fleet_copilot.agent.providers import ADAPTERS
from fleet_copilot.config import ModelSettings


def build_live_gateway(settings: ModelSettings) -> ChatGateway:
    """The live gateway of the selected provider, without any cassette around it."""
    adapter = ADAPTERS[settings.provider]
    if settings.provider_settings is None:
        raise ValueError(f"live mode needs {settings.provider} settings")
    model = adapter.build_model(
        settings.provider_settings,
        temperature=settings.temperature,
        timeout=settings.call_timeout_s,
    )
    return ChatGateway(model, adapter=adapter, call_timeout_s=settings.call_timeout_s)


def build_gateway(settings: ModelSettings) -> LLMGateway:
    if settings.mode == "replay":
        return CassetteGateway(settings.cassette_path)
    live = build_live_gateway(settings)
    return CassetteGateway(settings.cassette_path, inner=live) if settings.record else live
