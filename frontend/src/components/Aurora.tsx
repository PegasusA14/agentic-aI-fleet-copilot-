interface AuroraProps {
  docked: boolean;
  streaming: boolean;
}

/** The ground. On the first question it compresses into a glow under the composer. */
export function Aurora({ docked, streaming }: AuroraProps) {
  return (
    <div className="aurora" data-docked={docked} data-streaming={streaming} aria-hidden="true">
      <div className="aurora__sky" />
      <div className="aurora__glow" />
    </div>
  );
}
