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
from datetime import datetime, timedelta, timezone
from pathlib import Path

FMT = "%Y-%m-%dT%H:%M:%SZ"
CHECKS = ("disk_encryption", "screen_lock", "os_up_to_date")
SERVICE = "Service Recommended"
SOFTWARE_WINDOWS = (7, 14, 30)


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
    if not statuses:
        return "insufficient", None
    last = statuses[-1]
    start = len(statuses)
    while start > 0 and statuses[start - 1] == last:
        start -= 1
    if start == 0:
        if len(statuses) < 3:
            return "insufficient", None
        return ("persistent_failure" if last == "fail" else "passing"), None
    return ("transition" if last == "fail" else "recovered"), start


def tenant_facts(company, series, as_of):
    devices = sorted(d for (c, d) in series if c == company)
    rows = {d: series[(company, d)] for d in devices}
    latest = {d: rows[d][-1] for d in devices}
    laptops = [d for d in devices if any("battery" in r and r["battery"]["battery_present"] for r in rows[d])]

    def window(device, days):
        since = as_of - timedelta(days=days)
        return [r for r in rows[device] if ts(r["collected_at"]) >= since]

    facts = {"devices": devices, "laptops": laptops}

    facts["low_disk_lt10"] = {
        "population": len(devices),
        "devices": [d for d in devices if free_percent(latest[d]) < 10.0],
    }

    mac = [d for d in devices if latest[d]["os"]["product_name"] == "macOS"]
    older, unknown = [], []
    for d in mac:
        parts = latest[d]["os"]["product_version"].split(".")
        if not all(p.isdigit() for p in parts):
            unknown.append(d)
        elif int(parts[0]) < 15:
            older.append(d)
    facts["macos_below_15"] = {
        "population": len(mac),
        "devices": older,
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

    notable, missing_latest, no_reading = {}, [], []
    for d in laptops:
        if "battery" not in latest[d]:
            missing_latest.append(d)
        readings = [r for r in window(d, 30) if "battery" in r and r["battery"]["battery_present"]]
        if not readings:
            no_reading.append(d)
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
        if (change is not None and change < -1.0) or streak > 0:
            notable[d] = {
                "capacity_change_pct": None if change is None else round(change, 6),
                "service_streak": streak,
            }
    facts["battery_30"] = {"notable": notable, "missing_latest": missing_latest, "no_reading": no_reading}

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
