import * as Dialog from "@radix-ui/react-dialog";
import { ClipboardCheck, Plus, ScrollText, Trash2, X } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { groupConversations } from "../lib/conversations";
import { GLIDE, usePrefersReducedMotion } from "../lib/motion";
import { Count } from "./Count";
import type { ConversationSummary } from "../lib/transport";

export type SidebarView = "chat" | "approvals" | "audit";

interface SidebarProps {
  /** The history list, newest first; null until it has loaded. */
  conversations: ConversationSummary[] | null;
  loadFailed?: boolean;
  /** The conversation the thread is continuing, if it has been answered at least once. */
  activeId: string | null;
  /** Proposals waiting for a decision; null until the first count arrives. */
  pending: number | null;
  view: SidebarView;
  /** Under 960px the sidebar is a drawer that the top bar's menu button opens. */
  narrow: boolean;
  drawerOpen: boolean;
  onDrawerChange: (open: boolean) => void;
  onNew: () => void;
  onOpen: (id: string) => void;
  /** Rejects when the conversation could not be deleted; the row then says so. */
  onDelete: (id: string) => Promise<void>;
  onRetry: () => void;
  /** The browser clock the Today / Yesterday split is measured against. */
  now?: Date;
}

interface RowProps {
  item: ConversationSummary;
  active: boolean;
  confirming: boolean;
  busy: boolean;
  failed: boolean;
  onOpen: () => void;
  onAskDelete: () => void;
  onCancel: () => void;
  onConfirm: () => void;
}

function Row({ item, active, confirming, busy, failed, onOpen, onAskDelete, onCancel, onConfirm }: RowProps) {
  const reduce = usePrefersReducedMotion();
  /** Rows glide to their new place when one above is added or removed; the first render does not animate. */
  const glide = { layout: reduce ? (false as const) : ("position" as const), transition: GLIDE };
  if (confirming) {
    return (
      <motion.li className="convo convo--confirm" {...glide}>
        <div
          role="group"
          aria-label={`Delete ${item.title}?`}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.stopPropagation();
              onCancel();
            }
          }}
        >
          <p className="convo__ask">Delete this conversation?</p>
          <div className="convo__choices">
            <button type="button" className="convo__btn" autoFocus onClick={onCancel} disabled={busy}>
              Cancel
            </button>
            <button type="button" className="convo__btn convo__btn--danger" onClick={onConfirm} disabled={busy}>
              Delete
            </button>
          </div>
          {failed ? (
            <p className="convo__error" role="alert">
              Could not delete that conversation. Try again.
            </p>
          ) : null}
        </div>
      </motion.li>
    );
  }
  return (
    <motion.li className="convo" data-active={active} {...glide}>
      <button type="button" className="convo__open" aria-current={active ? "true" : undefined} onClick={onOpen}>
        <span className="convo__title">{item.title}</span>
      </button>
      <button
        type="button"
        className="convo__delete"
        data-delete-for={item.conversation_id}
        aria-label={`Delete conversation: ${item.title}`}
        onClick={onAskDelete}
      >
        <Trash2 size={15} aria-hidden="true" />
      </button>
    </motion.li>
  );
}

