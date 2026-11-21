import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ConversationSummary } from "../lib/transport";
import { Sidebar } from "./Sidebar";

const NOW = new Date(2026, 10, 21, 15, 30);
const at = (days: number) => new Date(2026, 10, 21 - days, 9, 0).toISOString();

const LIST: ConversationSummary[] = [
  { conversation_id: "c3", title: "Which devices are low on disk space?", updated_at: at(0), turns: 2 },
  { conversation_id: "c2", title: "Laptops with battery wear", updated_at: at(1), turns: 1 },
  { conversation_id: "c1", title: "Outdated macOS", updated_at: at(9), turns: 3 },
];

function setup(props: Partial<React.ComponentProps<typeof Sidebar>> = {}) {
  const handlers = {
    onNew: vi.fn(),
    onOpen: vi.fn(),
    onDelete: vi.fn(async () => {}),
    onRetry: vi.fn(),
    onDrawerChange: vi.fn(),
  };
  const view = render(
    <Sidebar
      conversations={LIST}
      activeId={null}
      pending={null}
      view="chat"
      now={NOW}
      narrow={false}
      drawerOpen={false}
      {...handlers}
      {...props}
    />,
  );
  return { ...handlers, ...view, user: userEvent.setup() };
}

describe("the history sidebar", () => {
  it("groups conversations under Today, Yesterday and Earlier", () => {
    setup();
    const nav = screen.getByRole("navigation", { name: "Conversation history" });
    const headings = within(nav).getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual(["Today", "Yesterday", "Earlier"]);
    const today = within(nav).getByRole("list", { name: "Today" });
    expect(within(today).getByRole("button", { name: "Which devices are low on disk space?" })).toBeInTheDocument();
    expect(within(within(nav).getByRole("list", { name: "Earlier" })).getByText("Outdated macOS")).toBeInTheDocument();
  });

  it("marks the open conversation with aria-current, and only on the chat view", () => {
    const { rerender, ...rest } = setup({ activeId: "c2" });
    expect(screen.getByRole("button", { name: "Laptops with battery wear" })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: "Outdated macOS" })).not.toHaveAttribute("aria-current");
    rerender(
      <Sidebar
        conversations={LIST}
        activeId="c2"
        pending={null}
        view="audit"
        now={NOW}
        narrow={false}
        drawerOpen={false}
        onNew={rest.onNew}
        onOpen={rest.onOpen}
        onDelete={rest.onDelete}
        onRetry={rest.onRetry}
        onDrawerChange={rest.onDrawerChange}
      />,
    );
    expect(screen.getByRole("button", { name: "Laptops with battery wear" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "Audit" })).toHaveAttribute("aria-current", "page");
  });

  it("starts a new chat and opens a conversation by its id", async () => {
    const { user, onNew, onOpen } = setup();
    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(onNew).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Outdated macOS" }));
    expect(onOpen).toHaveBeenCalledWith("c1");
  });

  it("asks before deleting: cancel keeps it and returns focus, confirm deletes it", async () => {
    const { user, onDelete } = setup();
    const trigger = screen.getByRole("button", { name: "Delete conversation: Outdated macOS" });
    await user.click(trigger);
    const confirm = screen.getByRole("group", { name: "Delete Outdated macOS?" });
    expect(within(confirm).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Delete conversation: Outdated macOS" })).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "Delete conversation: Outdated macOS" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(onDelete).toHaveBeenCalledWith("c1");
  });

  it("cancels the confirmation on Escape", async () => {
    const { user, onDelete } = setup();
    await user.click(screen.getByRole("button", { name: "Delete conversation: Outdated macOS" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("group", { name: /Delete Outdated macOS/ })).not.toBeInTheDocument();
    expect(onDelete).not.toHaveBeenCalled();
  });

  it("says so when a delete fails, and keeps the conversation", async () => {
    const { user } = setup({ onDelete: vi.fn(async () => Promise.reject(new Error("nope"))) });
    await user.click(screen.getByRole("button", { name: "Delete conversation: Outdated macOS" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not delete that conversation. Try again.");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Outdated macOS" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("links to Approvals with the pending count, and to Audit", () => {
    setup({ pending: 3, view: "approvals" });
    const approvals = screen.getByRole("link", { name: "Approvals, 3 pending" });
    expect(approvals).toHaveAttribute("href", "#/approvals");
    expect(approvals).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Audit" })).toHaveAttribute("href", "#/audit");
  });

  it("names approvals plainly when none are pending", () => {
    setup({ pending: 0 });
    expect(screen.getByRole("link", { name: "Approvals" })).toBeInTheDocument();
  });

  it("explains an empty history, a loading one and a failed one", async () => {
    const empty = setup({ conversations: [] });
    expect(screen.getByText("Your conversations will appear here.")).toBeInTheDocument();
    empty.unmount();
    const loading = setup({ conversations: null });
    expect(screen.getByRole("status")).toHaveTextContent("Loading conversations");
    loading.unmount();
    const failed = setup({ conversations: null, loadFailed: true });
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load your conversations.");
    await failed.user.click(screen.getByRole("button", { name: "Try again" }));
    expect(failed.onRetry).toHaveBeenCalled();
  });
});

describe("the sidebar as a drawer on small screens", () => {
  it("renders nothing inline until the menu opens it", () => {
    setup({ narrow: true, drawerOpen: false });
    expect(screen.queryByRole("navigation", { name: "Conversation history" })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens as a dialog that traps focus, and Escape asks to close it", async () => {
    const { user, onDrawerChange } = setup({ narrow: true, drawerOpen: true });
    const dialog = await screen.findByRole("dialog", { name: "Menu" });
    expect(within(dialog).getByRole("navigation", { name: "Conversation history" })).toBeInTheDocument();
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    for (let i = 0; i < 25; i += 1) {
      await user.tab();
      expect(dialog).toContainElement(document.activeElement as HTMLElement);
    }
    await user.keyboard("{Escape}");
    expect(onDrawerChange).toHaveBeenCalledWith(false);
  });

  it("closes the drawer when a conversation is opened, a chat started or a link followed", async () => {
    const { user, onDrawerChange, onOpen, onNew } = setup({ narrow: true, drawerOpen: true });
    await user.click(await screen.findByRole("button", { name: "Outdated macOS" }));
    expect(onOpen).toHaveBeenCalledWith("c1");
    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(onNew).toHaveBeenCalled();
    await user.click(screen.getByRole("link", { name: "Audit" }));
    expect(onDrawerChange).toHaveBeenCalledTimes(3);
    expect(onDrawerChange).toHaveBeenLastCalledWith(false);
  });

  it("has a close button", async () => {
    const { user, onDrawerChange } = setup({ narrow: true, drawerOpen: true });
    await user.click(await screen.findByRole("button", { name: "Close menu" }));
    expect(onDrawerChange).toHaveBeenCalledWith(false);
  });
});
