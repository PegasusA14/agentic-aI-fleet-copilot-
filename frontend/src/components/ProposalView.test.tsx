import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { getFixture } from "../lib/fixtures";
import type { ApprovalResult, Effect } from "../lib/transport";
import type { EvidenceItem, ProposalCard } from "../lib/types";
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

function setup(decide = deciders(), extra: { onDecided?: (c: ProposalCard) => void; onEditDetails?: (text: string) => void; evidence?: EvidenceItem[]; onSessionEnded?: () => void } = {}) {
  const user = userEvent.setup();
  render(
    <ProposalView
      proposal={CARD}
      decide={decide}
      onEditDetails={extra.onEditDetails}
      evidence={extra.evidence}
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

const EVIDENCE = getFixture("acme-001", "proposal").envelope.evidence;

describe("the proposal card", () => {
  it("heads the card with a short title for the action and the amber awaiting label", () => {
    setup();
    const card = screen.getByRole("region", { name: "Proposed action" });
    expect(within(card).getByRole("heading", { name: "Open remediation ticket" })).toBeInTheDocument();
    expect(card.querySelector(".mark")).toHaveAttribute("data-tone", "attention");
    expect(card).toHaveAttribute("data-status", "pending");
  });

  it.each([
    ["upgrade_order", "Create upgrade order"],
    ["remediation_ticket", "Open remediation ticket"],
    ["replacement_flag", "Flag for replacement"],
    ["employee_notification", "Notify employee"],
  ] as const)("titles a %s card %s", (action_type, title) => {
    render(<ProposalView proposal={{ ...CARD, action_type, scope: {} }} />);
    expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
  });

  it("shows the device as hostname with its serial quietly beside it, then the check and its severity", () => {
    setup();
    const card = screen.getByRole("region", { name: "Proposed action" });
    const field = (label: string) => within(card).getByText(label, { selector: "dt" }).nextElementSibling as HTMLElement;
    expect(field("Device")).toHaveTextContent("acme-macbook-2");
    expect(field("Device").querySelector(".pfields__value")).toHaveTextContent("acme-macbook-2");
    expect(field("Device").querySelector(".pfields__sub")).toHaveTextContent("1LYSSFD074BB");
    expect(field("Check")).toHaveTextContent("Screen lock");
    expect(field("Severity")).toHaveTextContent("Medium");
  });

  it("reads an upgrade as component and spec", () => {
    const upgrade: ProposalCard = {
      ...CARD,
      action_type: "upgrade_order",
      scope: { device: CARD.target_device_id, component: "memory", current: "16 GB", target: "32 GB" },
    };
    render(<ProposalView proposal={upgrade} />);
    const field = (label: string) => screen.getByText(label, { selector: "dt" }).nextElementSibling as HTMLElement;
    expect(screen.getByRole("heading", { name: "Create upgrade order" })).toBeInTheDocument();
    expect(field("Component")).toHaveTextContent("Memory");
    expect(field("Spec")).toHaveTextContent("16 GB → 32 GB");
    expect(screen.queryByText("Check", { selector: "dt" })).not.toBeInTheDocument();
  });

  it("reads a replacement as a reason, and a notification as recipient and topic", () => {
    const flag: ProposalCard = { ...CARD, action_type: "replacement_flag", scope: { device: "D", reason: "battery_service_recommended" } };
    const { unmount } = render(<ProposalView proposal={flag} />);
    expect(screen.getByText("Reason", { selector: "dt" }).nextElementSibling).toHaveTextContent("Battery service recommended");
    unmount();
    const note: ProposalCard = { ...CARD, action_type: "employee_notification", scope: { device: "D", topic: "low_disk", employee: "E1" } };
    render(<ProposalView proposal={note} />);
    expect(screen.getByText("Recipient", { selector: "dt" }).nextElementSibling).toHaveTextContent("The assigned employee");
    expect(screen.getByText("Topic", { selector: "dt" }).nextElementSibling).toHaveTextContent("Low disk");
  });

  it("gives the why in words, with check codes read as the checks they name", () => {
    setup();
    const card = screen.getByRole("region", { name: "Proposed action" });
    expect(within(card).getByText("Why")).toBeInTheDocument();
    expect(within(card).getByText(/^Screen lock reports fail with medium severity/)).toBeInTheDocument();
    expect(card).not.toHaveTextContent("screen_lock");
  });

  it("lists the proposal's own evidence inline, numbered from 1, with no drawer and no raw reference", () => {
    setup(deciders(), { evidence: EVIDENCE });
    const card = screen.getByRole("region", { name: "Proposed action" });
    const rows = within(within(card).getByRole("list", { name: "Evidence" })).getAllByRole("listitem");
    expect(rows).toHaveLength(CARD.evidence_refs.length);
    expect(rows[0]).toHaveTextContent("1");
    expect(rows[1]).toHaveTextContent("2");
    expect(rows[0]).toHaveTextContent("Screen lock: fail (medium severity)");
    expect(rows[0]).toHaveTextContent("2026-06-12 09:47 UTC");
    expect(card).not.toHaveTextContent("#compliance[");
    expect(within(card).queryByRole("button", { name: /evidence for this proposal/i })).not.toBeInTheDocument();
  });

  it("still names a reading it holds no value for, and what it was", () => {
    setup(deciders(), { evidence: [] });
    const rows = within(screen.getByRole("list", { name: "Evidence" })).getAllByRole("listitem");
    expect(rows).toHaveLength(CARD.evidence_refs.length);
    expect(rows[0]).toHaveTextContent("Compliance: screen lock");
  });

  it("states what the approval touches in a note, from the card's risk", () => {
    render(<ProposalView proposal={{ ...CARD, risk: "Affects only acme-macbook-2 in acme-001. The decision is recorded in Audit." }} />);
    expect(screen.getByText("Affects only acme-macbook-2 in acme-001. The decision is recorded in Audit.")).toBeInTheDocument();
  });

  it("falls back to a true sentence when an older card has no risk note", () => {
    render(<ProposalView proposal={{ ...CARD, risk: "" }} />);
    expect(screen.getByText("Affects only acme-macbook-2. Nothing leaves this system. The decision is recorded in Audit.")).toBeInTheDocument();
  });

  it("no longer carries the 'If you approve' block or the evidence drawer link", () => {
    setup();
    expect(screen.queryByText("If you approve")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /evidence/i })).not.toBeInTheDocument();
  });

  it("offers no decision when it is given no way to decide", () => {
    render(<ProposalView proposal={CARD} />);
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit details" })).not.toBeInTheDocument();
  });
});

