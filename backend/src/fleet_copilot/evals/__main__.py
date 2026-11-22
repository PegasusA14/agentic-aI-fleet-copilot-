"""`python -m fleet_copilot.evals run [--live] [--refresh] [--suite NAME] [--fail-under RATE] [--write-results PATH]`

Replay (the default) needs no network and no key: model answers come from the committed cassette, and a
question nobody recorded is a failed case reported as "not recorded". `--live` calls the configured provider
(`FLEET_MODEL_PROVIDER`) and records every
miss into the same cassette; `--refresh` (live only) deletes the cassette first so every answer is new.
Only this entry point reads `.env`.

The exit code is the CI gate: 0 ok, 1 gate failed, 2 cannot run. By default every case must pass;
`--fail-under RATE` (0 to 1) lets the overall pass rate be lower. A tenant leak or an approval bypass fails the
run whatever the rate, and a case that was not recorded counts as a failure.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from fleet_copilot.agent.cassette import CassetteGateway
from fleet_copilot.agent.gateways import build_live_gateway
from fleet_copilot.agent.llm import LLMGateway
from fleet_copilot.config import REPO_ROOT, ConfigError, ModelSettings, Settings, load_env
from fleet_copilot.evals import results, runner
from fleet_copilot.evals.scenarios import ScenarioError, load_suites
from fleet_copilot.evals.store import EvalStore
from fleet_copilot.observability.store import TraceStore

SCENARIOS = REPO_ROOT / "evals" / "scenarios"
CASSETTE = REPO_ROOT / "evals" / "cassettes" / "eval.jsonl"
REPORTS = REPO_ROOT / "evals" / "reports"


def _rate(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number from 0 to 1") from None
    if not 0.0 <= value <= 1.0:
        raise argparse.ArgumentTypeError("must be a number from 0 to 1")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m fleet_copilot.evals")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run the evaluation suites")
    run.add_argument(
        "--live", action="store_true", help="call the configured provider and record misses"
    )
    run.add_argument(
        "--refresh", action="store_true", help="with --live: delete the cassette first"
    )
    run.add_argument("--suite", help="run only this suite")
    run.add_argument(
        "--fail-under",
        type=_rate,
        metavar="RATE",
        help="exit 1 if the overall pass rate (0 to 1) is below RATE; default: every case must pass",
    )
    run.add_argument(
        "--write-results",
        type=Path,
        metavar="PATH",
        help="write a markdown summary (suites, metrics, failing instances) to PATH",
    )
    return parser


def _live_models() -> ModelSettings:
    return ModelSettings.from_env({**os.environ, "FLEET_MODEL_MODE": "live"})


def _gateway(live: bool, refresh: bool) -> LLMGateway:
    if not live:
        return CassetteGateway(CASSETTE)
    models = _live_models()
    if refresh:
        CASSETTE.unlink(missing_ok=True)
    return CassetteGateway(CASSETTE, inner=build_live_gateway(models))


async def _run(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    state = settings.state_path()
    mode = "eval-live" if args.live else "eval-replay"
    suites = load_suites(SCENARIOS)
    models = (
        _live_models() if args.live else None
    )  # the provider, label and temperature recorded with the run
    result = await runner.run(
        suites,
        gateway=_gateway(args.live, args.refresh),
        settings=settings,
        store=EvalStore(state / "evals.db"),
        traces=TraceStore(state / "traces.db", model_mode=mode),
        run_id=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        only_suite=args.suite,
        model_mode=mode,
        provider=models.provider if models else None,
        model_name=models.model_name if models else None,
        temperature=models.temperature if models else None,
    )
    print(runner.format_summary(result))
    print(f"run {result.run_id} ({mode}); report: {runner.write_report(result, REPORTS)}")
    if args.write_results is not None:
        print(f"results: {results.write_results(result, args.write_results)}")
    failures = runner.gate_failures(result, args.fail_under)
    for reason in failures:
        print(f"gate failed: {reason}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.refresh and not args.live:
        _parser().error("--refresh only makes sense with --live")
    try:
        return asyncio.run(_run(args))
    except (ConfigError, ScenarioError, ValueError) as exc:  # fixed, value-free messages
        print(f"eval could not run: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # anything else is reported as a type, never a traceback with values
        print(f"eval could not run: {type(exc).__name__}. Try `make load-data`.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    load_env()
    raise SystemExit(main())
