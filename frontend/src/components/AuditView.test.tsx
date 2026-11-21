import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { ApiError } from "../lib/api";
import { getFixture } from "../lib/fixtures";
import type { AuditEntry, Session, Transport } from "../lib/transport";
import { AuditView } from "./AuditView";

const PROPOSAL = getFixture("acme-001", "proposal").envelope.proposal!;
const REFS = PROPOSAL.evidence_refs;

function entry(seq: number, over: Partial<AuditEntry>): AuditEntry {
  return {
    seq,
    at: `2026-06-12T11:0${seq}:00Z`,
    company_id: "acme-001",
    actor_id: "admin-acme",
    event: "proposal_created",
    proposal_id: PROPOSAL.proposal_id,
    trace_id: "trace-1",
    action_type: PROPOSAL.action_type,
    target_device_id: PROPOSAL.target_device_id,
    payload_hash: PROPOSAL.payload_hash,
    refs: REFS,
    detail: {},
    ...over,
  };
}

const ENTRIES: AuditEntry[] = [
  entry(3, { event: "proposal_executed", detail: { effect_id: 7 } }),
  entry(2, {
    event: "approval_refused",
    refs: [],
    detail: { reason: "hash_mismatch", operation: "approve" },
  }),
  entry(1, { event: "proposal_refused", proposal_id: null, action_type: null, refs: [], detail: { reason: "not_eligible" } }),
];

function fake(list: AuditEntry[] = ENTRIES, overrides: Partial<Transport> = {}) {
  return {
    mode: "live",
    audit: vi.fn(async () => list),
    ...overrides,
  } as unknown as Transport;
}

async function findRows() {
  const list = await screen.findByRole("list", { name: "Audit entries" });
  return within(list).getAllByRole("listitem");
}

function renderView(transport: Transport, onOpenEvidence: (refs: string[], subject: string) => void = () => {}) {
  return render(<AuditView transport={transport} onOpenEvidence={onOpenEvidence} onSessionEnded={() => {}} />);
}

