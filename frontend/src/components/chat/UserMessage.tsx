/** What the admin asked, right-aligned in a quiet filled bubble. Always plain text. */
export function UserMessage({ text }: { text: string }) {
  return (
    <div className="msg msg--user">
      <p className="msg__bubble">{text}</p>
    </div>
  );
}
