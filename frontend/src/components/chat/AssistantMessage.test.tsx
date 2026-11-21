import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getFixture } from "../../lib/fixtures";
import type { ApprovalResult } from "../../lib/transport";
import type { AnswerEnvelope, EntityList, ProposalCard } from "../../lib/types";
import type { Decide } from "../ProposalView";
import { setReducedMotion } from "../../test/motion";
import { AssistantMessage } from "./AssistantMessage";

const envelopeOf = (scenario: string): AnswerEnvelope => getFixture("acme-001", scenario).envelope;
const LOW_DISK = envelopeOf("low_disk");
const PROPOSAL = envelopeOf("proposal");
const CARD = PROPOSAL.proposal as ProposalCard;

function setup(envelope: AnswerEnvelope, extra: Partial<React.ComponentProps<typeof AssistantMessage>> = {}) {
  const handlers = { onOpenEvidence: vi.fn(), onOpenTrace: vi.fn(), onAsk: vi.fn() };
  const user = userEvent.setup();
  const view = render(<AssistantMessage envelope={envelope} busy={false} {...handlers} {...extra} />);
  return { user, ...handlers, ...view };
}

describe("an answer", () => {
  it("leads with the reply text and carries no state word", () => {
    setup(LOW_DISK);
    expect(screen.getByText(LOW_DISK.headline)).toBeInTheDocument();
    expect(screen.getByText(LOW_DISK.claims[0].text)).toBeInTheDocument();
    expect(screen.queryByText("Answer")).not.toBeInTheDocument();
    expect(document.querySelector(".mark")).toBeNull();
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  });

  it("lists the devices as ordered chips with hostname and id", () => {
    setup(LOW_DISK);
    const list = screen.getByRole("list", { name: /low disk/i });
    const chips = within(list).getAllByRole("listitem");
    expect(chips).toHaveLength(2);
    expect(chips[0]).toHaveTextContent("acme-macbook-4");
    expect(chips[0]).toHaveTextContent("MT7PJB7N5LRE");
    expect(chips[1]).toHaveTextContent("acme-thinkpad-8");
  });

  it("shows the verified explanation instead of the headline and claims, which move into Details", async () => {
    const explained = { ...LOW_DISK, explanation: "Two laptops are nearly out of disk space and need attention." };
    const { user } = setup(explained);
    expect(screen.getByText(explained.explanation as string)).toBeInTheDocument();
    expect(screen.queryByText(LOW_DISK.headline)).not.toBeInTheDocument();
    expect(screen.queryByText(LOW_DISK.claims[0].text)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText(LOW_DISK.claims[0].text)).toBeInTheDocument();
  });

  it("reads one claim as a paragraph, not a one-bullet list", () => {
    const one = { ...LOW_DISK, claims: [LOW_DISK.claims[0]] };
    const { container } = setup(one);
    expect(screen.getByText(one.claims[0].text).tagName).toBe("P");
    expect(container.querySelector(".msg__claims")).toBeNull();
  });

  it("keeps several claims as a list", () => {
    const { container } = setup({ ...LOW_DISK, claims: [LOW_DISK.claims[0], { ...LOW_DISK.claims[0], text: "Another claim." }] });
    expect(container.querySelectorAll(".msg__claims li")).toHaveLength(2);
  });

  it("says 'in this order' only for a list of more than one device", () => {
    const one = {
      ...LOW_DISK,
      entity_lists: [{ ...LOW_DISK.entity_lists[0], items: [LOW_DISK.entity_lists[0].items[0]] as EntityList["items"] }],
    };
    const { unmount } = setup(LOW_DISK);
    expect(screen.getByText(/in this order/)).toBeInTheDocument();
    unmount();
    setup(one);
    expect(screen.queryByText(/in this order/)).not.toBeInTheDocument();
  });

  it("keeps rule, coverage, evidence and trace behind a collapsed Details disclosure", async () => {
    const { user, onOpenEvidence, onOpenTrace } = setup(LOW_DISK);
    const toggle = screen.getByRole("button", { name: "Details" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/Evaluated/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Show work/ })).not.toBeInTheDocument();

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(LOW_DISK.scope.rule as string)).toBeInTheDocument();
    expect(screen.getByText("Evaluated")).toBeInTheDocument();
    expect(screen.getByText("Stale readings")).toBeInTheDocument();
    expect(screen.getByText("No latest reading")).toBeInTheDocument();
    expect(screen.getByText("Excluded")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Evidence (2)" }));
    expect(onOpenEvidence).toHaveBeenCalledWith();
    await user.click(screen.getByRole("button", { name: "Show work" }));
    expect(onOpenTrace).toHaveBeenCalledTimes(1);

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    // Closing folds the panel (so it can animate); it is inert and hidden from assistive technology meanwhile.
    expect(document.getElementById(toggle.getAttribute("aria-controls") as string)).toHaveAttribute("inert");
    expect(screen.queryByRole("button", { name: "Show work" })).not.toBeInTheDocument();
  });

  it("folds Details open and shut on one panel that is inert while closed", async () => {
    const { user } = setup(LOW_DISK);
    const toggle = screen.getByRole("button", { name: "Details" });
    const panel = document.getElementById(toggle.getAttribute("aria-controls") as string) as HTMLElement;
    expect(panel).toHaveAttribute("data-open", "false");
    expect(panel).toHaveAttribute("inert");
    expect(panel).toHaveAttribute("aria-hidden", "true");
    await user.click(toggle);
    expect(panel).toHaveAttribute("data-open", "true");
    expect(panel).not.toHaveAttribute("inert");
    expect(panel).not.toHaveAttribute("aria-hidden", "true");
    await user.click(toggle);
    expect(panel).toHaveAttribute("data-open", "false");
    expect(panel).toHaveAttribute("inert");
  });

  it("opens and closes Details from the keyboard", async () => {
    const { user } = setup(LOW_DISK);
    await user.tab();
    const toggle = screen.getByRole("button", { name: "Details" });
    expect(toggle).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    await user.keyboard(" ");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("says a replayed answer is simulated", async () => {
    const { user } = setup(LOW_DISK, { note: "Built from a fixture." });
    await user.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText(/Simulated example\. Built from a fixture\./)).toBeInTheDocument();
  });

  it("renders model text as plain text, never as markup", () => {
    setup({ ...LOW_DISK, explanation: "<b>bold</b> <img src=x onerror=alert(1)>" });
    expect(document.querySelector("b, img")).toBeNull();
    expect(screen.getByText(/<b>bold<\/b>/)).toBeInTheDocument();
  });
});

