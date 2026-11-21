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

Two limits are inherent: cross-script homoglyphs (for example Cyrillic letters that look Latin) and number glyphs
that are not digits (for example CJK numerals) are not detected. Code-written claims and evidence remain the source
of truth.

## Running the API and the model

The agent talks to a model through a gateway chosen by `FLEET_MODEL_MODE`:

- `replay` (the default) answers from a recorded cassette and needs no credentials. It reads
  `FLEET_CASSETTE_PATH` (default `var/cassettes/live.jsonl`). A question nobody recorded gets the fixed
  tool-error text, not a made-up answer.
- `live` calls an Azure OpenAI deployment. It needs `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`,
  `AZURE_OPENAI_API_VERSION` and `AZURE_OPENAI_DEPLOYMENT` in the git-ignored `.env`. Set
  `FLEET_CASSETTE_RECORD=1` to also record each response to the cassette.

Keep LangSmith tracing (`LANGSMITH_*`, `LANGCHAIN_*`) off: it exports prompts and answers off-box and must
never be on for tenant data.

Other variables: `FLEET_DATA_PATH`, `FLEET_DB_PATH`, `FLEET_STATE_DIR`, `FLEET_PORT`, `FLEET_ALLOWED_ORIGINS`,
`FLEET_SESSION_SECRET`, `FLEET_OPERATOR_PASSCODE`.

Run `make doctor` first. It prints which variables are set or missing (never their values), the mode, the
number of recorded cassette responses, whether the dataset database exists, and, in live mode, makes one
tiny real model call that proves the deployment name and API version. `make ask Q="Which devices are low on
disk space?"` asks one question from the terminal and prints the trace.

`make api` serves on `127.0.0.1:$FLEET_PORT` (default 8000) only; the host is not configurable. Sign in with a demo key
(`admin-acme`, `admin-globex`, `admin-initech`) through `POST /api/session`. The `operator` key also needs
`FLEET_OPERATOR_PASSCODE` to be set and sent as `passcode`; without it operator sign-in is disabled.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | liveness |
| `GET /api/demo-users` | the demo sign-in keys |
| `POST`, `GET`, `DELETE /api/session` | sign in, current user, sign out |
| `POST /api/conversations`, `DELETE /api/conversations/{id}` | create and delete a conversation |
| `POST /api/chat` | ask a question; the answer streams as server-sent events |
| `GET /api/evidence?ref=` | resolve one evidence reference |
| `GET /api/traces/{trace_id}` | the steps of one of your own turns |
| `GET /api/policy` | the defaults and limits in force |
| `GET /api/observe/turns`, `GET /api/observe/traces/{trace_id}` | operator console reads |
| `GET /api/proposals`, `GET /api/proposals/{id}`, `POST .../approve`, `POST .../reject`, `GET /api/audit` | action proposals, decisions and the audit log (see "Actions, approval and audit") |
| `GET /api/observe/audit`, `GET /api/observe/effects` | operator reads of the audit log and the simulated-effect counts |

Evidence refs contain `#`, which a URL path would drop, so they are fetched with the `ref` query parameter.

### Recording and replaying

`make live-check` runs seven demo questions against the live model, checks each answer against the independent
golden file, and records the model responses to `var/cassettes/live-check.jsonl`. Replay the same questions
offline with `FLEET_MODEL_MODE=replay` and `FLEET_CASSETTE_PATH=var/cassettes/live-check.jsonl` (replay
defaults to `var/cassettes/live.jsonl`, a different file); `tests/test_live_replay.py` does this automatically once the cassette
exists and is skipped until then.

The recording is read-through: when `var/cassettes/live-check.jsonl` already exists, `make live-check` replays
the recorded hits and only calls the model for questions that are not in the file. A re-run therefore does NOT
re-test the live model. Delete the file to record again.

### Where state lives

Traces are in `var/traces.db` and the API's conversation checkpoints in `var/checkpoints.db`. The terminal
`ask` command keeps its own checkpoints in `cli-checkpoints.db` and `make live-check` in
`live-check-checkpoints.db`, in the same folder. Action proposals, their simulated effects and the audit log are in
`actions.db` (`cli-actions.db` for `ask`, `live-check-actions.db` for `make live-check`). Set `FLEET_STATE_DIR` to
move all of them.

### Limits

30 s per model call, 3 attempts per call (rate limits, timeouts, connection and 5xx errors only), and 120 s
per turn.

### Troubleshooting

