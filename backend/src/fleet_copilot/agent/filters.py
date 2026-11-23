"""Filters the model adds on its own. A device filter must come from the user's words.

The act model sometimes narrows a call with a filter nobody asked for (`os_name="macOS"` for "which computers are
out of disk space", `form_factor="laptop"` left over from a note the user has since forgotten). The answer then
describes a smaller fleet than the one the user meant, and says so with confidence. Code checks each
`form_factor`, `os_name` and `model_name` value against this turn's question and the conversation's active notes;
a value they do not support is removed before the tool runs. No filter is the wider, safe answer.

Grounding is a word match, so a typo ("laptps") grounds nothing and its filter is dropped: the answer is wider than
asked, never narrower. Other arguments (hostname, employee, device ids, check, severity) are never touched.
"""

from __future__ import annotations

import re
from typing import Any

# What counts as the user naming a value. Matched case-insensitively against the question plus the active notes.
FORM_FACTOR_WORDS = {
    "laptop": re.compile(r"\b(?:laptop|notebook|macbook|thinkpad)\w*", re.IGNORECASE),
    "desktop": re.compile(
        r"\b(?:desktop|imac|workstation|tower)\w*|\bmac\s*(?:mini|studio)\b", re.IGNORECASE
    ),
}
OS_WORDS = {
    "macos": re.compile(
        r"\b(?:macs?|macos|macintosh|macbooks?|imacs?)\b|\bmac\s*(?:mini|studio|os)\b|\bapple\b|\bos\s*x\b",
        re.IGNORECASE,
    ),
    "windows": re.compile(r"\bwindows\b|\bwin\s*1[01]\b|\bwin\b|\bmicrosoft\b", re.IGNORECASE),
}
# Model names are free-form; any one distinctive word of the name is enough. These are too common to count.
MODEL_STOP_WORDS = frozenset({"mac", "pro"})
MODEL_MIN_TOKEN = 3

FILTER_KEYS = ("form_factor", "os_name", "model_name")


def _grounded_by_table(value: Any, table: dict[str, re.Pattern[str]], sources: str) -> bool:
    if not isinstance(value, str):
        return False
    pattern = table.get(value.strip().lower())
    return pattern is not None and pattern.search(sources) is not None


def _model_grounded(value: Any, sources: str) -> bool:
    if not isinstance(value, str):
        return False
    for token in re.split(r"[\s._-]+", value.lower()):
        if len(token) >= MODEL_MIN_TOKEN and token not in MODEL_STOP_WORDS:
            if re.search(rf"\b{re.escape(token)}\w*", sources, re.IGNORECASE):
                return True
    return False


def ungrounded_filters(args: dict[str, Any], sources: str) -> list[str]:
    """Names of the filter arguments whose value `sources` never names, in a fixed order."""
    checks = {
        "form_factor": lambda v: _grounded_by_table(v, FORM_FACTOR_WORDS, sources),
        "os_name": lambda v: _grounded_by_table(v, OS_WORDS, sources),
        "model_name": lambda v: _model_grounded(v, sources),
    }
    return [key for key in FILTER_KEYS if args.get(key) is not None and not checks[key](args[key])]


def grounded_args(args: dict[str, Any], sources: str) -> dict[str, Any]:
    """The call's arguments without the filters the user's words do not support."""
    dropped = set(ungrounded_filters(args, sources))
    return {key: value for key, value in args.items() if key not in dropped}
