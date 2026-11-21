import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { getFixture } from "../lib/fixtures";
import type { ApprovalResult } from "../lib/transport";
import type { ProposalCard } from "../lib/types";
import { AnswerSheet } from "./AnswerSheet";
import type { Decide } from "./ProposalView";

const ENVELOPE = getFixture("acme-001", "proposal").envelope;
const CARD = ENVELOPE.proposal as ProposalCard;
const noop = () => {};

function setup(status: ProposalCard["status"], via: "approve" | "reject" = "approve") {
  const decide = {
    approve: vi.fn(
      async (): Promise<ApprovalResult> => ({
        proposal: { ...CARD, status },
        effect: status === "executed" ? { effect_id: 3, simulated: true } : null,
        duplicate: false,
      }),
    ),
    reject: vi.fn(async () => ({ proposal: { ...CARD, status } })),
  } as unknown as Decide;
  const user = userEvent.setup();
  const { container } = render(
    <AnswerSheet envelope={ENVELOPE} onOpenEvidence={noop} onOpenTrace={noop} onAsk={noop} decide={decide} />,
  );
  async function decideNow() {
    if (via === "reject") {
      await user.click(screen.getByRole("button", { name: "Reject" }));
      return;
    }
    await user.click(screen.getByRole("button", { name: "Approve" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Approve and execute (simulated)" }));
  }
  return { user, container, decideNow };
}

function chip(container: HTMLElement) {
  return container.querySelector(".sheet__head .mark") as HTMLElement;
}

describe("a proposal answer sheet", () => {
  it("reads as waiting while nobody has decided", () => {
    const { container } = setup("executed");
    expect(chip(container)).toHaveTextContent("Proposal for your decision");
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(/waits for your approval/);
  });

  it("says it was executed, in code-written words, once approved", async () => {
    const { container, decideNow } = setup("executed");
    await decideNow();
    expect(await screen.findByRole("heading", { level: 2 })).toHaveTextContent(
      "Executed (simulated): Remediation ticket for acme-macbook-2.",
    );
    expect(chip(container)).toHaveTextContent("Executed (simulated)");
    expect(chip(container)).toHaveAttribute("data-tone", "pass");
    expect(chip(container).querySelector("svg")).not.toBeNull();
    expect(document.body.textContent).not.toMatch(/waits for your approval|Proposal for your decision/);
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("says nothing was done once rejected", async () => {
    const { container, decideNow } = setup("rejected", "reject");
    await decideNow();
    expect(await screen.findByRole("heading", { level: 2 })).toHaveTextContent("Rejected: no action was taken.");
    expect(chip(container)).toHaveTextContent("Rejected");
    expect(document.body.textContent).not.toMatch(/waits for your approval/);
  });

  it("says the evidence no longer supports an invalidated proposal", async () => {
    const { container, decideNow } = setup("invalidated");
    await decideNow();
    expect(await screen.findByRole("heading", { level: 2 })).toHaveTextContent(
      "Not executed: the evidence no longer supports it.",
    );
    expect(chip(container)).toHaveTextContent("Not executed");
  });

  it("says the simulated action failed", async () => {
    const { container, decideNow } = setup("failed");
    await decideNow();
    expect(await screen.findByRole("heading", { level: 2 })).toHaveTextContent(
      "Not executed: the simulated action failed.",
    );
    expect(chip(container)).toHaveAttribute("data-tone", "alert");
  });
});
