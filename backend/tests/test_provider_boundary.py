"""Only the providers module knows provider names: everything else asks the adapter."""

import re
from pathlib import Path

from fleet_copilot.agent.providers import ADAPTERS

SRC = Path(__file__).resolve().parents[1] / "src" / "fleet_copilot"
NAMES = "|".join(re.escape(name) for name in ADAPTERS)
# a provider name compared, tested for membership, matched on, or used as a dictionary key
BRANCH = re.compile(
    rf"""(?:==|!=|\bis\b|\bin\b|\bcase\b|\bget\()\s*[(\[{{]?\s*["'](?:{NAMES})["']"""
    rf"""|["'](?:{NAMES})["']\s*(?:==|!=|:\s|\bin\b)""",
    re.IGNORECASE,
)


def test_no_module_outside_the_providers_module_branches_on_a_provider_name():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "providers.py":
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if BRANCH.search(code):
                offenders.append(f"{path.relative_to(SRC)}:{number}")
    assert offenders == []


def test_the_pattern_catches_the_branches_it_is_meant_to_catch():
    for line in (
        'if settings.provider == "azure":',
        "if provider in ('openai', 'openrouter'):",
        'x = {"openrouter": 1}',
        'match p:\n    case "openai":',
        'if "azure" == provider:',
    ):
        assert BRANCH.search(line), line
    assert not BRANCH.search('provider: str = "azure"  # a key of ADAPTERS')
