"""Battery insights, the fleet health overview, the insight contract and the estimate wording."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from agent_support import ACME, GLOBEX, repo_for, run
from factories import make_conn, make_row
from pydantic import ValidationError

from fleet_copilot import policy
from fleet_copilot.agent.compose import compose
from fleet_copilot.agent.insights import estimate_phrase, estimates_of
from fleet_copilot.agent.verify import composition_of, verify_evidence, verify_prose
from fleet_copilot.contracts import (
    AnswerEnvelope,
    Insight,
    InsightFit,
    InsightProjection,
    InsightSeries,
    KeyValue,
    ScopeInfo,
    SeriesPoint,
)
from fleet_copilot.domain.battery import BatteryReading, battery_severity, capacity_index
from fleet_copilot.domain.fit import NO_PROJECTION, Projection
from fleet_copilot.fleet import health
from fleet_copilot.fleet import queries as q
from fleet_copilot.repository import FleetRepository, TenantContext, dataset_as_of
from fleet_copilot.timeutil import parse_ts

AS_OF = parse_ts("2026-06-12T11:00:00Z")
ACME_ACT_SOON_BATTERIES = ["7VP16KHV88LM", "8NM23J95R5I6", "M4XVHUV1MEPZ"]


def synth_repo(rows):
    return FleetRepository(make_conn(rows), TenantContext("acme-001", "insight-test"))


def battery(capacity, condition="Normal", cycles=100):
    return {
        "battery_present": True,
        "condition": condition,
        "cycle_count": cycles,
        "full_charge_capacity": capacity,
        "percentage": 80,
    }


def daily(device, capacities, conditions=None, end_day=12):
    """One battery reading per day ending on `end_day` of June 2026, 09:00."""
    first = end_day - len(capacities) + 1
    return [
        make_row(
            device,
            ts=f"2026-06-{first + i:02d}T09:00:00Z",
            battery=battery(cap, (conditions or {}).get(i, "Normal")),
        )
        for i, cap in enumerate(capacities)
    ]


def finding_of(rows, device="L"):
    result = q.battery_overview(synth_repo(rows), AS_OF, 30)
    return next((f for f in result.findings if f.device_id == device), None)


# ---- the capacity index and the severity rule ----------------------------------------------------


def test_capacity_index_is_relative_to_the_batterys_own_first_reading():
    day = datetime(2026, 6, 1, tzinfo=UTC)
    readings = [
        BatteryReading(day, "Normal", 1, None),  # no capacity: skipped
        BatteryReading(day + timedelta(days=1), "Normal", 1, 5000),
        BatteryReading(day + timedelta(days=2), "Normal", 1, 4750),
    ]
    assert [round(v, 6) for _, v in capacity_index(readings)] == [100.0, 95.0]
    assert capacity_index([BatteryReading(day, None, None, 0)]) == []


def soon(days):
    return Projection("projected", days_to_estimate=days)


@pytest.mark.parametrize(
    ("streak", "change", "projection", "expected"),
    [
        (3, None, NO_PROJECTION, "act_soon"),
        (14, -4.2, soon(60), "act_soon"),
        (0, -3.0, soon(30), "act_soon"),  # within 30 days, inclusive
        (0, -3.0, soon(30.5), "watch"),
        (0, -1.01, NO_PROJECTION, "watch"),
        (0, -1.0, NO_PROJECTION, "ok"),  # the decline limit is exclusive, as it always was
        (2, None, NO_PROJECTION, "watch"),  # a flag shorter than our act rule
        (0, 0.0, NO_PROJECTION, "ok"),
        (0, None, Projection("beyond_horizon", days_to_estimate=400), "ok"),
    ],
)
def test_battery_severity_rule(streak, change, projection, expected):
    assert (
        battery_severity(service_streak=streak, capacity_change_pct=change, projection=projection)
        == expected
    )


# ---- battery insight values on synthetic laptops ----------------------------------------------------


def test_a_fast_decline_is_act_soon_through_its_estimate_alone():
    # 1% per day from 10000: 90% of the first reading is reached about 4 days after the last reading
    rows = daily("L", [10000 - 100 * i for i in range(10)])
    values = finding_of(rows).values
    assert values["service_streak"] == 0
    assert values["severity"] == "act_soon"
    assert values["projection"]["status"] == "projected"
    assert values["projection"]["days_to_estimate"] <= policy.ACT_SOON_PROJECTION_DAYS
    assert values["rate_per_day"] == pytest.approx(-1.0)
    assert values["fit"]["r2"] == pytest.approx(1.0)
    assert values["service_index"] == policy.BATTERY_SERVICE_INDEX


def test_a_slow_decline_is_a_watch_with_an_estimate_beyond_thirty_days():
    rows = daily("L", [10000 - 20 * i for i in range(10)])  # -0.2 points a day: -1.8% in the window
    values = finding_of(rows).values
    assert values["severity"] == "watch"
    assert values["projection"]["status"] == "projected"
    assert values["projection"]["days_to_estimate"] > policy.ACT_SOON_PROJECTION_DAYS


def test_a_very_slow_decline_is_not_projected_within_the_horizon():
    rows = daily("L", [10000 - 3 * i for i in range(10)] + [9950] * 0)
    assert finding_of(rows) is None  # under the 1% decline limit and no flag: not a finding
    longer = daily("L", [10000 - 12 * i for i in range(12)])  # -1.3%, 0.12 points per day
    values = finding_of(longer).values
    assert values["severity"] == "watch"
    assert values["projection"]["status"] == "beyond_horizon" or (
        values["projection"]["days_to_estimate"] > 30
    )


def test_a_flat_battery_has_no_estimate_and_is_not_a_finding():
    assert finding_of(daily("L", [5000] * 10)) is None


def test_service_streak_three_is_act_soon_and_two_is_a_watch():
    flagged = {7: "Service Recommended", 8: "Service Recommended", 9: "Service Recommended"}
    act = finding_of(daily("L", [5000] * 10, flagged)).values
    assert (act["service_streak"], act["severity"]) == (3, "act_soon")
    watch = finding_of(daily("L", [5000] * 10, {8: flagged[8], 9: flagged[9]})).values
    assert (watch["service_streak"], watch["severity"]) == (2, "watch")


def test_fewer_than_seven_capacity_readings_is_no_estimate():
    values = finding_of(daily("L", [10000 - 150 * i for i in range(6)])).values
    assert values["fit"]["n"] == 6
    assert values["projection"]["status"] == "none"
    assert (
        values["severity"] == "watch"
    )  # -7.5% against its first reading, but no estimate to act on


def test_three_readings_is_enough_for_a_fit_and_two_is_insufficient_history():
    three = finding_of(daily("L", [5000, 4900, 4800])).values
    assert three["fit"]["n"] == 3 and three["projection"]["status"] == "none"
    result = q.battery_overview(synth_repo(daily("L", [5000, 4000])), AS_OF, 30)
    assert result.findings == () and result.excluded == {"insufficient_history": ("L",)}


def test_capacity_readings_missing_from_some_snapshots_are_skipped_in_the_index():
    rows = daily("L", [10000, None, 9900, None, 9800, 9700, 9600, 9500, 9400, 9300])
    values = finding_of(rows).values
    assert values["fit"]["n"] == 8
    assert len(values["index_series"]) == 8 and values["index_series"][0]["value"] == 100.0


# ---- real data against the oracle -----------------------------------------------------------------


def test_the_three_acme_service_batteries_are_act_soon_with_a_labelled_estimate(
    real_db_path, real_conn
):
    record = run(real_db_path, real_conn, "trend", {"metric": "battery"})
    severities = {f.device_id: f.values["severity"] for f in record.findings}
    assert severities == dict.fromkeys(ACME_ACT_SOON_BATTERIES, "act_soon")
    for f in record.findings:
        assert f.values["projection"]["status"] == "projected"
        assert f.values["fit"]["n"] == 30 and f.values["fit"]["r2"] > 0.99
        assert f.values["rate_per_day"] < 0


def test_the_battery_answer_has_ranked_insights_with_the_estimate_and_its_evidence(
    real_db_path, real_conn
):
    record = run(real_db_path, real_conn, "trend", {"metric": "battery"})
    comp = compose([record], resolve=repo_for(real_conn).resolve)
    assert comp.outcome == "answer"
    assert {i.device_id for i in comp.insights} == set(ACME_ACT_SOON_BATTERIES)
    # most urgent first, and within a severity the steepest decline first
    rates = [i.fit.slope_per_day for i in comp.insights]
    assert rates == sorted(rates)
    for insight in comp.insights:
        assert insight.category == "battery" and insight.severity == "act_soon"
        assert "estimated to reach our service threshold" in insight.finding
        assert insight.method == "Estimate · straight-line fit · 30 snapshots"
        assert insight.series.threshold.value == policy.BATTERY_SERVICE_INDEX
        assert insight.series.threshold.source == "policy"
        assert insight.series.points[0].value == 100.0 and len(insight.series.points) == 30
        assert {kv.label for kv in insight.evidence_kv} >= {"Cycles", "Rate", "Vendor condition"}
        assert estimate_phrase(insight.projection) in insight.finding
    assert comp.estimates == estimates_of(comp.insights) and comp.estimates
    shown = {item.ref for item in comp.evidence}
    assert all(ref in shown for insight in comp.insights for ref in insight.refs)
    envelope = comp.to_envelope(company_id=ACME, as_of=AS_OF, trace_id="t")
    assert [i.device_id for i in envelope.insights] == [i.device_id for i in comp.insights]
    assert "show no meaningful decline" in envelope.summary


def test_every_insight_ref_resolves_to_the_reading_it_names(real_db_path, real_conn):
    record = run(real_db_path, real_conn, "fleet_health", {})
    comp = compose([record], resolve=repo_for(real_conn).resolve)
    envelope = comp.to_envelope(company_id=ACME, as_of=dataset_as_of(real_conn), trace_id="t")
    assert verify_evidence(envelope, repo_for(real_conn).resolve) == []


# ---- fleet health ------------------------------------------------------------------------------


def test_fleet_health_for_acme_is_ranked_and_headlined(real_db_path, real_conn):
    record = run(real_db_path, real_conn, "fleet_health", {})
    assert record.error is None and record.population == 10
    comp = compose([record], resolve=repo_for(real_conn).resolve)
    envelope = comp.to_envelope(company_id=ACME, as_of=AS_OF, trace_id="t", company_name="Acme")
    assert envelope.headline == "7 devices need attention at Acme: 4 act soon, 3 to watch."
    order = [(i.severity, i.category) for i in envelope.insights]
    assert len(envelope.insights) == 10 <= 10
    assert order == sorted(
        order, key=lambda o: (("act_soon", "watch").index(o[0]), health.CATEGORY_ORDER.index(o[1]))
    )
    assert {i.device_id for i in envelope.insights if i.severity == "act_soon"} == {
        *ACME_ACT_SOON_BATTERIES,
        "MT7PJB7N5LRE",
    }
    assert any("Showing the 10 most urgent of 11 findings" in note for note in envelope.notes)
    line = envelope.scope_line
    assert (line.checked, line.matched, line.ok) == (10, 7, 3)
    assert envelope.table is None
    # ties inside a severity and category are broken by device id
    batteries = [i.device_id for i in envelope.insights if i.category == "battery"]
    assert batteries == sorted(batteries)


def test_a_headline_without_a_company_name_drops_the_company(real_db_path, real_conn):
    record = run(real_db_path, real_conn, "fleet_health", {})
    comp = compose([record], resolve=repo_for(real_conn).resolve)
    envelope = comp.to_envelope(company_id=ACME, as_of=AS_OF, trace_id="t")
    assert envelope.headline == "7 devices need attention: 4 act soon, 3 to watch."


def test_fleet_health_is_scoped_to_the_signed_in_company(real_db_path, real_conn):
    record = run(real_db_path, real_conn, "fleet_health", {}, tenant=GLOBEX)
    assert all(f.refs and all(r.startswith("globex-002/") for r in f.refs) for f in record.findings)
    assert not {f.device_id for f in record.findings} & {
        *ACME_ACT_SOON_BATTERIES,
        "MT7PJB7N5LRE",
    }


def test_fleet_health_can_be_narrowed_to_devices(real_db_path, real_conn):
    record = run(real_db_path, real_conn, "fleet_health", {"device_ids": ["MT7PJB7N5LRE"]})
    assert {f.device_id for f in record.findings} == {"MT7PJB7N5LRE"}
    assert record.population == 1


def test_fleet_health_window_is_honoured_and_a_short_window_reports_thin_history(
    real_db_path, real_conn
):
    record = run(real_db_path, real_conn, "fleet_health", {"window_days": 1})
    assert record.excluded["insufficient_history"]
    assert record.findings == []


def test_a_failing_high_severity_check_is_act_soon_and_a_medium_transition_is_a_watch():
    def row(device, day, status):
        r = make_row(device, ts=f"2026-06-{day:02d}T09:00:00Z", battery=None)
        for check in r["compliance_results"]:
            if check["check_id"] == "disk_encryption":
                check["status"] = status  # disk_encryption is high severity in the fixtures
            if check["check_id"] == "screen_lock":
                check["status"] = "pass" if day < 11 else "fail"  # medium: a transition
        return r

    rows = [row("HI", day, "fail") for day in (8, 9, 10, 11, 12)]
    result = health.fleet_health(synth_repo(rows), AS_OF, 30)
    got = {(f.values["check_id"], f.values["severity"]) for f in result.findings}
    assert got == {("disk_encryption", "act_soon"), ("screen_lock", "watch")}
    assert [f.values["check_id"] for f in result.findings] == ["disk_encryption", "screen_lock"]


def test_a_company_with_nothing_to_flag_is_not_an_answer():
    rows = [make_row("A", ts=f"2026-06-{d:02d}T09:00:00Z") for d in (9, 10, 11, 12)]
    comp = compose([_record_of(synth_repo(rows))], resolve=synth_repo(rows).resolve)
    assert comp.outcome == "no_match" and not comp.insights


def _record_of(repo):
    from fleet_copilot.agent.records import ToolRecord

    return ToolRecord.from_query(
        "fleet_health", {"window_days": 30}, health.fleet_health(repo, AS_OF, 30)
    )


# ---- the insight contract ----------------------------------------------------------------------------


def make_insight(**changes):
    base = {
        "device_id": "DEV1",
        "label": "acme-host-DEV1",
        "category": "battery",
        "severity": "act_soon",
        "finding": "acme-host-DEV1's battery is estimated to reach our service threshold between Jun 20 and Jul 3.",
        "evidence_kv": [KeyValue(label="Cycles", value="512")],
        "series": InsightSeries(
            unit="Capacity index",
            points=[SeriesPoint(at=AS_OF, value=100.0)],
        ),
        "fit": InsightFit(slope_per_day=-0.14, r2=0.99, n=30),
        "projection": InsightProjection(
            status="projected",
            estimate=date(2026, 6, 25),
            earliest=date(2026, 6, 20),
            latest=date(2026, 7, 3),
        ),
        "method": "Estimate · straight-line fit · 30 snapshots",
        "why": "Because the vendor flagged it.",
        "refs": ["acme-001/DEV1@2026-06-12T09:00:00Z#battery"],
        "suggested_action_types": ["replacement_flag"],
    }
    base.update(changes)
    return Insight(**base)


def envelope_with(insights, *, outcome="answer", evidence_refs=None, company="acme-001"):
    from fleet_copilot.contracts import Claim, EvidenceItem

    ref = "acme-001/DEV1@2026-06-12T09:00:00Z#battery"
    known = evidence_refs if evidence_refs is not None else [ref]
    return AnswerEnvelope(
        outcome=outcome,
        trace_id="t",
        headline="h",
        claims=[Claim(text="c", refs=[known[0]])] if outcome == "answer" else [],
        evidence=[EvidenceItem(ref=r, label="l", value="v") for r in known]
        if outcome == "answer"
        else [],
        scope=ScopeInfo(company_id=company, as_of=AS_OF),
        reason_code="no_matching_records" if outcome == "no_match" else None,
        insights=insights,
    )


def test_a_valid_insight_envelope_round_trips():
    envelope = envelope_with([make_insight()])
    assert AnswerEnvelope.model_validate(envelope.model_dump(mode="json")) == envelope


def test_an_envelope_without_insights_still_validates_with_the_default():
    assert envelope_with([]).insights == []


def test_insights_belong_to_an_answer_only():
    with pytest.raises(ValidationError, match="only an answer outcome may carry insights"):
        envelope_with([make_insight()], outcome="no_match")


def test_an_insight_may_cite_only_evidence_the_answer_shows():
    with pytest.raises(ValidationError, match="insight cites refs without evidence"):
        envelope_with(
            [make_insight()],
            evidence_refs=["acme-001/OTHER@2026-06-12T09:00:00Z#battery"],
        )


def test_an_insight_may_not_cite_another_company_or_another_device():
    foreign = "globex-002/DEV1@2026-06-12T09:00:00Z#battery"
    with pytest.raises(ValidationError, match="another company"):
        envelope_with([make_insight(refs=[foreign])], evidence_refs=[foreign])
    other_device = "acme-001/DEV2@2026-06-12T09:00:00Z#battery"
    with pytest.raises(ValidationError, match="another device"):
        envelope_with([make_insight(refs=[other_device])], evidence_refs=[other_device])


def test_an_envelope_carries_at_most_ten_insights():
    envelope_with([make_insight() for _ in range(10)])
    with pytest.raises(ValidationError):
        envelope_with([make_insight() for _ in range(11)])


@pytest.mark.parametrize("field", ["finding", "why"])
def test_finding_and_why_are_capped_at_240_characters(field):
    make_insight(**{field: "x" * 240})
    with pytest.raises(ValidationError):
        make_insight(**{field: "x" * 241})


def test_other_insight_caps():
    with pytest.raises(ValidationError):
        make_insight(refs=[])
    with pytest.raises(ValidationError):
        make_insight(evidence_kv=[KeyValue(label="k", value="v")] * 9)
    with pytest.raises(ValidationError):
        make_insight(refs=["acme-001/DEV1@2026-06-12T09:00:00Z#battery"] * 9)
    with pytest.raises(ValidationError):
        make_insight(category="network")
    with pytest.raises(ValidationError):
        make_insight(severity="critical")


def test_a_projection_is_consistent():
    with pytest.raises(ValidationError, match="needs an estimate"):
        InsightProjection(status="projected")
    with pytest.raises(ValidationError, match="contain the estimate"):
        InsightProjection(
            status="projected",
            estimate=date(2026, 6, 25),
            earliest=date(2026, 6, 26),
            latest=date(2026, 7, 3),
        )
    with pytest.raises(ValidationError, match="only a projected estimate"):
        InsightProjection(status="none", estimate=date(2026, 6, 25))
    InsightProjection(status="beyond_horizon")
    InsightProjection(status="projected", estimate=date(2026, 6, 25), earliest=date(2026, 6, 20))


# ---- estimates and the prose gate ------------------------------------------------------------------------


def test_the_estimate_phrase_forms():
    d = date
    both = InsightProjection(
        status="projected", estimate=d(2026, 6, 25), earliest=d(2026, 6, 20), latest=d(2026, 7, 3)
    )
    assert estimate_phrase(both) == "between Jun 20 and Jul 3"
    same = InsightProjection(
        status="projected", estimate=d(2026, 7, 22), earliest=d(2026, 7, 22), latest=d(2026, 7, 22)
    )
    assert estimate_phrase(same) == "around Jul 22"
    open_ended = InsightProjection(
        status="projected", estimate=d(2026, 7, 22), earliest=d(2026, 7, 20)
    )
    assert estimate_phrase(open_ended) == "as early as Jul 20"
    assert estimate_phrase(InsightProjection(status="beyond_horizon")) is None
    assert estimate_phrase(None) is None


@pytest.fixture
def battery_answer(real_db_path, real_conn):
    record = run(real_db_path, real_conn, "trend", {"metric": "battery"})
    comp = compose([record], resolve=repo_for(real_conn).resolve)
    envelope = comp.to_envelope(company_id=ACME, as_of=AS_OF, trace_id="t")
    return composition_of(envelope), comp


def test_the_composition_rebuilt_from_an_envelope_keeps_its_estimates(battery_answer):
    rebuilt, original = battery_answer
    assert rebuilt.estimates == original.estimates and rebuilt.estimates


def test_prose_may_repeat_a_code_written_estimate_and_nothing_else_looking_forward(battery_answer):
    comp, _ = battery_answer
    insight = comp.insights[0]
    phrase = estimate_phrase(insight.projection)
    text = f"{insight.label}'s battery is estimated to reach our service threshold {phrase}."
    verdict = verify_prose(comp, AS_OF, headline=None, explanation=text)
    assert verdict.problems == () and verdict.explanation == text


def test_a_date_that_the_code_did_not_write_is_rejected(battery_answer):
    comp, _ = battery_answer
    insight = comp.insights[0]
    wrong = f"{insight.label}'s battery is estimated to reach our service threshold between Aug 3 and Aug 9."
    verdict = verify_prose(comp, AS_OF, headline=None, explanation=wrong)
    assert verdict.explanation is None
    assert any(p.startswith("explanation:") for p in verdict.problems)


def test_a_stated_date_may_be_named_by_month_and_day_but_no_other(battery_answer):
    comp, _ = battery_answer
    stated = verify_prose(
        comp,
        AS_OF,
        headline=None,
        explanation="acme-macbook-1 has had the vendor flag since May 30.",
    )
    assert stated.problems == ()  # the claim states 2026-05-30
    invented = verify_prose(
        comp,
        AS_OF,
        headline=None,
        explanation="acme-macbook-1 has had the vendor flag since May 20.",
    )
    assert "explanation:date" in invented.problems


def test_an_unrelated_prediction_is_still_rejected_next_to_an_estimate(battery_answer):
    comp, _ = battery_answer
    insight = comp.insights[0]
    phrase = estimate_phrase(insight.projection)
    text = f"{insight.label} will fail soon, {phrase}."
    verdict = verify_prose(comp, AS_OF, headline=None, explanation=text)
    assert "explanation:predictive" in verdict.problems


def test_estimates_are_not_needed_for_an_answer_without_insights(real_db_path, real_conn):
    record = run(real_db_path, real_conn, "low_disk_devices")
    comp = compose([record], resolve=repo_for(real_conn).resolve)
    assert comp.estimates == [] and comp.insights == []
    assert (
        composition_of(comp.to_envelope(company_id=ACME, as_of=AS_OF, trace_id="t")).estimates == []
    )
