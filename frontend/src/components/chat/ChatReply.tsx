interface ReplyTextProps {
  /** The reply's paragraphs (see `paragraphsOf`). Nothing renders when there are none. */
  paragraphs: string[];
  /** The reply is the whole message (small talk), so it sets at the lead's size. */
  alone?: boolean;
  /** Where the words sit when they open a card: the refusal card sets them without the page's gap above. */
  inCard?: boolean;
}

/**
 * The model's words, as plain text: one paragraph per blank line. React escapes every string it renders, and nothing
 * here parses markdown or sets HTML, so a reply that carries markup or script shows as the literal characters.
 */
export function ReplyText({ paragraphs, alone = false, inCard = false }: ReplyTextProps) {
  if (paragraphs.length === 0) return null;
  return (
    <div className="reply" data-alone={alone || undefined} data-in-card={inCard || undefined}>
      {paragraphs.map((text, index) => (
        <p key={`${index}:${text}`} className="reply__p">
          {text}
        </p>
      ))}
    </div>
  );
}
