"""The provider-neutral gateway and the provider adapters: models build offline, errors sort the same way."""

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from model_support import FakeChatModel

from fleet_copilot.agent.chat_gateway import ChatGateway, ModelUnavailable
from fleet_copilot.agent.gateways import build_gateway, build_live_gateway
from fleet_copilot.agent.llm import ContentFiltered, Plan, Reply
from fleet_copilot.agent.providers import ADAPTERS, ProviderAdapter
from fleet_copilot.agent.tools import TOOLS
from fleet_copilot.config import ModelSettings

MESSAGES = [HumanMessage("hi")]
PLAN = Plan(intent="answer", summary="look it up")
KEY = "sk-not-a-real-key"

# One working environment per registered provider. A new adapter must add its entry here.
ENVS = {
    "azure": {
        "AZURE_OPENAI_API_KEY": KEY,
        "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
        "AZURE_OPENAI_API_VERSION": "2024-10-21",
        "AZURE_OPENAI_DEPLOYMENT": "gpt-4o",
    },
    "openai": {"OPENAI_API_KEY": KEY, "OPENAI_MODEL": "gpt-4o-mini"},
    "openrouter": {"OPENROUTER_API_KEY": KEY, "OPENROUTER_MODEL": "openai/gpt-4o-mini"},
}


def built(provider, extra=None, *, temperature=0.0, timeout=5.0):
    adapter = ADAPTERS[provider]
    settings = adapter.settings_from_env({**ENVS[provider], **(extra or {})})
    return adapter.build_model(settings, temperature=temperature, timeout=timeout)


def sent_headers(model: ChatOpenAI) -> dict[str, str]:
    """The headers the SDK would put on a request, without sending one."""
    from openai._models import FinalRequestOptions

    options = FinalRequestOptions.construct(method="post", url="/chat/completions", headers={})
    return {k.lower(): v for k, v in model.root_async_client._build_headers(options).items()}


# ---- the registry -----------------------------------------------------------------------------------


def test_the_registry_holds_the_three_providers():
    assert list(ADAPTERS) == ["azure", "openai", "openrouter"]


@pytest.mark.parametrize("name", list(ADAPTERS))
def test_every_adapter_satisfies_the_protocol_and_builds_offline(name):
    adapter = ADAPTERS[name]
    assert isinstance(adapter, ProviderAdapter) and adapter.name == name
    assert name in ENVS, "add a working environment for the new adapter to ENVS"
    assert adapter.required_vars and set(adapter.required_vars) <= set(ENVS[name])
    assert all(v.isupper() for v in adapter.required_vars + adapter.optional_vars)
    settings = adapter.settings_from_env(ENVS[name])
    assert settings.label and KEY not in repr(settings) and KEY not in str(settings)
    model = adapter.build_model(settings, temperature=0.25, timeout=7.0)
    assert model.temperature == 0.25 and model.request_timeout == 7.0 and model.max_retries == 0
    ChatGateway(model, adapter=adapter)  # builds the structured-output runnables, no network
    names = [t["function"]["name"] for t in model.bind_tools(TOOLS).kwargs["tools"]]
    assert names == [t.name for t in TOOLS]
    assert KEY not in repr(model)


@pytest.mark.parametrize("name", list(ADAPTERS))
def test_every_adapter_names_what_is_missing_and_never_a_value(name):
    adapter = ADAPTERS[name]
    for var in adapter.required_vars:
        env = {**ENVS[name], var: ""}
        with pytest.raises(Exception) as err:
            adapter.settings_from_env(env)
        assert var in str(err.value) and name in str(err.value)
        assert all(v not in str(err.value) for v in ENVS[name].values())


@pytest.mark.parametrize("name", list(ADAPTERS))
def test_every_adapter_sorts_errors_the_same_basic_way(name):
    adapter = ADAPTERS[name]
    request = httpx.Request("POST", "https://example.invalid/x")

    def status(cls, code, body=None):
        return cls("text", response=httpx.Response(code, request=request), body=body)

    assert adapter.classify_error(status(openai.RateLimitError, 429)) == "retry"
    assert adapter.classify_error(status(openai.InternalServerError, 503)) == "retry"
    assert adapter.classify_error(openai.APIConnectionError(request=request)) == "retry"
    assert adapter.classify_error(TimeoutError()) == "retry"
    assert adapter.classify_error(status(openai.AuthenticationError, 401)) == "auth"
    assert adapter.classify_error(status(openai.NotFoundError, 404)) == "unavailable"
    assert adapter.classify_error(ValueError("x")) == "unavailable"
    refusal = status(openai.BadRequestError, 400, {"code": "content_policy_violation"})
    assert adapter.classify_error(refusal) == "content_filtered"
    assert adapter.content_filter_category(refusal) == "content"
    assert adapter.content_filter_category(ValueError("x")) is None


