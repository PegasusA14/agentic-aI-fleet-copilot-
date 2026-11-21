import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { getFixture } from "../lib/fixtures";
import type { Transport } from "../lib/transport";
import type { ProposalCard } from "../lib/types";
import { ApprovalsView } from "./ApprovalsView";

const ONE = getFixture("acme-001", "proposal").envelope.proposal as ProposalCard;
const TWO: ProposalCard = {
  ...ONE,
  proposal_id: "prp_2",
  action_type: "replacement_flag",
  target_device_id: "ACME2",
  target_label: "acme-laptop-9",
  payload_hash: "b".repeat(64),
  scope: { device: "ACME2", reason: "battery_service_recommended" },
};

function fake(list: ProposalCard[] = [ONE, TWO], overrides: Partial<Transport> = {}) {
  return {
    mode: "live",
    proposals: vi.fn(async () => list.map((c) => ({ ...c }))),
    approve: vi.fn(async (id: string) => ({
      proposal: { ...list.find((c) => c.proposal_id === id)!, status: "executed" as const },
      effect: { effect_id: 3, simulated: true },
      duplicate: false,
    })),
    reject: vi.fn(async (id: string) => ({
      proposal: { ...list.find((c) => c.proposal_id === id)!, status: "rejected" as const },
    })),
    ...overrides,
  } as unknown as Transport;
}

function renderView(transport: Transport, extra: { onDecided?: () => void; onOpenEvidence?: (refs: string[], subject: string) => void; onSessionEnded?: () => void } = {}) {
  return render(
    <ApprovalsView
      transport={transport}
      onOpenEvidence={extra.onOpenEvidence ?? (() => {})}
      onDecided={extra.onDecided ?? (() => {})}
      onSessionEnded={extra.onSessionEnded ?? (() => {})}
    />,
  );
}

describe("the approvals view", () => {
  it("lists every pending proposal as a card, with the count in words", async () => {
    const transport = fake();
    renderView(transport);
    expect(await screen.findByRole("heading", { name: "Approvals" })).toBeInTheDocument();
    expect(transport.proposals).toHaveBeenCalledWith("pending");
    const cards = await screen.findAllByRole("region", { name: "Proposed action" });
    expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByText("Open a remediation ticket for acme-macbook-2")).toBeInTheDocument();
    expect(within(cards[1]).getByText("Flag acme-laptop-9 for replacement")).toBeInTheDocument();
    expect(screen.getByText("2 proposals awaiting your decision")).toBeInTheDocument();
  });

  it("updates the count and keeps the outcome visible after a decision", async () => {
    const user = userEvent.setup();
    const onDecided = vi.fn();
    const transport = fake();
    renderView(transport, { onDecided });
    const cards = await screen.findAllByRole("region", { name: "Proposed action" });
    await user.click(within(cards[1]).getByRole("button", { name: "Reject" }));
    expect(await within(cards[1]).findByRole("status")).toHaveTextContent("Nothing was executed.");
    expect(screen.getByText("1 proposal awaiting your decision")).toBeInTheDocument();
    expect(onDecided).toHaveBeenCalledTimes(1);

    await user.click(within(cards[0]).getByRole("button", { name: "Approve" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Approve and execute (simulated)" }));
    expect(await within(cards[0]).findByText("Executed (simulated)")).toBeInTheDocument();
    for (const card of cards) {
      expect(within(card).queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
      expect(within(card).queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
    }
    expect(screen.getByText("No proposals awaiting your decision")).toBeInTheDocument();
    expect(onDecided).toHaveBeenCalledTimes(2);
  });

  it("says plainly when nothing is waiting", async () => {
    renderView(fake([]));
    expect(await screen.findByText(/nothing is waiting for your decision/i)).toBeInTheDocument();
  });

  it("opens the evidence for a card's own references", async () => {
    const user = userEvent.setup();
    const onOpenEvidence = vi.fn();
    renderView(fake(), { onOpenEvidence });
    const cards = await screen.findAllByRole("region", { name: "Proposed action" });
    await user.click(within(cards[0]).getByRole("button", { name: /evidence for this proposal/i }));
    expect(onOpenEvidence).toHaveBeenCalledWith(ONE.evidence_refs, "Open a remediation ticket for acme-macbook-2");
  });

  it("offers a retry when the list cannot be loaded", async () => {
    const user = userEvent.setup();
    const proposals = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(0, "The copilot could not be reached."))
      .mockResolvedValueOnce([ONE]);
    renderView(fake([ONE], { proposals } as Partial<Transport>));
    expect(await screen.findByRole("alert")).toHaveTextContent("The proposals could not be loaded.");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findAllByRole("region", { name: "Proposed action" })).toHaveLength(1);
  });

  it("hands a lost session back to the app", async () => {
    const onSessionEnded = vi.fn();
    const proposals = vi.fn().mockRejectedValue(new ApiError(401, "sign in first"));
    renderView(fake([], { proposals } as Partial<Transport>), { onSessionEnded });
    await waitFor(() => expect(onSessionEnded).toHaveBeenCalled());
  });
});
