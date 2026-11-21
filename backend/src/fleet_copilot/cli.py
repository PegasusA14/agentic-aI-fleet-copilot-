"""Ask the agent from a terminal, and check that the model settings work.

Output never includes keys, endpoints or the raw provider error text.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from langchain_core.messages import HumanMessage

from fleet_copilot import policy
from fleet_copilot.agent.azure import ModelUnavailable
from fleet_copilot.agent.gateways import build_gateway
from fleet_copilot.agent.identity import DEMO_IDENTITIES, new_conversation_id
from fleet_copilot.agent.service import (
    AgentService,
    DatasetUnavailable,
    InvalidQuestion,
    TurnResult,
)
from fleet_copilot.config import ConfigError, ModelSettings, Settings, load_env
from fleet_copilot.data.loader import SCHEMA_VERSION, connect, stored_schema_version
from fleet_copilot.observability.store import TraceStore

_AZURE_VARS = (
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_DEPLOYMENT",
)


def render(result: TurnResult, *, show_trace: bool) -> str:
    env = result.envelope
    lines = [env.headline]
    lines += [f"  - {claim.text}" for claim in env.claims]
    if env.explanation:
        lines.append(env.explanation)
    if env.clarification:
        lines.append(f"  ? {env.clarification.question}")
        lines += [f"    * {option}" for option in env.clarification.options]
    lines += [f"  note: {note}" for note in env.notes]
    lines.append(
        f"[{env.outcome}] evidence: {len(env.evidence)} refs, as of {env.scope.as_of.isoformat()}"
    )
    if show_trace:
        lines.append("trace:")
        lines += [
            f"  {e.seq:>2} {e.kind:<11} {e.tool or '-':<24} {e.summary}"
            + (f" ({e.latency_ms} ms)" if e.latency_ms is not None else "")
            for e in result.trace
        ]
    return "\n".join(lines)


async def _ask(args: argparse.Namespace) -> int:
    try:
        settings, models = Settings.from_env(), ModelSettings.from_env()
    except ConfigError as exc:
        print(f"{exc}. Run make doctor.", file=sys.stderr)  # the text names variables, never values
        return 2
    identity = DEMO_IDENTITIES.get(args.as_user)
    if identity is None:
        print(f"unknown user; choose one of: {', '.join(DEMO_IDENTITIES)}", file=sys.stderr)
        return 2
    state = settings.state_path()
    store = TraceStore(state / "traces.db", model_mode=models.mode)
    async with AgentService.open(
        db_path=settings.db_path,
        gateway=build_gateway(models),
        checkpoint_path=state / "cli-checkpoints.db",
        recorder=store.record,
        actions_db_path=state / "cli-actions.db",
    ) as service:
        try:
            result = await service.run_turn(
                identity, args.conversation or new_conversation_id(), args.question
            )
        except InvalidQuestion as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except DatasetUnavailable:
            print("The dataset is not available. Run `make load-data`.", file=sys.stderr)
            return 2
    if args.json:
        print(result.envelope.model_dump_json(indent=2))
    else:
        print(render(result, show_trace=args.trace))
    return 0


async def _probe(models: ModelSettings) -> str:
    try:
        gateway = build_gateway(
            ModelSettings(
                mode=models.mode,
                azure=models.azure,
                cassette_path=models.cassette_path,
                record=False,  # a probe must never write a cassette
                call_timeout_s=models.call_timeout_s,
            )
        )
        await gateway.explain([HumanMessage("Reply with a one-word headline.")])
    except ModelUnavailable as exc:
        return f"failed (ModelUnavailable: {exc.reason})"  # reason is an exception type name
    except Exception as exc:
        return f"failed ({type(exc).__name__})"
    return "ok"


def _db_state(db_path: Path) -> str:
    """ "present", "missing (...)" or "stale (...)": a db built by an older loader lacks tables."""
    if not db_path.exists():
        return "missing (run make load-data)"
    try:
        conn = connect(db_path, read_only=True)
    except Exception:
        return "unreadable (run make load-data)"
    try:
        current = stored_schema_version(conn) == SCHEMA_VERSION
    finally:
        conn.close()
    return "present" if current else "stale (run make load-data)"


def _doctor(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    healthy = True
    raw_mode = (os.environ.get("FLEET_MODEL_MODE") or "replay").strip().lower()
    print(f"mode: {raw_mode if raw_mode in ('replay', 'live') else 'invalid'}")
    for name in _AZURE_VARS:
        present = bool((os.environ.get(name) or "").strip())
        print(f"{name}: {'set' if present else 'missing'}")
        if raw_mode == "live" and not present:
            healthy = False
    try:
        models = ModelSettings.from_env()
    except ConfigError as exc:
        print(f"settings: {exc}")
        return 1
    cassette = Path(models.cassette_path)
    entries = len(cassette.read_text().splitlines()) if cassette.exists() else 0
    print(f"cassette: {entries} recorded responses")
    db_state = _db_state(settings.db_path)
    print(f"dataset db: {db_state}")
    healthy = healthy and db_state == "present"
    print(
        f"limits: {policy.MODEL_CALL_TIMEOUT_S:.0f}s per call, {policy.MODEL_ATTEMPTS} attempts, "
        f"{policy.TURN_TIMEOUT_S}s per turn"
    )
    if args.live:
        if models.mode != "live":
            print("live probe: skipped (FLEET_MODEL_MODE is not live)")
        else:
            outcome = asyncio.run(_probe(models))
            print(f"live probe: {outcome}")
            healthy = healthy and outcome == "ok"
    return 0 if healthy else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fleet-copilot")
    sub = parser.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask", help="ask one question")
    ask.add_argument("question")
    ask.add_argument(
        "--as", dest="as_user", default="admin-acme", help="demo user (default admin-acme)"
    )
    ask.add_argument("--conversation", help="reuse a conversation id for follow-ups")
    ask.add_argument("--trace", action="store_true", help="print the trace steps")
    ask.add_argument("--json", action="store_true", help="print the envelope as JSON")
    doctor = sub.add_parser("doctor", help="check settings without printing secrets")
    doctor.add_argument("--live", action="store_true", help="make one tiny real model call")
    args = parser.parse_args(argv)
    load_env()
    if args.command == "ask":
        return asyncio.run(_ask(args))
    return _doctor(args)


if __name__ == "__main__":
    raise SystemExit(main())
