import { motion } from "motion/react";
import { ENTER, usePrefersReducedMotion } from "../../lib/motion";

/** What the admin asked, right-aligned in a quiet filled bubble. Always plain text. A question asked now arrives; a reopened one is there. */
export function UserMessage({ text, fresh = false }: { text: string; fresh?: boolean }) {
  const reduce = usePrefersReducedMotion();
  return (
    <motion.div
      className="msg msg--user"
      initial={fresh && !reduce ? { opacity: 0, y: 6 } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={ENTER}
    >
      <p className="msg__bubble">{text}</p>
    </motion.div>
  );
}
