# NOTE: no `from __future__ import annotations` in this module. LangChain inspects the tool signatures,
# including the injected `ToolRuntime` annotation, and Python 3.14 already evaluates annotations lazily.
from collections.abc import Callable
from typing import Annotated, Any, Literal

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.prebuilt.tool_node import ToolInvocationError
from pydantic import Field

from fleet_copilot import policy
from fleet_copilot.agent.context import AgentContext
from fleet_copilot.agent.records import ToolRecord, redact_args
from fleet_copilot.data.loader import connect
from fleet_copilot.fleet import inventory
from fleet_copilot.fleet import queries as q
from fleet_copilot.fleet.queries import QueryResult
from fleet_copilot.repository import FleetRepository, NotFound, TenantContext

CheckId = Literal["disk_encryption", "screen_lock", "os_up_to_date"]
Severity = Literal["low", "medium", "high", "critical"]
FormFactor = Literal["laptop", "desktop"]
OsName = Literal["macOS", "Windows"]
Window = Annotated[int, Field(ge=1, le=policy.MAX_WINDOW_DAYS)]
Percent = Annotated[float, Field(gt=0, le=100)]
OsVersion = Annotated[str, Field(pattern=r"^\d{1,3}(\.\d{1,3}){0,2}$")]
# Free-text filters are model-supplied: constrain the characters so odd input is rejected at the schema.
Hostname = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")]
ModelName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}$")]
EmployeeId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")]
DeviceIds = Annotated[list[Annotated[str, Field(max_length=40)]], Field(max_length=100)] | None


# Text the retry wrapper looks for: errors the model caused are never retried, only transient failures are.
INVALID_INPUT_PREFIX = "Invalid tool input"


class ToolInputError(ValueError):
    """Bad tool input that the model can fix and retry. The message is written by us, not by input."""


# ---- runners: (repo, as_of, args) -> QueryResult -----------------------------------------


def _only(args: dict[str, Any]):
    return args.get("device_ids")


def _run_low_disk(repo: FleetRepository, as_of, a: dict[str, Any]) -> QueryResult:
    return q.low_disk(
        repo, as_of, a.get("threshold_pct", policy.LOW_DISK_FREE_PCT), only_devices=_only(a)
    )


def _run_macos(repo: FleetRepository, as_of, a: dict[str, Any]) -> QueryResult:
    return q.macos_below(repo, as_of, a.get("older_than", "15"), only_devices=_only(a))


def _run_compliance(repo: FleetRepository, as_of, a: dict[str, Any]) -> QueryResult:
    return q.compliance_failures(
        repo,
        as_of,
        check_id=a.get("check_id"),
        severity=a.get("severity"),
        form_factor=a.get("form_factor"),
        only_devices=_only(a),
    )


def _run_memory_now(repo: FleetRepository, as_of, a: dict[str, Any]) -> QueryResult:
    return q.memory_state(
        repo, as_of, a.get("threshold_pct", policy.MEMORY_USED_PCT), only_devices=_only(a)
    )


def _run_trend(repo: FleetRepository, as_of, a: dict[str, Any]) -> QueryResult:
    metric, days, threshold, only = (
        a["metric"],
        a.get("window_days"),
        a.get("threshold_pct"),
        _only(a),
    )
    if metric == "memory":
        return q.memory_trend(
            repo,
            as_of,
            days or policy.TREND_WINDOW_DAYS,
            threshold if threshold is not None else policy.MEMORY_USED_PCT,
            only_devices=only,
        )
    if metric == "disk":
        return q.disk_trend(
            repo,
            as_of,
            days or policy.TREND_WINDOW_DAYS,
            threshold if threshold is not None else policy.LOW_DISK_FREE_PCT,
            only_devices=only,
        )
    if metric == "compliance":
        if not a.get("check_id"):
            raise ToolInputError("check_id is required for a compliance trend")
        return q.compliance_trend(
            repo,
            as_of,
            check_id=a["check_id"],
            window_days=days or policy.COMPLIANCE_WINDOW_DAYS,
            only_devices=only,
        )
    if metric == "battery":
        return q.battery_overview(
            repo, as_of, days or policy.BATTERY_WINDOW_DAYS, only_devices=only
        )
    raise ToolInputError("unknown trend metric")


