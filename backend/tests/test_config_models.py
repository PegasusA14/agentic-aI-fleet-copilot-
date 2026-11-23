import pytest
from pydantic import SecretStr

from fleet_copilot import policy
from fleet_copilot.agent.identity import DEMO_IDENTITIES, OPERATOR_IDENTITY
from fleet_copilot.agent.providers import (
    DEFAULT_PROVIDER,
    AzureSettings,
    OpenAICompatibleSettings,
)
from fleet_copilot.config import ApiSettings, ConfigError, ModelSettings, Settings

AZURE = {
    "AZURE_OPENAI_API_KEY": "sk-not-a-real-key",
    "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
    "AZURE_OPENAI_API_VERSION": "2024-10-21",
    "AZURE_OPENAI_DEPLOYMENT": "gpt-4o",
}


def test_replay_is_the_default_and_needs_no_azure_settings():
    settings = ModelSettings.from_env({})
    assert settings.mode == "replay" and settings.provider_settings is None
    assert settings.record is False


def test_live_mode_builds_azure_settings():
    settings = ModelSettings.from_env({"FLEET_MODEL_MODE": "live", **AZURE})
    assert isinstance(settings.provider_settings, AzureSettings)
    assert settings.provider_settings.deployment == "gpt-4o"
    assert settings.provider_settings.api_key.get_secret_value() == AZURE["AZURE_OPENAI_API_KEY"]


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


def test_the_endpoint_and_deployment_stay_out_of_repr():
    shown = repr(ModelSettings.from_env({"FLEET_MODEL_MODE": "live", **AZURE}))
    assert "example.openai.azure.com" not in shown
    assert "gpt-4o" not in shown
    assert AZURE["AZURE_OPENAI_API_KEY"] not in shown


def test_the_temperature_defaults_to_zero_and_the_name_to_the_deployment():
    replay = ModelSettings.from_env({})
    assert replay.temperature == 0.0 and replay.model_name is None
    live = ModelSettings.from_env({"FLEET_MODEL_MODE": "live", **AZURE})
    assert live.temperature == 0.0
    assert live.model_name == "gpt-4o"


@pytest.mark.parametrize("raw, expected", [("0", 0.0), ("0.2", 0.2), (" 1 ", 1.0), ("", 0.0)])
def test_a_valid_temperature_is_read(raw, expected):
    env = {"FLEET_MODEL_MODE": "live", **AZURE, "FLEET_MODEL_TEMPERATURE": raw}
    settings = ModelSettings.from_env(env)
    assert settings.temperature == expected


@pytest.mark.parametrize("raw", ["hot", "1.5", "-0.1", "nan", "inf", "7.25"])
def test_a_bad_temperature_names_the_variable_but_not_the_value(raw):
    with pytest.raises(ConfigError) as err:
        ModelSettings.from_env({"FLEET_MODEL_TEMPERATURE": raw})
    assert "FLEET_MODEL_TEMPERATURE" in str(err.value)
    assert raw not in str(err.value)


def test_the_model_name_is_a_label_that_leaves_the_deployment_alone():
    env = {"FLEET_MODEL_MODE": "live", **AZURE, "FLEET_MODEL_NAME": " gpt-4o-2024-11-20 "}
    settings = ModelSettings.from_env(env)
    assert settings.model_name == "gpt-4o-2024-11-20"
    assert settings.provider_settings.deployment == "gpt-4o"
    assert ModelSettings.from_env({"FLEET_MODEL_NAME": "label"}).model_name == "label"


OPENAI = {"OPENAI_API_KEY": "sk-openai-not-real", "OPENAI_MODEL": "gpt-4o-mini"}
OPENROUTER = {
    "OPENROUTER_API_KEY": "sk-or-not-real",
    "OPENROUTER_MODEL": "openai/gpt-4o-mini",
}


def live(provider, extra):
    return ModelSettings.from_env(
        {"FLEET_MODEL_MODE": "live", "FLEET_MODEL_PROVIDER": provider, **extra}
    )


def test_the_provider_defaults_to_azure():
    assert ModelSettings.from_env({}).provider == "azure" == DEFAULT_PROVIDER
    settings = ModelSettings.from_env({"FLEET_MODEL_MODE": "live", **AZURE})
    assert settings.provider == "azure"
    assert isinstance(settings.provider_settings, AzureSettings)
    blank = ModelSettings.from_env(
        {"FLEET_MODEL_MODE": "live", "FLEET_MODEL_PROVIDER": " ", **AZURE}
    )
    assert blank.provider == "azure"


