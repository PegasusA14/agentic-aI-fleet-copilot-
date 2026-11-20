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

Evidence refs contain `#`, which a URL path would drop, so they are fetched with the `ref` query parameter.

### Recording and replaying

`make live-check` runs six demo questions against the live model, checks each answer against the independent
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
`live-check-checkpoints.db`, in the same folder. Set `FLEET_STATE_DIR` to move all of them.

### Limits

30 s per model call, 3 attempts per call (rate limits, timeouts, connection and 5xx errors only), and 120 s
per turn.

### Troubleshooting

- `401` from Azure: the key or the endpoint is wrong.
- `404 DeploymentNotFound`: the deployment name (`AZURE_OPENAI_DEPLOYMENT`) is wrong.
- `400` that mentions `api-version`: the API version is wrong.
- A rejection of `temperature`: the deployment is a reasoning model. Use a non-reasoning deployment.

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
- **Newly installed**: an app outside the common set whose first appearance on a device is a later snapshot than
  that device's earliest one in the window. The answer says "not in the common set" and "newly installed" and
  never "unapproved", "risky" or "malicious": the telemetry carries no such label. A short window is not an
  error: asking about the last 7 days returns a no-match (`no_matching_records`) on this dataset, because every
  first-seen date is older than 7 days before the as-of time.
- **Verify gate (`judgment_word`)**: it applies only to answers that cite `#software[...]` evidence. It rejects a
  model-written headline or explanation that labels apps (risky, unapproved, malicious, not approved, and
  similar inflected or negated forms). Code-written claims never use such words. A rejected explanation is
  dropped, and a rejected headline falls back to the code headline.
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