- `401` from Azure: the key or the endpoint is wrong.
- `404 DeploymentNotFound`: the deployment name (`AZURE_OPENAI_DEPLOYMENT`) is wrong.
- `400` that mentions `api-version`: the API version is wrong.
- A rejection of `temperature`: the deployment is a reasoning model. Use a non-reasoning deployment.

## Actions, approval and audit

The agent can propose four actions from the SOP. A proposal is a pending record, never an effect: only a person
approves it, through the API, and execution is simulated. The model has no tool that approves, rejects or
executes anything, and no text it writes reaches a ticket, order, flag, notification, proposal card or audit
entry; all of that is written by code from the eligibility result.

### The four actions

The model has one tool, `propose_action(action_type, device_id, check_id?, component?, topic?)`. It proposes ONE
action for ONE device and works only when the planner classed the turn as `propose`; on any other turn it is an
input error. At most one proposal is made per turn (extra calls in the same message are dropped), and a request
that resolves to several devices ("flag all of them") is refused as `unsupported_action`. Eligibility is decided
in `actions/eligibility.py` from the signed-in company's own data, as of the dataset's pinned time. A latest
reading older than `STALE_HOURS` (48 h) before that time is `insufficient_evidence`.

| Action | Parameters (all code-derived) | Eligible when |
| --- | --- | --- |
| `remediation_ticket` | `check_id` (enumerated), `severity` (from the data) | that compliance check is `fail` in the latest snapshot |
| `replacement_flag` | `reason = battery_service_recommended` | the last 3 or more battery readings in the 30-day window (`REPLACEMENT_MIN_SNAPSHOTS`), ending at the latest one, are all "Service Recommended" |
| `upgrade_order` | `component` (`memory` or `storage`), `current`, `target` | the 7-day memory (85% or more used) or disk (under 10% free) trend is `sustained`, and a larger standard size exists |
| `employee_notification` | `topic` (`low_disk`, `memory_pressure`, `failing_check`, `battery_service`) and `message` | the topic's finding is true on the latest reading |

Upgrade targets come from `policy.py`: memory doubles the current total; storage steps through 256, 512, 1024 and
2048 GiB (shown as "512 GiB", "1 TiB", "2 TiB"); at 2 TiB or more there is no larger standard size and the
action is not eligible. A notification's text is a fixed template per topic filled from the latest reading, and
it is part of the payload hash, so the approver approves the exact words. The employee is resolved by the server
from the device and is never shown to the model.

An action that is not eligible is not proposed. The answer is `denied` with reason `not_eligible` (the evidence
does not support it) or `insufficient_evidence` (a parameter is missing, there are too few readings, or the
latest one is stale), a fixed headline and a code-written note saying why. An unknown device, or one that belongs
to another company, is the same uniform `not_found` as everywhere else. Every refusal is written to the audit log.

### Proposals, approval and execution

- **Idempotent proposals.** The payload hash is a SHA-256 over the company, action type, device and parameters.
  If a pending proposal with the same hash exists it is returned instead of a new one, so a model repeat or the
  tool wrapper's one retry cannot create a duplicate.
- **Hash-bound approval.** `POST /api/proposals/{id}/approve` takes `{"payload_hash": "..."}`, the hash on the
  card the admin was shown. A different hash is `409 {"detail": "proposal changed"}` and changes nothing.
- **Order of checks on approve.** The caller must be an admin (the operator is `403`); the proposal must belong
  to the caller's company (a foreign or unknown id is `404 {"detail": "not found"}`, identical for both);
  then its status, then the hash. Only a `pending` proposal can be approved.
- **Single use.** Approving an `executed` proposal again is `200` with `"duplicate": true` and no second effect
  (audit `duplicate_approval`; each effect table also has `UNIQUE (proposal_id)`). A proposal that is rejected,
  invalidated or failed answers `409 {"detail": "proposal is <status>"}`.
- **Re-check at execution.** Approval does not trust the stored card. It runs the eligibility check again on the
  current data with the stored parameters. If the device is no longer eligible, or the parameters or hash would
  now differ, the proposal becomes `invalidated` and nothing is executed.
- **Simulated executors.** An eligible approval inserts one row in `tickets`, `replacement_flags`,
  `upgrade_orders` or `employee_notifications` with `simulated = 1`. Nothing leaves the process, and every
  effect text says "(simulated)". The executor, the status change and the audit entries are one transaction; an
  executor exception is rolled back at a savepoint and the proposal becomes `failed`, with only the exception
  type name recorded.
- **Rejection.** `POST /api/proposals/{id}/reject` (admin, pending only) sets `rejected`. No effect rows are
  ever written for a rejected proposal.

### Audit

