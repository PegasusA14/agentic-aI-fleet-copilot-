import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { numberEvidence } from "../../lib/answerTable";
import { getFixture } from "../../lib/fixtures";
import type { AnswerEnvelope } from "../../lib/types";
import { AssistantMessage } from "./AssistantMessage";
import { InsightList } from "./InsightList";

const BATTERY = { ...getFixture("acme-001", "battery_wear").envelope, reply: "", explanation: null };
const HEALTH = { ...getFixture("acme-001", "fleet_health").envelope, reply: "", explanation: null };

function list(envelope: AnswerEnvelope) {
  const onAsk = vi.fn();
  render(
    <InsightList
      envelope={envelope}
      numbered={numberEvidence(envelope)}
      open={{ evidence: false, notChecked: false, how: false }}
      onOpen={() => {}}
      hot={null}
      rowId={(n) => `ev-${n}`}
      onOpenTrace={() => {}}
      busy={false}
      onAsk={onAsk}
    />,
  );
  return { onAsk };
}

describe("InsightList", () => {
  it("renders one card per insight, in the order the answer gave", () => {
    list(HEALTH);
    const cards = screen.getAllByRole("article");
    expect(cards).toHaveLength(HEALTH.insights.length);
    HEALTH.insights.forEach((insight, i) => {
      expect(within(cards[i]!).getByText(insight.finding)).toBeInTheDocument();
    });
  });

  it("keeps act-soon cards in full and folds the later watch cards", () => {
    list(HEALTH);
    const cards = screen.getAllByRole("article");
    HEALTH.insights.forEach((insight, i) => {
      const folded = cards[i]!.hasAttribute("data-compact");
      expect(folded).toBe(insight.severity !== "act_soon" && i > 0);
    });
    expect(screen.getAllByRole("button", { name: "Show details" }).length).toBe(
      HEALTH.insights.filter((x) => x.severity === "watch").length,
    );
  });

  it("shows the first card in full even when it is only a watch", () => {
    const watchFirst = { ...HEALTH, insights: HEALTH.insights.filter((i) => i.severity === "watch") } as AnswerEnvelope;
    list(watchFirst);
    const cards = screen.getAllByRole("article");
    expect(cards[0]).not.toHaveAttribute("data-compact");
    expect(cards[1]).toHaveAttribute("data-compact");
  });

  it("charts the measured series and leaves a pass/fail check as numbers only", () => {
    list(HEALTH);
    const charts = screen.getAllByRole("img");
    const charted = HEALTH.insights.filter((i) => i.category !== "compliance" && i.severity === "act_soon");
    expect(charts).toHaveLength(charted.length);
  });

  it("puts what the answer left out under Everything else, from the scope line", async () => {
    const user = userEvent.setup();
    list(BATTERY);
    const fold = screen.getByRole("button", { name: /Everything else/ });
    expect(fold).toHaveTextContent("6 with no wear signal");
    expect(fold).toHaveAttribute("aria-expanded", "false");
    await user.click(fold);
    expect(screen.getByText("9 laptops checked · 6 with no wear signal")).toBeInTheDocument();
  });

  it("has no Everything else when the scope line leaves nothing out", () => {
    list({ ...BATTERY, scope_line: { ...BATTERY.scope_line!, ok: 0 } });
    expect(screen.queryByRole("button", { name: /Everything else/ })).not.toBeInTheDocument();
  });
});

describe("an insight answer in the reply", () => {
  function reply(envelope: AnswerEnvelope) {
    const onAsk = vi.fn();
    render(
      <AssistantMessage
        envelope={envelope}
        busy={false}
        onOpenEvidence={() => {}}
        onOpenTrace={() => {}}
        onAsk={onAsk}
        question="Which batteries are approaching end-of-life?"
      />,
    );
    return { onAsk };
  }

  it("leads with the headline and its sentence, shows the cards instead of the table, and offers follow-ups once", () => {
    reply(BATTERY);
    expect(document.querySelector(".msg__lead")).toHaveTextContent(BATTERY.headline);
    expect(screen.getByText(BATTERY.summary)).toHaveClass("msg__support");
    expect(screen.getAllByRole("article").some((a) => a.classList.contains("insight"))).toBe(true);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Details" })).not.toBeInTheDocument();
    // each device's actions sit on its own card, so Next steps does not say them a second time
    expect(screen.getAllByRole("button", { name: /Flag acme-thinkpad-8 for replacement/ })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "What should we do about these?" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Copy/ })).toBeInTheDocument();
  });

  it("asks a card's History through the same path as any chip", async () => {
    const user = userEvent.setup();
    const { onAsk } = reply(BATTERY);
    await user.click(screen.getAllByRole("button", { name: "View history" })[0]!);
    expect(onAsk).toHaveBeenCalledWith("Show the 30-day battery history of acme-thinkpad-8");
  });

  it("copies the findings as text", () => {
    reply(HEALTH);
    expect(screen.getByRole("button", { name: /Copy/ })).toBeInTheDocument();
  });
});