@pytest.mark.parametrize("raw", ["gemini", "azure-openai", "sk-pasted-in-the-wrong-place"])
def test_an_unknown_provider_names_the_variable_but_not_the_value(raw):
    with pytest.raises(ConfigError) as err:
        ModelSettings.from_env({"FLEET_MODEL_PROVIDER": raw})
    assert "FLEET_MODEL_PROVIDER" in str(err.value) and raw not in str(err.value)


def test_the_provider_is_case_insensitive():
    assert live("OpenRouter", OPENROUTER).provider == "openrouter"


def test_openai_settings():
    settings = live("openai", OPENAI)
    creds = settings.provider_settings
    assert settings.provider == "openai" and isinstance(creds, OpenAICompatibleSettings)
    assert creds.api_key.get_secret_value() == OPENAI["OPENAI_API_KEY"]
    assert creds.model == "gpt-4o-mini" and creds.base_url == "https://api.openai.com/v1"
    assert dict(creds.headers) == {}
    assert settings.model_name == "gpt-4o-mini"


def test_openai_takes_an_optional_https_base_url():
    settings = live("openai", {**OPENAI, "OPENAI_BASE_URL": " https://llm.example.net/v1 "})
    assert settings.provider_settings.base_url == "https://llm.example.net/v1"


@pytest.mark.parametrize("url", ["http://llm.example.net/v1", "https://", "ftp://llm.example.net"])
def test_openai_base_url_must_be_https(url):
    with pytest.raises(ConfigError) as err:
        live("openai", {**OPENAI, "OPENAI_BASE_URL": url})
    assert "OPENAI_BASE_URL" in str(err.value) and "llm.example.net" not in str(err.value)


def test_openrouter_settings_and_defaults():
    settings = live("openrouter", OPENROUTER)
    creds = settings.provider_settings
    assert settings.provider == "openrouter" and isinstance(creds, OpenAICompatibleSettings)
    assert creds.api_key.get_secret_value() == OPENROUTER["OPENROUTER_API_KEY"]
    assert creds.model == "openai/gpt-4o-mini"
    assert creds.base_url == "https://openrouter.ai/api/v1"
    assert dict(creds.headers) == {"X-Title": "Fleet Copilot"}
    assert settings.model_name == "openai/gpt-4o-mini"


def test_the_openrouter_endpoint_cannot_be_changed():
    settings = live(
        "openrouter", {**OPENROUTER, "OPENROUTER_BASE_URL": "https://elsewhere.example.net"}
    )
    assert settings.provider_settings.base_url == "https://openrouter.ai/api/v1"


def test_openrouter_app_name_is_configurable():
    settings = live("openrouter", {**OPENROUTER, "OPENROUTER_APP_NAME": " Fleet Lab "})
    assert dict(settings.provider_settings.headers) == {"X-Title": "Fleet Lab"}
    with pytest.raises(ConfigError) as err:
        live("openrouter", {**OPENROUTER, "OPENROUTER_APP_NAME": "two\nlines"})
    assert "OPENROUTER_APP_NAME" in str(err.value)


@pytest.mark.parametrize(
    ("provider", "good", "missing"),
    [
        ("openai", OPENAI, ["OPENAI_API_KEY", "OPENAI_MODEL"]),
        ("openrouter", OPENROUTER, ["OPENROUTER_API_KEY", "OPENROUTER_MODEL"]),
    ],
)
def test_a_provider_names_its_missing_variables_never_values(provider, good, missing):
    for name in missing:
        with pytest.raises(ConfigError) as err:
            live(provider, {**good, name: " "})
        text = str(err.value)
        assert name in text and provider in text
        assert all(value not in text for value in good.values())
    with pytest.raises(ConfigError) as err:
        live(provider, {})
    assert all(name in str(err.value) for name in missing)


def test_only_the_selected_providers_variables_are_checked():
    assert live("openai", {**OPENAI, **AZURE}).provider_settings.model == "gpt-4o-mini"
    assert live("openrouter", {**OPENROUTER, **OPENAI}).provider_settings.model == (
        "openai/gpt-4o-mini"
    )
    with pytest.raises(ConfigError) as err:
        live("openai", {**OPENROUTER, **AZURE})
    assert "AZURE_OPENAI" not in str(err.value) and "OPENROUTER" not in str(err.value)
    with pytest.raises(ConfigError) as err:
        ModelSettings.from_env({"FLEET_MODEL_MODE": "live", **OPENAI})  # provider azure
    assert "AZURE_OPENAI_API_KEY" in str(err.value) and "OPENAI_MODEL" not in str(err.value)


