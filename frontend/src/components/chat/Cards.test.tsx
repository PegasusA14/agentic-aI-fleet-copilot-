import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { getFixture } from "../../lib/fixtures";
import { refusalKind } from "../../lib/refusal";
import type { AnswerEnvelope } from "../../types/AnswerEnvelope";
import { AssistantMessage } from "./AssistantMessage";

const fixture = (scenario: string, tenant = "initech-003"): AnswerEnvelope => getFixture(tenant, scenario).envelope;

function setup(envelope: AnswerEnvelope, extra: Partial<React.ComponentProps<typeof AssistantMessage>> = {}) {
  const handlers = { onOpenEvidence: vi.fn(), onOpenTrace: vi.fn(), onAsk: vi.fn() };
  const user = userEvent.setup();
  const view = render(<AssistantMessage envelope={envelope} busy={false} {...handlers} {...extra} />);
  return { user, ...handlers, ...view };
}

describe("which card a refusal takes", () => {
  it.each([
    ["not_eligible", "denied", "evidence"],
    ["insufficient_evidence", "denied", "evidence"],
    ["already_done", "denied", "evidence"],
    ["too_many_pending", "denied", "evidence"],
    ["other_company", "denied", "company"],
    ["not_found", "denied", "uniform"],
    ["prompt_injection", "denied", "uniform"],
    ["off_topic", "unsupported", "scope"],
    ["data_unavailable", "unsupported", "scope"],
    ["unsupported_action", "unsupported", "neutral"],
  ] as const)("%s is a %s reply with the %s card", (code, outcome, kind) => {
    expect(refusalKind({ ...fixture("device_not_found"), outcome, reason_code: code })).toBe(kind);
  });

  it("leaves other outcomes to their own layout", () => {
    expect(refusalKind(fixture("tool_error"))).toBeNull();
    expect(refusalKind(fixture("low_disk"))).toBeNull();
    expect(refusalKind({ ...fixture("device_not_found"), reason_code: "something_new" as never })).toBeNull();
  });
});