describe("Edit details", () => {
  it("hands the composer a request to finish, naming the device, and decides nothing", async () => {
    const onEditDetails = vi.fn();
    const { user, decide } = setup(deciders(), { onEditDetails });
    await user.click(screen.getByRole("button", { name: "Edit details" }));
    expect(onEditDetails).toHaveBeenCalledTimes(1);
    expect(onEditDetails).toHaveBeenCalledWith("Change the proposal for acme-macbook-2: ");
    expect(decide.approve).not.toHaveBeenCalled();
    expect(decide.reject).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
  });

  it("is not offered when nothing can receive it", () => {
    setup();
    expect(screen.queryByRole("button", { name: "Edit details" })).not.toBeInTheDocument();
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
    expect(status).toHaveTextContent("Remediation ticket approved");
    expect(status).toHaveTextContent("on acme-macbook-2 · approved by you · simulated");
    expect(status).toHaveTextContent("Recorded in Tickets as entry 7. Nothing left this system.");
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
    expect(await screen.findByRole("status")).toHaveTextContent(/No longer valid.*no longer holds.*nothing was executed/i);
    expect(screen.getByText("Invalidated")).toBeInTheDocument();
  });

  it("reports a failed execution", async () => {
    const approve = vi.fn(async () => ({ proposal: { ...CARD, status: "failed" as const }, effect: null, duplicate: false }));
    const { user } = setup(deciders({ approve }));
    await approveThroughDialog(user);
    expect(await screen.findByRole("status")).toHaveTextContent(/Could not run.*did not run.*nothing was recorded/i);
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
    expect(screen.getByText("Awaiting your approval")).toBeInTheDocument();
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
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Remediation ticket rejected");
    expect(status).toHaveTextContent("Nothing was changed. The rejection is logged.");
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
       
      />,
    );
    expect(screen.getByText("Executed (simulated)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });
});

describe("a proposal reopened from a stored conversation", () => {
  function reopened(current: () => Promise<ProposalCard>, extra: { onSynced?: (c: ProposalCard) => void; onSessionEnded?: () => void } = {}) {
    const proposal = vi.fn(current);
    const decide = { ...deciders(), proposal } as unknown as Decide;
    render(<ProposalView proposal={CARD} decide={decide} recheck {...extra} />);
    return { proposal, decide };
  }

  it("shows an executed proposal as executed, with no buttons and no 'If you approve'", async () => {
    const onSynced = vi.fn();
    const { proposal } = reopened(async () => ({ ...CARD, status: "executed" }), { onSynced });
    expect(await screen.findByText("Executed (simulated)")).toBeInTheDocument();
    expect(proposal).toHaveBeenCalledTimes(1);
    expect(proposal).toHaveBeenCalledWith(CARD.proposal_id);
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
    expect(screen.getByText("Remediation ticket approved")).toBeInTheDocument();
    expect(screen.getByText(/· approved · simulated/)).toBeInTheDocument();
    expect(onSynced).toHaveBeenCalledWith(expect.objectContaining({ status: "executed" }));
  });

  it.each(["rejected", "invalidated", "failed"] as const)("shows a %s proposal as decided", async (status) => {
    reopened(async () => ({ ...CARD, status }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View in Audit" })).toBeInTheDocument();
  });

  it("keeps the buttons disabled while the current card loads, then enables them when it is still pending", async () => {
    let resolve!: (card: ProposalCard) => void;
    const { proposal } = reopened(() => new Promise<ProposalCard>((r) => (resolve = r)));
    expect(screen.getByText("Awaiting your approval")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
    resolve(CARD);
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled();
    expect(proposal).toHaveBeenCalledTimes(1);
  });

  it("says the proposal is not available on a 404, with no buttons", async () => {
    reopened(async () => {
      throw new ApiError(404, "not found");
    });
    expect(await screen.findByRole("status")).toHaveTextContent("This proposal is not available.");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("keeps the stored card with enabled buttons when the server cannot be reached", async () => {
    const { proposal } = reopened(async () => {
      throw new ApiError(0, "unreachable");
    });
    await waitFor(() => expect(proposal).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled();
    expect(screen.getByText("Awaiting your approval")).toBeInTheDocument();
  });

  it("hands a lost session back to the app", async () => {
    const onSessionEnded = vi.fn();
    reopened(async () => {
      throw new ApiError(401, "no session");
    }, { onSessionEnded });
    await waitFor(() => expect(onSessionEnded).toHaveBeenCalled());
  });

  it("does not fetch again when it re-renders", async () => {
    const proposal = vi.fn(async () => CARD);
    const decide = { ...deciders(), proposal } as unknown as Decide;
    const view = render(<ProposalView proposal={CARD} decide={decide} recheck />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled());
    view.rerender(<ProposalView proposal={CARD} decide={decide} recheck />);
    expect(proposal).toHaveBeenCalledTimes(1);
  });

  it("does not fetch for a card made in this session, or one already decided", () => {
    const proposal = vi.fn(async () => CARD);
    const decide = { ...deciders(), proposal } as unknown as Decide;
    render(<ProposalView proposal={CARD} decide={decide} />);
    render(<ProposalView proposal={{ ...CARD, status: "executed" }} decide={decide} recheck />);
    expect(proposal).not.toHaveBeenCalled();
    expect(screen.getAllByRole("button", { name: "Approve" })[0]).toBeEnabled();
  });
});

describe("the result that replaces the decision row", () => {
  const upgrade: ProposalCard = {
    ...CARD,
    action_type: "upgrade_order",
    target_label: "initech-macbook-2",
    scope: { device: "SPKRRDF0L786", component: "memory", current: "16 GB", target: "32 GB" },
  };

  function decided(status: ProposalCard["status"], card = upgrade) {
    render(<ProposalView proposal={{ ...card, status }} decide={deciders()} />);
    return screen.getByRole("status");
  }

  it("says an approved upgrade in the card's words, with the spec, the host and a link to its Audit entries", () => {
    const result = decided("executed");
    expect(result).toHaveTextContent("Upgrade order approved");
    expect(result).toHaveTextContent("Memory 16 GB → 32 GB on initech-macbook-2");
    const link = within(result).getByRole("link", { name: "View in Audit" });
    expect(link).toHaveAttribute("href", `#/audit?proposal=${encodeURIComponent(upgrade.proposal_id)}`);
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit details" })).not.toBeInTheDocument();
  });

  it("says a rejection changed nothing and was logged", () => {
    const result = decided("rejected");
    expect(result).toHaveTextContent("Upgrade order rejected");
    expect(result).toHaveTextContent("Nothing was changed. The rejection is logged.");
    expect(within(result).getByRole("link", { name: "View in Audit" })).toBeInTheDocument();
  });

  it("marks an invalidated proposal as no longer valid, in words", () => {
    const result = decided("invalidated");
    expect(result).toHaveTextContent("No longer valid");
    expect(result).toHaveTextContent("no longer holds");
    expect(result).toHaveTextContent("Nothing was executed");
    expect(within(result).getByRole("link", { name: "View in Audit" })).toBeInTheDocument();
  });

  it("claims 'by you' only for a decision made here", async () => {
    const { user } = setup(deciders({ approve: async () => ({ proposal: { ...CARD, status: "executed" }, effect: null, duplicate: false }) }));
    await approveThroughDialog(user);
    expect(await screen.findByText(/approved by you/)).toBeInTheDocument();
  });
});
