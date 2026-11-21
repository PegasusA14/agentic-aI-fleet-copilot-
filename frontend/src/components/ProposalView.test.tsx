import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { getFixture } from "../lib/fixtures";
import type { ApprovalResult, Effect } from "../lib/transport";
import type { ProposalCard } from "../lib/types";
import { type Decide, ProposalView } from "./ProposalView";

const CARD = getFixture("acme-001", "proposal").envelope.proposal as ProposalCard;
const HASH = CARD.payload_hash;

function deciders(overrides: { approve?: unknown; reject?: unknown } = {}) {
  const effect: Effect = { effect_id: 7, simulated: true, check_id: "screen_lock" };
  return {
    approve: vi.fn(
      async (): Promise<ApprovalResult> => ({
        proposal: { ...CARD, status: "executed" },
        effect,
        duplicate: false,
      }),
    ),
    reject: vi.fn(async () => ({ proposal: { ...CARD, status: "rejected" as const } })),
    ...overrides,
  } as unknown as Decide & { approve: ReturnType<typeof vi.fn>; reject: ReturnType<typeof vi.fn> };
}

function setup(decide = deciders(), extra: { onDecided?: (c: ProposalCard) => void; onOpenEvidence?: (r: string[]) => void; onSessionEnded?: () => void } = {}) {
  const user = userEvent.setup();
  render(
    <ProposalView
      proposal={CARD}
      decide={decide}
      onOpenEvidence={extra.onOpenEvidence ?? (() => {})}
      onDecided={extra.onDecided}
      onSessionEnded={extra.onSessionEnded}
    />,
  );
  return { user, decide };
}

async function approveThroughDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Approve" }));
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "Approve and execute (simulated)" }));
}

describe("the proposal card", () => {
  it("states the action in words, the device, the rationale, the effect, the scope and a short hash", () => {
    setup();
    const card = screen.getByRole("region", { name: "Proposed action" });
    expect(within(card).getByText("Open a remediation ticket for acme-macbook-2")).toBeInTheDocument();
    expect(within(card).getByText("1LYSSFD074BB", { selector: ".token" })).toBeInTheDocument();
    expect(within(card).getByText(CARD.rationale)).toBeInTheDocument();
    expect(within(card).getByText(CARD.expected_effect)).toBeInTheDocument();
    expect(within(card).getByText("Check").nextElementSibling).toHaveTextContent("Screen lock");
    expect(within(card).getByText("Severity").nextElementSibling).toHaveTextContent("medium");
    expect(within(card).getByText(HASH.slice(0, 12))).toBeInTheDocument();
    expect(within(card).queryByText(HASH)).not.toBeInTheDocument();
    expect(within(card).getByText("Pending")).toBeInTheDocument();
  });

  it("opens the evidence on the card's own references", async () => {
    const onOpenEvidence = vi.fn();
    const { user } = setup(deciders(), { onOpenEvidence });
    await user.click(screen.getByRole("button", { name: /evidence for this proposal \(2\)/i }));
    expect(onOpenEvidence).toHaveBeenCalledWith(CARD.evidence_refs);
  });

  it("offers no decision when it is given no way to decide", () => {
    render(<ProposalView proposal={CARD} onOpenEvidence={() => {}} />);
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });
});