def _run_find(repo: FleetRepository, as_of, a: dict[str, Any]) -> QueryResult:
    # `group_by` is deliberately not passed to the query: the answer composer produces the grouped
    # counts from the recorded `group_by` argument (see ToolRecord.args).
    return inventory.find_devices(
        repo,
        as_of,
        os_name=a.get("os_name"),
        model_name=a.get("model_name"),
        form_factor=a.get("form_factor"),
        hostname=a.get("hostname"),
        employee_id=a.get("employee_id"),
        only_devices=_only(a),
    )


def _run_history(repo: FleetRepository, as_of, a: dict[str, Any]) -> QueryResult:
    if a.get("metric") == "compliance" and not a.get("check_id"):
        raise ToolInputError("check_id is required for a compliance history")
    return inventory.device_history(
        repo,
        as_of,
        device_id=a["device_id"],
        metric=a["metric"],
        window_days=a.get("window_days") or policy.TREND_WINDOW_DAYS,
        check_id=a.get("check_id"),
    )


_RUNNERS: dict[str, Callable[[FleetRepository, Any, dict[str, Any]], QueryResult]] = {
    "low_disk_devices": _run_low_disk,
    "macos_version_check": _run_macos,
    "compliance_failures_now": _run_compliance,
    "memory_pressure_now": _run_memory_now,
    "trend": _run_trend,
    "find_devices": _run_find,
    "device_history": _run_history,
}


def execute(tool_name: str, args: dict[str, Any], ctx: AgentContext) -> ToolRecord:
    """Run one tool as the context's tenant on a fresh READ-ONLY connection.

    Unknown or foreign devices produce the one uniform `not_found` record; this function
    never raises `NotFound`.
    """
    runner = _RUNNERS[tool_name]
    recorded = redact_args(args)
    conn = connect(ctx.db_path, read_only=True)
    try:
        repo = FleetRepository(conn, TenantContext(ctx.company_id, ctx.actor_id))
        requested = args.get("device_ids")
        unresolved = 0
        if requested is not None:
            known = {d.device_id for d in repo.list_devices()}
            if not known & set(requested):
                return ToolRecord(tool=tool_name, args=recorded, error="not_found")
            unresolved = len(set(requested) - known)
        try:
            result = runner(repo, ctx.as_of, args)
        except NotFound:
            return ToolRecord(tool=tool_name, args=recorded, error="not_found")
        record = ToolRecord.from_query(tool_name, recorded, result)
        record.unresolved_ids = unresolved
        return record
    finally:
        conn.close()


def _respond(
    name: str, args: dict[str, Any], runtime: ToolRuntime[AgentContext]
) -> tuple[str, dict[str, Any]]:
    record = execute(name, args, runtime.context)
    return record.llm_view(), record.model_dump(mode="json")


# ---- the seven tools (model-facing): enumerated, bounded, no tenant arguments -------------


@tool(response_format="content_and_artifact")
def low_disk_devices(
    runtime: ToolRuntime[AgentContext],
    threshold_pct: Percent = policy.LOW_DISK_FREE_PCT,
    device_ids: DeviceIds = None,
) -> tuple[str, dict[str, Any]]:
    """Devices whose primary volume has less than `threshold_pct` percent free space at their latest snapshot.
    Pass `device_ids` to narrow the check to devices from an earlier answer."""
    return _respond(
        "low_disk_devices", {"threshold_pct": threshold_pct, "device_ids": device_ids}, runtime
    )


@tool(response_format="content_and_artifact")
def macos_version_check(
    runtime: ToolRuntime[AgentContext],
    older_than: OsVersion = "15",
    device_ids: DeviceIds = None,
) -> tuple[str, dict[str, Any]]:
    """macOS devices running a version older than `older_than` (for example "15"). Windows devices are out of
    scope and reported separately; unparseable versions are reported as unknown."""
    return _respond(
        "macos_version_check", {"older_than": older_than, "device_ids": device_ids}, runtime
    )