# ---- Azure builder ----------------------------------------------------------------------------------


def test_the_azure_builder_makes_an_azure_model():
    model = built("azure", temperature=0.3, timeout=7.0)
    assert isinstance(model, AzureChatOpenAI)
    assert model.deployment_name == "gpt-4o" and model.openai_api_version == "2024-10-21"
    assert model.temperature == 0.3 and model.request_timeout == 7.0


def test_the_azure_endpoint_must_be_https():
    with pytest.raises(Exception) as err:
        ADAPTERS["azure"].settings_from_env(
            {**ENVS["azure"], "AZURE_OPENAI_ENDPOINT": "http://example.com"}
        )
    assert "AZURE_OPENAI_ENDPOINT" in str(err.value) and "example.com" not in str(err.value)


# ---- OpenAI and OpenRouter builders -----------------------------------------------------------------


def test_the_openai_builder_uses_the_model_and_the_default_endpoint():
    model = built("openai", temperature=0.2, timeout=7.0)
    assert type(model) is ChatOpenAI
    assert model.model_name == "gpt-4o-mini" and model.temperature == 0.2
    assert model.request_timeout == 7.0 and model.max_retries == 0
    assert str(model.root_client.base_url) == "https://api.openai.com/v1/"
    assert model.openai_api_key.get_secret_value() == KEY
    assert "x-title" not in sent_headers(model)


def test_the_openai_builder_takes_a_compatible_endpoint():
    model = built("openai", {"OPENAI_BASE_URL": "https://llm.example.net/v1"})
    assert str(model.root_client.base_url) == "https://llm.example.net/v1/"
    assert str(model.root_async_client.base_url) == "https://llm.example.net/v1/"


def test_the_openai_builder_ignores_an_endpoint_named_by_the_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://elsewhere.example.net/v1")
    monkeypatch.setenv("OPENAI_API_BASE", "https://elsewhere.example.net/v1")
    assert str(built("openai").root_client.base_url) == "https://api.openai.com/v1/"


def test_the_openrouter_builder_sets_the_endpoint_and_one_header():
    model = built("openrouter", temperature=0.1, timeout=9.0)
    assert type(model) is ChatOpenAI
    assert model.model_name == "openai/gpt-4o-mini" and model.temperature == 0.1
    assert model.request_timeout == 9.0 and model.max_retries == 0
    assert str(model.root_client.base_url) == "https://openrouter.ai/api/v1/"
    assert str(model.root_async_client.base_url) == "https://openrouter.ai/api/v1/"
    headers = sent_headers(model)
    assert headers["x-title"] == "Fleet Copilot"
    assert "http-referer" not in headers and "openai-organization" not in headers


def test_the_openrouter_app_name_is_the_title_header():
    model = built("openrouter", {"OPENROUTER_APP_NAME": "Fleet Lab"})
    assert sent_headers(model)["x-title"] == "Fleet Lab"


def test_an_openai_account_in_the_environment_is_not_sent_to_other_endpoints(monkeypatch):
    monkeypatch.setenv("OPENAI_ORG_ID", "org-from-the-environment")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "proj-from-the-environment")
    router = sent_headers(built("openrouter"))
    custom = sent_headers(built("openai", {"OPENAI_BASE_URL": "https://llm.example.net/v1"}))
    for headers in (router, custom):
        assert "openai-organization" not in headers and "openai-project" not in headers
    assert sent_headers(built("openai"))["openai-organization"] == "org-from-the-environment"


# ---- picking the gateway ----------------------------------------------------------------------------


def models(provider, **kw):
    adapter = ADAPTERS[provider]
    base = dict(
        mode="live",
        provider=provider,
        provider_settings=adapter.settings_from_env(ENVS[provider]),
        cassette_path="unused.jsonl",
        record=False,
        call_timeout_s=4.0,
        temperature=0.4,
    )
    return ModelSettings(**{**base, **kw})


