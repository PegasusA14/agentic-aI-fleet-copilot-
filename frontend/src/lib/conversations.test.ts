import { describe, expect, it } from "vitest";
import { groupConversations } from "./conversations";
import type { ConversationSummary } from "./transport";

const NOW = new Date(2026, 10, 21, 15, 30); // 21 Nov 2026, local time

function at(days: number, hour = 9): string {
  return new Date(2026, 10, 21 - days, hour, 0).toISOString();
}

const item = (id: string, updated_at: string): ConversationSummary => ({
  conversation_id: id,
  title: id,
  updated_at,
  turns: 1,
});

describe("groupConversations", () => {
  it("groups by the browser's calendar day: Today, Yesterday, Earlier", () => {
    const groups = groupConversations(
      [item("a", at(0, 14)), item("b", at(0, 0)), item("c", at(1, 23)), item("d", at(2)), item("e", at(40))],
      NOW,
    );
    expect(groups.map((g) => [g.label, g.items.map((i) => i.conversation_id)])).toEqual([
      ["Today", ["a", "b"]],
      ["Yesterday", ["c"]],
      ["Earlier", ["d", "e"]],
    ]);
  });

  it("leaves out empty groups and keeps the given order inside a group", () => {
    const groups = groupConversations([item("x", at(3)), item("y", at(2))], NOW);
    expect(groups.map((g) => g.label)).toEqual(["Earlier"]);
    expect(groups[0].items.map((i) => i.conversation_id)).toEqual(["x", "y"]);
    expect(groupConversations([], NOW)).toEqual([]);
  });

  it("treats a clock-skewed future time as today and an unreadable one as earlier", () => {
    const groups = groupConversations([item("f", at(-1)), item("g", "not a date")], NOW);
    expect(groups.map((g) => [g.label, g.items[0].conversation_id])).toEqual([
      ["Today", "f"],
      ["Earlier", "g"],
    ]);
  });
});
