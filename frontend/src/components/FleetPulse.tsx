import { motion } from "motion/react";
import { usePrefersReducedMotion } from "../lib/motion";
import { Count } from "./Count";

/** A fleet of up to 20 sits in two rows; a larger one in rows of 10. */
const columnsFor = (size: number) => (size <= 20 ? Math.ceil(size / 2) : 10);
/** The whole fade-in, first dot to last, stays under this many seconds. */
const ENTRY_SPAN = 0.36;
const ENTRY_FADE = 0.22;
const BREATH_MS = 2400;

/** A stable, spread-out place on the grid for a device id (FNV-1a), probing on to the next free cell. */
function slotsFor(ids: string[], size: number): Set<number> {
  const taken = new Set<number>();
  for (const id of ids) {
    if (taken.size >= size) break;
    let hash = 2166136261;
    for (let i = 0; i < id.length; i++) hash = Math.imul(hash ^ id.charCodeAt(i), 16777619) >>> 0;
    let slot = hash % size;
    while (taken.has(slot)) slot = (slot + 1) % size;
    taken.add(slot);
  }
  return taken;
}

interface FleetPulseProps {
  /** Devices in the signed-in tenant's fleet; the pulse is not drawn when this is unknown. */
  size: number | null;
  /** Device ids with a current finding, when the client already holds them. Empty keeps every dot neutral. */
  flagged?: string[];
}

/**
 * One dot per device. Dots with a finding are lit in the accent colour and breathe; the rest stay neutral.
 * Nothing is drawn for an unknown fleet size: no placeholder, no invented number.
 */
export function FleetPulse({ size, flagged = [] }: FleetPulseProps) {
  const reduce = usePrefersReducedMotion();
  if (!size || size < 1) return null;
  const lit = slotsFor(flagged, size);
  const columns = columnsFor(size);
  const step = Math.min(0.03, ENTRY_SPAN / size);
  const label =
    `${size} ${size === 1 ? "device" : "devices"} in your fleet` +
    (lit.size > 0 ? `, ${lit.size} with a low disk, high memory or battery wear finding` : "");
  return (
    <div className="pulse">
      <div
        className="pulse__grid"
        role="img"
        aria-label={label}
        style={{ gridTemplateColumns: `repeat(${columns}, var(--pulse-dot))` }}
      >
        {Array.from({ length: size }, (_, i) => (
          <motion.span
            key={i}
            className="pulse__dot"
            data-lit={lit.has(i) || undefined}
            initial={reduce ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: ENTRY_FADE, delay: i * step, ease: "easeOut" }}
          >
            <span
              className="pulse__core"
              style={lit.has(i) && !reduce ? { animationDelay: `${(i * 330) % BREATH_MS}ms` } : undefined}
            />
          </motion.span>
        ))}
      </div>
      <p className="pulse__label" aria-hidden="true">
        <Count value={size} countUp /> {size === 1 ? "device" : "devices"}
        {lit.size > 0 ? (
          <>
            {" · "}
            <Count value={lit.size} countUp /> flagged
          </>
        ) : null}
      </p>
    </div>
  );
}
