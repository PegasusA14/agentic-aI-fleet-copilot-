import NumberFlow, { useIsSupported } from "@number-flow/react";
import { useEffect, useState, type ReactNode } from "react";
import { EASE_OUT, usePrefersReducedMotion } from "../lib/motion";

const ROLL = { duration: 420, easing: `cubic-bezier(${EASE_OUT.join(",")})` };
const FADE = { duration: 240, easing: "ease-out" };

interface CountProps {
  value: number;
  /** Roll up from zero when the number first appears. Only for a number that has just arrived, never a reopened one. */
  countUp?: boolean;
}

/** A whole number that rolls when it changes. Under reduced motion it never animates. */
export function Count({ value, countUp = false }: CountProps) {
  const reduce = usePrefersReducedMotion();
  const supported = useIsSupported();
  const [shown, setShown] = useState(countUp && !reduce ? 0 : value);
  useEffect(() => setShown(value), [value]);
  // Where the browser cannot run the roll (no custom elements or adopted style sheets) the number is plain text.
  if (!supported) return <span className="count">{shown.toLocaleString("en-US")}</span>;
  return (
    <NumberFlow
      value={shown}
      animated={!reduce}
      respectMotionPreference
      transformTiming={ROLL}
      spinTiming={ROLL}
      opacityTiming={FADE}
    />
  );
}

/**
 * A sentence with ticking numbers in it. The visible pieces are hidden from assistive technology and the whole
 * sentence is read once, as plain text, so a screen reader never meets the individual digits.
 */
export function CountedText({ text, children }: { text: string; children: ReactNode }) {
  return (
    <>
      <span className="sr-only">{text}</span>
      <span aria-hidden="true">{children}</span>
    </>
  );
}

/** passed/total, read as one phrase. */
export function Tally({ passed, total }: { passed: number; total: number }) {
  return (
    <CountedText text={`${passed}/${total}`}>
      <Count value={passed} />/<Count value={total} />
    </CountedText>
  );
}
