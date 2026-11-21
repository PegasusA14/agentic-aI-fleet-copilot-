const ROWS = 3;
const COLS = 8;

/** Which dots a still matrix lights: a diagonal band, so a person who asked for no motion still sees "working". */
const lit = (row: number, col: number) => (col + row) % 4 === 0;

/**
 * The 3 by 8 working loader: a grid of small squares that light up in the accent colour in a sweep, row by row
 * behind the column. Decorative (the words beside it say what is happening). `still` freezes it.
 */
export function DotMatrix({ still = false }: { still?: boolean }) {
  return (
    <span className="dotmatrix" data-still={still || undefined} aria-hidden="true">
      {Array.from({ length: ROWS * COLS }, (_, i) => {
        const row = Math.floor(i / COLS);
        const col = i % COLS;
        return (
          <span
            key={i}
            className="dotmatrix__dot"
            data-lit={still && lit(row, col) ? "" : undefined}
            style={still ? undefined : { animationDelay: `${col * 90 + row * 140}ms` }}
          />
        );
      })}
    </span>
  );
}
