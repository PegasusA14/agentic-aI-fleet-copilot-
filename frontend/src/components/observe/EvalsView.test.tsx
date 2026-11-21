import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../lib/api";
import type { EvalCase, EvalRun, EvalRunDetail, Transport } from "../../lib/transport";
import { EvalsView } from "./EvalsView";

const tally = (passed: number, total: number, not_recorded = 0) => ({
  passed,
  total,
  rate: total ? passed / total : 0,
  not_recorded,
});

const RUNS: EvalRun[] = [
  {
    run_id: "run-b",
    started_at: "2026-06-12T11:30:00Z",
    model_mode: "eval-replay",
    dataset_sha256: "abcdef0123456789".repeat(4),
    as_of: "2026-06-12T11:00:00Z",
    policy: {},
    totals: {
      suites: { grounding: tally(12, 12), actions: tally(3, 5), guardrails: tally(0, 2, 2) },
      overall: tally(15, 19, 2),
    },
  },
  {
    run_id: "run-a",
    started_at: "2026-06-12T10:00:00Z",
    model_mode: "eval-live",
    dataset_sha256: "1234567890abcdef".repeat(4),
    as_of: "2026-06-12T11:00:00Z",
    policy: {},
    totals: {},
  },
];

const CASES: EvalCase[] = [
  {
    instance_id: "grounding.low_disk_acme#1",
    suite: "grounding",
    case_id: "low_disk_acme",
    phrasing_index: 1,
    user: "admin-acme",
    questions: ["Which devices are low on disk space?"],
    passed: true,
    checks: [{ name: "outcome", passed: true, detail: "outcome is answer", turn: 1 }],
    trace_ids: ["trace-pass"],
    effects: [],
  },
  {
    instance_id: "grounding.low_disk_acme#2",
    suite: "grounding",
    case_id: "low_disk_acme",
    phrasing_index: 2,
    user: "admin-acme",
    questions: ["low disk?"],
    passed: false,
    checks: [
      { name: "outcome", passed: true, detail: "outcome is answer", turn: 1 },
      { name: "devices", passed: false, detail: "missing D1 from D2, D3", turn: 1 },
    ],
    trace_ids: ["trace-fail"],
    effects: [],
  },
  {
    instance_id: "actions.ticket_x#1",
    suite: "actions",
    case_id: "ticket_x",
    phrasing_index: 1,
    user: "admin-globex",
    questions: ["Open a ticket"],
    passed: true,
    checks: [{ name: "proposal", passed: true, detail: "ok", turn: 1 }],
    trace_ids: ["trace-action"],
    effects: [
      { step: 1, do: "approve", user: null, status: "executed", duplicate: false, error: null, before: { tickets: 0 }, after: { tickets: 1 }, delta: { tickets: 1 } },
      { step: 2, do: "approve_again", user: null, status: "executed", duplicate: true, error: null, before: { tickets: 1 }, after: { tickets: 1 }, delta: {} },
    ],
  },
  {
    instance_id: "guardrails.other_company#1",
    suite: "guardrails",
    case_id: "other_company",
    phrasing_index: 1,
    user: "admin-initech",
    questions: ["Show Globex's devices"],
    passed: false,
    checks: [{ name: "recorded", passed: false, detail: "not recorded", turn: 1 }],
    trace_ids: ["trace-missed"],
    effects: [],
  },
];

const DETAIL: EvalRunDetail = { ...RUNS[0], cases: CASES };

function fake(overrides: Partial<Transport> = {}) {
  return {
    mode: "live",
    observeEvals: vi.fn(async () => RUNS),
    observeEval: vi.fn(async () => DETAIL),
    observeTrace: vi.fn(async (traceId: string) => ({
      trace_id: traceId,
      events: [
        { trace_id: traceId, seq: 1, kind: "scope", tool: null, summary: "Scoped to Acme", latency_ms: 2, data: {} },
      ],
    })),
    ...overrides,
  } as unknown as Transport;
}

function renderView(transport: Transport, onSessionEnded: () => void = () => {}) {
  return render(<EvalsView transport={transport} nonce={0} onSessionEnded={onSessionEnded} />);
}