describe("a refused action", () => {
  const DENIED = envelopeOf("device_not_found");

  it.each([
    ["not_eligible", "Not supported yet", /not supported yet\. this device is not eligible/i],
    ["insufficient_evidence", "Not enough evidence", /not enough evidence in the readings/i],
  ] as const)("shows %s as the word %s and explains the reason in Details", async (code, word, reason) => {
    const { user } = setup({ ...DENIED, reason_code: code });
    expect(document.querySelector(".msg__state .mark")).toHaveTextContent(word);
    expect(document.querySelector(".msg__state .mark svg")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText("Reason").nextElementSibling).toHaveTextContent(reason);
    expect(screen.queryByText(code)).not.toBeInTheDocument();
  });

  it("keeps Not found for a missing device", () => {
    setup({ ...DENIED, reason_code: "not_found" });
    expect(document.querySelector(".msg__state .mark")).toHaveTextContent("Not found");
  });
});

describe("outcomes that are not an answer", () => {
  const CASES: Array<[string, string, string]> = [
    ["compliance_high_no_match", "No match", "No devices matched (10 evaluated)"],
    ["insufficient_trend", "Not enough data", "There are not enough snapshots in that window to report a trend."],
    ["device_not_found", "Not found", "I can't find that in your fleet."],
    ["unsupported", "Not something I can do", "I can only answer questions about this company's managed devices."],
    ["tool_error", "Lookup failed", "A data lookup failed, so I can't answer reliably. No changes were made."],
  ];

  it.each(CASES)("%s shows a mark, the word %s and the reply", (scenario, word, reply) => {
    setup(envelopeOf(scenario));
    const mark = document.querySelector(".msg__state .mark") as HTMLElement;
    expect(mark).toHaveTextContent(word);
    expect(mark.querySelector("svg")).not.toBeNull();
    expect(screen.getByText(reply)).toBeInTheDocument();
  });

  it("keeps a coverage note readable in the reply", () => {
    setup(envelopeOf("insufficient_trend"));
    expect(screen.getByText(/10 devices have fewer than 3 snapshots/)).toBeInTheDocument();
  });

  it("asks a clarification and offers its options as chips that send the option text", async () => {
    const envelope = envelopeOf("clarification");
    const { user, onAsk } = setup(envelope);
    expect(document.querySelector(".msg__state .mark")).toHaveTextContent("One detail needed");
    expect(screen.getByText(envelope.clarification!.question)).toBeInTheDocument();
    const group = screen.getByRole("group", { name: "Choose one" });
    const options = within(group).getAllByRole("button");
    expect(options.map((o) => o.textContent)).toEqual(envelope.clarification!.options);
    await user.click(screen.getByRole("button", { name: "Outdated macOS" }));
    expect(onAsk).toHaveBeenCalledWith("Outdated macOS");
  });

  it("disables clarification options while a turn runs", () => {
    setup(envelopeOf("clarification"), { busy: true });
    for (const option of within(screen.getByRole("group", { name: "Choose one" })).getAllByRole("button")) {
      expect(option).toBeDisabled();
    }
  });
});

