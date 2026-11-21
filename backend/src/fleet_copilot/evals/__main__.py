"""`python -m fleet_copilot.evals run [--live] [--refresh] [--suite NAME]`

Replay (the default) needs no network and no key: model answers come from the committed cassette, and a
question nobody recorded is a failed case reported as "not recorded". `--live` calls Azure and records every
miss into the same cassette; `--refresh` (live only) deletes the cassette first so every answer is new.
Only this entry point reads `.env`.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime

from fleet_copilot.agent.azure import ChatGateway, build_azure_model
from fleet_copilot.agent.cassette import CassetteGateway
from fleet_copilot.agent.llm import LLMGateway
from fleet_copilot.config import REPO_ROOT, ConfigError, ModelSettings, Settings, load_env
from fleet_copilot.evals import runner
from fleet_copilot.evals.scenarios import ScenarioError, load_suites
from fleet_copilot.evals.store import EvalStore
from fleet_copilot.observability.store import TraceStore

SCENARIOS = REPO_ROOT / "evals" / "scenarios"
CASSETTE = REPO_ROOT / "evals" / "cassettes" / "eval.jsonl"
REPORTS = REPO_ROOT / "evals" / "reports"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m fleet_copilot.evals")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run the evaluation suites")
    run.add_argument("--live", action="store_true", help="call Azure and record misses")
    run.add_argument(
        "--refresh", action="store_true", help="with --live: delete the cassette first"
    )
    run.add_argument("--suite", help="run only this suite")
    return parser


def _gateway(live: bool, refresh: bool) -> LLMGateway:
    if not live:
        return CassetteGateway(CASSETTE)
    models = ModelSettings.from_env({**os.environ, "FLEET_MODEL_MODE": "live"})
    assert models.azure is not None  # live mode without Azure settings raises ConfigError
    if refresh:
        CASSETTE.unlink(missing_ok=True)
    inner = ChatGateway(
        build_azure_model(models.azure, models.call_timeout_s),
        call_timeout_s=models.call_timeout_s,
    )
    return CassetteGateway(CASSETTE, inner=inner)


async def _run(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    state = settings.state_path()
    mode = "eval-live" if args.live else "eval-replay"
    suites = load_suites(SCENARIOS)
    result = await runner.run(
        suites,
        gateway=_gateway(args.live, args.refresh),
        settings=settings,
        store=EvalStore(state / "evals.db"),
        traces=TraceStore(state / "traces.db", model_mode=mode),
        run_id=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        only_suite=args.suite,
        model_mode=mode,
    )
    print(runner.format_summary(result))
    print(f"run {result.run_id} ({mode}); report: {runner.write_report(result, REPORTS)}")
    return runner.exit_code(result)


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
