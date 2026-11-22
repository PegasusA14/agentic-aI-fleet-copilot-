import random
from datetime import UTC, datetime, timedelta

import pytest

from fleet_copilot import policy
from fleet_copilot.domain.fit import fit_line, project_crossing

ORIGIN = datetime(2026, 5, 14, 9, 0, tzinfo=UTC)


def line(slope, intercept, n, step=1.0):
    return [(i * step, intercept + slope * i * step) for i in range(n)]


def as_of_after(days):
    return ORIGIN + timedelta(days=days)


# ---- the fit ---------------------------------------------------------------------------------


def test_an_exact_line_is_recovered():
    fit = fit_line(line(-0.15, 100.0, 30))
    assert fit.slope == pytest.approx(-0.15)
    assert fit.intercept == pytest.approx(100.0)
    assert fit.r2 == pytest.approx(1.0)
    assert fit.residual_sd == pytest.approx(0.0, abs=1e-9)
    assert fit.n == 30


def test_a_monotone_decreasing_series_has_a_negative_slope():
    rng = random.Random(7)
    for _ in range(25):
        steps = [rng.uniform(0.01, 0.5) for _ in range(20)]
        values, level = [], 100.0
        for step in steps:
            level -= step
            values.append(level)
        fit = fit_line([(float(i), v) for i, v in enumerate(values)])
        assert fit.slope < 0


def test_a_flat_series_has_zero_slope_and_no_projection():
    fit = fit_line([(float(i), 96.0) for i in range(20)])
    assert fit.slope == pytest.approx(0.0)
    assert fit.r2 == 0.0
    projection = project_crossing(fit, threshold=90.0, origin=ORIGIN, as_of=as_of_after(19))
    assert projection.status == "none" and projection.estimate is None


def test_too_few_points_or_no_spread_in_time_is_not_a_fit():
    assert fit_line([]) is None
    assert fit_line([(0.0, 100.0)]) is None
    assert fit_line([(1.0, 100.0), (1.0, 99.0)]) is None


def test_two_points_have_no_residual_spread():
    fit = fit_line([(0.0, 100.0), (2.0, 99.0)])
    assert fit.slope == pytest.approx(-0.5)
    assert fit.residual_sd == 0.0 and fit.slope_se == 0.0


# ---- the projection --------------------------------------------------------------------------


def test_an_exact_decline_crosses_the_threshold_on_the_computed_day():
    fit = fit_line(line(-0.5, 100.0, 21))  # 90 at t = 20; the as-of is t = 10 (index 95)
    projection = project_crossing(fit, threshold=90.0, origin=ORIGIN, as_of=as_of_after(10))
    assert projection.status == "projected"
    assert projection.days_to_estimate == pytest.approx(10.0)
    assert projection.estimate == (as_of_after(10) + timedelta(days=10)).date()
    assert projection.earliest <= projection.estimate <= projection.latest


def test_fewer_than_the_minimum_points_is_not_projected():
    fit = fit_line(line(-0.5, 100.0, policy.MIN_FIT_POINTS - 1))
    projection = project_crossing(fit, threshold=90.0, origin=ORIGIN, as_of=as_of_after(5))
    assert projection.status == "none"
    enough = fit_line(line(-0.5, 100.0, policy.MIN_FIT_POINTS))
    assert (
        project_crossing(enough, threshold=90.0, origin=ORIGIN, as_of=as_of_after(6)).status
        == "projected"
    )


def test_an_upward_slope_is_not_projected():
    fit = fit_line(line(0.2, 90.0, 20))
    assert (
        project_crossing(fit, threshold=90.0, origin=ORIGIN, as_of=as_of_after(19)).status == "none"
    )


def test_a_crossing_past_the_horizon_is_named_as_such():
    fit = fit_line(line(-0.01, 100.0, 30))  # 90 is 1000 days from the start
    projection = project_crossing(fit, threshold=90.0, origin=ORIGIN, as_of=as_of_after(29))
    assert projection.status == "beyond_horizon"
    assert projection.estimate is None and projection.earliest is None
    assert projection.days_to_estimate > policy.PROJECTION_HORIZON_DAYS


def test_a_crossing_exactly_at_the_horizon_is_still_projected():
    # 100 -> 90 in just under 180 days from the as-of (the limit itself is inclusive)
    fit = fit_line([(float(t), 100.0 - (10.0 / 179.99) * t) for t in range(0, 30)])
    projection = project_crossing(fit, threshold=90.0, origin=ORIGIN, as_of=as_of_after(0))
    assert projection.status == "projected"
    assert projection.days_to_estimate == pytest.approx(179.99, abs=1e-3)
    past = fit_line([(float(t), 100.0 - (10.0 / 180.5) * t) for t in range(0, 30)])
    assert (
        project_crossing(past, threshold=90.0, origin=ORIGIN, as_of=as_of_after(0)).status
        == "beyond_horizon"
    )


def test_a_line_already_past_the_threshold_crosses_now():
    fit = fit_line(line(-1.0, 100.0, 20))  # 81 at t = 19
    as_of = as_of_after(19)
    projection = project_crossing(fit, threshold=90.0, origin=ORIGIN, as_of=as_of)
    assert projection.status == "projected"
    assert projection.days_to_estimate == 0.0
    assert projection.estimate == as_of.date()


def test_noisy_series_range_contains_the_point_estimate():
    rng = random.Random(11)
    checked = 0
    for _ in range(200):
        slope = -rng.uniform(0.05, 0.6)
        points = [(float(i), 100.0 + slope * i + rng.gauss(0, 0.4)) for i in range(30)]
        fit = fit_line(points)
        projection = project_crossing(fit, threshold=90.0, origin=ORIGIN, as_of=as_of_after(29))
        if projection.status != "projected":
            continue
        checked += 1
        assert projection.earliest <= projection.estimate
        assert projection.latest is None or projection.estimate <= projection.latest
    assert checked > 50


def test_a_noisier_series_has_a_wider_range():
    rng = random.Random(3)
    clean = fit_line([(float(i), 100.0 - 0.3 * i + rng.gauss(0, 0.05)) for i in range(30)])
    noisy = fit_line([(float(i), 100.0 - 0.3 * i + rng.gauss(0, 1.0)) for i in range(30)])
    assert noisy.slope_se > clean.slope_se
    assert noisy.r2 < clean.r2


def test_when_the_shallow_slope_never_crosses_there_is_no_upper_bound():
    points = [(float(i), 100.0 - 0.03 * i + (3.0 if i % 2 else -3.0)) for i in range(30)]
    fit = fit_line(points)
    assert fit.slope < 0 and fit.slope + fit.slope_se >= 0
    projection = project_crossing(
        fit, threshold=fit.at(29.0) - 1.0, origin=ORIGIN, as_of=as_of_after(29)
    )
    assert projection.status == "projected"
    assert projection.latest is None and projection.earliest <= projection.estimate