describe("suggested actions", () => {
  const ADVISE: AnswerEnvelope = {
    ...LOW_DISK,
    headline: "2 actions are supported by the evidence",
    suggested_actions: [
      {
        action_type: "upgrade_order",
        device_id: "MT7PJB7N5LRE",
        target_label: "acme-macbook-4",
        label: "Order a storage upgrade (512 GiB to 1 TiB)",
        reason: "2.0% free of 512.0 GiB.",
        request: "Order a storage upgrade for acme-macbook-4",
        refs: [LOW_DISK.evidence[0].ref],
      },
      {
        action_type: "upgrade_order",
        device_id: "8NM23J95R5I6",
        target_label: "acme-thinkpad-8",
        label: "Order a storage upgrade (1 TiB to 2 TiB)",
        reason: "2.6% free of 1.0 TiB.",
        request: "Order a storage upgrade for acme-thinkpad-8",
        refs: [LOW_DISK.evidence[1].ref],
      },
    ],
  };

  it("shows each suggestion's label and device, and sends its request when chosen", async () => {
    const { user, onAsk } = setup(ADVISE);
    const group = screen.getByRole("group", { name: "Suggested actions" });
    const chips = within(group).getAllByRole("button");
    expect(chips).toHaveLength(2);
    expect(chips[0]).toHaveTextContent("Order a storage upgrade (512 GiB to 1 TiB)");
    expect(chips[0]).toHaveTextContent("acme-macbook-4");
    await user.click(chips[1]);
    expect(onAsk).toHaveBeenCalledWith("Order a storage upgrade for acme-thinkpad-8");
  });

  it("is reachable and activated by keyboard", async () => {
    const { user, onAsk } = setup(ADVISE);
    const chips = within(screen.getByRole("group", { name: "Suggested actions" })).getAllByRole("button");
    chips[0].focus();
    await user.keyboard("{Enter}");
    expect(onAsk).toHaveBeenLastCalledWith("Order a storage upgrade for acme-macbook-4");
    await user.tab();
    expect(chips[1]).toHaveFocus();
    await user.keyboard(" ");
    expect(onAsk).toHaveBeenLastCalledWith("Order a storage upgrade for acme-thinkpad-8");
  });

  it("disables every chip while a turn runs", async () => {
    const { user, onAsk } = setup(ADVISE, { busy: true });
    const chips = within(screen.getByRole("group", { name: "Suggested actions" })).getAllByRole("button");
    for (const chip of chips) expect(chip).toBeDisabled();
    await user.click(chips[0]);
    expect(onAsk).not.toHaveBeenCalled();
  });

  it("does not repeat the claims in the reply when chips already name each action; they stay under Details", async () => {
    const { user } = setup(ADVISE);
    expect(screen.queryByText(LOW_DISK.claims[0].text)).not.toBeInTheDocument();
    expect(document.querySelector(".msg__claims, .msg__claim")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText(LOW_DISK.claims[0].text)).toBeInTheDocument();
  });

  it("gives each chip a third line with its reason when no explanation covers it, and keeps the title", () => {
    setup(ADVISE);
    const chips = within(screen.getByRole("group", { name: "Suggested actions" })).getAllByRole("button");
    expect(chips[0]).toHaveTextContent("2.0% free of 512.0 GiB.");
    expect(chips[0].querySelector(".chip__reason")).not.toBeNull();
    expect(chips[0]).toHaveAttribute("title", "2.0% free of 512.0 GiB.");
  });

  it("leaves the reason off the chips when the reply's explanation already gives it", () => {
    setup({ ...ADVISE, explanation: "Both laptops are nearly full, so each needs more storage." });
    const chips = within(screen.getByRole("group", { name: "Suggested actions" })).getAllByRole("button");
    expect(chips[0].querySelector(".chip__reason")).toBeNull();
    expect(chips[0]).toHaveAttribute("title", "2.0% free of 512.0 GiB.");
  });

  it("marks a chip as used once picked: a check and hidden '(asked)' text, no toggle state, still focusable", async () => {
    const { user, onAsk } = setup(ADVISE);
    const chips = within(screen.getByRole("group", { name: "Suggested actions" })).getAllByRole("button");
    expect(chips[0]).not.toHaveAttribute("aria-pressed");
    expect(chips[0]).not.toHaveTextContent("(asked)");
    await user.click(chips[0]);
    expect(onAsk).toHaveBeenCalledWith("Order a storage upgrade for acme-macbook-4");
    expect(chips[0]).not.toHaveAttribute("aria-pressed");
    expect(chips[0]).toHaveAccessibleName(/\(asked\)/);
    expect(chips[0].querySelector(".sr-only")).toHaveTextContent("(asked)");
    expect(chips[0].querySelector(".chip__label svg")).not.toBeNull();
    expect(chips[1]).not.toHaveAccessibleName(/\(asked\)/);
    expect(chips[1].querySelector("svg")).toBeNull();
    chips[0].focus();
    expect(chips[0]).toHaveFocus();
    expect(chips[0]).toBeEnabled();
  });

  describe("returning focus to the composer after a pick", () => {
    function withComposer() {
      const input = document.createElement("input");
      input.id = "ask-input";
      document.body.append(input);
      return input;
    }
    const original = window.matchMedia;
    function pointer(fine: boolean) {
      window.matchMedia = ((query: string) =>
        ({
          matches: fine && query.includes("hover: hover") && query.includes("pointer: fine"),
          media: query,
          addEventListener: () => {},
          removeEventListener: () => {},
        }) as unknown as MediaQueryList) as typeof window.matchMedia;
    }
    afterEach(() => {
      window.matchMedia = original;
      document.getElementById("ask-input")?.remove();
    });

    it("refocuses the composer with a mouse or trackpad", async () => {
      const input = withComposer();
      pointer(true);
      const { user } = setup(ADVISE);
      await user.click(within(screen.getByRole("group", { name: "Suggested actions" })).getAllByRole("button")[0]);
      expect(input).toHaveFocus();
    });

    it("leaves the keyboard down on a touch screen", async () => {
      const input = withComposer();
      pointer(false);
      const { user } = setup(ADVISE);
      await user.click(within(screen.getByRole("group", { name: "Suggested actions" })).getAllByRole("button")[0]);
      expect(input).not.toHaveFocus();
    });
  });

  it("does not list the same devices twice as device chips", () => {
    setup({ ...ADVISE, entity_lists: [{ list_id: "x", title: "Suggested actions", items: [{ device_id: "MT7PJB7N5LRE", label: "acme-macbook-4", rank: 1 }] }] });
    expect(screen.queryByRole("list", { name: "Suggested actions" })).not.toBeInTheDocument();
  });

  it("shows no group when there are no suggestions, even for an older stored envelope", () => {
    const old = { ...LOW_DISK } as Partial<AnswerEnvelope>;
    delete old.suggested_actions;
    setup(old as AnswerEnvelope);
    expect(screen.queryByRole("group", { name: "Suggested actions" })).not.toBeInTheDocument();
  });
});

