from __future__ import annotations

import argparse
from pathlib import Path

from fleet_copilot.config import EXPECTED_DATASET_SHA256, Settings
from fleet_copilot.data.loader import load_dataset


def main(argv: list[str] | None = None) -> int:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="Load the telemetry NDJSON into SQLite")
    parser.add_argument("--data", type=Path, default=settings.data_path)
    parser.add_argument("--db", type=Path, default=settings.db_path)
    parser.add_argument("--no-verify", action="store_true", help="skip the pinned sha256 check")
    args = parser.parse_args(argv)

    report = load_dataset(
        args.data, args.db, expected_sha256=None if args.no_verify else EXPECTED_DATASET_SHA256
    )
    print(
        f"Loaded {report.snapshots} snapshots, {report.devices} devices, "
        f"{report.companies} companies into {args.db}"
    )
    print(f"Quarantined rows: {len(report.quarantined)}")
    print(f"Dataset sha256 {report.sha256}")
    print(f"Data spans {report.min_collected_at} to {report.max_collected_at}; as of {report.max_collected_at}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