`audit_log` in `actions.db` is append-only, enforced by SQLite triggers that abort any `UPDATE` or `DELETE`
(`audit log is append-only`), whatever code runs. It records `proposal_created`, `proposal_refused`,
`proposal_approved`, `proposal_rejected`, `proposal_executed`, `proposal_invalidated`, `proposal_failed`,
`approval_refused` (wrong role, foreign id, wrong status or wrong hash) and `duplicate_approval`, each with the
actor, company, proposal, trace id, action type, target device, payload hash and evidence refs. It holds ids and
refs, never raw telemetry beyond the code-written text on the card.

### Endpoints

| Endpoint | Who | Result |
| --- | --- | --- |
| `GET /api/proposals?status=` | admin | the company's proposal cards |
| `GET /api/proposals/{id}` | admin | one card; `404` if foreign or unknown |
| `POST /api/proposals/{id}/approve` | admin | `200 {"proposal", "effect", "duplicate"}`; `404`; `409` |
| `POST /api/proposals/{id}/reject` | admin | `200 {"proposal"}`; `404`; `409` |
| `GET /api/audit?proposal_id=&target=` | admin | the company's own audit entries |
| `GET /api/observe/audit?company=&actor=&target=` | operator | audit entries across companies |
| `GET /api/observe/effects` | operator | simulated effect row counts per table and company |

Auth is checked before the body is validated, a foreign `Origin` is refused on every `POST`, and there is no CORS.
`make live-check` includes a `remediation_ticket` case that asks for a proposal and checks it is pending for the
right device; it never approves anything.

## Installed software

Each telemetry row's `installed_software` (name, version, publisher) is loaded into the `software` table, one
row per app per snapshot, and read through the same tenant filter as compliance results. A name is 1 to 60
characters of letters, digits, space and `._+-` (it starts with a letter or digit and has no space at either
end). A version is 1 to 40 characters of letters, digits and `._+-`, starting with a letter or digit. A
publisher is 1 to 80 characters of letters, digits, space and `._,&+'-`, starting with a letter or digit and
with no space at either end. A row holds at most 200 apps. Anything outside these shapes quarantines the whole
row. `network` is still dropped at load.

- **Common set**: the apps present on every device of the company in that device's earliest snapshot inside the
  window. The default window is `SOFTWARE_WINDOW_DAYS`, 30 days, counted back from the dataset's as-of time;
  `window_days` can ask for 1 to 60.
  A device whose first in-window snapshot reports no apps makes the common set empty (the golden-answer oracle
  uses the same definition); the inventory view's shared set, by contrast, ignores devices that report no apps.
- **Newly installed**: an app outside the common set whose first appearance on a device is a later snapshot than
  that device's earliest one in the window. The answer says "not in the common set" and "newly installed" and
  never "unapproved", "risky" or "malicious": the telemetry carries no such label. A short window is not an
  error: asking about the last 7 days returns a no-match (`no_matching_records`) on this dataset, because every
  first-seen date is older than 7 days before the as-of time.
- **Verify gate (`judgment_word`)**: it applies only to answers that cite `#software[...]` evidence. It rejects a
  model-written headline or explanation that labels apps (risky, unapproved, malicious, not approved, and
  similar inflected or negated forms). Code-written claims never use such words. A rejected explanation is
  dropped, and a rejected headline falls back to the code headline.
  The gate is lexical: it rejects label words and ungrounded numbers or ids, but it does not check app names or
  descriptive phrases, so the code-written claims and the cited evidence remain the source of truth.
- **Tool**: `software_inventory(view, app_name, window_days, device_ids)`. `current` lists the installed apps
  (or the devices with one app); `new_installs` lists one entry per device and app with the first-seen time. An
  app name the company has never reported is an input error that lists the company's own app names, so an app
  only another company has reads the same as one nobody has.
- **Evidence**: `#software[<name>]` resolves to `{name, version, publisher}` and is fetched like any other
  reference (`GET /api/evidence?ref=...`, with the spaces URL-encoded). A full ref is
  `<tenant>/<device>@<collected_at>#software[<name>]`, for example
  `initech-003/W2656QDAE2ZJ@2026-06-02T10:45:00Z#software[uTorrent]`.
- **Golden answers**: `make golden` recomputes the common sets, new installs and holders with the stdlib-only
  oracle; `make fixtures` regenerates the replay fixtures (including `new_software`).
- **Limit**: the verify gate compares each number on its own, so a date written from numbers that all appear in
  the facts (for example a different day in the same month) is not caught; the code-written claims and
  evidence remain the source of truth.