@pytest.mark.parametrize(("provider", "cls"), [("azure", AzureChatOpenAI), ("openai", ChatOpenAI)])
def test_the_gateway_builds_the_selected_providers_model(provider, cls):
    gateway = build_live_gateway(models(provider))
    assert isinstance(gateway, ChatGateway) and isinstance(gateway._model, cls)
    assert gateway._model.temperature == 0.4 and gateway._model.request_timeout == 4.0
    assert gateway._adapter is ADAPTERS[provider]


def test_the_openrouter_gateway_points_at_openrouter():
    model = build_live_gateway(models("openrouter"))._model
    assert str(model.root_client.base_url) == "https://openrouter.ai/api/v1/"


@pytest.mark.parametrize("provider", list(ADAPTERS))
def test_live_mode_without_the_providers_settings_is_refused_by_name(provider):
    with pytest.raises(ValueError) as err:
        build_gateway(models(provider, provider_settings=None))
    assert provider in str(err.value)


# ---- error mapping through the gateway, for every adapter -------------------------------------------


def status_error(cls, status, message="provider text that must not leak", body=None):
    request = httpx.Request("POST", "https://example.invalid/x")
    return cls(message, response=httpx.Response(status, request=request), body=body)


@pytest.fixture(params=list(ADAPTERS))
def adapter(request):
    return ADAPTERS[request.param]


def gateway(model, adapter, **kw):
    slept: list[float] = []

    async def sleep(seconds):
        slept.append(seconds)

    return ChatGateway(model, adapter=adapter, sleep=sleep, **kw), slept


@pytest.mark.parametrize(
    "make",
    [
        lambda: status_error(openai.RateLimitError, 429),
        lambda: status_error(openai.InternalServerError, 502),
        lambda: openai.APIConnectionError(request=httpx.Request("POST", "https://example.invalid")),
        lambda: openai.APITimeoutError(request=httpx.Request("POST", "https://example.invalid")),
        lambda: TimeoutError(),
    ],
    ids=["rate-limit", "5xx", "connection", "api-timeout", "timeout"],
)
async def test_transient_errors_are_retried(make, adapter):
    model = FakeChatModel([make(), PLAN])
    gw, slept = gateway(model, adapter)
    assert await gw.plan(MESSAGES) == PLAN
    assert len(slept) == 1


@pytest.mark.parametrize(
    ("cls", "status"),
    [
        (openai.AuthenticationError, 401),
        (openai.PermissionDeniedError, 403),
        (openai.NotFoundError, 404),
        (openai.BadRequestError, 400),
        (openai.UnprocessableEntityError, 422),
    ],
)
async def test_other_client_errors_are_not_retried_and_report_a_type_name_only(
    cls, status, adapter
):
    model = FakeChatModel([status_error(cls, status), PLAN])
    gw, slept = gateway(model, adapter)
    with pytest.raises(ModelUnavailable) as err:
        await gw.plan(MESSAGES)
    assert err.value.reason == cls.__name__
    assert "provider text" not in str(err.value) and err.value.__cause__ is None
    assert slept == [] and len(model.script) == 1


POLICY_REFUSALS = {
    "openai code": status_error(
        openai.BadRequestError,
        400,
        "Your request was rejected by the safety system.",
        {"code": "content_policy_violation", "message": "x"},
    ),
    "openai message": status_error(
        openai.BadRequestError,
        400,
        "Invalid prompt: your prompt was flagged as potentially violating our content policy.",
        {"code": "invalid_prompt", "message": "x"},
    ),
    "openrouter moderation": status_error(
        openai.PermissionDeniedError,
        403,
        "Input flagged by moderation",
        {"code": 403, "message": "Input flagged by moderation", "metadata": {"reasons": ["x"]}},
    ),
    "plain content_filter code": status_error(
        openai.BadRequestError, 400, "refused", {"code": "content_filter", "message": "refused"}
    ),
}


@pytest.mark.parametrize("name", list(POLICY_REFUSALS))
async def test_a_content_policy_refusal_becomes_content_filtered_without_retry(name, adapter):
    model = FakeChatModel([POLICY_REFUSALS[name], PLAN])
    gw, slept = gateway(model, adapter)
    with pytest.raises(ContentFiltered) as err:
        await gw.plan(MESSAGES)
    assert err.value.category == "content"
    assert "provider text" not in str(err.value) and err.value.__cause__ is None
    assert slept == [] and len(model.script) == 1


def test_an_unrelated_bad_request_is_not_taken_for_a_policy_refusal(adapter):
    other = status_error(
        openai.BadRequestError,
        400,
        "This model's maximum context length is 8192 tokens.",
        {"code": "context_length_exceeded", "message": "x"},
    )
    assert adapter.content_filter_category(other) is None
    assert adapter.classify_error(other) == "unavailable"


