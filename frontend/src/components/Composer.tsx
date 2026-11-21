import { ArrowUp, Lightbulb } from "lucide-react";
import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";
import { SUGGESTIONS } from "../lib/suggestions";
import { useMediaQuery } from "../lib/useMediaQuery";

interface ComposerProps {
  docked: boolean;
  busy: boolean;
  onAsk: (question: string) => void;
  /** The server's words when it refused the question; shown under the box. */
  error?: string | null;
  /** Put a refused question back in the box so it can be edited. Pass a new object each time. */
  restore?: { text: string } | null;
  /** Called when the person edits the text, so a stale error can be cleared. */
  onEdit?: () => void;
}

export function Composer({ docked, busy, onAsk, error = null, restore = null, onEdit }: ComposerProps) {
  const [value, setValue] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const menuId = useId();
  const errorId = useId();
  const canSend = value.trim().length > 0 && !busy;
  /** Docked on a phone the one-row box has no room for the full prompt. */
  const tight = useMediaQuery("(max-width: 640px)");
  const placeholder = !docked ? "Ask about your fleet…" : tight ? "Ask a follow-up…" : "Ask a follow-up, or ask what to do…";

  useEffect(() => {
    const el = input.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [value]);

  useEffect(() => {
    if (!restore) return;
    setValue(restore.text);
    input.current?.focus();
  }, [restore]);

  function submit() {
    if (!canSend) return;
    onAsk(value.trim());
    setValue("");
    setMenuOpen(false);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
    if (event.key === "Escape") setMenuOpen(false);
  }

  return (
    <>
      <form
        className="composer"
        data-docked={docked}
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <label className="sr-only" htmlFor="ask-input">
          Ask a question about your fleet
        </label>
        <textarea
          id="ask-input"
          ref={input}
          className="composer__input"
          rows={1}
          placeholder={placeholder}
          value={value}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          onChange={(event) => {
            setValue(event.target.value);
            onEdit?.();
          }}
          onKeyDown={onKeyDown}
        />
        <div className="composer__row">
          <div className="composer__left">
            <button
              type="button"
              className="icon-btn"
              aria-label="Suggested questions"
              title="Suggested questions"
              aria-haspopup="true"
              aria-expanded={menuOpen}
              aria-controls={menuOpen ? menuId : undefined}
              onClick={() => setMenuOpen((open) => !open)}
            >
              <Lightbulb size={18} aria-hidden="true" />
            </button>
          </div>
          <button type="submit" className="send" disabled={!canSend} aria-label="Send question">
            <ArrowUp size={20} strokeWidth={2.6} aria-hidden="true" />
          </button>
        </div>
        {menuOpen ? (
          <ul className="menu" id={menuId} aria-label="Suggested questions">
            <li className="menu__title" aria-hidden="true">
              Try asking
            </li>
            {SUGGESTIONS.map((question) => (
              <li key={question}>
                <button
                  type="button"
                  onClick={() => {
                    setValue(question);
                    setMenuOpen(false);
                    input.current?.focus();
                  }}
                >
                  {question}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </form>
      {error ? (
        <p className="form-error" role="alert" id={errorId}>
          {error}
        </p>
      ) : null}
    </>
  );
}
