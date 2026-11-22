import { Check, Shield } from "lucide-react";
import { motion } from "motion/react";
import { useId, useState, type ReactNode } from "react";
import { GLIDE, usePrefersReducedMotion } from "../../lib/motion";
import type { Alternative, SuggestedAction } from "../../types/AnswerEnvelope";

/**
 * After a pick the composer takes focus, so a disabled chip does not drop focus onto the page. Only where a keyboard is
 * already physical: on a touch screen, focusing the input would raise the on-screen keyboard over the answer.
 */
export function finePointer(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(hover: hover) and (pointer: fine)").matches;
}

export function focusComposer() {
  if (!finePointer()) return;
  document.getElementById("ask-input")?.focus();
}

interface SuggestionChipsProps {
  actions: SuggestedAction[];
  /** A turn is running: chips wait, like the composer's send button. */
  busy: boolean;
  /** Sends the suggestion's own request as the next user message. */
  onAsk: (request: string) => void;
  /** Say why under each action. Left off when the reply's own explanation already does. */
  withReason?: boolean;
  /** Under a list answer's Next steps: the first chip leads in green. */
  compact?: boolean;
}

const keyOf = (action: SuggestedAction) => `${action.action_type}:${action.device_id}:${action.label}`;

/** The chip label, with the hostname kept whole: a name that wraps at its hyphen reads as two. */
function LabelText({ action }: { action: SuggestedAction }) {
  const at = action.label.toLowerCase().indexOf(action.target_label.toLowerCase());
  if (at < 0 || !action.target_label) return <span>{action.label}</span>;
  const end = at + action.target_label.length;
  return (
    <span>
      {action.label.slice(0, at)}
      <span className="nowrap">{action.label.slice(at, end)}</span>
      {action.label.slice(end)}
    </span>
  );
}

/** Action chips shown before the rest fold behind "+N more". Nothing is dropped: every approvable action stays one press away. */
export const CHIP_LIMIT = 3;

/** Every suggested action is advice that becomes a proposal, and a proposal waits for a person: each chip says so. */
export function SuggestionChips({ actions, busy, onAsk, withReason = false, compact = false }: SuggestionChipsProps) {
  /** The suggestions already asked for, so a person can see where they have been. They stay reachable. */
  const reduce = usePrefersReducedMotion();
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
  const [expanded, setExpanded] = useState(false);
  const moreId = useId();
  if (actions.length === 0) return null;
  const folded = actions.length > CHIP_LIMIT;
  const visible = folded && !expanded ? actions.slice(0, CHIP_LIMIT) : actions;
  return (
    <div className="chips" role="group" aria-label="Suggested actions" id={moreId}>
      {visible.map((action, index) => {
        const used = picked.has(keyOf(action));
        const named = action.label.toLowerCase().includes(action.target_label.toLowerCase());
        return (
          <motion.button
            key={keyOf(action)}
            // The chips glide when one is added, removed or grows a tick; a press scales them (the transform is Motion's).
            layout={reduce ? false : "position"}
            transition={GLIDE}
            whileTap={reduce ? undefined : { scale: 0.98 }}
            type="button"
            className="chip chip--action"
            data-primary={(compact && index === 0) || undefined}
            data-compact={compact || undefined}
            disabled={busy}
            data-asked={used || undefined}
            title={action.reason}
            onClick={() => {
              setPicked((now) => new Set(now).add(keyOf(action)));
              onAsk(action.request);
              focusComposer();
            }}
          >
            <span className="chip__main">
              <span className="chip__label">
                {used ? <Check size={14} strokeWidth={2.6} aria-hidden="true" /> : null}
                <LabelText action={action} />
                {used ? <span className="sr-only"> (asked)</span> : null}
              </span>
              <span className="chip__approval">
                <Shield size={13} strokeWidth={1.75} aria-hidden="true" />
                Needs your approval
              </span>
            </span>
            {named ? null : <span className="chip__device">{action.target_label}</span>}
            {withReason ? <span className="chip__reason">{action.reason}</span> : null}
          </motion.button>
        );
      })}
      {folded ? (
        <motion.button
          layout={reduce ? false : "position"}
          transition={GLIDE}
          type="button"
          className="chip chip--more"
          aria-expanded={expanded}
          aria-controls={moreId}
          onClick={() => setExpanded((now) => !now)}
        >
          {expanded ? "Show fewer" : `+${actions.length - CHIP_LIMIT} more`}
        </motion.button>
      ) : null}
    </div>
  );
}

interface AlternativeChipsProps {
  alternatives: Alternative[];
  busy: boolean;
  /** Sends the alternative's own request as the next user message. */
  onAsk: (request: string) => void;
  /** Names the group for assistive technology. */
  label?: string;
  /** More chips for the same group, after the alternatives (the refusal card's "Switch company"). */
  extra?: ReactNode;
}

/**
 * A group of chips that each send a request of their own. A chip already pressed is marked asked: a check, and
 * "(asked)" for a screen reader. It stays pressable, so there is no aria-pressed.
 */
export function RequestChips({ alternatives, busy, onAsk, label, extra, strong }: AlternativeChipsProps & { strong?: (a: Alternative) => boolean }) {
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
  if (alternatives.length === 0 && !extra) return null;
  return (
    <div className="chips" role="group" aria-label={label}>
      {alternatives.map((alternative) => {
        const used = picked.has(alternative.label);
        return (
          <button
            key={alternative.label}
            type="button"
            className="chip"
            disabled={busy}
            data-asked={used || undefined}
            data-strong={strong?.(alternative) || undefined}
            title={alternative.request}
            onClick={() => {
              setPicked((now) => new Set(now).add(alternative.label));
              onAsk(alternative.request);
              focusComposer();
            }}
          >
            {used ? <Check size={14} strokeWidth={2.6} aria-hidden="true" className="chip__check" /> : null}
            {alternative.label}
            {used ? <span className="sr-only"> (asked)</span> : null}
          </button>
        );
      })}
      {extra}
    </div>
  );
}

/** What to ask instead, or next. The questions are code-written; a chip sends its request as written. */
export function AlternativeChips({ alternatives, busy, onAsk, label = "You could ask", extra }: AlternativeChipsProps) {
  return <RequestChips alternatives={alternatives} busy={busy} onAsk={onAsk} label={label} extra={extra} />;
}

interface NextStepsProps {
  actions: SuggestedAction[];
  followUps: Alternative[];
  busy: boolean;
  onAsk: (request: string) => void;
  withReason?: boolean;
  compact?: boolean;
}

/** The answer's way forward: actions that need approval first, then plain questions to ask next. */
export function NextSteps({ actions, followUps, busy, onAsk, withReason, compact }: NextStepsProps) {
  if (actions.length === 0 && followUps.length === 0) return null;
  return (
    <div className="nextsteps">
      <p className="label">Next steps</p>
      <div className="nextsteps__chips">
        <SuggestionChips actions={actions} busy={busy} onAsk={onAsk} withReason={withReason} compact={compact} />
        <AlternativeChips alternatives={followUps} busy={busy} onAsk={onAsk} label="Follow-up questions" />
      </div>
    </div>
  );
}
