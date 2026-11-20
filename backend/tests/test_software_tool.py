import json

import pytest
from agent_support import ACME, GLOBEX, INITECH, context_for, run
from test_agent_tools import _through_toolnode

from fleet_copilot.agent.records import FindingRecord, ToolRecord
from fleet_copilot.agent.tools import DEVICE_FILTER_TOOLS, TOOLS, ToolInputError, execute
from fleet_copilot.agent.trace import trace_args
from fleet_copilot.config import REPO_ROOT

GOLDEN = json.loads((REPO_ROOT / "evals" / "golden" / "golden.json").read_text(encoding="utf-8"))
TENANTS = [ACME, GLOBEX, INITECH]


def call(real_db_path, real_conn, args, tenant=ACME):
    return run(real_db_path, real_conn, "software_inventory", args, tenant)


def golden_pairs(tenant, window="30"):
    return sorted(
        (i["device_id"], i["app"])
        for i in GOLDEN["tenants"][tenant]["software"]["new_installs"][window]
    )


def test_the_tool_is_registered_and_takes_device_filters():
    assert "software_inventory" in {t.name for t in TOOLS}
    assert "software_inventory" in DEVICE_FILTER_TOOLS


def test_the_schema_has_enumerated_views_a_patterned_app_name_and_no_tenant_argument():
    tool = next(t for t in TOOLS if t.name == "software_inventory")
    schema = tool.tool_call_schema.model_json_schema()
    assert schema["required"] == ["view"]
    assert schema["properties"]["view"]["enum"] == ["current", "new_installs"]
    pattern = schema["properties"]["app_name"]["anyOf"][0]["pattern"]
    assert pattern.startswith("^[A-Za-z0-9]")
    window = schema["properties"]["window_days"]["anyOf"][0]
    assert (window["minimum"], window["maximum"]) == (1, 60)
    text = json.dumps(schema).lower()
    assert "runtime" not in text and "company" not in text and "tenant" not in text


@pytest.mark.parametrize("tenant", TENANTS)
def test_new_installs_are_the_golden_pairs_for_every_tenant(real_db_path, real_conn, tenant):
    record = call(real_db_path, real_conn, {"view": "new_installs"}, tenant)
    assert record.error is None
    assert sorted((f.device_id, f.values["app"]) for f in record.findings) == golden_pairs(tenant)
    assert all(ref.startswith(f"{tenant}/") for f in record.findings for ref in f.refs)
    assert "common set" in record.rule


def test_the_window_argument_reaches_the_query(real_db_path, real_conn):
    wide = call(real_db_path, real_conn, {"view": "new_installs", "window_days": 60})
    narrow = call(real_db_path, real_conn, {"view": "new_installs", "window_days": 14})
    assert sorted((f.device_id, f.values["app"]) for f in narrow.findings) == golden_pairs(
        ACME, "14"
    )
    assert len(wide.findings) == 2 and "60 days" in wide.rule


def test_current_view_with_an_app_lists_its_holders_case_insensitively(real_db_path, real_conn):
    for spelling in ("uTorrent", "utorrent", " UTORRENT "):
        record = call(real_db_path, real_conn, {"view": "current", "app_name": spelling})
        assert record.device_ids == ["1LYSSFD074BB"], spelling
        assert record.findings[0].values["name"] == "uTorrent"  # the data's own spelling
    assert call(real_db_path, real_conn, {"view": "current", "app_name": "Slack"}).population == 10


def test_current_view_without_an_app_is_the_inventory(real_db_path, real_conn):
    record = call(real_db_path, real_conn, {"view": "current"})
    assert len(record.findings) == 10 and record.population == 10
    assert "Google Chrome 126.0" in record.rule


def test_new_installs_can_be_narrowed_to_one_app(real_db_path, real_conn):
    record = call(real_db_path, real_conn, {"view": "new_installs", "app_name": "cleanmymac x"})
    assert record.device_ids == ["7VP16KHV88LM"]


def test_an_unknown_app_is_an_input_error_that_lists_only_this_companys_apps(
    real_db_path, real_conn
):
    with pytest.raises(ToolInputError) as err:
        call(real_db_path, real_conn, {"view": "current", "app_name": "Nonexistent App"})
    text = str(err.value)
    assert "Nonexistent" not in text  # the input is never echoed
    for own in ("CleanMyMac X", "uTorrent", "Google Chrome", "Zoom"):
        assert own in text
    assert "TeamViewer" not in text  # an app only Initech has


def test_an_app_only_another_company_has_reads_exactly_like_an_unknown_one(real_db_path, real_conn):
    messages = []
    for name in ("TeamViewer", "Nonexistent App"):
        for view in ("current", "new_installs"):
            with pytest.raises(ToolInputError) as err:
                call(real_db_path, real_conn, {"view": view, "app_name": name})
            messages.append(str(err.value))
    assert len(set(messages)) == 1
    # and the owner of that app can ask about it
    owner = call(real_db_path, real_conn, {"view": "current", "app_name": "TeamViewer"}, INITECH)
    assert owner.device_ids == ["DARFCP0BQM2G"]


