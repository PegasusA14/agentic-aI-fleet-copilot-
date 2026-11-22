import { Bookmark, X } from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { ApiError } from "../lib/api";
import type { RememberedNote, Transport } from "../lib/transport";
import { focusComposer } from "./chat/SuggestionChips";

interface NotesHintProps {
  transport: Pick<Transport, "notes" | "forgetNote">;
  conversationId: string;
  /** How many notes the latest reply said are held. The control is hidden at zero. */
  count: number;
  onSessionEnded?: () => void;
}

const LOAD_FAILED = "The notes could not be loaded.";
const FORGET_FAILED = "That note could not be removed. Try again.";

const noteWords = (n: number) => `${n} ${n === 1 ? "note" : "notes"}`;

/**
 * "Remembered · N notes": what the person asked the copilot to keep in this conversation, one small control above the
 * composer. It opens a list from its own corner, and each note can be removed after one inline confirmation.
 */
export function NotesHint({ transport, conversationId, count, onSessionEnded }: NotesHintProps) {
  const [open, setOpen] = useState(false);
  const [notes, setNotes] = useState<RememberedNote[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  /** The note whose removal is waiting for a yes. */
  const [asking, setAsking] = useState<number | null>(null);
  const [working, setWorking] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const keep = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const shown = notes ? notes.length : count;

  function fail(error: unknown, words: string) {
    if (error instanceof ApiError && error.status === 401) onSessionEnded?.();
    setProblem(words);
  }

  async function load() {
    try {
      setNotes(await transport.notes(conversationId));
      setProblem(null);
    } catch (error) {
      fail(error, LOAD_FAILED);
    }
  }

  useEffect(() => {
    if (!open) return;
    void load();
    panel.current?.focus({ preventScroll: true });
    const away = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
    // The list is read once each time the popover opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (asking !== null) keep.current?.focus();
  }, [asking]);

  function close() {
    setOpen(false);
    setAsking(null);
    setProblem(null);
    trigger.current?.focus();
  }

  function onKeyDown(event: KeyboardEvent) {
    if (event.key !== "Escape") return;
    event.preventDefault();
    event.stopPropagation();
    if (asking !== null) setAsking(null);
    else close();
  }

  async function forget(index: number) {
    setWorking(true);
    try {
      await transport.forgetNote(conversationId, index);
      const left = await transport.notes(conversationId);
      setNotes(left);
      setAsking(null);
      setProblem(null);
      if (left.length === 0) {
        setOpen(false);
        focusComposer();
      }
    } catch (error) {
      fail(error, FORGET_FAILED);
    } finally {
      setWorking(false);
    }
  }

  if (shown === 0) return null;

  return (
    <div className="notes" ref={root}>
      <button
        ref={trigger}
        type="button"
        className="notes__trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <Bookmark size={14} strokeWidth={1.75} aria-hidden="true" />
        Remembered · {noteWords(shown)}
      </button>
      {open ? (
        <div
          className="notes__panel"
          id={panelId}
          ref={panel}
          role="dialog"
          aria-label="Remembered notes"
          tabIndex={-1}
          onKeyDown={onKeyDown}
        >
          <p className="notes__label">Remembered in this chat</p>
          {notes === null && !problem ? <p className="notes__quiet">Loading notes…</p> : null}
          {notes ? (
            <ul className="notes__list">
              {notes.map((note) => (
                <li key={note.index} className="notes__item" data-asking={asking === note.index || undefined}>
                  <span className="notes__text">{note.text}</span>
                  {asking === note.index ? (
                    <span className="notes__confirm" role="group" aria-label={`Remove this note: ${note.text}`}>
                      <button
                        type="button"
                        className="notes__btn notes__btn--danger"
                        disabled={working}
                        onClick={() => void forget(note.index)}
                      >
                        Remove
                      </button>
                      <button ref={keep} type="button" className="notes__btn" disabled={working} onClick={() => setAsking(null)}>
                        Keep
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      className="notes__remove"
                      aria-label={`Remove note: ${note.text}`}
                      onClick={() => {
                        setProblem(null);
                        setAsking(note.index);
                      }}
                    >
                      <X size={15} strokeWidth={1.75} aria-hidden="true" />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          ) : null}
          {problem ? (
            <p className="notes__problem" role="alert">
              {problem}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
