"""A straight-line fit over a series, and an estimate of when it crosses a threshold.

Pure arithmetic over `(t_days, value)` points. Nothing here reads a database or a clock.

The fit is ordinary least squares. The projection is an ESTIMATE, and it says so in its shape:

  * the point estimate is where the fitted line, taken from its value at the as-of time, reaches the threshold;
  * the range re-runs that crossing with the slope moved by one standard error of the slope
    (`residual_sd / sqrt(sum of squared time deviations)`): a steeper slope gives the earliest date, a
    shallower one the latest. This is a simple band, not a prediction interval, and it only reflects how
    far the points scatter around the line.

Three cases are NOT projected, and each is named: too few points or a slope that is not downward
(`none`), and a crossing more than the horizon away (`beyond_horizon`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from fleet_copilot import policy

ProjectionStatus = Literal["projected", "none", "beyond_horizon"]
_SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class LineFit:
    slope: float  # value units per day
    intercept: float  # value at t = 0
    r2: float  # share of the variance the line explains; 0.0 when the values do not vary
    residual_sd: float  # sqrt(SSE / (n - 2)); 0.0 when n <= 2
    slope_se: float  # residual_sd / sqrt(Sxx): the standard error of the slope
    n: int

    def at(self, t_days: float) -> float:
        return self.intercept + self.slope * t_days


def fit_line(points: Sequence[tuple[float, float]]) -> LineFit | None:
    """Least-squares line through `(t_days, value)` points, or None for fewer than two distinct times."""
    n = len(points)
    if n < 2:
        return None
    t_mean = sum(t for t, _ in points) / n
    v_mean = sum(v for _, v in points) / n
    sxx = sum((t - t_mean) ** 2 for t, _ in points)
    if sxx == 0.0:
        return None
    sxy = sum((t - t_mean) * (v - v_mean) for t, v in points)
    slope = sxy / sxx
    intercept = v_mean - slope * t_mean
    sse = sum((v - (intercept + slope * t)) ** 2 for t, v in points)
    syy = sum((v - v_mean) ** 2 for _, v in points)
    r2 = 0.0 if syy == 0.0 else max(0.0, 1.0 - sse / syy)
    residual_sd = math.sqrt(sse / (n - 2)) if n > 2 else 0.0
    return LineFit(
        slope=slope,
        intercept=intercept,
        r2=r2,
        residual_sd=residual_sd,
        slope_se=residual_sd / math.sqrt(sxx),
        n=n,
    )


@dataclass(frozen=True)
class Projection:
    status: ProjectionStatus
    estimate: date | None = None
    earliest: date | None = None
    # None when the shallower slope never reaches the threshold within the horizon: no upper bound.
    latest: date | None = None
    days_to_estimate: float | None = None  # from the as-of time; 0.0 when already at or past it


NO_PROJECTION = Projection("none")


def _days_to_cross(level: float, slope: float, threshold: float) -> float | None:
    """Days for `level` to fall to `threshold` at `slope` per day; None if the slope is not downward."""
    if slope >= 0.0:
        return None
    if level <= threshold:
        return 0.0
    return (level - threshold) / -slope


def project_crossing(
    fit: LineFit | None,
    *,
    threshold: float,
    origin: datetime,
    as_of: datetime,
    min_points: int = policy.MIN_FIT_POINTS,
    horizon_days: int = policy.PROJECTION_HORIZON_DAYS,
) -> Projection:
    """When the fitted line falls to `threshold`, as a date with an earliest-latest range.

    `origin` is the datetime that `t_days = 0` stands for. A fit with fewer than `min_points` points, or
    whose slope is zero or upward, gives `none`; a crossing more than `horizon_days` after `as_of` gives
    `beyond_horizon`. A line already at or below the threshold at `as_of` crosses "now" (0 days).
    """
    if fit is None or fit.n < min_points or fit.slope >= 0.0:
        return NO_PROJECTION
    level = fit.at((as_of - origin).total_seconds() / _SECONDS_PER_DAY)
    point = _days_to_cross(level, fit.slope, threshold)
    if point is None:  # unreachable: the slope is downward here
        return NO_PROJECTION
    if point > horizon_days:
        return Projection("beyond_horizon", days_to_estimate=point)

    def on(days: float) -> date:
        return (as_of + timedelta(days=days)).date()

    earliest = _days_to_cross(level, fit.slope - fit.slope_se, threshold)
    latest = _days_to_cross(level, fit.slope + fit.slope_se, threshold)
    return Projection(
        "projected",
        estimate=on(point),
        earliest=on(earliest if earliest is not None else point),
        latest=on(latest) if latest is not None and latest <= horizon_days else None,
        days_to_estimate=point,
    )
