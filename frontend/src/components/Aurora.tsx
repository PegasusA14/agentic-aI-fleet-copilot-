interface AuroraProps {
  docked: boolean;
}

/** The ground: plain warm white, with one faint glow from the top only on the empty home state. */
export function Aurora({ docked }: AuroraProps) {
  return (
    <div className="aurora" data-docked={docked} aria-hidden="true">
      <div className="aurora__sky" />
    </div>
  );
}