describe("a proposal in the thread", () => {
  function proposal(status: ProposalCard["status"], via: "approve" | "reject" = "approve") {
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
    const view = setup(PROPOSAL, { decide });
    async function decideNow() {
      if (via === "reject") {
        await view.user.click(screen.getByRole("button", { name: "Reject" }));
        return;
      }
      await view.user.click(screen.getByRole("button", { name: "Approve" }));
      await view.user.click(
        within(await screen.findByRole("dialog")).getByRole("button", { name: "Approve and execute (simulated)" }),
      );
    }
    return { ...view, decideNow };
  }
  const marks = () => Array.from(document.querySelectorAll(".mark"));
  const cardMark = () => document.querySelector(".proposal .mark") as HTMLElement;

  it("reads as waiting while nobody has decided, with one mark and one lead", () => {
    proposal("executed");
    expect(screen.getByText("Here is the proposal. Nothing changes until you approve it.")).toBeInTheDocument();
    expect(marks()).toHaveLength(1);
    expect(cardMark()).toHaveTextContent("Awaiting your approval");
    expect(document.querySelector(".msg__state")).toBeNull();
    expect(document.body.textContent).not.toMatch(/Proposal for your decision|waits for your approval/);
    expect(screen.getByRole("region", { name: "Proposed action" })).toBeInTheDocument();
  });

  it("says it was executed, in code-written words, once approved, with the card's single mark", async () => {
    const { decideNow } = proposal("executed");
    await decideNow();
    expect(await screen.findByText("Executed (simulated): Remediation ticket for acme-macbook-2.")).toBeInTheDocument();
    expect(marks()).toHaveLength(1);
    expect(cardMark()).toHaveTextContent("Executed (simulated)");
    expect(cardMark()).toHaveAttribute("data-tone", "pass");
    expect(cardMark().querySelector("svg")).not.toBeNull();
    expect(document.body.textContent).not.toMatch(/waits for your approval|Proposal for your decision|Here is the proposal/);
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("says nothing was done once rejected", async () => {
    const { decideNow } = proposal("rejected", "reject");
    await decideNow();
    expect(await screen.findByText("Rejected: no action was taken.")).toBeInTheDocument();
    expect(marks()).toHaveLength(1);
    expect(cardMark()).toHaveTextContent("Rejected");
    expect(document.body.textContent).not.toMatch(/waits for your approval/);
  });

  it("says the evidence no longer supports an invalidated proposal", async () => {
    const { decideNow } = proposal("invalidated");
    await decideNow();
    expect(await screen.findByText("Not executed: the evidence no longer supports it.")).toBeInTheDocument();
    expect(cardMark()).toHaveTextContent("Invalidated");
  });

  it("says the simulated action failed", async () => {
    const { decideNow } = proposal("failed");
    await decideNow();
    expect(await screen.findByText("Not executed: the simulated action failed.")).toBeInTheDocument();
    expect(cardMark()).toHaveAttribute("data-tone", "alert");
  });
});

describe("a reply's motion and ticking numbers", () => {
  afterEach(() => setReducedMotion(true));

  it("reads a code-written count once, as the whole sentence, with the numbers set to tick", () => {
    const { container } = setup(LOW_DISK);
    const lead = container.querySelector(".msg__lead") as HTMLElement;
    expect(screen.getByText("2 of 10 devices have under 10% disk space free")).toHaveClass("sr-only");
    expect(lead.querySelector("[aria-hidden='true']")).not.toBeNull();
    expect(lead.textContent).toContain("2");
  });

  it("never parses the model's explanation for a count", () => {
    const explained = { ...LOW_DISK, explanation: "2 of 10 laptops are nearly full." };
    const { container } = setup(explained);
    const lead = container.querySelector(".msg__lead") as HTMLElement;
    expect(lead.textContent).toBe("2 of 10 laptops are nearly full.");
    expect(lead.querySelector(".sr-only")).toBeNull();
  });

  it("never parses a clarifying question for a count", () => {
    const asking = envelopeOf("clarification");
    const { container } = setup({ ...asking, clarification: { ...asking.clarification!, question: "2 of 3 matched. Which one?" } });
    expect((container.querySelector(".msg__lead") as HTMLElement).querySelector(".sr-only")).toBeNull();
  });

  it("arrives with an opacity and a 6px rise when it was asked in this session", () => {
    setReducedMotion(false);
    const { container } = setup(LOW_DISK, { reopened: false });
    const article = container.querySelector("article") as HTMLElement;
    expect(article.style.opacity).toBe("0");
    expect(article.style.transform).toContain("translateY(6px)");
  });

  it("is simply there when it was reopened from history", () => {
    setReducedMotion(false);
    const { container } = setup(LOW_DISK, { reopened: true });
    const article = container.querySelector("article") as HTMLElement;
    expect(article.style.opacity).not.toBe("0");
    expect(article.style.transform).not.toContain("translateY(6px)");
  });

  it("does not move under reduced motion, even when asked in this session", () => {
    setReducedMotion(true);
    const { container } = setup(LOW_DISK, { reopened: false });
    const article = container.querySelector("article") as HTMLElement;
    expect(article.style.opacity).not.toBe("0");
    expect(article.style.transform).not.toContain("translateY(6px)");
  });

  it("folds Details open and shut with its body leaving only after the exit has played", async () => {
    setReducedMotion(false);
    const { user } = setup(LOW_DISK);
    const toggle = screen.getByRole("button", { name: "Details" });
    await user.click(toggle);
    expect(await screen.findByText("Evaluated")).toBeInTheDocument();
    await user.click(toggle);
    const panel = document.getElementById(toggle.getAttribute("aria-controls") as string) as HTMLElement;
    expect(panel).toHaveAttribute("inert");
    await waitFor(() => expect(screen.queryByText("Evaluated")).not.toBeInTheDocument(), { timeout: 2000 });
  });
});