describe("the Evaluations tab", () => {
  it("lists runs with mode, short dataset sha, as-of and per-suite results as mark plus word", async () => {
    renderView(fake());
    const table = await screen.findByRole("table", { name: "Evaluation runs" });
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual(["Started", "Mode", "Dataset", "As of", "Grounding", "Actions", "Guardrails", "Overall"]);
    const [first, second] = within(table).getAllByRole("row").slice(1);

    expect(within(first).getByText("eval-replay")).toBeInTheDocument();
    expect(within(first).getByText("abcdef012345")).toHaveClass("mono");
    expect(within(first).getByText("2026-06-12 11:00 UTC")).toBeInTheDocument();
    const cells = within(first).getAllByRole("cell");
    expect(cells[4]).toHaveTextContent("Passed");
    expect(cells[4]).toHaveTextContent("12/12");
    expect(cells[5]).toHaveTextContent("Failed");
    expect(cells[5]).toHaveTextContent("3/5");
    expect(cells[6]).toHaveTextContent("Not recorded");
    expect(cells[6]).toHaveTextContent("0/2");
    expect(cells[7]).toHaveTextContent("Failed");
    expect(cells[7]).toHaveTextContent("15/19");

    // A run that never finished stores no totals and says so.
    const unfinished = within(second).getAllByRole("cell");
    expect(unfinished[4]).toHaveTextContent("—");
    expect(unfinished[7]).toHaveTextContent("Not finished");
  });

  it("says plainly when there are no runs, and when the list cannot be loaded", async () => {
    const empty = renderView(fake({ observeEvals: vi.fn(async () => []) }));
    expect(await screen.findByText("No evaluation runs yet. Run make eval.")).toBeInTheDocument();
    empty.unmount();

    const user = userEvent.setup();
    const observeEvals = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(500, "boom"))
      .mockResolvedValue(RUNS);
    renderView(fake({ observeEvals }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The evaluation runs could not be loaded.");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table", { name: "Evaluation runs" })).toBeInTheDocument();
  });

  it("ends the session when a request finds it gone", async () => {
    const ended = vi.fn();
    renderView(fake({ observeEvals: vi.fn().mockRejectedValue(new ApiError(401, "sign in first")) }), ended);
    await waitFor(() => expect(ended).toHaveBeenCalled());
  });

  it("shows a run's cases with a result word, the failing checks in plain text and the company", async () => {
    const user = userEvent.setup();
    const transport = fake();
    renderView(transport);
    expect(transport.observeEval).not.toHaveBeenCalled();
    await user.click(await screen.findByRole("button", { name: /2026-06-12 11:30 UTC/ }));
    expect(transport.observeEval).toHaveBeenCalledWith("run-b");

    const table = await screen.findByRole("table", { name: "Cases in run run-b" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Suite",
      "Case",
      "Phrasing",
      "Company",
      "Result",
      "Details",
      "Effects",
      "Trace",
    ]);
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(4);

    expect(within(rows[0]).getByText("Grounding")).toBeInTheDocument();
    expect(within(rows[0]).getByText("low_disk_acme")).toHaveClass("mono");
    expect(within(rows[0]).getByText("Acme")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Passed")).toBeInTheDocument();

    expect(within(rows[1]).getByText("2")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Failed")).toBeInTheDocument();
    expect(within(rows[1]).getByText("devices: missing D1 from D2, D3")).toBeInTheDocument();
    expect(within(rows[1]).queryByText(/outcome is answer/)).not.toBeInTheDocument();
    expect(within(rows[1]).getByText("low disk?")).toBeInTheDocument();
  });

  it("tells a case that was never recorded apart from one that failed", async () => {
    const user = userEvent.setup();
    renderView(fake());
    await user.click(await screen.findByRole("button", { name: /2026-06-12 11:30 UTC/ }));
    const rows = within(await screen.findByRole("table", { name: "Cases in run run-b" })).getAllByRole("row").slice(1);
    const missed = rows[3];
    expect(within(missed).getByText("Not recorded")).toBeInTheDocument();
    expect(within(missed).queryByText("Failed")).not.toBeInTheDocument();
    expect(within(missed).getByText(/make eval-live/)).toBeInTheDocument();
    expect(within(missed).getByText("Initech")).toBeInTheDocument();
    const failed = within(rows[1]).getByText("Failed").closest(".mark");
    const notRecorded = within(missed).getByText("Not recorded").closest(".mark");
    expect(failed?.getAttribute("data-tone")).not.toBe(notRecorded?.getAttribute("data-tone"));
  });

  it("words an action case's effect changes, simulated", async () => {
    const user = userEvent.setup();
    renderView(fake());
    await user.click(await screen.findByRole("button", { name: /2026-06-12 11:30 UTC/ }));
    const rows = within(await screen.findByRole("table", { name: "Cases in run run-b" })).getAllByRole("row").slice(1);
    expect(within(rows[2]).getByText("Tickets +1 (simulated)")).toBeInTheDocument();
    expect(within(rows[2]).getByText("No effect change")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Globex")).toBeInTheDocument();
    expect(within(rows[0]).queryByText(/simulated/)).not.toBeInTheDocument();
  });

  it("opens a case's trace through the console's trace timeline", async () => {
    const user = userEvent.setup();
    const transport = fake();
    renderView(transport);
    await user.click(await screen.findByRole("button", { name: /2026-06-12 11:30 UTC/ }));
    const rows = within(await screen.findByRole("table", { name: "Cases in run run-b" })).getAllByRole("row").slice(1);
    expect(transport.observeTrace).not.toHaveBeenCalled();
    await user.click(within(rows[1]).getByRole("button", { name: /^Trace/ }));
    expect(transport.observeTrace).toHaveBeenCalledWith("trace-fail");
    const steps = await screen.findByRole("list", { name: "Trace steps" });
    expect(within(steps).getByText("Scoped to Acme")).toBeInTheDocument();
    expect(within(rows[1]).getByRole("button", { name: /^Trace/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("labels an example run from the offline replay", async () => {
    const user = userEvent.setup();
    const example = { ...RUNS[0], model_mode: "fixtures" };
    renderView(
      fake({
        observeEvals: vi.fn(async () => [example]),
        observeEval: vi.fn(async () => ({ ...DETAIL, model_mode: "fixtures" })),
      }),
    );
    await screen.findByRole("table", { name: "Evaluation runs" });
    expect(screen.getByText(/example run for the offline replay/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /2026-06-12 11:30 UTC/ }));
    expect(await screen.findByRole("table", { name: "Cases in run run-b" })).toBeInTheDocument();
  });

  it("says plainly when a run's cases cannot be loaded", async () => {
    const user = userEvent.setup();
    renderView(fake({ observeEval: vi.fn().mockRejectedValue(new ApiError(404, "not found")) }));
    await user.click(await screen.findByRole("button", { name: /2026-06-12 11:30 UTC/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The cases of this run could not be loaded.");
  });
});
