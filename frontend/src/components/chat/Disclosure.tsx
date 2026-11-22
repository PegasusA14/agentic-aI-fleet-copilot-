import { ChevronRight } from "lucide-react";
import { motion } from "motion/react";
import { useId, type ReactNode } from "react";
import { FOLD_IN, FOLD_OUT, Presence } from "../../lib/motion";

interface DisclosureProps {
  title: string;
  /** Mono count beside the title: "4 records". */
  meta?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
}

/**
 * One row of the answer's disclosures (Evidence, Not checked, How I got this). The button names the row; the panel is
 * always in the page for it to point at, folds its height open and shut with Motion, and is inert while closed.
 */
export function Disclosure({ title, meta, open, onOpenChange, children }: DisclosureProps) {
  const panelId = useId();
  return (
    <div className="disc" data-open={open}>
      <button
        type="button"
        className="disc__toggle"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => onOpenChange(!open)}
      >
        <ChevronRight className="disc__chevron" size={16} strokeWidth={2} aria-hidden="true" />
        <span className="disc__title">{title}</span>
        {meta ? (
          <>
            {" "}
            <span className="disc__meta">{meta}</span>
          </>
        ) : null}
      </button>
      <div className="disc__panel" id={panelId} data-open={open} inert={!open} aria-hidden={!open}>
        <Presence>
          {open ? (
            <motion.div
              key="body"
              className="disc__clip"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1, transition: FOLD_IN }}
              exit={{ height: 0, opacity: 0, transition: FOLD_OUT }}
            >
              <div className="disc__body">{children}</div>
            </motion.div>
          ) : null}
        </Presence>
      </div>
    </div>
  );
}
