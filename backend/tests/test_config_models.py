import pytest
from pydantic import SecretStr

from fleet_copilot import policy
from fleet_copilot.agent.identity import DEMO_IDENTITIES, OPERATOR_IDENTITY
from fleet_copilot.config import (
    ApiSettings,
    AzureSettings,
    ConfigError,
    ModelSettings,
    Settings,
)

AZURE = {
    "AZURE_OPENAI_API_KEY": "sk-not-a-real-key",
    "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
    "AZURE_OPENAI_API_VERSION": "2024-10-21",
    "AZURE_OPENAI_DEPLOYMENT": "gpt-4o",
}


def test_replay_is_the_default_and_needs_no_azure_settings():
    settings = ModelSettings.from_env({})
    assert settings.mode == "replay" and settings.azure is None and settings.record is False


def test_live_mode_builds_azure_settings():
    settings = ModelSettings.from_env({"FLEET_MODEL_MODE": "live", **AZURE})
    assert isinstance(settings.azure, AzureSettings)
    assert settings.azure.deployment == "gpt-4o"
    assert settings.azure.api_key.get_secret_value() == AZURE["AZURE_OPENAI_API_KEY"]


def test_live_mode_names_the_missing_variables_but_never_values():
    env = {
        "FLEET_MODEL_MODE": "live",
        **AZURE,
        "AZURE_OPENAI_DEPLOYMENT": "",
        "AZURE_OPENAI_API_VERSION": " ",
    }
    with pytest.raises(ConfigError) as err:
        ModelSettings.from_env(env)
    text = str(err.value)
    assert "AZURE_OPENAI_DEPLOYMENT" in text and "AZURE_OPENAI_API_VERSION" in text
    assert AZURE["AZURE_OPENAI_API_KEY"] not in text


def test_the_secret_is_masked_in_repr():
    settings = ModelSettings.from_env({"FLEET_MODEL_MODE": "live", **AZURE})
    assert AZURE["AZURE_OPENAI_API_KEY"] not in repr(settings)


@pytest.mark.parametrize("mode", ["scripted", "fast", "LIVE2"])
def test_unknown_modes_are_refused(mode):
    with pytest.raises(ConfigError):
        ModelSettings.from_env({"FLEET_MODEL_MODE": mode})


def test_the_azure_endpoint_must_be_https():
    env = {"FLEET_MODEL_MODE": "live", **AZURE, "AZURE_OPENAI_ENDPOINT": "http://example.com"}
    with pytest.raises(ConfigError) as err:
        ModelSettings.from_env(env)
    assert "example.com" not in str(err.value)


def test_record_flag_and_cassette_path(tmp_path):
    settings = ModelSettings.from_env(
        {"FLEET_CASSETTE_RECORD": "true", "FLEET_CASSETTE_PATH": str(tmp_path / "c.jsonl")}
    )
    assert settings.record is True and settings.cassette_path == tmp_path / "c.jsonl"


def test_api_settings_default_origins_and_random_secret():
    one, two = ApiSettings.from_env({}), ApiSettings.from_env({})
    assert one.session_secret.get_secret_value() != two.session_secret.get_secret_value()
    assert "http://localhost:5173" in one.allowed_origins
    assert one.operator_passcode is None and one.port == 8000


def test_a_wildcard_origin_is_refused():
    with pytest.raises(ConfigError):
        ApiSettings.from_env({"FLEET_ALLOWED_ORIGINS": "*"})


def test_extra_origins_are_added():
    settings = ApiSettings.from_env(
        {"FLEET_ALLOWED_ORIGINS": "http://localhost:4000, http://127.0.0.1:4000"}
    )
    assert {"http://localhost:4000", "http://127.0.0.1:4000"} <= settings.allowed_origins


def test_state_dir_defaults_next_to_the_database(tmp_path):
    assert (
        Settings(data_path=tmp_path / "d", db_path=tmp_path / "x" / "f.db").state_path()
        == tmp_path / "x"
    )
    explicit = Settings(
        data_path=tmp_path / "d", db_path=tmp_path / "f.db", state_dir=tmp_path / "s"
    )
    assert explicit.state_path() == tmp_path / "s"


def test_the_operator_is_not_a_tenant_admin():
    assert OPERATOR_IDENTITY.role == "operator"
    assert OPERATOR_IDENTITY.company_id not in {i.company_id for i in DEMO_IDENTITIES.values()}


def test_model_limits_are_sane():
    assert policy.MODEL_CALL_TIMEOUT_S * policy.MODEL_ATTEMPTS > policy.MODEL_CALL_TIMEOUT_S
    assert policy.TURN_TIMEOUT_S == 120
    assert len(policy.MODEL_BACKOFF_S) >= 1


def test_the_session_secret_is_a_secretstr():
    assert isinstance(ApiSettings.from_env({}).session_secret, SecretStr)
