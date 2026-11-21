import type { ConversationSummary } from "./transport";

export interface ConversationGroup {
  label: "Today" | "Yesterday" | "Earlier";
  items: ConversationSummary[];
}

/**
 * Sorts the history list into Today / Yesterday / Earlier by the browser's calendar day. This is wall-clock
 * history (when the conversation last moved), not the dataset's as-of time. The given order is kept inside a
 * group, empty groups are left out, a time in the future (clock skew) counts as today and an unreadable one as earlier.
 */
export function groupConversations(items: ConversationSummary[], now: Date = new Date()): ConversationGroup[] {
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const startYesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1).getTime();
  const groups: ConversationGroup[] = [
    { label: "Today", items: [] },
    { label: "Yesterday", items: [] },
    { label: "Earlier", items: [] },
  ];
  for (const item of items) {
    const when = Date.parse(item.updated_at);
    const at = Number.isNaN(when) ? 2 : when >= startToday ? 0 : when >= startYesterday ? 1 : 2;
    groups[at].items.push(item);
  }
  return groups.filter((group) => group.items.length > 0);
}