def test_azure_adds_its_own_filter_rule_and_the_jailbreak_category():
    azure_filter = status_error(
        openai.BadRequestError,
        400,
        "provider text",
        {
            "code": "content_filter",
            "innererror": {
                "code": "ResponsibleAIPolicyViolation",
                "content_filter_result": {"jailbreak": {"detected": True, "filtered": True}},
            },
        },
    )
    assert ADAPTERS["azure"].content_filter_category(azure_filter) == "jailbreak"
    # the shared rules know "content_filter" but not Azure's jailbreak detail
    assert ADAPTERS["openai"].content_filter_category(azure_filter) == "content"
    inner_only = status_error(
        openai.BadRequestError,
        400,
        "x",
        {"innererror": {"code": "ResponsibleAIPolicyViolation", "content_filter_result": {}}},
    )
    assert ADAPTERS["azure"].content_filter_category(inner_only) == "content"
    assert ADAPTERS["openai"].content_filter_category(inner_only) is None


class FilteredRunnable:
    """Plays a call the provider cut off with finish_reason "content_filter"."""

    def __init__(self, result):
        self.result = result

    async def ainvoke(self, messages, config=None):
        message = AIMessage(content="", response_metadata={"finish_reason": "content_filter"})
        for handler in config["callbacks"]:
            handler.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]))
        return self.result


class FilteredModel:
    def __init__(self, result):
        self.result = result

    def with_structured_output(self, schema, method=None):
        return FilteredRunnable(self.result)

    def bind_tools(self, tools):
        return FilteredRunnable(self.result)


@pytest.mark.parametrize("result", [None, AIMessage(content="")])
async def test_a_content_filter_finish_reason_becomes_content_filtered(result, adapter):
    gw, slept = gateway(FilteredModel(result), adapter)
    with pytest.raises(ContentFiltered) as err:
        await gw.plan(MESSAGES) if result is None else await gw.act(MESSAGES)
    assert err.value.category == "content" and slept == []


async def test_a_model_that_returns_no_structured_plan_is_still_rejected(adapter):
    from fleet_copilot.agent.llm import PlanRejected

    gw, _ = gateway(FakeChatModel([None]), adapter)
    with pytest.raises(PlanRejected):
        await gw.plan(MESSAGES)


# ---- reasoning models and the reply model -----------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "o1",
        "o1-mini",
        "o3-mini",
        "O3-Mini",
        "o4-mini",
        "gpt-5",
        "gpt-5-mini",
        "GPT-5.1",
        "openai/o3-mini",
        "openai/gpt-5",
        "openrouter/OpenAI/O1-preview",
    ],
)
def test_reasoning_model_names_take_no_temperature(name):
    for adapter in ADAPTERS.values():
        assert adapter.supports_temperature(name) is False


@pytest.mark.parametrize(
    "name",
    [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "openai/gpt-4o-mini",
        "anthropic/claude-sonnet",
        "my-deployment",
    ],
)
def test_other_model_names_take_a_temperature(name):
    for adapter in ADAPTERS.values():
        assert adapter.supports_temperature(name) is True


@pytest.mark.parametrize("provider", list(ADAPTERS))
def test_the_temperature_is_left_out_for_a_reasoning_model_and_kept_otherwise(provider):
    adapter = ADAPTERS[provider]
    settings = adapter.settings_from_env(ENVS[provider])
    kept = adapter.build_model(adapter.with_model(settings, "gpt-4o"), temperature=0.4, timeout=5.0)
    left_out = adapter.build_model(
        adapter.with_model(settings, "o3-mini"), temperature=0.4, timeout=5.0
    )
    assert kept.temperature == 0.4
    assert left_out.temperature is None
    assert "temperature" not in left_out._default_params


@pytest.mark.parametrize("provider", list(ADAPTERS))
def test_with_model_changes_only_the_model(provider):
    adapter = ADAPTERS[provider]
    settings = adapter.settings_from_env(ENVS[provider])
    other = adapter.with_model(settings, "other-model")
    assert other.label == "other-model"
    assert other.api_key is settings.api_key or other.api_key == settings.api_key
    assert KEY not in repr(other)
    assert settings.label != "other-model"


def model_settings(provider, tmp_path, **kw):
    env = {**ENVS[provider], "FLEET_MODEL_MODE": "live", "FLEET_MODEL_PROVIDER": provider, **kw}
    return ModelSettings.from_env({**env, "FLEET_CASSETTE_PATH": str(tmp_path / "c.jsonl")})