describe("a refusal for want of evidence", () => {
  const A = fixture("insufficient_evidence");

  it("is a region named by its headline, with the reason, what it would need, and chips", () => {
    setup(A);
    const card = screen.getByRole("region", { name: A.headline });
    expect(card).toHaveAttribute("data-kind", "evidence");
    expect(within(card).getByText(A.notes[0])).toBeInTheDocument();
    expect(within(card).getByText("What I'd need to see")).toBeInTheDocument();
    const needs = within(card).getByRole("list", { name: "What I'd need to see" });
    expect(within(needs).getAllByRole("listitem").map((li) => li.textContent)).toEqual(A.needs);
    expect(card.querySelector(".rcard__mark svg")).not.toBeNull();
  });

  it("keeps the state's mark and word above the card", () => {
    setup(A);
    expect(document.querySelector(".msg__state .mark")).toHaveTextContent("Not supported yet");
  });

  it("sends each alternative's own request, not its label", async () => {
    const { user, onAsk } = setup(A);
    const group = screen.getByRole("group", { name: "You could ask" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(A.alternatives.map((a) => a.label));
    await user.click(screen.getByRole("button", { name: "Show its 30-day health" }));
    expect(onAsk).toHaveBeenCalledWith("Show the 30-day history of initech-macbook-2");
  });

  it("marks a pressed chip asked for a screen reader, without aria-pressed", async () => {
    const { user } = setup(A);
    const chip = screen.getByRole("button", { name: "Show its 30-day health" });
    await user.click(chip);
    expect(chip).toHaveTextContent("(asked)");
    expect(chip).not.toHaveAttribute("aria-pressed");
  });

  it("shows no needs block when there are none, and waits while a turn runs", () => {
    setup({ ...A, needs: [] }, { busy: true });
    expect(screen.queryByText("What I'd need to see")).not.toBeInTheDocument();
    for (const chip of within(screen.getByRole("group", { name: "You could ask" })).getAllByRole("button")) {
      expect(chip).toBeDisabled();
    }
  });

  it("offers no Switch company chip", () => {
    setup(A, { onSwitchCompany: vi.fn() });
    expect(screen.queryByRole("button", { name: "Switch company" })).not.toBeInTheDocument();
  });
});

describe("a question about another company", () => {
  const B = fixture("other_company");

  it("names the company it can see, says why, and offers Switch company", () => {
    setup(B, { onSwitchCompany: vi.fn() });
    const card = screen.getByRole("region", { name: "You're working in Initech, so I can only see Initech." });
    expect(card).toHaveAttribute("data-kind", "company");
    expect(within(card).getByText(B.notes[0])).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Switch company" }).querySelector("svg")).not.toBeNull();
    expect(document.querySelector(".msg__state .mark")).toHaveTextContent("Outside your company");
  });

  it("runs the same switch action as the top bar when the chip is pressed", async () => {
    const onSwitchCompany = vi.fn();
    const { user } = setup(B, { onSwitchCompany });
    await user.click(screen.getByRole("button", { name: "Switch company" }));
    expect(onSwitchCompany).toHaveBeenCalledTimes(1);
  });

  it("offers no chip when it cannot switch", () => {
    setup(B);
    expect(screen.queryByRole("button", { name: "Switch company" })).not.toBeInTheDocument();
  });

  it("offers no chip when the envelope does not offer one", () => {
    setup({ ...B, offer_switch_company: false }, { onSwitchCompany: vi.fn() });
    expect(screen.queryByRole("button", { name: "Switch company" })).not.toBeInTheDocument();
  });
});

describe("a missing thing is one uniform answer", () => {
  it("carries a headline and a note and no chips, whatever the envelope holds", () => {
    const missing = fixture("device_not_found");
    setup(
      { ...missing, alternatives: [{ label: "Anything", request: "Anything" }], offer_switch_company: true, needs: ["x"] },
      { onSwitchCompany: vi.fn() },
    );
    const card = screen.getByRole("region", { name: missing.headline });
    expect(card).toHaveAttribute("data-kind", "uniform");
    expect(within(card).queryByRole("button")).toBeNull();
    expect(within(card).queryByRole("group")).toBeNull();
    expect(within(card).queryByText("What I'd need to see")).toBeNull();
    expect(card.querySelector(".rcard__mark svg")).not.toBeNull();
  });
});

describe("a question outside the data", () => {
  const D = fixture("unsupported_wifi");

  it("has no mark, the capability note and the alternatives as chips", async () => {
    const { user, onAsk } = setup(D);
    const card = screen.getByRole("region", { name: D.headline });
    expect(card).toHaveAttribute("data-kind", "scope");
    expect(card.querySelector(".rcard__mark")).toBeNull();
    expect(within(card).getByText(D.notes[0])).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Fleet health summary" }));
    expect(onAsk).toHaveBeenCalledWith("Give me a fleet health summary");
  });
});

describe("an action it does not take in chat", () => {
  it("shows a neutral mark, the headline and the note", () => {
    setup({
      ...fixture("device_not_found"),
      outcome: "unsupported",
      reason_code: "unsupported_action",
      headline: "I can't approve a proposal from the chat.",
      notes: ["Approvals are decided on the proposal itself."],
    });
    const card = screen.getByRole("region", { name: "I can't approve a proposal from the chat." });
    expect(card).toHaveAttribute("data-kind", "neutral");
    expect(card.querySelector(".rcard__mark svg")).not.toBeNull();
    expect(within(card).getByText("Approvals are decided on the proposal itself.")).toBeInTheDocument();
    expect(document.querySelector(".msg__state .mark")).toHaveTextContent("Not available here");
  });
});

describe("a clarification", () => {
  const C = fixture("clarification_device");

  it("asks its question in a region of its own and sends each choice's full request", async () => {
    const { user, onAsk } = setup(C);
    const card = screen.getByRole("region", { name: C.clarification!.question });
    const group = within(card).getByRole("group", { name: "Choose one" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(C.clarification!.choices.map((c) => c.label));
    await user.click(within(group).getByRole("button", { name: "initech-macbook-6" }));
    expect(onAsk).toHaveBeenCalledWith("How is initech-macbook-6 doing on battery?");
    expect(onAsk).not.toHaveBeenCalledWith("initech-macbook-6");
  });

  it("styles All of them with the green outline, and no other choice", () => {
    setup(C);
    const strong = [...document.querySelectorAll(".chip[data-strong]")];
    expect(strong.map((c) => c.textContent)).toEqual(["All of them"]);
  });

  it("sends the request of All of them", async () => {
    const { user, onAsk } = setup(C);
    await user.click(screen.getByRole("button", { name: "All of them" }));
    expect(onAsk).toHaveBeenCalledWith("How are all macbooks doing on battery?");
  });

  it("marks a chosen chip asked, visually hidden, with no aria-pressed", async () => {
    const { user } = setup(C);
    const chip = screen.getByRole("button", { name: "initech-macbook-2" });
    await user.click(chip);
    expect(chip).toHaveTextContent("(asked)");
    expect(chip.querySelector(".sr-only")).not.toBeNull();
    expect(chip).not.toHaveAttribute("aria-pressed");
  });

  it("falls back to the option text when an older envelope has no choices", async () => {
    const old = fixture("clarification");
    expect(old.clarification!.choices).toEqual([]);
    const { user, onAsk } = setup(old);
    const group = screen.getByRole("group", { name: "Choose one" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(old.clarification!.options);
    await user.click(screen.getByRole("button", { name: "Low disk space" }));
    expect(onAsk).toHaveBeenCalledWith("Low disk space");
    expect(document.querySelector(".chip[data-strong]")).toBeNull();
  });

  it("waits while a turn runs", () => {
    setup(C, { busy: true });
    for (const chip of within(screen.getByRole("group", { name: "Choose one" })).getAllByRole("button")) {
      expect(chip).toBeDisabled();
    }
  });
});
