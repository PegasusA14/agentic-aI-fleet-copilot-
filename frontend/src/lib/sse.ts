export interface SseEvent {
  event: string;
  data: unknown;
}

function parseData(data: string): unknown {
  try {
    return JSON.parse(data);
  } catch {
    return data;
  }
}

/**
 * Parse a text/event-stream body (fetch + POST, so no EventSource) into events.
 *
 * Handles chunk boundaries anywhere (also inside a multi-byte character or between a CR and its LF),
 * `\n`, `\r\n` and `\r` line endings, multi-line `data:` fields and comment lines. `data` is parsed
 * as JSON when it is JSON, else kept as text. An event the stream cut off before its blank line is
 * dropped, as the SSE spec says.
 */
export async function* parseSse(stream: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let event = "";
  let data: string[] = [];

  function* handleLine(line: string): Generator<SseEvent> {
    if (line === "") {
      if (data.length > 0) yield { event: event || "message", data: parseData(data.join("\n")) };
      event = "";
      data = [];
      return;
    }
    if (line.startsWith(":")) return;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        buffer += decoder.decode();
      } else {
        buffer += decoder.decode(value, { stream: true });
      }
      for (;;) {
        const end = buffer.search(/[\r\n]/);
        if (end === -1) break;
        // A trailing CR may be the first half of CRLF: wait for the next chunk unless the stream is over.
        if (buffer[end] === "\r" && end === buffer.length - 1 && !done) break;
        const line = buffer.slice(0, end);
        buffer = buffer.slice(buffer[end] === "\r" && buffer[end + 1] === "\n" ? end + 2 : end + 1);
        yield* handleLine(line);
      }
      if (done) {
        if (buffer.length > 0) yield* handleLine(buffer);
        return;
      }
    }
  } finally {
    reader.releaseLock();
  }
}
