"""Golden references: how a scenario points at an expected value in `evals/golden/golden.json`.

Forms (all are JSON objects):

- `{"golden": "<tenant>/<path/with/slashes>"}` is the value at that path under `golden["tenants"]`.
  A segment is a dict key, or a list index when it meets a list.
- `{"golden": ..., "keys_where": "<value>"}` is the keys of a dict whose value (or `value["kind"]`) equals it.
- `{"golden": ..., "keys": true}` is every key of a dict.
- `{"golden": ..., "pluck": "<field>"}` is that field of each item of a list of objects (sorted, unique).
- `{"devices": [...], "source": "<why>"}` is a literal, for facts the golden file lacks; the note is required.

Nothing here imports product code: the expected values must stay independent of what is being tested.
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from fleet_copilot.config import REPO_ROOT

GOLDEN_PATH = REPO_ROOT / "evals" / "golden" / "golden.json"
_MODIFIERS = ("keys_where", "keys", "pluck")


class GoldenRefError(ValueError):
    """A reference is malformed or points at nothing."""


@lru_cache(maxsize=1)
def load_golden(path: Path = GOLDEN_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def is_ref(value: Any) -> bool:
    return isinstance(value, dict) and any(key in value for key in ("golden", "source", "devices"))


def validate_ref(ref: Any) -> str | None:
    """A readable problem with the SHAPE of a reference, or None. It does not look anything up."""
    if not isinstance(ref, dict):
        return "a golden reference must be an object"
    if "golden" in ref:
        path = ref["golden"]
        if (
            not isinstance(path, str)
            or path.count("/") < 1
            or any(not part for part in path.split("/"))
        ):
            return "golden must be '<tenant>/<path>' with no empty segment"
        extra = set(ref) - {"golden", *_MODIFIERS}
        if extra:
            return f"unknown golden reference key {sorted(extra)}"
        used = [m for m in _MODIFIERS if m in ref]
        if len(used) > 1:
            return f"use only one of {_MODIFIERS} (got keys {used})"
        if "keys_where" in ref and not isinstance(ref["keys_where"], str | int | bool):
            return "keys_where must be a plain value"
        if "keys" in ref and ref["keys"] is not True:
            return "keys must be true"
        if "pluck" in ref and not (isinstance(ref["pluck"], str) and ref["pluck"]):
            return "pluck must be a field name"
        return None
    if "devices" in ref or "source" in ref:
        extra = set(ref) - {"devices", "source"}
        if extra:
            return f"unknown literal key {sorted(extra)}"
        devices = ref.get("devices")
        if not isinstance(devices, list) or not all(isinstance(d, str) and d for d in devices):
            return "a literal needs a list of device ids in 'devices'"
        source = ref.get("source")
        if not isinstance(source, str) or not source.strip():
            return "a literal needs a 'source' note saying why the golden file lacks the fact"
        return None
    return (
        "not a golden reference: expected a 'golden' path or a literal with 'devices' and 'source'"
    )


def _walk(golden: dict[str, Any], path: str) -> Any:
    tenant, *rest = path.split("/")
    tenants = golden.get("tenants", {})
    if tenant not in tenants:
        raise GoldenRefError(f"golden has no tenant {tenant!r} (in {path!r})")
    node: Any = tenants[tenant]
    walked = tenant
    for part in rest:
        walked = f"{walked}/{part}"
        if isinstance(node, dict):
            if part not in node:
                raise GoldenRefError(f"golden has no entry {walked!r}")
            node = node[part]
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                raise GoldenRefError(f"golden has no entry {walked!r}") from None
        else:
            raise GoldenRefError(f"golden has no entry {walked!r} (the parent is a plain value)")
    return node


def resolve(ref: dict[str, Any], golden: dict[str, Any]) -> Any:
    """The value a reference points at, in the golden dict's own order."""
    problem = validate_ref(ref)
    if problem is not None:
        raise GoldenRefError(f"{problem}: {ref!r}")
    if "golden" not in ref:
        return list(ref["devices"])
    value = _walk(golden, ref["golden"])
    if "keys" in ref or "keys_where" in ref:
        if not isinstance(value, dict):
            raise GoldenRefError(f"{ref['golden']!r} is not an object, so it has no keys")
        if "keys" in ref:
            return list(value)
        wanted = ref["keys_where"]
        return [
            key
            for key, item in value.items()
            if (item["kind"] if isinstance(item, dict) and "kind" in item else item) == wanted
        ]
    if "pluck" in ref:
        field = ref["pluck"]
        if not isinstance(value, list) or not all(
            isinstance(i, dict) and field in i for i in value
        ):
            raise GoldenRefError(
                f"{ref['golden']!r} is not a list of objects that all have {field!r}"
            )
        return sorted({item[field] for item in value})
    return copy.deepcopy(value)


def _suggestion_pairs(items: list[dict[str, Any]], golden: dict[str, Any]) -> list[dict[str, Any]]:
    """One `{action_type, device}` pair per device a reference names, in the reference's order."""
    pairs: list[dict[str, Any]] = []
    for item in items:
        device = item["device"]
        devices = resolve(device, golden) if is_ref(device) else [device]
        pairs.extend({"action_type": item["action_type"], "device": d} for d in devices)
    return pairs


def resolve_expect(expect: dict[str, Any], golden: dict[str, Any]) -> dict[str, Any]:
    """A copy of a turn's `expect` with every reference replaced by its value, ready for `check_turn`."""
    resolved: dict[str, Any] = {}
    for key, value in expect.items():
        if key == "proposal":
            card = dict(value)
            if is_ref(card.get("target")):
                targets = resolve(card["target"], golden)
                if not isinstance(targets, list) or len(targets) != 1:
                    raise GoldenRefError(
                        f"a proposal target must resolve to exactly one device, got {targets!r}"
                    )
                card["target"] = targets[0]
            resolved[key] = card
        elif key == "suggestions_include":
            resolved[key] = _suggestion_pairs(value, golden)
        elif is_ref(value):
            resolved[key] = resolve(value, golden)
        else:
            resolved[key] = copy.deepcopy(value)
    return resolved
