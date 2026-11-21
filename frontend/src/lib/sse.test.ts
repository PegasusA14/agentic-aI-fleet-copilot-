import { describe, expect, it } from "vitest";
import { parseSse } from "./sse";

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

function streamOfBytes(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>) {
  const out: { event: string; data: unknown }[] = [];
  for await (const item of parseSse(stream)) out.push(item);
  return out;
}

const WIRE =
  'event: trace\ndata: {"seq":1,"summary":"Checking scope"}\n\n' +
  'event: trace\ndata: {"seq":2,"summary":"Reading data"}\n\n' +
  'event: answer\ndata: {"outcome":"answered"}\n\n' +
  "event: done\ndata: {}\n\n";

describe("parseSse", () => {
  it("yields trace, answer and done in order", async () => {
    const events = await collect(streamOf([WIRE]));
    expect(events.map((e) => e.event)).toEqual(["trace", "trace", "answer", "done"]);
    expect(events[0].data).toEqual({ seq: 1, summary: "Checking scope" });
    expect(events[2].data).toEqual({ outcome: "answered" });
  });

  it("gives the same events however the bytes are split, even one character at a time", async () => {
    const whole = await collect(streamOf([WIRE]));
    const chars = await collect(streamOf(WIRE.split("")));
    expect(chars).toEqual(whole);
    const cut = await collect(streamOf([WIRE.slice(0, 17), WIRE.slice(17, 61), WIRE.slice(61)]));
    expect(cut).toEqual(whole);
  });

  it("handles CRLF line endings, including a CR and LF split across chunks", async () => {
    const wire = WIRE.replace(/\n/g, "\r\n");
    expect(await collect(streamOf([wire]))).toEqual(await collect(streamOf([WIRE])));
    const split = wire.split("\r\n").join("\r|\n").split("|");
    expect(await collect(streamOf(split))).toEqual(await collect(streamOf([WIRE])));
  });

  it("joins multi-line data with a newline before parsing", async () => {
    const events = await collect(streamOf(['event: answer\ndata: {"a":\ndata: 1}\n\n']));
    expect(events).toEqual([{ event: "answer", data: { a: 1 } }]);
  });

  it("keeps non-JSON data as text and ignores comments and unknown fields", async () => {
    const events = await collect(
      streamOf([": keep-alive\n\nid: 7\nretry: 100\nevent: note\ndata: plain words\n\n"]),
    );
    expect(events).toEqual([{ event: "note", data: "plain words" }]);
  });

  it("defaults the event name to message", async () => {
    expect(await collect(streamOf(['data: {"x":1}\n\n']))).toEqual([
      { event: "message", data: { x: 1 } },
    ]);
  });

  it("does not split a multi-byte character across chunks", async () => {
    const bytes = new TextEncoder().encode('event: trace\ndata: {"s":"naïve – café"}\n\n');
    const cut = bytes.indexOf(0xc3) + 1; // inside the two-byte "ï"
    const events = await collect(streamOfBytes([bytes.slice(0, cut), bytes.slice(cut)]));
    expect(events).toEqual([{ event: "trace", data: { s: "naïve – café" } }]);
  });

  it("drops an event the stream cut off before its blank line", async () => {
    const events = await collect(streamOf(['event: trace\ndata: {"seq":1}\n\nevent: answer\ndata: {"a"']));
    expect(events.map((e) => e.event)).toEqual(["trace"]);
  });
});