def test_every_tenant_only_ever_names_its_own_apps(real_db_path, real_conn):
    for tenant in TENANTS:
        mine = set(GOLDEN["tenants"][tenant]["software"]["holders"])
        others = (
            set().union(*(set(GOLDEN["tenants"][t]["software"]["holders"]) for t in TENANTS)) - mine
        )
        for app in others:
            with pytest.raises(ToolInputError):
                call(real_db_path, real_conn, {"view": "current", "app_name": app}, tenant)


def test_device_ids_narrow_the_view_and_foreign_ids_are_not_found(real_db_path, real_conn):
    one = call(real_db_path, real_conn, {"view": "new_installs", "device_ids": ["7VP16KHV88LM"]})
    assert one.device_ids == ["7VP16KHV88LM"] and one.population == 1
    foreign = call(
        real_db_path, real_conn, {"view": "new_installs", "device_ids": ["Q1AFVLH3YV33"]}
    )
    unknown = call(real_db_path, real_conn, {"view": "new_installs", "device_ids": ["NOPE"]})
    assert foreign.error == unknown.error == "not_found"
    assert foreign.llm_view() == unknown.llm_view()


def test_a_missing_or_unknown_view_is_an_input_error(real_db_path, real_conn):
    for args in ({}, {"view": "everything"}):
        with pytest.raises(ToolInputError, match="view"):
            execute("software_inventory", args, context_for(real_db_path, real_conn))


def test_the_model_sees_counts_and_ids_not_versions_or_publishers(real_db_path, real_conn):
    view = json.loads(call(real_db_path, real_conn, {"view": "new_installs"}).llm_view())
    assert view["match_count"] == 2 and view["device_ids"] == ["1LYSSFD074BB", "7VP16KHV88LM"]
    text = json.dumps(view)
    assert "Rainberry" not in text and "3.6" not in text and "Globex" not in text


def test_a_device_with_two_findings_is_listed_once_to_the_model():
    findings = [
        FindingRecord(device_id="D1", hostname="h", values={}, refs=["r1"]),
        FindingRecord(device_id="D1", hostname="h", values={}, refs=["r2"]),
        FindingRecord(device_id="D2", hostname="h2", values={}, refs=["r3"]),
    ]
    record = ToolRecord(tool="software_inventory", findings=findings)
    assert record.device_ids == ["D1", "D2"]
    assert json.loads(record.llm_view())["match_count"] == 3


# ---- through a real ToolNode ----------------------------------------------------------------------------


def test_bad_inputs_get_the_fixed_format_error_without_echoing_the_value(real_db_path, real_conn):
    ctx = context_for(real_db_path, real_conn)
    for args in (
        {"view": "SECRETVIEW"},
        {"view": "current", "app_name": "SECRET;drop"},
        {"view": "current", "app_name": "x" * 70},
        {"view": "current", "app_name": ""},
        {"view": "new_installs", "window_days": 999},
    ):
        message = _through_toolnode({"name": "software_inventory", "args": args, "id": "c1"}, ctx)
        assert message.status == "error" and message.artifact is None, args
        content = str(message.content)
        assert content.startswith("Invalid tool input") and "SECRET" not in content, args


def test_an_unknown_app_reaches_the_model_as_an_input_error_it_can_fix(real_db_path, real_conn):
    ctx = context_for(real_db_path, real_conn)
    args = {"view": "current", "app_name": "Nope"}
    message = _through_toolnode({"name": "software_inventory", "args": args, "id": "c1"}, ctx)
    assert message.status == "error" and message.artifact is None
    assert str(message.content).startswith("Invalid tool input: app_name is not an app")
    assert "uTorrent" in str(message.content)


def test_a_good_call_returns_a_typed_artifact(real_db_path, real_conn):
    ctx = context_for(real_db_path, real_conn)
    args = {"view": "new_installs"}
    message = _through_toolnode({"name": "software_inventory", "args": args, "id": "c1"}, ctx)
    assert message.status == "success"
    assert ToolRecord.model_validate(message.artifact).device_ids == [
        "1LYSSFD074BB",
        "7VP16KHV88LM",
    ]
    assert json.loads(message.content)["match_count"] == 2


# ---- traces ---------------------------------------------------------------------------------------------


def test_a_trace_shows_the_view_and_the_window_but_never_the_app_name():
    args = {
        "view": "new_installs",
        "app_name": "uTorrent",
        "window_days": 14,
        "device_ids": ["A", "B"],
    }
    assert trace_args(args) == {
        "view": "new_installs",
        "app_name": "applied",
        "window_days": 14,
        "device_ids": 2,
    }
    assert trace_args({"view": "anything the model made up"}) == {"view": "applied"}
    assert trace_args({"view": "current", "app_name": None}) == {"view": "current"}
