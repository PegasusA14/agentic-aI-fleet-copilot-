# Backend

Python service: dataset loader, tenant-scoped queries, and the agent graph. Run everything through `uv`
(`uv run pytest`, `uv run ruff format src tests`; `make lint` from the repository root).

## Grounding is lexical

The verify gate (`fleet_copilot/agent/verify.py`) decides whether model-written prose may be shown. It
checks numbers, device ids, hostnames, links and markup, characters that hide or imitate text (rejected,
never stripped), a model headline that must restate the numbers of the code-written headline, and, on an
answer, a deny-list of negation, universal and reassurance words (`CLAIM_WORDS`).

It cannot check meaning. A sentence can use only grounded numbers and still be wrong. The code-written
claims, evidence readings and headline remain the source of truth; model prose is an optional rewording that is
dropped, field by field, whenever it fails a check.
