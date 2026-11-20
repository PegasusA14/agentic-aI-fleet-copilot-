import { ArrowUp, MessageCircle, Plus } from "lucide-react";
import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";
import { SUGGESTIONS } from "../lib/suggestions";

interface ComposerProps {
  docked: boolean;
  busy: boolean;
  onAsk: (question: string) => void;
}

export function Composer({ docked, busy, onAsk }: ComposerProps) {
  const [value, setValue] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const menuId = useId();
  const canSend = value.trim().length > 0 && !busy;

  useEffect(() => {
    const el = input.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [value]);

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
        placeholder="Ask about your fleet…"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={onKeyDown}
      />
      <div className="composer__row">
        <div className="composer__left">
          <button
            type="button"
            className="icon-btn"
            aria-label="Suggested questions"
            aria-haspopup="true"
            aria-expanded={menuOpen}
            aria-controls={menuOpen ? menuId : undefined}
            onClick={() => setMenuOpen((open) => !open)}
          >
            <Plus size={20} aria-hidden="true" />
          </button>
          <span className="mode">
            <MessageCircle size={15} aria-hidden="true" />
            Ask
          </span>
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
  );
}
