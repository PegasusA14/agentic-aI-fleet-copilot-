"""Independent answer key for the Fleet Copilot evals.

Everything here is recomputed from the raw NDJSON with plain loops. It imports
nothing from the product package on purpose: if the product code and its tests
shared logic, one bug could make both agree on a wrong answer.

Usage (from the repository root):
    python3 evals/golden/oracle.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

FMT = "%Y-%m-%dT%H:%M:%SZ"
CHECKS = ("disk_encryption", "screen_lock", "os_up_to_date")
SERVICE = "Service Recommended"
SOFTWARE_WINDOWS = (7, 14, 30)
# Battery insight policy. Our own numbers: the dataset has no battery design capacity, so wear is the
# capacity as a percentage of the battery's own first reading in the window (100 = that reading).
MIN_READINGS = 3  # fewest readings a trend or a transition is judged from
MIN_FIT_POINTS = 7  # fewest capacity readings a straight-line estimate is made from
HORIZON_DAYS = 180
SERVICE_INDEX = 90.0
ACT_SOON_DAYS = 30
REPLACEMENT_STREAK = 3
DECLINE_PCT = 1.0


def ts(text):
    return datetime.strptime(text, FMT).replace(tzinfo=timezone.utc)


def primary_volume(row):
    volumes = row["disk_volumes"]
    for volume in volumes:
        if volume["mount_point"] in ("/", "C:\\"):
            return volume
    return volumes[0]


def free_percent(row):
    volume = primary_volume(row)
    return volume["available_bytes"] * 100.0 / volume["size_bytes"]


def memory_percent(row):
    memory = row["memory"]
    return memory["used_memory_bytes"] * 100.0 / memory["total_memory_bytes"]


def status_of(row, check):
    for result in row["compliance_results"]:
        if result["check_id"] == check:
            return result["status"]
    return None


def names_of(row):
    return [item["name"] for item in row["installed_software"]]


def software_in_window(devices, rows, latest, as_of, days):
    """The common set and the new installs for one window, with plain loops and no shared logic.

    Common set: apps on EVERY device (that has a snapshot in the window) in that device's earliest
    snapshot in the window. New install: an app outside the common set whose first appearance on a device
    is a later snapshot than that device's earliest one in the window.
    """
    since = as_of - timedelta(days=days)
    inside = {}
    for d in devices:
        kept = [r for r in rows[d] if ts(r["collected_at"]) >= since]
        if kept:
            inside[d] = kept
    common = None
    for d in sorted(inside):
        starting = set(names_of(inside[d][0]))
        common = starting if common is None else common & starting
    common = common or set()
    installs = []
    for d in sorted(inside):
        earliest = inside[d][0]["collected_at"]
        seen = {}
        for r in inside[d]:
            for item in r["installed_software"]:
                if item["name"] not in seen:
                    seen[item["name"]] = (r["collected_at"], item["version"])
        for app in sorted(seen):
            when, version = seen[app]
            if app in common or when == earliest:
                continue
            installs.append(
                {
                    "device_id": d,
                    "app": app,
                    "version": version,
                    "first_seen": when,
                    "still_present": app in names_of(latest[d]),
                }
            )
    return sorted(common), installs


def trailing_true(flags):
    count = 0
    for flag in reversed(flags):
        if not flag:
            break
        count += 1
    return count


def trend_class(flags):
    n = len(flags)
    if n < 3:
        return "insufficient"
    hits = sum(1 for f in flags if f)
    if hits == 0:
        return "none"
    share = hits / n
    if share >= 0.8:
        return "sustained" if flags[-1] else "recovering"
    run = trailing_true(flags)
    if flags[-1] and run == hits and run >= 3:
        return "onset"
    if share < 0.2:
        return "isolated"
    return "intermittent"


def compliance_kind(statuses):
    if len(statuses) < MIN_READINGS:
        return "insufficient", None
    last = statuses[-1]
    start = len(statuses)
    while start > 0 and statuses[start - 1] == last:
        start -= 1
    if start == 0:
        return ("persistent_failure" if last == "fail" else "passing"), None
    return ("transition" if last == "fail" else "recovered"), start


def least_squares(points):
    """Slope, intercept, r squared and the standard error of the slope, from raw sums."""
    n = len(points)
    st = sum(t for t, _ in points)
    sy = sum(y for _, y in points)
    stt = sum(t * t for t, _ in points)
    sty = sum(t * y for t, y in points)
    syy = sum(y * y for _, y in points)
    denominator = n * stt - st * st
    slope = (n * sty - st * sy) / denominator
    intercept = (sy - slope * st) / n
    residual = sum((y - (intercept + slope * t)) ** 2 for t, y in points)
    spread = syy - sy * sy / n
    r2 = 0.0 if spread <= 0 else max(0.0, 1.0 - residual / spread)
    sd = math.sqrt(residual / (n - 2)) if n > 2 else 0.0
    se = sd / math.sqrt(denominator / n)
    return slope, intercept, r2, se


def crossing_days(level, slope):
    """Days until `level` falls to the service index at `slope` per day (None if it never does)."""
    if slope >= 0:
        return None
    if level <= SERVICE_INDEX:
        return 0.0
    return (level - SERVICE_INDEX) / -slope


def project_battery(index_points, as_of):
    """Where the straight line through (days since first reading, index) reaches the service index."""
    none = {
        "status": "none",
        "slope_per_day": None,
        "estimate": None,
        "earliest": None,
        "latest": None,
        "days_to_estimate": None,
    }
    if len(index_points) < MIN_READINGS:
        return none
    origin = index_points[0][0]
    points = [((at - origin).total_seconds() / 86400.0, value) for at, value in index_points]
    slope, intercept, _, se = least_squares(points)
    result = dict(none, slope_per_day=round(slope + 0.0, 4))
    if len(index_points) < MIN_FIT_POINTS or slope >= 0:
        return result
    level = intercept + slope * ((as_of - origin).total_seconds() / 86400.0)
    point = crossing_days(level, slope)
    if point > HORIZON_DAYS:
        return dict(result, status="beyond_horizon", days_to_estimate=round(point, 2))
    early = crossing_days(level, slope - se)
    late = crossing_days(level, slope + se)

    def on(days):
        return (as_of + timedelta(days=days)).date().isoformat()

    return dict(
        result,
        status="projected",
        days_to_estimate=round(point, 2),
        estimate=on(point),
        earliest=on(early),
        latest=on(late) if late is not None and late <= HORIZON_DAYS else None,
    )


def battery_severity(streak, change, projection):
    """act_soon: a Service Recommended streak of 3 or more, or an estimate within 30 days. watch: capacity
    down by more than 1% from the first reading (or a shorter streak). Otherwise ok."""
    if streak >= REPLACEMENT_STREAK:
        return "act_soon"
    if projection["status"] == "projected" and projection["days_to_estimate"] <= ACT_SOON_DAYS:
        return "act_soon"
    if (change is not None and change < -DECLINE_PCT) or streak > 0:
        return "watch"
    return "ok"


def tenant_facts(company, series, as_of):
    devices = sorted(d for (c, d) in series if c == company)
    rows = {d: series[(company, d)] for d in devices}
    latest = {d: rows[d][-1] for d in devices}
    laptops = [d for d in devices if any("battery" in r and r["battery"]["battery_present"] for r in rows[d])]

    def window(device, days):
        since = as_of - timedelta(days=days)
        return [r for r in rows[device] if ts(r["collected_at"]) >= since]

    facts = {
        "devices": devices,
        "laptops": laptops,
        "desktops": [d for d in devices if d not in laptops],
        "hostnames": {d: latest[d]["os"]["hostname"] for d in devices},
    }

    low = [d for d in devices if free_percent(latest[d]) < 10.0]
    least = min((free_percent(latest[d]) for d in low), default=None)
    facts["low_disk_lt10"] = {
        "population": len(devices),
        "devices": low,
        # The device(s) with the least free space among the low ones, by hostname.
        "lowest_hostnames": [latest[d]["os"]["hostname"] for d in low if free_percent(latest[d]) == least],
    }

    mac = [d for d in devices if latest[d]["os"]["product_name"] == "macOS"]
    older, unknown = [], []
    for d in mac:
        parts = latest[d]["os"]["product_version"].split(".")
        if not all(p.isdigit() for p in parts):
            unknown.append(d)
        elif int(parts[0]) < 15:
            older.append(d)
    def version_key(device):
        return tuple(int(p) for p in latest[device]["os"]["product_version"].split("."))

    oldest = min((version_key(d) for d in older), default=None)
    facts["macos_below_15"] = {
        "population": len(mac),
        "devices": older,
        # The device(s) on the oldest macOS among the ones below 15, by hostname.
        "oldest_hostnames": [latest[d]["os"]["hostname"] for d in older if version_key(d) == oldest],
        "unknown_version": unknown,
        "out_of_scope": [d for d in devices if d not in mac],
    }

    def failing(group, check=None, severity=None):
        found = []
        for d in group:
            for result in latest[d]["compliance_results"]:
                if (
                    result["status"] == "fail"
                    and (check is None or result["check_id"] == check)
                    and (severity is None or result["severity"] == severity)
                ):
                    found.append(d)
                    break
        return found

    facts["failing_latest"] = {
        form: {check: failing(group, check=check) for check in CHECKS}
        for form, group in (("all", devices), ("laptop", laptops))
    }
    facts["failing_high_severity"] = {
        "all": failing(devices, severity="high"),
        "laptop": failing(laptops, severity="high"),
    }

    def classes(days, breached):
        found = {}
        for d in devices:
            cls = trend_class([breached(r) for r in window(d, days)])
            if cls not in ("none", "insufficient"):
                found[d] = cls
        return found

    facts["memory_trend"] = {
        str(threshold): {
            str(days): classes(days, lambda r, t=threshold: memory_percent(r) >= t) for days in (7, 30)
        }
        for threshold in (85, 90)
    }
    facts["disk_trend_lt10"] = {
        str(days): classes(days, lambda r: free_percent(r) < 10.0) for days in (7, 30)
    }

    def compliance_trends(check, days):
        found = {}
        for d in devices:
            observed = [r for r in window(d, days) if status_of(r, check) is not None]
            kind, start = compliance_kind([status_of(r, check) for r in observed])
            if kind in ("persistent_failure", "transition", "recovered"):
                found[d] = {
                    "kind": kind,
                    "change_at": None if start is None else observed[start]["collected_at"],
                }
        return found

    facts["compliance_trend"] = {
        check: {"30": compliance_trends(check, 30)} for check in ("screen_lock", "os_up_to_date")
    }

    notable, missing_latest, no_reading, thin = {}, [], [], []
    battery_projection = {}
    for d in laptops:
        if "battery" not in latest[d]:
            missing_latest.append(d)
        readings = [r for r in window(d, 30) if "battery" in r and r["battery"]["battery_present"]]
        if not readings:
            no_reading.append(d)
            continue
        if len(readings) < MIN_READINGS:
            thin.append(d)
            continue
        capacities = [
            r["battery"]["full_charge_capacity"]
            for r in readings
            if r["battery"].get("full_charge_capacity") is not None
        ]
        change = None
        if len(capacities) >= 2 and capacities[0]:
            change = (capacities[-1] - capacities[0]) / capacities[0] * 100.0
        streak = 0
        for r in reversed(readings):
            if r["battery"].get("condition") != SERVICE:
                break
            streak += 1
        index_points = []
        if capacities and capacities[0] > 0:
            index_points = [
                (ts(r["collected_at"]), r["battery"]["full_charge_capacity"] * 100.0 / capacities[0])
                for r in readings
                if r["battery"].get("full_charge_capacity") is not None
            ]
        projection = project_battery(index_points, as_of)
        severity = battery_severity(streak, change, projection)
        battery_projection[d] = {
            "slope_per_day": projection["slope_per_day"],
            "estimate": projection["estimate"],
            "earliest": projection["earliest"],
            "latest": projection["latest"],
            "status": projection["status"],
            "severity": severity,
            "service_streak": streak,
            "readings": len(readings),
        }
        if (change is not None and change < -DECLINE_PCT) or streak > 0 or severity != "ok":
            notable[d] = {
                "capacity_change_pct": None if change is None else round(change, 6),
                "service_streak": streak,
            }
    facts["battery_30"] = {
        "notable": notable,
        "missing_latest": missing_latest,
        "no_reading": no_reading,
        "insufficient_history": thin,
    }
    by_severity = {
        level: sorted(d for d, v in battery_projection.items() if v["severity"] == level)
        for level in ("act_soon", "watch", "ok")
    }
    facts["battery_insight"] = {
        "devices": battery_projection,
        **by_severity,
        "needs_attention": sorted(by_severity["act_soon"] + by_severity["watch"]),
    }

    # Fleet health: each device at its worst finding across battery, disk, memory and compliance.
    worst = {}

    def raise_to(device, level):
        order = ("act_soon", "watch")
        if device not in worst or order.index(level) < order.index(worst[device]):
            worst[device] = level

    for device in by_severity["act_soon"]:
        raise_to(device, "act_soon")
    for device in by_severity["watch"]:
        raise_to(device, "watch")
    for found in (facts["disk_trend_lt10"]["30"], facts["memory_trend"]["85"]["30"]):
        for device, cls in found.items():
            if cls == "sustained":
                raise_to(device, "act_soon")
            elif cls in ("onset", "intermittent"):
                raise_to(device, "watch")
    for check in CHECKS:
        for device in devices:
            observed = [r for r in window(device, 30) if status_of(r, check) is not None]
            kind, _ = compliance_kind([status_of(r, check) for r in observed])
            if kind not in ("persistent_failure", "transition"):
                continue
            severity = next(
                c["severity"] for c in latest[device]["compliance_results"] if c["check_id"] == check
            )
            if severity in ("high", "critical"):
                raise_to(device, "act_soon")
            elif kind == "transition":
                raise_to(device, "watch")
    act = sorted(d for d, level in worst.items() if level == "act_soon")
    watch = sorted(d for d, level in worst.items() if level == "watch")
    facts["fleet_health"] = {
        "act_soon": act,
        "watch": watch,
        "devices_needing_attention": len(worst),
    }

    common_sets, new_installs = {}, {}
    for days in SOFTWARE_WINDOWS:
        common, installs = software_in_window(devices, rows, latest, as_of, days)
        common_sets[str(days)], new_installs[str(days)] = common, installs
    holders = {}
    for d in devices:
        for app in names_of(latest[d]):
            if app not in common_sets["30"]:
                holders.setdefault(app, []).append(d)
    facts["software"] = {
        "common_set": common_sets,
        "new_installs": new_installs,
        "holders": {app: sorted(found) for app, found in sorted(holders.items())},
    }
    return facts


def generate(raw):
    rows = [json.loads(line) for line in raw.decode("utf-8").split("\n") if line.strip()]
    as_of = max(ts(r["collected_at"]) for r in rows)
    series = {}
    for row in rows:
        series.setdefault((row["company_id"], row["device_id"]), []).append(row)
    for key in series:
        series[key].sort(key=lambda r: r["collected_at"])
    companies = sorted({company for company, _ in series})
    return {
        "meta": {
            "dataset_sha256": hashlib.sha256(raw).hexdigest(),
            "as_of": as_of.strftime(FMT),
            "policy": {
                "low_disk_free_pct_below": 10,
                "memory_used_pct_at_least": [85, 90],
                "windows_days": [7, 30],
                "stale_hours": 48,
                "software_windows_days": list(SOFTWARE_WINDOWS),
            },
        },
        "tenants": {company: tenant_facts(company, series, as_of) for company in companies},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Regenerate the golden answers")
    parser.add_argument("--data", type=Path, default=Path("data/device-telemetry-dataset.ndjson"))
    parser.add_argument("--out", type=Path, default=Path("evals/golden/golden.json"))
    args = parser.parse_args(argv)
    golden = generate(args.data.read_bytes())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(golden, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
