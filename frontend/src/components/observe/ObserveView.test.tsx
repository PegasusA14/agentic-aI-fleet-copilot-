import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../lib/api";
import { getFixture } from "../../lib/fixtures";
import type { AuditEntry, ObservedTurn, Transport } from "../../lib/transport";
import type { TraceEvent } from "../../lib/types";
import { ObserveView } from "./ObserveView";

const FIXTURE = getFixture("acme-001", "low_disk");
const STEPS: TraceEvent[] = [
  ...FIXTURE.trace.slice(0, 3),
  {
    trace_id: FIXTURE.envelope.trace_id,
    seq: 4,
    kind: "error",
    tool: "fleet_query",
    summary: "The lookup did not complete",
    latency_ms: 31,
    data: { attempts: 2 },
  },
];

const TURNS: ObservedTurn[] = [
  {
    trace_id: FIXTURE.envelope.trace_id,
    company_id: "acme-001",
    actor_id: "admin-acme",
    conversation_id: "c1",
    started_at: "2026-06-12T11:02:00Z",
    elapsed_ms: 412,
    outcome: "answer",
    reason_code: null,
    model_mode: "deterministic",
  },
  {
    trace_id: "trace-2",
    company_id: "globex-002",
    actor_id: "admin-globex",
    conversation_id: "c2",
    started_at: "2026-06-12T11:01:00Z",
    elapsed_ms: 98,
    outcome: "denied",
    reason_code: "not_found",
    model_mode: "deterministic",
  },
];

const AUDIT: AuditEntry[] = [
  {
    seq: 1,
    at: "2026-06-12T11:03:00Z",
    company_id: "globex-002",
    actor_id: "admin-globex",
    event: "proposal_executed",
    proposal_id: "prp_9",
    trace_id: "trace-2",
    action_type: "replacement_flag",
    target_device_id: "GX1",
    payload_hash: "a".repeat(64),
    refs: ["globex-002/GX1@2026-06-12T08:39:00Z#battery"],
    detail: { effect_id: 1 },
  },
];

function fake(overrides: Partial<Transport> = {}) {
  return {
    mode: "live",
    observeTurns: vi.fn(async () => TURNS),
    observeTrace: vi.fn(async (traceId: string) => ({ trace_id: traceId, events: STEPS })),
    observeAudit: vi.fn(async () => AUDIT),
    observeEffects: vi.fn(async () => ({
      tickets: { "acme-001": 2, "globex-002": 1 },
      replacement_flags: { "globex-002": 3 },
      upgrade_orders: {},
      employee_notifications: { "initech-003": 5 },
    })),
    ...overrides,
  } as unknown as Transport;
}

function renderView(transport: Transport, onSessionEnded: () => void = () => {}) {
  return render(<ObserveView transport={transport} onSessionEnded={onSessionEnded} />);
}