describe("the tenant audit view", () => {
  it("lists entries in words, never codes or raw detail", async () => {
    const transport = fake();
    renderView(transport);
    expect(await screen.findByRole("heading", { name: "Audit" })).toBeInTheDocument();
    expect(transport.audit).toHaveBeenCalledWith({});
    const rows = await findRows();
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByText("Executed (simulated)")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Remediation ticket")).toBeInTheDocument();
    expect(within(rows[0]).getByText(PROPOSAL.target_device_id)).toBeInTheDocument();
    expect(within(rows[0]).getByText("2026-06-12 11:03 UTC")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Approval refused")).toBeInTheDocument();
    expect(within(rows[1]).getByText(/proposal changed/)).toBeInTheDocument();
    expect(within(rows[2]).getByText("Refused")).toBeInTheDocument();
    expect(within(rows[2]).getByText(/not supported by the evidence/)).toBeInTheDocument();
    const text = document.body.textContent ?? "";
    for (const raw of ["proposal_executed", "approval_refused", "hash_mismatch", "not_eligible", "effect_id", "remediation_ticket"]) {
      expect(text).not.toContain(raw);
    }
  });

  it("words every audit event and refusal reason", async () => {
    const events: AuditEntry["event"][] = [
      "proposal_created",
      "proposal_refused",
      "proposal_approved",
      "proposal_executed",
      "proposal_rejected",
      "proposal_invalidated",
      "proposal_failed",
      "approval_refused",
      "duplicate_approval",
    ];
    const reasons: [string, string][] = [
      ["not_eligible", "not supported by the evidence"],
      ["insufficient_evidence", "not enough recent evidence"],
      ["not_found", "not found"],
      ["hash_mismatch", "proposal changed"],
      ["forbidden", "not allowed"],
      ["not_pending:executed", "already executed"],
    ];
    const list = [
      ...events.map((event, i) => entry(i + 1, { event, proposal_id: `p${i}` })),
      ...reasons.map(([reason], i) => entry(20 + i, { event: "approval_refused", proposal_id: `r${i}`, detail: { reason } })),
    ];
    renderView(fake(list));
    for (const word of [
      "Proposed",
      "Refused",
      "Approved",
      "Executed (simulated)",
      "Rejected",
      "Invalidated",
      "Failed",
      "Duplicate approval",
    ]) {
      expect((await screen.findAllByText(word)).length).toBeGreaterThan(0);
    }
    expect(screen.getAllByText("Approval refused")).toHaveLength(1 + reasons.length);
    for (const [, words] of reasons) expect(screen.getByText(new RegExp(words))).toBeInTheDocument();
  });

  it("filters by proposal when its id is chosen, and clears the filters", async () => {
    const user = userEvent.setup();
    const transport = fake();
    renderView(transport);
    await findRows();
    await user.click(screen.getAllByRole("button", { name: `Show only ${PROPOSAL.proposal_id}` })[0]);
    await waitFor(() => expect(transport.audit).toHaveBeenLastCalledWith({ proposal_id: PROPOSAL.proposal_id }));
    expect(screen.getByLabelText("Proposal id")).toHaveValue(PROPOSAL.proposal_id);
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(transport.audit).toHaveBeenLastCalledWith({}));
    expect(screen.getByLabelText("Proposal id")).toHaveValue("");
  });

  it("filters by device id when the filter is applied", async () => {
    const user = userEvent.setup();
    const transport = fake();
    renderView(transport);
    await findRows();
    await user.type(screen.getByLabelText("Device id"), "  DEV9  {Enter}");
    await waitFor(() => expect(transport.audit).toHaveBeenLastCalledWith({ target: "DEV9" }));
  });

  it("says what an empty trail means, and what an empty filter result means", async () => {
    const user = userEvent.setup();
    const transport = fake([]);
    renderView(transport);
    expect(
      await screen.findByText("No audit entries yet. Proposals, decisions and refusals appear here."),
    ).toBeInTheDocument();
    await user.type(screen.getByLabelText("Device id"), "X1{Enter}");
    expect(await screen.findByText("No entries match these filters.")).toBeInTheDocument();
  });

  it("opens the cited readings from the references button", async () => {
    const user = userEvent.setup();
    const onOpenEvidence = vi.fn();
    renderView(fake(), onOpenEvidence);
    const rows = await findRows();
    expect(within(rows[1]).queryByRole("button", { name: /reference/i })).not.toBeInTheDocument();
    await user.click(within(rows[0]).getByRole("button", { name: `${REFS.length} evidence references` }));
    expect(onOpenEvidence).toHaveBeenCalledWith(REFS, expect.stringContaining("Executed (simulated)"));
  });

  it("shows the failure in words and retries; a 401 ends the session", async () => {
    const user = userEvent.setup();
    const audit = vi.fn().mockRejectedValueOnce(new ApiError(0, "x")).mockResolvedValue(ENTRIES);
    renderView(fake(ENTRIES, { audit }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The audit trail could not be loaded.");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await findRows()).toHaveLength(3);

    const ended = vi.fn();
    render(
      <AuditView
        transport={fake([], { audit: vi.fn().mockRejectedValue(new ApiError(401, "sign in first")) })}
        onOpenEvidence={() => {}}
        onSessionEnded={ended}
      />,
    );
    await waitFor(() => expect(ended).toHaveBeenCalled());
  });
});

describe("the audit route", () => {
  beforeEach(() => {
    window.location.hash = "#/audit";
  });

  it("is reached from the top bar and opens the drawer on an entry's readings", async () => {
    const user = userEvent.setup();
    const session: Session = { company_id: "acme-001", display_name: "Acme admin (demo)", role: "admin" };
    const transport = {
      ...fake(),
      session: vi.fn(async () => session),
      policy: vi.fn(async () => ({ as_of: "2026-06-12T11:00:00Z", dataset_sha256: "x", defaults: {} })),
      proposals: vi.fn(async () => []),
      evidence: vi.fn(async (ref: string) => ({ ref, collected_at: "2026-06-12T08:39:00Z", value: { free_gb: 4 } })),
    } as unknown as Transport;
    render(<App transport={transport} />);
    expect(await screen.findByRole("heading", { name: "Audit" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Audit" })).toHaveAttribute("aria-current", "page");
    const rows = await findRows();
    await user.click(within(rows[0]).getByRole("button", { name: `${REFS.length} evidence references` }));
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByRole("tab", { name: `Evidence (${REFS.length})` })).toBeInTheDocument();
    window.location.hash = "";
  });
});
