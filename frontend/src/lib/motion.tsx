import { AnimatePresence } from "motion/react";
import type { ReactNode } from "react";
import { useMediaQuery } from "./useMediaQuery";

/** The one arrival curve: fast start, long settle. */
export const EASE_OUT = [0.16, 1, 0.3, 1] as const;
/** A new turn arrives: opacity and a 6px rise. */
export const ENTER = { duration: 0.18, ease: EASE_OUT };
/** Rows and chips glide to their new place. */
export const GLIDE = { duration: 0.16, ease: EASE_OUT };
/** A working line gives way to the answer. */
export const CROSSFADE = { duration: 0.16, ease: EASE_OUT };
/** Details folds and the drawer slides: in at 200 ms, out a little faster. */
export const FOLD_IN = { duration: 0.2, ease: EASE_OUT };
export const FOLD_OUT = { duration: 0.16, ease: EASE_OUT };

/**
 * True when the person asked for less motion. Read from the media query itself so that every motion decision in the
 * app (and its tests) agrees; where this is true nothing moves and nothing waits on an animation.
 */
export function usePrefersReducedMotion(): boolean {
  return useMediaQuery("(prefers-reduced-motion: reduce)");
}

interface PresenceProps {
  children: ReactNode;
  mode?: "sync" | "wait" | "popLayout";
}

/**
 * `AnimatePresence` that steps aside under reduced motion: what leaves is gone at once, so nothing waits on an exit.
 * Children that are present on first render do not animate in.
 */
export function Presence({ children, mode }: PresenceProps) {
  const reduce = usePrefersReducedMotion();
  if (reduce) return <>{children}</>;
  return (
    <AnimatePresence initial={false} mode={mode}>
      {children}
    </AnimatePresence>
  );
}