export function Sidebar(props: SidebarProps) {
  const { narrow, drawerOpen, onDrawerChange } = props;
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  /** Where focus goes once the confirmation is gone: the row's delete button, or New chat after a delete. */
  const [refocus, setRefocus] = useState<string | "new" | null>(null);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!refocus) return;
    const target =
      refocus === "new"
        ? root.current?.querySelector<HTMLElement>("[data-new-chat]")
        : root.current?.querySelector<HTMLElement>(`[data-delete-for="${CSS.escape(refocus)}"]`);
    target?.focus();
    setRefocus(null);
  }, [refocus, confirmId]);

  function cancelConfirm(restore: boolean) {
    if (restore && confirmId) setRefocus(confirmId);
    setConfirmId(null);
    setFailed(false);
  }

  async function confirmDelete(id: string) {
    setBusy(true);
    setFailed(false);
    try {
      await props.onDelete(id);
      setConfirmId(null);
      setRefocus("new");
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  /** Choosing something in the drawer is the end of the drawer's job. */
  function chose(action: () => void) {
    action();
    if (narrow) onDrawerChange(false);
  }

  const groups = props.conversations ? groupConversations(props.conversations, props.now) : [];
  const approvalsName = props.pending ? `Approvals, ${props.pending} pending` : "Approvals";

  const body = (
    <nav className="sidebar" aria-label="Conversation history">
      <button type="button" className="sidebar__new" data-new-chat onClick={() => chose(props.onNew)}>
        <Plus size={16} aria-hidden="true" />
        New chat
      </button>

      {/* A scroll container: told to Motion so rows gliding inside it are measured against its scroll. */}
      <motion.div className="sidebar__history" layoutScroll>
        {props.conversations === null ? (
          props.loadFailed ? (
            <div className="sidebar__note">
              <p role="alert">Could not load your conversations.</p>
              <button type="button" className="link-btn" onClick={props.onRetry}>
                Try again
              </button>
            </div>
          ) : (
            <p className="sidebar__note" role="status">
              Loading conversations…
            </p>
          )
        ) : groups.length === 0 ? (
          <p className="sidebar__note">Your conversations will appear here.</p>
        ) : (
          groups.map((group) => (
            <section key={group.label} className="sidebar__group">
              <h2 className="sidebar__label" id={`history-${group.label}`}>
                {group.label}
              </h2>
              <ul className="sidebar__list" aria-labelledby={`history-${group.label}`}>
                {group.items.map((item) => (
                  <Row
                    key={item.conversation_id}
                    item={item}
                    active={props.view === "chat" && props.activeId === item.conversation_id}
                    confirming={confirmId === item.conversation_id}
                    busy={busy}
                    failed={failed && confirmId === item.conversation_id}
                    onOpen={() => chose(() => props.onOpen(item.conversation_id))}
                    onAskDelete={() => {
                      setFailed(false);
                      setConfirmId(item.conversation_id);
                    }}
                    onCancel={() => cancelConfirm(true)}
                    onConfirm={() => void confirmDelete(item.conversation_id)}
                  />
                ))}
              </ul>
            </section>
          ))
        )}
      </motion.div>

      <div className="sidebar__links">
        <a
          className="sidebar__link"
          href="#/approvals"
          aria-current={props.view === "approvals" ? "page" : undefined}
          aria-label={approvalsName}
          onClick={() => narrow && onDrawerChange(false)}
        >
          <ClipboardCheck size={16} aria-hidden="true" />
          <span aria-hidden="true">Approvals</span>
          {props.pending ? (
            <span className="pill__count" aria-hidden="true">
              <Count value={props.pending} />
            </span>
          ) : null}
        </a>
        <a
          className="sidebar__link"
          href="#/audit"
          aria-current={props.view === "audit" ? "page" : undefined}
          onClick={() => narrow && onDrawerChange(false)}
        >
          <ScrollText size={16} aria-hidden="true" />
          Audit
        </a>
      </div>
    </nav>
  );

  if (!narrow) {
    return (
      <div className="sidebar-wrap" ref={root}>
        {body}
      </div>
    );
  }

  return (
    <Dialog.Root open={drawerOpen} onOpenChange={onDrawerChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content
          className="drawer-nav"
          aria-describedby={undefined}
          ref={root as React.Ref<HTMLDivElement>}
          onEscapeKeyDown={(event) => {
            // Escape first backs out of an open "Delete this conversation?" and only then closes the drawer.
            if (confirmId) {
              event.preventDefault();
              cancelConfirm(true);
            }
          }}
        >
          <div className="drawer-nav__head">
            <Dialog.Title className="drawer-nav__title">Menu</Dialog.Title>
            <Dialog.Close className="drawer-nav__close" aria-label="Close menu">
              <X size={18} aria-hidden="true" />
            </Dialog.Close>
          </div>
          {body}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
