import asyncio

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from model_support import FakeChatModel

from fleet_copilot.agent.chat_gateway import ChatGateway, ModelUnavailable
from fleet_copilot.agent.llm import Explanation, Plan, PlanRejected
from fleet_copilot.agent.tools import TOOLS

MESSAGES = [HumanMessage("hi")]
PLAN = Plan(intent="answer", summary="look it up")
EXPLANATION = Explanation(headline="Two devices are low")


def rate_limited() -> openai.RateLimitError:
    request = httpx.Request("POST", "https://example.invalid/x")
    return openai.RateLimitError(
        "secret body text", response=httpx.Response(429, request=request), body=None
    )


def auth_failed() -> openai.AuthenticationError:
    request = httpx.Request("POST", "https://example.invalid/x")
    return openai.AuthenticationError(
        "bad key", response=httpx.Response(401, request=request), body=None
    )


def gateway(model, **kw):
    slept: list[float] = []

    async def sleep(seconds):
        slept.append(seconds)

    return ChatGateway(model, sleep=sleep, **kw), slept


async def test_plan_and_explain_use_function_calling_structured_output():
    model = FakeChatModel([PLAN, EXPLANATION])
    gw, _ = gateway(model)
    assert await gw.plan(MESSAGES) == PLAN
    assert await gw.explain(MESSAGES) == EXPLANATION
    assert {(s.__name__, m) for s, m in model.schemas} == {
        ("Plan", "function_calling"),
        ("Explanation", "function_calling"),
    }


async def test_act_binds_the_agent_tools_and_returns_the_ai_message():
    message = AIMessage(
        content="",
        tool_calls=[{"name": "low_disk_devices", "args": {}, "id": "1", "type": "tool_call"}],
    )
    model = FakeChatModel([message])
    gw, _ = gateway(model)
    assert await gw.act(MESSAGES) is message
    assert model.bound_tools is TOOLS


async def test_act_can_bind_only_the_named_tools_and_binds_each_set_once():
    message = AIMessage(content="")
    model = FakeChatModel([message])
    gw, _ = gateway(model)
    await gw.act(MESSAGES, tools=["trend", "find_devices"])
    assert [t.name for t in model.bound_tools] == ["trend", "find_devices"]
    assert gw._act_for(["find_devices", "trend"]) is gw._act_for(["trend", "find_devices"])
    assert gw._act_for(None) is gw._act  # no limit: the full set bound at start


async def test_a_rate_limit_is_retried_with_backoff_then_succeeds():
    model = FakeChatModel([rate_limited(), rate_limited(), PLAN])
    gw, slept = gateway(model, backoff_s=(1.0, 3.0))
    assert await gw.plan(MESSAGES) == PLAN
    assert slept == [1.0, 3.0]


async def test_gives_up_after_the_attempt_limit_with_only_a_type_name():
    model = FakeChatModel([rate_limited(), rate_limited(), rate_limited()])
    gw, slept = gateway(model, attempts=3)
    with pytest.raises(ModelUnavailable) as err:
        await gw.plan(MESSAGES)
    assert err.value.reason == "RateLimitError"
    assert "secret body text" not in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__
    assert len(slept) == 2  # no pause after the last attempt


async def test_an_authentication_error_is_not_retried():
    model = FakeChatModel([auth_failed(), PLAN])
    gw, slept = gateway(model)
    with pytest.raises(ModelUnavailable) as err:
        await gw.plan(MESSAGES)
    assert err.value.reason == "AuthenticationError" and "bad key" not in str(err.value)
    assert slept == [] and len(model.script) == 1


async def test_a_hung_call_times_out_and_is_retried():
    async def hang():
        await asyncio.sleep(10)

    model = FakeChatModel([hang, PLAN])
    gw, slept = gateway(model, call_timeout_s=0.05)
    assert await gw.plan(MESSAGES) == PLAN
    assert len(slept) == 1


async def test_a_wrong_result_type_is_refused():
    model = FakeChatModel([{"intent": "answer"}])
    gw, _ = gateway(model)
    with pytest.raises(PlanRejected):
        await gw.plan(MESSAGES)
    gw, _ = gateway(FakeChatModel([{"headline": "x"}]))
    with pytest.raises(ModelUnavailable) as err:
        await gw.explain(MESSAGES)
    assert err.value.reason == "bad_structured_output"


