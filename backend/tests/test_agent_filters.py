"""A filter the model adds on its own must come from the user's words. `find_devices(form_factor="laptop",
os_name="macOS")` for "each laptop battery" turned "3 of 9 laptops" into "the only laptop"; the unnamed OS filter
goes before the tool runs, and the wider answer is the safe one."""

from __future__ import annotations

import pytest
from graph_support import ACME_ADMIN, answer_plan, explanation, make_service

from fleet_copilot.agent.filters import grounded_args, ungrounded_filters
from fleet_copilot.agent.llm import ScriptedLLM, final_message, tool_calls_message


@pytest.mark.parametrize(
    ("args", "sources", "dropped"),
    [
        ({"form_factor": "laptop"}, "which laptops are low on disk?", []),
        ({"form_factor": "laptop"}, "which computers are low on disk?", ["form_factor"]),
        ({"form_factor": "laptop"}, "any notebooks with a worn battery", []),
        ({"form_factor": "laptop"}, "my MacBook Pro is slow", []),
        ({"form_factor": "laptop"}, "wich laptps r out of disk", ["form_factor"]),
        ({"form_factor": "desktop"}, "which workstations are old", []),
        ({"form_factor": "desktop"}, "the Mac mini in the lab", []),
        ({"form_factor": "desktop"}, "which laptops are old", ["form_factor"]),
        ({"os_name": "macOS"}, "which Macs need patching", []),
        ({"os_name": "macOS"}, "anything on Apple hardware", []),
        (
            {"os_name": "macOS"},
            "Estimate when each laptop battery will reach the threshold",
            ["os_name"],
        ),
        ({"os_name": "macOS"}, "Which machines are low on disk?", ["os_name"]),
        (
            {"os_name": "macOS"},
            "wich machnes r failing the screenlock compliance check",
            ["os_name"],
        ),
        ({"os_name": "macOS"}, "Which Macs are low on disk?", []),
        ({"os_name": "macOS"}, "any macbooks offline", []),
        ({"os_name": "macOS"}, "what is wrong with acme-macbook-4", []),
        ({"os_name": "macOS"}, "who runs mac os 14", []),
        ({"model_name": "MacBook Pro"}, "how are the macbooks doing", []),
        ({"model_name": "ThinkPad X1 Carbon"}, "any thinkpads offline", []),
        ({"os_name": "Windows"}, "which win11 machines are out of date", []),
        ({"os_name": "Windows"}, "which Microsoft devices fail the check", []),
        ({"os_name": "Windows"}, "which machines are out of date", ["os_name"]),
        ({"model_name": "MacBook Pro"}, "how is my macbook doing", []),
        ({"model_name": "MacBook Pro"}, "how are the pro machines doing", ["model_name"]),
        ({"model_name": "Mac mini"}, "any mini devices offline", []),
        ({"model_name": "Dell XPS 15"}, "is the xps ok", []),
        ({"model_name": "Dell XPS 15"}, "how are the dell machines", []),
        ({"model_name": "Dell XPS 15"}, "how are the laptops", ["model_name"]),
    ],
)
def test_a_filter_needs_the_user_to_have_named_it(args, sources, dropped):
    assert ungrounded_filters(args, sources) == dropped


def test_every_filter_is_checked_and_reported_in_a_fixed_order():
    args = {"model_name": "Dell XPS 15", "os_name": "Windows", "form_factor": "laptop"}
    assert ungrounded_filters(args, "how is everything") == ["form_factor", "os_name", "model_name"]


def test_a_note_grounds_a_filter_and_a_forgotten_note_does_not():
    question = "which computers fail the check?"
    note = "I only care about laptops"
    assert ungrounded_filters({"form_factor": "laptop"}, f"{question}\n{note}") == []
    assert ungrounded_filters({"form_factor": "laptop"}, question) == ["form_factor"]


def test_grounded_args_removes_only_the_ungrounded_filters():
    args = {
        "form_factor": "laptop",
        "os_name": "macOS",
        "hostname": "mac-1",
        "employee_id": "emp-1",
        "severity": "high",
        "device_ids": ["A1"],
    }
    assert grounded_args(args, "which laptops?") == {
        "form_factor": "laptop",
        "hostname": "mac-1",
        "employee_id": "emp-1",
        "severity": "high",
        "device_ids": ["A1"],
    }
    assert grounded_args({"metric": "disk"}, "") == {"metric": "disk"}


async def test_an_os_filter_the_question_never_named_is_dropped_before_the_tool_runs(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan(tools=["find_devices", "trend"])],
        acts=[
            tool_calls_message(
                ("find_devices", {"form_factor": "laptop", "os_name": "macOS"}),
                ("trend", {"metric": "battery"}),
            ),
            final_message(),
        ],
        explains=[explanation()],
    )
    result = await make_service(real_db_path, llm).run_turn(
        ACME_ADMIN,
        "c1",
        "Estimate when each laptop battery's capacity will reach our service threshold",
    )
    runs = [e for e in result.trace if e.kind == "tool_call" and e.tool == "find_devices"]
    executed = [e.data["args"] for e in runs if "args" in e.data]
    assert executed == [{"form_factor": "laptop"}]
    drops = [e.data for e in runs if "dropped_filters" in e.data]
    assert drops == [{"dropped_filters": ["os_name"]}]
    # The drop is traced before the call it changed.
    assert runs.index(next(e for e in runs if "dropped_filters" in e.data)) < runs.index(
        next(e for e in runs if "args" in e.data)
    )
