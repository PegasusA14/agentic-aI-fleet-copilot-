import pytest
from langchain_core.messages import HumanMessage
from pydantic import SecretStr

from fleet_copilot.agent.azure import ChatGateway
from fleet_copilot.agent.cassette import CassetteGateway, CassetteMiss
from fleet_copilot.agent.gateways import build_gateway
from fleet_copilot.config import AzureSettings, ModelSettings

AZURE = AzureSettings(
    api_key=SecretStr("sk-not-a-real-key"),
    endpoint="https://example.openai.azure.com/",
    api_version="2024-10-21",
    deployment="gpt-4o",
)


def settings(tmp_path, **kw):
    base = dict(
        mode="replay",
        azure=None,
        cassette_path=tmp_path / "c.jsonl",
        record=False,
        call_timeout_s=5.0,
    )
    return ModelSettings(**{**base, **kw})


async def test_replay_mode_is_offline_and_misses_loudly(tmp_path):
    gateway = build_gateway(settings(tmp_path))
    assert isinstance(gateway, CassetteGateway)
    with pytest.raises(CassetteMiss):
        await gateway.plan([HumanMessage("x")])


def test_live_mode_is_the_azure_gateway(tmp_path):
    assert isinstance(build_gateway(settings(tmp_path, mode="live", azure=AZURE)), ChatGateway)


def test_live_mode_with_recording_wraps_it_in_the_cassette(tmp_path):
    gateway = build_gateway(settings(tmp_path, mode="live", azure=AZURE, record=True))
    assert isinstance(gateway, CassetteGateway)


def test_live_mode_without_azure_settings_is_refused(tmp_path):
    with pytest.raises(ValueError):
        build_gateway(settings(tmp_path, mode="live", azure=None))