@tool(response_format="content_and_artifact")
def compliance_failures_now(
    runtime: ToolRuntime[AgentContext],
    check_id: CheckId | None = None,
    severity: Severity | None = None,
    form_factor: FormFactor | None = None,
    device_ids: DeviceIds = None,
) -> tuple[str, dict[str, Any]]:
    """Devices with a failing compliance check at their latest snapshot, optionally filtered by check, severity
    and form factor (laptop or desktop)."""
    args = {
        "check_id": check_id,
        "severity": severity,
        "form_factor": form_factor,
        "device_ids": device_ids,
    }
    return _respond("compliance_failures_now", args, runtime)


@tool(response_format="content_and_artifact")
def memory_pressure_now(
    runtime: ToolRuntime[AgentContext],
    threshold_pct: Percent = policy.MEMORY_USED_PCT,
    device_ids: DeviceIds = None,
) -> tuple[str, dict[str, Any]]:
    """Devices whose memory use at the latest snapshot is at or above `threshold_pct` percent."""
    return _respond(
        "memory_pressure_now", {"threshold_pct": threshold_pct, "device_ids": device_ids}, runtime
    )


@tool(response_format="content_and_artifact")
def trend(
    runtime: ToolRuntime[AgentContext],
    metric: Literal["memory", "disk", "compliance", "battery"],
    window_days: Window | None = None,
    threshold_pct: Percent | None = None,
    check_id: CheckId | None = None,
    device_ids: DeviceIds = None,
) -> tuple[str, dict[str, Any]]:
    """Classify a metric over a trailing window (1 to 60 days): sustained, onset, intermittent, recovering or
    isolated for memory and disk; persistent failure, transition or recovery for one compliance `check_id`;
    wear signals for batteries. The window defaults per metric (7 days for memory and disk, 30 for compliance and
    battery) and thresholds default to the disclosed policy."""
    args = {
        "metric": metric,
        "window_days": window_days,
        "threshold_pct": threshold_pct,
        "check_id": check_id,
        "device_ids": device_ids,
    }
    return _respond("trend", args, runtime)


@tool(response_format="content_and_artifact")
def find_devices(
    runtime: ToolRuntime[AgentContext],
    os_name: OsName | None = None,
    model_name: ModelName | None = None,
    form_factor: FormFactor | None = None,
    hostname: Hostname | None = None,
    employee_id: EmployeeId | None = None,
    group_by: Literal["os", "model", "form_factor"] | None = None,
    device_ids: DeviceIds = None,
) -> tuple[str, dict[str, Any]]:
    """Inventory lookup by OS, model, form factor, hostname or employee id, optionally grouped (`group_by`)
    for counts. The grouping is produced by the answer composer from the recorded `group_by` argument. The
    employee id only narrows the match and is never shown."""
    args = {
        "os_name": os_name,
        "model_name": model_name,
        "form_factor": form_factor,
        "hostname": hostname,
        "employee_id": employee_id,
        "group_by": group_by,
        "device_ids": device_ids,
    }
    return _respond("find_devices", args, runtime)


@tool(response_format="content_and_artifact")
def device_history(
    runtime: ToolRuntime[AgentContext],
    device_id: Annotated[str, Field(min_length=1, max_length=40)],
    metric: Literal["disk_free_pct", "memory_used_pct", "battery_capacity", "compliance"],
    window_days: Window = policy.TREND_WINDOW_DAYS,
    check_id: CheckId | None = None,
) -> tuple[str, dict[str, Any]]:
    """One device's readings for one metric over a trailing window (1 to 60 days). `check_id` is required when
    `metric` is "compliance"."""
    args = {
        "device_id": device_id,
        "metric": metric,
        "window_days": window_days,
        "check_id": check_id,
    }
    return _respond("device_history", args, runtime)


TOOLS = [
    low_disk_devices,
    macos_version_check,
    compliance_failures_now,
    memory_pressure_now,
    trend,
    find_devices,
    device_history,
]
DEVICE_FILTER_TOOLS = frozenset(t.name for t in TOOLS) - {"device_history"}


def tool_error_text(exc: Exception) -> str:
    """Fixed-format error text for the model. Never echoes input values or exception reprs."""
    if isinstance(exc, ToolInputError):
        return f"{INVALID_INPUT_PREFIX}: {exc}"
    if isinstance(exc, ToolInvocationError):
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.source.errors()
        )
        return f"{INVALID_INPUT_PREFIX}: {problems}"
    return "The data lookup failed. No result is available for this call."