@pytest.mark.parametrize("provider", list(ADAPTERS))
def test_the_reply_model_is_the_main_model_by_default(provider, tmp_path):
    gw = build_live_gateway(model_settings(provider, tmp_path))
    assert gw._explain is not None and gw._reply_model is gw._model


@pytest.mark.parametrize("provider", list(ADAPTERS))
def test_a_separate_reply_model_is_built_on_the_same_provider(provider, tmp_path):
    gw = build_live_gateway(model_settings(provider, tmp_path, FLEET_REPLY_MODEL="gpt-4.1"))
    assert gw._reply_model is not gw._model
    assert type(gw._reply_model) is type(gw._model)
    name = lambda m: getattr(m, "deployment_name", None) or m.model_name  # noqa: E731
    assert name(gw._reply_model) == "gpt-4.1" and name(gw._model) != "gpt-4.1"
    assert gw._reply_model.openai_api_key.get_secret_value() == KEY


def test_the_reply_temperature_alone_builds_a_second_model(tmp_path):
    gw = build_live_gateway(
        model_settings(
            "openai", tmp_path, FLEET_MODEL_TEMPERATURE="0.1", FLEET_REPLY_TEMPERATURE="0.7"
        )
    )
    assert gw._model.temperature == 0.1 and gw._reply_model.temperature == 0.7


def test_the_reply_temperature_defaults_to_the_main_one(tmp_path):
    gw = build_live_gateway(
        model_settings(
            "openai", tmp_path, FLEET_MODEL_TEMPERATURE="0.1", FLEET_REPLY_MODEL="gpt-4o"
        )
    )
    assert gw._model.temperature == 0.1 and gw._reply_model.temperature == 0.1


def test_a_reasoning_model_on_either_step_is_built_without_temperature(tmp_path):
    gw = build_live_gateway(
        model_settings(
            "openai",
            tmp_path,
            OPENAI_MODEL="o4-mini",
            FLEET_MODEL_TEMPERATURE="0.3",
            FLEET_REPLY_MODEL="gpt-4o-mini",
        )
    )
    assert gw._model.temperature is None and gw._reply_model.temperature == 0.3
    gw = build_live_gateway(
        model_settings(
            "openai", tmp_path, FLEET_MODEL_TEMPERATURE="0.3", FLEET_REPLY_MODEL="openai/o3-mini"
        )
    )
    assert gw._model.temperature == 0.3 and gw._reply_model.temperature is None


@pytest.mark.parametrize("method", ["explain", "reply"])
async def test_explain_uses_the_reply_model_and_plan_and_act_the_main_one(method):
    main = FakeChatModel([PLAN, AIMessage("looked"), PLAN])
    reply = FakeChatModel([Reply(text="Done.")])
    gw = ChatGateway(main, reply_model=reply)
    await gw.plan(MESSAGES)
    await gw.act(MESSAGES)
    result = await getattr(gw, method)(MESSAGES)
    assert result.text == "Done."
    assert [label for label, _ in main.calls] == ["Plan", "act"]
    assert [label for label, _ in reply.calls] == ["Reply"]
    assert reply.bound_tools is None


async def test_explain_uses_the_main_model_without_a_reply_model():
    main = FakeChatModel([Reply(text="Done.")])
    await ChatGateway(main).explain(MESSAGES)
    assert [label for label, _ in main.calls] == ["Reply"]


async def test_the_reply_model_gets_the_same_retries_and_error_mapping(adapter):
    request = httpx.Request("POST", "https://example.invalid")
    main = FakeChatModel([])
    reply = FakeChatModel([openai.APITimeoutError(request=request), Reply(text="Done.")])
    gw, slept = gateway(main, adapter, reply_model=reply)
    assert (await gw.explain(MESSAGES)).text == "Done."
    assert len(slept) == 1 and main.calls == []

    refused = status_error(openai.BadRequestError, 400, body={"code": "content_filter"})
    gw, _ = gateway(main, adapter, reply_model=FakeChatModel([refused]))
    with pytest.raises(ContentFiltered):
        await gw.explain(MESSAGES)

    gw, _ = gateway(main, adapter, reply_model=FakeChatModel([ValueError("secret text")]))
    with pytest.raises(ModelUnavailable) as err:
        await gw.explain(MESSAGES)
    assert err.value.reason == "ValueError" and "secret" not in str(err.value)