describe("the operator console", () => {
  it("has three tabs and no composer", async () => {
    renderView(fake());
    expect(await screen.findByRole("heading", { name: "Operator console" })).toBeInTheDocument();
    const tabs = within(screen.getByRole("tablist", { name: "Console sections" })).getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Traces", "Audit", "Effect ledger"]);
    expect(screen.queryByRole("tab", { name: /evaluations/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /ask/i })).not.toBeInTheDocument();
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("Traces");
  });

  it("moves between tabs with the arrow keys, Home and End, with a roving tab stop", async () => {
    const user = userEvent.setup();
    renderView(fake());
    const traces = await screen.findByRole("tab", { name: "Traces" });
    const audit = screen.getByRole("tab", { name: "Audit" });
    const ledger = screen.getByRole("tab", { name: "Effect ledger" });
    expect([traces, audit, ledger].map((t) => t.getAttribute("tabindex"))).toEqual(["0", "-1", "-1"]);

    traces.focus();
    await user.keyboard("{ArrowRight}");
    expect(audit).toHaveFocus();
    expect(audit).toHaveAttribute("aria-selected", "true");
    expect([traces, audit, ledger].map((t) => t.getAttribute("tabindex"))).toEqual(["-1", "0", "-1"]);
    expect(await screen.findByRole("tabpanel")).toHaveAccessibleName("Audit");

    await user.keyboard("{ArrowRight}");
    expect(ledger).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(traces).toHaveFocus();
    expect(traces).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowLeft}");
    expect(ledger).toHaveFocus();
    await user.keyboard("{Home}");
    expect(traces).toHaveFocus();
    await user.keyboard("{End}");
    expect(ledger).toHaveFocus();
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("Effect ledger");
  });

  it("lists recent turns in words and opens a turn's trace timeline", async () => {
    const user = userEvent.setup();
    const transport = fake();
    renderView(transport);
    const table = await screen.findByRole("table", { name: "Recent turns" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Acme")).toBeInTheDocument();
    expect(within(rows[0]).getByText("admin-acme")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Answer")).toBeInTheDocument();
    expect(within(rows[0]).getByText("412 ms")).toBeInTheDocument();
    expect(within(rows[0]).getByText("—")).toBeInTheDocument();
    expect(within(rows[0]).queryByText("None")).not.toBeInTheDocument();
    expect(within(rows[0]).getByText("deterministic")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Not found")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Not found in your fleet")).toBeInTheDocument();
    expect(transport.observeTrace).not.toHaveBeenCalled();

    await user.click(within(rows[0]).getByRole("button", { name: /2026-06-12 11:02 UTC/ }));
    expect(transport.observeTrace).toHaveBeenCalledWith(FIXTURE.envelope.trace_id);
    const steps = await screen.findByRole("list", { name: "Trace steps" });
    const items = within(steps).getAllByRole("listitem");
    expect(items).toHaveLength(STEPS.length);
    expect(within(items[0]).getByText(STEPS[0].summary)).toBeInTheDocument();
    expect(within(items[3]).getByText("fleet_query")).toBeInTheDocument();
    expect(within(items[3]).getByText("31 ms")).toBeInTheDocument();
    expect(within(items[3]).getByText("attempts")).toBeInTheDocument();
    expect(within(items[3]).getByText("The lookup did not complete")).toBeInTheDocument();
    // An error step is told apart by its mark and word, not by colour alone.
    expect(items[3]).toHaveAttribute("data-kind", "error");
    expect(within(items[3]).getByText("Error")).toBeInTheDocument();
    expect(items[0]).not.toHaveAttribute("data-kind", "error");
    expect(within(rows[0]).getByRole("button", { name: /2026-06-12 11:02 UTC/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("says plainly when there are no turns, and when a trace cannot be loaded", async () => {
    const user = userEvent.setup();
    const empty = renderView(fake({ observeTurns: vi.fn(async () => []) }));
    expect(await screen.findByText("No turns recorded yet. Questions asked by any company appear here.")).toBeInTheDocument();
    empty.unmount();

    renderView(fake({ observeTrace: vi.fn().mockRejectedValue(new ApiError(404, "not found")) }));
    await user.click(await screen.findByRole("button", { name: /2026-06-12 11:02 UTC/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The trace could not be loaded.");
  });

  it("ends the session when a request finds it gone", async () => {
    const ended = vi.fn();
    renderView(fake({ observeTurns: vi.fn().mockRejectedValue(new ApiError(401, "sign in first")) }), ended);
    await waitFor(() => expect(ended).toHaveBeenCalled());
  });

  it("filters the audit trail by company, actor and device through the transport", async () => {
    const user = userEvent.setup();
    const transport = fake();
    renderView(transport);
    await user.click(await screen.findByRole("tab", { name: "Audit" }));
    expect(transport.observeAudit).toHaveBeenCalledWith({});
    const list = await screen.findByRole("list", { name: "Audit entries" });
    expect(within(list).getByText("Recorded").closest("time")).toHaveTextContent("Recorded 2026-06-12 11:03 UTC");
    expect(within(list).getByText("prp_9")).toHaveAttribute("title", "prp_9");
    const row = within(list).getByRole("listitem");
    expect(within(row).getByText("Executed (simulated)")).toBeInTheDocument();
    expect(within(row).getByText("Replacement flag")).toBeInTheDocument();
    expect(within(row).getByText("Globex")).toBeInTheDocument();
    expect(within(row).getByText("admin-globex")).toBeInTheDocument();
    expect(within(row).getByText("GX1")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("proposal_executed");

    await user.selectOptions(screen.getByLabelText("Company"), "globex-002");
    await user.type(screen.getByLabelText("Actor"), "admin-globex");
    await user.type(screen.getByLabelText("Device id"), "GX1{Enter}");
    await waitFor(() =>
      expect(transport.observeAudit).toHaveBeenLastCalledWith({
        company: "globex-002",
        actor: "admin-globex",
        target: "GX1",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(transport.observeAudit).toHaveBeenLastCalledWith({}));
  });

  it("shows the effect ledger per table and company, all labelled simulated", async () => {
    const user = userEvent.setup();
    renderView(fake());
    await user.click(await screen.findByRole("tab", { name: "Effect ledger" }));
    const table = await screen.findByRole("table", { name: /simulated/i });
    const rowFor = (name: string) => within(table).getByRole("row", { name: new RegExp(`^${name}`) });
    const cells = (row: HTMLElement) => within(row).getAllByRole("cell").map((c) => c.textContent);
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Effect table",
      "Acme",
      "Globex",
      "Initech",
      "Total",
    ]);
    expect(cells(rowFor("Tickets"))).toEqual(["2", "1", "0", "3"]);
    expect(cells(rowFor("Replacement flags"))).toEqual(["0", "3", "0", "3"]);
    expect(cells(rowFor("Upgrade orders"))).toEqual(["0", "0", "0", "0"]);
    expect(cells(rowFor("Employee notifications"))).toEqual(["0", "0", "5", "5"]);
    expect(screen.getByText(/simulated/i, { selector: "p" })).toBeInTheDocument();
  });
});