def test_replay_needs_no_provider_variables():
    for provider in ("azure", "openai", "openrouter"):
        settings = ModelSettings.from_env({"FLEET_MODEL_PROVIDER": provider})
        assert settings.provider == provider and settings.model_name is None
        assert settings.provider_settings is None


@pytest.mark.parametrize(
    ("provider", "env", "hidden"),
    [
        (
            "openai",
            {**OPENAI, "OPENAI_BASE_URL": "https://llm.example.net/v1"},
            ["sk-openai-not-real", "llm.example.net", "gpt-4o-mini"],
        ),
        ("openrouter", OPENROUTER, ["sk-or-not-real", "openai/gpt-4o-mini"]),
        ("azure", AZURE, ["sk-not-a-real-key", "example.openai.azure.com", "gpt-4o"]),
    ],
)
def test_keys_and_endpoints_stay_out_of_repr_and_str(provider, env, hidden):
    settings = live(provider, env)
    shown = repr(settings) + str(settings) + repr(settings.provider_settings)
    shown += str(settings.provider_settings)
    for text in hidden:
        assert text not in shown


def test_temperature_and_the_label_apply_to_every_provider():
    extra = {"FLEET_MODEL_TEMPERATURE": "0.3", "FLEET_MODEL_NAME": "label"}
    one = live("openai", {**OPENAI, **extra})
    two = live("openrouter", {**OPENROUTER, **extra})
    assert one.temperature == two.temperature == 0.3
    assert one.model_name == two.model_name == "label"


# ---- the reply model --------------------------------------------------------------------------------


def test_the_reply_model_and_temperature_default_to_the_main_ones():
    settings = ModelSettings.from_env(
        {"FLEET_MODEL_MODE": "live", **AZURE, "FLEET_MODEL_TEMPERATURE": "0.3"}
    )
    assert settings.reply_model is None and settings.reply_temperature is None
    assert settings.reply_model_name == "gpt-4o"
    assert settings.effective_reply_temperature == 0.3


def test_a_reply_model_and_temperature_are_read():
    env = {
        "FLEET_MODEL_MODE": "live",
        **AZURE,
        "FLEET_MODEL_TEMPERATURE": "0.3",
        "FLEET_REPLY_MODEL": " gpt-4o-mini ",
        "FLEET_REPLY_TEMPERATURE": "0.6",
    }
    settings = ModelSettings.from_env(env)
    assert settings.reply_model == "gpt-4o-mini" and settings.reply_model_name == "gpt-4o-mini"
    assert settings.temperature == 0.3 and settings.effective_reply_temperature == 0.6
    assert "gpt-4o-mini" not in repr(settings)


def test_a_blank_reply_model_means_the_main_model():
    env = {
        "FLEET_MODEL_MODE": "live",
        **AZURE,
        "FLEET_REPLY_MODEL": " ",
        "FLEET_REPLY_TEMPERATURE": "",
    }
    settings = ModelSettings.from_env(env)
    assert settings.reply_model is None and settings.reply_temperature is None


@pytest.mark.parametrize("raw", ["hot", "1.5", "-0.1", "nan", "7.25"])
def test_a_bad_reply_temperature_names_the_variable_but_not_the_value(raw):
    with pytest.raises(ConfigError) as err:
        ModelSettings.from_env({"FLEET_REPLY_TEMPERATURE": raw})
    assert "FLEET_REPLY_TEMPERATURE" in str(err.value) and raw not in str(err.value)


def test_a_reply_model_over_several_lines_is_refused_without_its_text():
    with pytest.raises(ConfigError) as err:
        ModelSettings.from_env({"FLEET_REPLY_MODEL": "line1\nsecretline2"})
    assert "FLEET_REPLY_MODEL" in str(err.value) and "secretline2" not in str(err.value)


def test_replay_reads_the_committed_evaluation_cassette_by_default():
    from fleet_copilot.config import REPO_ROOT

    settings = ModelSettings.from_env({})
    assert settings.mode == "replay"
    assert settings.cassette_path == REPO_ROOT / "evals" / "cassettes" / "eval.jsonl"


def test_recording_never_writes_into_the_committed_cassette_by_default():
    from fleet_copilot.config import REPO_ROOT

    settings = ModelSettings.from_env({"FLEET_CASSETTE_RECORD": "1"})
    assert settings.cassette_path == REPO_ROOT / "var" / "cassettes" / "live.jsonl"
