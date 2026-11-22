import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { getFixture } from "../../lib/fixtures";
import type { AnswerEnvelope } from "../../types/AnswerEnvelope";
import { AssistantMessage } from "./AssistantMessage";

const spoken = (scenario: string, tenant = "acme-001"): AnswerEnvelope => getFixture(tenant, scenario).envelope;
const bare = (envelope: AnswerEnvelope): AnswerEnvelope => ({ ...envelope, reply: "", explanation: null });

function setup(envelope: AnswerEnvelope, extra: Partial<React.ComponentProps<typeof AssistantMessage>> = {}) {
  const handlers = { onOpenEvidence: vi.fn(), onOpenTrace: vi.fn(), onAsk: vi.fn() };
  const user = userEvent.setup();
  const view = render(<AssistantMessage envelope={envelope} busy={false} {...handlers} {...extra} />);
  return { user, ...handlers, ...view };
}

describe("the reply as the lead of an answer", () => {
  const LOW = spoken("low_disk");

  it("sets the bold headline first, then the reply as plain paragraphs split on blank lines", () => {
    const { container } = setup(LOW);
    const lead = container.querySelector(".msg__lead") as HTMLElement;
    expect(lead).toHaveTextContent(LOW.headline.replace(/\s+/g, " ").slice(0, 20));
    const paragraphs = [...container.querySelectorAll(".reply .reply__p")];
    expect(paragraphs.map((p) => p.textContent)).toEqual(LOW.reply.split("\n\n"));
    expect(paragraphs).toHaveLength(2);
    // The headline comes before the reply in the document.
    expect(lead.compareDocumentPosition(paragraphs[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("does not set the first sentence of an explanation under the headline once a reply leads", () => {
    const { container } = setup(LOW);
    expect(container.querySelector(".msg__support")).toBeNull();
    // The reply is never repeated inside Details.
    expect(screen.queryAllByText(LOW.reply.split("\n\n")[0])).toHaveLength(1);
  });

  it("moves the code summary into the table card, under its title", () => {
    const { container } = setup(LOW);
    const card = container.querySelector(".atable") as HTMLElement;
    const summary = within(card).getByText(LOW.summary);
    expect(summary).toHaveClass("atable__summary");
    expect(card.querySelector(".atable__title")!.compareDocumentPosition(summary) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // And not again under the headline.
    expect(screen.getAllByText(LOW.summary)).toHaveLength(1);
  });

  it("keeps today's headline and summary when the reply is empty", () => {
    const { container } = setup(bare(LOW));
    expect(container.querySelector(".reply")).toBeNull();
    expect(container.querySelector(".msg__support")).toHaveTextContent(LOW.summary);
    expect(container.querySelector(".atable__summary")).toBeNull();
  });

  it("leads an insight answer with its reply too", () => {
    const { container } = setup(spoken("fleet_health"));
    expect(container.querySelector(".reply")).toHaveTextContent("These are ranked by urgency, with acme-macbook-1 first.");
  });

  it("sets a pending proposal's reply under what the copilot would do", () => {
    const { container } = setup(spoken("proposal"));
    expect(container.querySelector(".msg__lead")).toHaveTextContent("I'd open a screen lock ticket for acme-macbook-2.");
    expect(container.querySelector(".reply")).toHaveTextContent("That ticket proposal is ready for your review");
    expect(container.querySelector(".msg__support")).toBeNull();
  });
});

describe("a chat turn", () => {
  const HELLO = spoken("chat_capabilities");

  it("is a plain message: the reply text only, with no card, table, badge line or headline", () => {
    const { container } = setup(HELLO);
    expect(container.querySelectorAll(".reply__p")).toHaveLength(2);
    expect(screen.getByText(/I answer questions about this company's devices/)).toBeInTheDocument();
    expect(screen.queryByText(HELLO.headline)).not.toBeInTheDocument();
    expect(container.querySelector(".msg__state, .mark, .rcard, .atable, .details")).toBeNull();
    expect(screen.queryByText("Reply")).not.toBeInTheDocument();
  });

  it("offers copy (the reply, alone) and retry", async () => {
    const asked = vi.fn();
    const { user } = setup(HELLO, { question: "What can you do?", onAsk: asked });
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(asked).toHaveBeenCalledWith("What can you do?");
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: write }, configurable: true });
    await user.click(screen.getByRole("button", { name: "Copy answer" }));
    expect(write).toHaveBeenCalledWith(HELLO.reply);
  });

  it("shows follow-up chips when the turn carries any", async () => {
    const withChips: AnswerEnvelope = { ...HELLO, follow_ups: [{ label: "Fleet health summary", request: "Give me a fleet health summary" }] };
    const { user, onAsk } = setup(withChips);
    await user.click(screen.getByRole("button", { name: /Fleet health summary/ }));
    expect(onAsk).toHaveBeenCalledWith("Give me a fleet health summary");
  });

  it("falls back to its fixed headline when the model wrote nothing", () => {
    setup({ ...HELLO, reply: "", explanation: null });
    expect(screen.getByText(HELLO.headline)).toBeInTheDocument();
  });
});

describe("a refusal card with a reply", () => {
  const WHY = spoken("insufficient_evidence");

  it("sets the reply as the first paragraph in the card, above what would be needed", () => {
    const { container } = setup(WHY);
    const card = container.querySelector(".rcard") as HTMLElement;
    const reply = card.querySelector(".reply") as HTMLElement;
    expect(reply).toHaveTextContent("I can't flag that laptop for replacement yet");
    expect(reply.textContent).toBe(WHY.reply.replaceAll("\n\n", ""));
    const needs = within(card).getByText("What I'd need to see");
    expect(reply.compareDocumentPosition(needs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(card.querySelector(".rcard__headline")).toHaveTextContent(WHY.headline);
  });

  it("is unchanged when there is no reply", () => {
    const { container } = setup(bare(WHY));
    expect(container.querySelector(".rcard .reply")).toBeNull();
    expect(screen.getByText("What I'd need to see")).toBeInTheDocument();
  });

  it("frames a data-unavailable refusal the same way", () => {
    const { container } = setup(spoken("unsupported_wifi"));
    expect(container.querySelector(".rcard .reply")).toHaveTextContent("I can't rank devices by Wi-Fi signal");
  });
});

describe("a repeat card with a reply", () => {
  const REPEAT: AnswerEnvelope = { ...bare(spoken("low_disk")), repeat_of: "earlier", reply: "That's the same list as before." };

  it("says the reply instead of the fixed sentence, and keeps both ways on", async () => {
    const onJump = vi.fn();
    const { user, container } = setup(REPEAT, { onJump });
    expect(container.querySelector(".repeat__text")).toHaveTextContent("That's the same list as before.");
    expect(screen.queryByText(/Same answer as above/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Jump to it" }));
    expect(onJump).toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Show again" }));
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("keeps the fixed sentence when there is no reply", () => {
    setup({ ...REPEAT, reply: "" });
    expect(screen.getByText(/Same answer as above\. Nothing has changed since/)).toBeInTheDocument();
  });
});

describe("the reply is plain text", () => {
  const HOSTILE = "Done <script>alert(1)</script> and **bold** with <b>tags</b>.\n\nSecond: <img src=x onerror=alert(1)>";

  it("shows script tags, asterisks and HTML as literal characters", () => {
    const { container } = setup({ ...spoken("low_disk"), reply: HOSTILE });
    const paragraphs = [...container.querySelectorAll(".reply__p")];
    expect(paragraphs[0].textContent).toBe("Done <script>alert(1)</script> and **bold** with <b>tags</b>.");
    expect(paragraphs[1].textContent).toBe("Second: <img src=x onerror=alert(1)>");
    expect(container.querySelector(".reply script, .reply b, .reply strong, .reply img")).toBeNull();
    expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
  });

  it("does the same for a chat message and a refusal card", () => {
    const chat = render(<AssistantMessage envelope={{ ...spoken("chat_hello"), reply: HOSTILE }} busy={false} onOpenEvidence={vi.fn()} onOpenTrace={vi.fn()} onAsk={vi.fn()} />);
    expect(chat.container.querySelector("script, b, strong, img")).toBeNull();
    expect(chat.container.querySelector(".reply__p")?.textContent).toContain("<script>alert(1)</script>");
    chat.unmount();
    const card = render(<AssistantMessage envelope={{ ...spoken("insufficient_evidence"), reply: HOSTILE }} busy={false} onOpenEvidence={vi.fn()} onOpenTrace={vi.fn()} onAsk={vi.fn()} />);
    expect(card.container.querySelector(".rcard script, .rcard b, .rcard strong, .rcard img")).toBeNull();
    expect(card.container.querySelector(".rcard .reply__p")?.textContent).toContain("**bold**");
  });
});