def content_filtered(jailbreak: bool) -> openai.BadRequestError:
    request = httpx.Request("POST", "https://example.invalid/x")
    flags = {
        "jailbreak": {"detected": jailbreak, "filtered": jailbreak},
        "hate": {"filtered": not jailbreak},
    }
    body = {
        "code": "content_filter",
        "message": "provider text that must not leak",
        "innererror": {"code": "ResponsibleAIPolicyViolation", "content_filter_result": flags},
    }
    return openai.BadRequestError(
        "provider text that must not leak", response=httpx.Response(400, request=request), body=body
    )


@pytest.mark.parametrize(("jailbreak", "category"), [(True, "jailbreak"), (False, "content")])
async def test_a_provider_content_filter_becomes_content_filtered_without_retry(
    jailbreak, category
):
    from fleet_copilot.agent.llm import ContentFiltered

    model = FakeChatModel([content_filtered(jailbreak), PLAN])
    gw, slept = gateway(model)
    with pytest.raises(ContentFiltered) as err:
        await gw.plan(MESSAGES)
    assert err.value.category == category
    assert "provider text" not in str(err.value) and err.value.__cause__ is None
    assert slept == [] and len(model.script) == 1


async def test_other_bad_requests_are_not_mistaken_for_the_content_filter():
    request = httpx.Request("POST", "https://example.invalid/x")
    plain = openai.BadRequestError(
        "bad", response=httpx.Response(400, request=request), body={"code": "invalid_value"}
    )
    gw, _ = gateway(FakeChatModel([plain]))
    with pytest.raises(ModelUnavailable) as err:
        await gw.plan(MESSAGES)
    assert err.value.reason == "BadRequestError"


# ---- token usage -------------------------------------------------------------------------------------


def llm_result(*usages):
    from langchain_core.outputs import ChatGeneration, LLMResult

    return LLMResult(
        generations=[
            [
                ChatGeneration(message=AIMessage(content="", usage_metadata=usage))
                for usage in usages
            ]
        ]
    )


def test_the_collector_adds_up_the_usage_metadata_of_every_generation():
    from fleet_copilot.agent.usage import UsageCollector

    collector = UsageCollector()
    assert collector.usage() is None
    collector.on_llm_end(
        llm_result(
            {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
            {"input_tokens": 5, "output_tokens": 1, "total_tokens": 6},
        )
    )
    assert collector.usage() == {"prompt": 105, "completion": 21}


def test_the_collector_falls_back_to_the_provider_token_usage_and_ignores_junk():
    from langchain_core.outputs import LLMResult

    from fleet_copilot.agent.usage import UsageCollector

    collector = UsageCollector()
    collector.on_llm_end(
        LLMResult(
            generations=[[]],
            llm_output={"token_usage": {"prompt_tokens": 40, "completion_tokens": 8}},
        )
    )
    assert collector.usage() == {"prompt": 40, "completion": 8}
    nothing = UsageCollector()
    nothing.on_llm_end(LLMResult(generations=[[]], llm_output={"token_usage": "lots"}))
    assert nothing.usage() is None


async def test_every_call_carries_a_collector_and_publishes_what_it_saw():
    from fleet_copilot.agent import usage

    model = FakeChatModel([])

    async def respond():
        collector = model.configs[-1]["callbacks"][0]
        collector.on_llm_end(
            llm_result({"input_tokens": 60, "output_tokens": 9, "total_tokens": 69})
        )
        return PLAN

    model.script.append(respond)
    gw, _ = gateway(model)
    usage.CURRENT.set(None)
    assert await gw.plan(MESSAGES) == PLAN
    assert usage.CURRENT.get() == {"prompt": 60, "completion": 9}


async def test_a_provider_that_reports_nothing_publishes_none():
    from fleet_copilot.agent import usage

    usage.CURRENT.set({"prompt": 1, "completion": 1})
    gw, _ = gateway(FakeChatModel([PLAN]))
    await gw.plan(MESSAGES)
    assert usage.CURRENT.get() is None


async def test_only_the_attempt_that_succeeded_is_published_after_a_retry():
    from fleet_copilot.agent import usage

    model = FakeChatModel([])

    async def respond():
        model.configs[-1]["callbacks"][0].on_llm_end(
            llm_result({"input_tokens": 11, "output_tokens": 2, "total_tokens": 13})
        )
        return PLAN

    model.script.extend([rate_limited(), respond])
    gw, _ = gateway(model)
    await gw.plan(MESSAGES)
    assert usage.CURRENT.get() == {"prompt": 11, "completion": 2}
