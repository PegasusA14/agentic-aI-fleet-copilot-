import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fixtureTransport, liveTransport } from "../lib/transport";
import { NotesHint } from "./NotesHint";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const NOTES = [
  { index: 0, text: "I only care about laptops", turn: 1 },
  { index: 1, text: "Ignore the lab devices", turn: 3 },
];

describe("the notes hint on the live transport", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("is hidden with no notes, and says how many there are otherwise", () => {
    const { container, rerender } = render(<NotesHint transport={liveTransport} conversationId="c1" count={0} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<NotesHint transport={liveTransport} conversationId="c1" count={1} />);
    expect(screen.getByRole("button", { name: /Remembered · 1 note$/ })).toBeInTheDocument();
    rerender(<NotesHint transport={liveTransport} conversationId="c1" count={2} />);
    expect(screen.getByRole("button", { name: "Remembered · 2 notes" })).toBeInTheDocument();
  });

  it("opens a list read from the notes endpoint, and Escape closes it and gives focus back", async () => {
    fetchMock.mockResolvedValueOnce(json(NOTES));
    const user = userEvent.setup();
    render(<NotesHint transport={liveTransport} conversationId="c 1" count={2} />);
    const trigger = screen.getByRole("button", { name: "Remembered · 2 notes" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await user.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "Remembered notes" });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(await within(dialog).findByText("I only care about laptops")).toBeInTheDocument();
    expect(within(dialog).getByText("Ignore the lab devices")).toBeInTheDocument();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/conversations/c%201/notes");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("asks before it removes, then deletes by index and shows what is left", async () => {
    fetchMock
      .mockResolvedValueOnce(json(NOTES))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(json([{ index: 0, text: "Ignore the lab devices", turn: 3 }]));
    const user = userEvent.setup();
    render(<NotesHint transport={liveTransport} conversationId="c1" count={2} />);
    await user.click(screen.getByRole("button", { name: "Remembered · 2 notes" }));
    await user.click(await screen.findByRole("button", { name: "Remove note: I only care about laptops" }));
    // Nothing has been sent yet: the row asks first, with Keep in focus.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const keep = screen.getByRole("button", { name: "Keep" });
    expect(keep).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(screen.queryByText("I only care about laptops")).not.toBeInTheDocument());
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/conversations/c1/notes/0");
    expect(init.method).toBe("DELETE");
    expect(screen.getByRole("button", { name: "Remembered · 1 note" })).toBeInTheDocument();
  });

  it("keeps the note when the person says Keep, and Escape backs out of the question first", async () => {
    fetchMock.mockResolvedValueOnce(json(NOTES));
    const user = userEvent.setup();
    render(<NotesHint transport={liveTransport} conversationId="c1" count={2} />);
    await user.click(screen.getByRole("button", { name: "Remembered · 2 notes" }));
    await user.click(await screen.findByRole("button", { name: "Remove note: Ignore the lab devices" }));
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove note: Ignore the lab devices" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove note: I only care about laptops" }));
    await user.click(screen.getByRole("button", { name: "Keep" }));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByText("I only care about laptops")).toBeInTheDocument();
  });

  it("says so inline when the note cannot be removed, and keeps it", async () => {
    fetchMock
      .mockResolvedValueOnce(json(NOTES))
      .mockResolvedValueOnce(json({ detail: "not found" }, 404));
    const user = userEvent.setup();
    render(<NotesHint transport={liveTransport} conversationId="c1" count={2} />);
    await user.click(screen.getByRole("button", { name: "Remembered · 2 notes" }));
    await user.click(await screen.findByRole("button", { name: "Remove note: I only care about laptops" }));
    await user.click(screen.getByRole("button", { name: "Remove" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That note could not be removed. Try again.");
    expect(screen.getByText("I only care about laptops")).toBeInTheDocument();
  });

  it("says so when the list cannot be read", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("offline"));
    const user = userEvent.setup();
    render(<NotesHint transport={liveTransport} conversationId="c1" count={1} />);
    await user.click(screen.getByRole("button", { name: /Remembered/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The notes could not be loaded.");
  });

  it("closes the popover and hides itself when the last note is removed", async () => {
    fetchMock
      .mockResolvedValueOnce(json([NOTES[0]]))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(json([]));
    const user = userEvent.setup();
    const { container } = render(<NotesHint transport={liveTransport} conversationId="c1" count={1} />);
    await user.click(screen.getByRole("button", { name: /Remembered/ }));
    await user.click(await screen.findByRole("button", { name: /Remove note/ }));
    await user.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});

describe("the notes hint on the fixture transport", () => {
  async function conversation() {
    const transport = fixtureTransport(0);
    await transport.signIn("admin-acme");
    return { transport, id: (await transport.newConversation()).conversation_id };
  }

  it("keeps a statement as a note in memory, lists it and forgets it by index", async () => {
    const { transport, id } = await conversation();
    expect(await transport.notes(id)).toEqual([]);
    const envelope = await transport.ask(id, "I only care about laptops", () => {});
    expect(envelope.outcome).toBe("chat");
    expect(envelope.notes_count).toBe(1);
    expect(await transport.notes(id)).toEqual([{ index: 0, text: "I only care about laptops", turn: 1 }]);
    await transport.forgetNote(id, 0);
    expect(await transport.notes(id)).toEqual([]);
    await expect(transport.forgetNote(id, 0)).rejects.toMatchObject({ status: 404 });
  });

  it("keeps notes per conversation and drops the last on 'forget that'", async () => {
    const { transport, id } = await conversation();
    const other = (await transport.newConversation()).conversation_id;
    await transport.ask(id, "I only care about laptops", () => {});
    await transport.ask(id, "Keep in mind the lab is closed", () => {});
    expect((await transport.notes(id)).map((n) => n.text)).toEqual(["I only care about laptops", "Keep in mind the lab is closed"]);
    expect(await transport.notes(other)).toEqual([]);
    const forgotten = await transport.ask(id, "Forget that", () => {});
    expect(forgotten.notes_count).toBe(1);
    expect((await transport.notes(id)).map((n) => n.text)).toEqual(["I only care about laptops"]);
    const later = await transport.ask(id, "Which devices are low on disk space?", () => {});
    expect(later.notes_count).toBe(1);
  });

  it("works with the hint: open, list, remove after one confirmation", async () => {
    const { transport, id } = await conversation();
    await transport.ask(id, "I only care about laptops", () => {});
    const user = userEvent.setup();
    const { container } = render(<NotesHint transport={transport} conversationId={id} count={1} />);
    await user.click(screen.getByRole("button", { name: "Remembered · 1 note" }));
    expect(await screen.findByText("I only care about laptops")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove note: I only care about laptops" }));
    await user.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(container).toBeEmptyDOMElement());
    expect(await transport.notes(id)).toEqual([]);
  });

  it("says nothing was remembered a second time for a repeated thank-you (small talk is never a repeat)", async () => {
    const { transport, id } = await conversation();
    await transport.ask(id, "Thanks!", () => {});
    const again = await transport.ask(id, "Thanks!", () => {});
    expect(again.repeat_of).toBeNull();
    expect(again.reply).not.toBe("");
  });
});