describe("approving", () => {
  it("asks first, restating the action, device, scope and short hash, with focus on Cancel", async () => {
    const { user, decide } = setup();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Open a remediation ticket for acme-macbook-2")).toBeInTheDocument();
    expect(within(dialog).getByText("1LYSSFD074BB", { selector: ".token" })).toBeInTheDocument();
    expect(within(dialog).getByText("Check").nextElementSibling).toHaveTextContent("Screen lock");
    expect(within(dialog).getByText("Severity").nextElementSibling).toHaveTextContent("medium");
    expect(within(dialog).getByText(HASH.slice(0, 12))).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(decide.approve).not.toHaveBeenCalled();
  });

  it("sends the exact payload hash and shows the simulated effect", async () => {
    const onDecided = vi.fn();
    const { user, decide } = setup(deciders(), { onDecided });
    await approveThroughDialog(user);
    expect(decide.approve).toHaveBeenCalledTimes(1);
    expect(decide.approve).toHaveBeenCalledWith(CARD.proposal_id, HASH);
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/recorded in tickets as entry 7/i);
    expect(status).toHaveTextContent(/\(simulated\)/i);
    expect(screen.getByText("Executed (simulated)")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onDecided).toHaveBeenCalledWith(expect.objectContaining({ status: "executed" }));
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("can be done from the keyboard, and Escape closes the dialog without deciding", async () => {
    const { user, decide } = setup();
    await user.tab();
    await user.tab();
    await user.tab();
    const approve = screen.getByRole("button", { name: "Approve" });
    while (document.activeElement !== approve) await user.tab();
    await user.keyboard("{Enter}");
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(decide.approve).not.toHaveBeenCalled();
    expect(approve).toHaveFocus();

    await user.keyboard("{Enter}");
    await screen.findByRole("dialog");
    await user.tab();
    expect(screen.getByRole("button", { name: "Approve and execute (simulated)" })).toHaveFocus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(decide.approve).toHaveBeenCalledWith(CARD.proposal_id, HASH));
  });

  it("cancel leaves the proposal pending and calls nothing", async () => {
    const { user, decide } = setup();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(decide.approve).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
  });

  it("disables both buttons while the request is in flight", async () => {
    let finish!: (value: ApprovalResult) => void;
    const approve = vi.fn(() => new Promise<ApprovalResult>((resolve) => (finish = resolve)));
    const { user } = setup(deciders({ approve }));
    await approveThroughDialog(user);
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent(/recording/i);
    finish({ proposal: { ...CARD, status: "executed" }, effect: { effect_id: 1, simulated: true }, duplicate: false });
    expect(await screen.findByText("Executed (simulated)")).toBeInTheDocument();
  });

  it("says a repeated approval made no second effect", async () => {
    const approve = vi.fn(async () => ({ proposal: { ...CARD, status: "executed" as const }, effect: null, duplicate: true }));
    const { user } = setup(deciders({ approve }));
    await approveThroughDialog(user);
    expect(await screen.findByRole("status")).toHaveTextContent("Already executed — no second effect.");
    expect(screen.getByText("Executed (simulated)")).toBeInTheDocument();
  });

  it("says nothing was executed when the proposal changed since it was seen", async () => {
    const approve = vi.fn(async () => {
      throw new ApiError(409, "proposal changed");
    });
    const { user } = setup(deciders({ approve }));
    await approveThroughDialog(user);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "This proposal changed since you saw it. Nothing was executed.",
    );
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("shows the current status when the proposal is no longer pending", async () => {
    const approve = vi.fn(async () => {
      throw new ApiError(409, "proposal is rejected");
    });
    const { user } = setup(deciders({ approve }));
    await approveThroughDialog(user);
    expect(await screen.findByRole("status")).toHaveTextContent("This proposal is already rejected. Nothing was executed.");
    expect(screen.getByText("Rejected")).toBeInTheDocument();
  });

  it("says the proposal is not available on a 404", async () => {
    const approve = vi.fn(async () => {
      throw new ApiError(404, "not found");
    });
    const { user } = setup(deciders({ approve }));
    await approveThroughDialog(user);
    expect(await screen.findByRole("status")).toHaveTextContent("This proposal is not available.");
  });

  it("explains a proposal that no longer holds on the current data", async () => {
    const approve = vi.fn(async () => ({ proposal: { ...CARD, status: "invalidated" as const }, effect: null, duplicate: false }));
    const { user } = setup(deciders({ approve }));
    await approveThroughDialog(user);
    expect(await screen.findByRole("status")).toHaveTextContent(/no longer holds.*nothing was executed/i);
    expect(screen.getByText("Invalidated")).toBeInTheDocument();
  });

  it("reports a failed execution", async () => {
    const approve = vi.fn(async () => ({ proposal: { ...CARD, status: "failed" as const }, effect: null, duplicate: false }));
    const { user } = setup(deciders({ approve }));
    await approveThroughDialog(user);
    expect(await screen.findByRole("status")).toHaveTextContent(/did not run.*nothing was recorded/i);
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("keeps the proposal pending and allows a retry when the server cannot be reached", async () => {
    const approve = vi.fn(async () => {
      throw new ApiError(0, "The copilot could not be reached.");
    });
    const { user } = setup(deciders({ approve }));
    await approveThroughDialog(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("The copilot could not be reached.");
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
    expect(screen.getByText("Pending")).toBeInTheDocument();
  });

  it("hands a lost session back to the app", async () => {
    const approve = vi.fn(async () => {
      throw new ApiError(401, "sign in first");
    });
    const onSessionEnded = vi.fn();
    const { user } = setup(deciders({ approve }), { onSessionEnded });
    await approveThroughDialog(user);
    await waitFor(() => expect(onSessionEnded).toHaveBeenCalled());
  });
});

describe("rejecting", () => {
  it("rejects in one click, with no dialog, and nothing is executed", async () => {
    const onDecided = vi.fn();
    const { user, decide } = setup(deciders(), { onDecided });
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(decide.reject).toHaveBeenCalledWith(CARD.proposal_id);
    expect(decide.approve).not.toHaveBeenCalled();
    expect(await screen.findByRole("status")).toHaveTextContent("Rejected. Nothing was executed.");
    expect(screen.getByText("Rejected", { selector: ".mark span" })).toBeInTheDocument();
    expect(onDecided).toHaveBeenCalledWith(expect.objectContaining({ status: "rejected" }));
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });
});

describe("a proposal that is already decided", () => {
  it("shows its status and offers no decision", () => {
    render(
      <ProposalView
        proposal={{ ...CARD, status: "executed" }}
        decide={deciders()}
        onOpenEvidence={() => {}}
      />,
    );
    expect(screen.getByText("Executed (simulated)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });
});
