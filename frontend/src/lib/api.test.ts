import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { liveTransport } from "./transport";
import type { AnswerEnvelope, TraceEvent } from "./types";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function sse(frames: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
}

const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const step = (seq: number): TraceEvent => ({
  data: {},
  kind: "plan",
  latency_ms: null,
  seq,
  summary: `step ${seq}`,
  tool: null,
  trace_id: "t1",
});
const envelope = { outcome: "answered", trace_id: "t1" } as unknown as AnswerEnvelope;

async function rejection(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError);
    return error as ApiError;
  }
  throw new Error("expected the call to reject");
}

describe("live transport errors", () => {
  it.each([
    [401, "sign in first"],
    [422, "Ask a question of at most 10000 characters."],
    [503, "The dataset is not available. Run `make load-data` and try again."],
  ])("turns a JSON %i into an ApiError carrying the server's detail", async (status, detail) => {
    fetchMock.mockResolvedValueOnce(json({ detail }, status));
    const error = await rejection(liveTransport.newConversation());
    expect(error.status).toBe(status);
    expect(error.detail).toBe(detail);
    expect(error.message).toBe(detail);
  });

  it("joins a validation-error list into one readable detail", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ detail: [{ loc: ["body", "question"], msg: "Field required", type: "missing" }] }, 422),
    );
    const error = await rejection(liveTransport.newConversation());
    expect(error.detail).toBe("Field required");
  });

  it("falls back to a plain sentence when the body is not JSON", async () => {
    fetchMock.mockResolvedValueOnce(new Response("<html>bad gateway</html>", { status: 502 }));
    const error = await rejection(liveTransport.newConversation());
    expect(error.status).toBe(502);
    expect(error.detail).not.toContain("<html>");
    expect(error.detail.length).toBeGreaterThan(0);
  });

  it("reports an unreachable server as status 0", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const error = await rejection(liveTransport.newConversation());
    expect(error.status).toBe(0);
    expect(error.detail).toBe("The copilot could not be reached.");
  });

  it("restores no session on 401 instead of throwing", async () => {
    fetchMock.mockResolvedValueOnce(json({ detail: "sign in first" }, 401));
    await expect(liveTransport.session()).resolves.toBeNull();
  });
});

describe("liveTransport.ask", () => {
  it("posts the question, calls onTrace per trace event and resolves with the answer", async () => {
    fetchMock.mockResolvedValueOnce(
      sse([frame("trace", step(1)), frame("trace", step(2)), frame("answer", envelope), frame("done", {})]),
    );
    const seen: number[] = [];
    const result = await liveTransport.ask("c1", "Which devices are low on disk?", (e) =>
      seen.push(e.seq),
    );
    expect(seen).toEqual([1, 2]);
    expect(result).toEqual(envelope);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/chat");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(JSON.parse(init.body)).toEqual({
      conversation_id: "c1",
      question: "Which devices are low on disk?",
    });
  });

  it("rejects when the stream ends without an answer", async () => {
    fetchMock.mockResolvedValueOnce(sse([frame("trace", step(1)), frame("done", {})]));
    const onTrace = vi.fn();
    const error = await rejection(liveTransport.ask("c1", "q", onTrace));
    expect(onTrace).toHaveBeenCalledTimes(1);
    expect(error.detail).toMatch(/without an answer/i);
  });

  it("surfaces a pre-stream JSON error with its detail", async () => {
    fetchMock.mockResolvedValueOnce(json({ detail: "invalid conversation id" }, 422));
    const error = await rejection(liveTransport.ask("bad id", "q", () => {}));
    expect(error.status).toBe(422);
    expect(error.detail).toBe("invalid conversation id");
  });
});

describe("liveTransport proposals", () => {
  it("approve sends the payload hash as JSON on the same origin", async () => {
    fetchMock.mockResolvedValueOnce(json({ proposal: { proposal_id: "prp_1" }, effect: null, duplicate: false }));
    await liveTransport.approve("prp_1", "abc123");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/proposals/prp_1/approve");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(init.headers["content-type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({ payload_hash: "abc123" });
  });

  it("surfaces a 409 with its detail", async () => {
    fetchMock.mockResolvedValueOnce(json({ detail: "proposal changed" }, 409));
    const error = await rejection(liveTransport.approve("prp_1", "stale"));
    expect(error.status).toBe(409);
    expect(error.detail).toBe("proposal changed");
  });

  it("reject posts with no body and filters pass as a query string", async () => {
    fetchMock.mockResolvedValueOnce(json({ proposal: { proposal_id: "prp_1" } }));
    await liveTransport.reject("prp_1");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/proposals/prp_1/reject");
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");

    fetchMock.mockResolvedValueOnce(json([]));
    await liveTransport.proposals("pending");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/proposals?status=pending");

    fetchMock.mockResolvedValueOnce(json([]));
    await liveTransport.audit({ proposal_id: "prp_1", target: "DEV 1" });
    expect(fetchMock.mock.calls[2][0]).toBe("/api/audit?proposal_id=prp_1&target=DEV+1");

    fetchMock.mockResolvedValueOnce(json([]));
    await liveTransport.observeAudit({ company: "acme-001" });
    expect(fetchMock.mock.calls[3][0]).toBe("/api/observe/audit?company=acme-001");

    fetchMock.mockResolvedValueOnce(json([]));
    await liveTransport.observeEvals(5);
    expect(fetchMock.mock.calls[4][0]).toBe("/api/observe/evals?limit=5");

    fetchMock.mockResolvedValueOnce(json({ run_id: "a/b", cases: [] }));
    await liveTransport.observeEval("a/b");
    expect(fetchMock.mock.calls[5][0]).toBe("/api/observe/evals/a%2Fb");
  });

  it("encodes evidence refs and ids", async () => {
    fetchMock.mockResolvedValueOnce(json({ ref: "r", collected_at: "t", value: {} }));
    await liveTransport.evidence("acme-001/D1@2026-06-12T09:47:00Z#compliance[screen_lock]");
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/evidence?ref=acme-001%2FD1%402026-06-12T09%3A47%3A00Z%23compliance%5Bscreen_lock%5D",
    );
    fetchMock.mockResolvedValueOnce(json({ trace_id: "a/b", events: [] }));
    await liveTransport.trace("a/b");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/traces/a%2Fb");
  });

  it("signs in with the user and optional passcode and signs out with DELETE", async () => {
    fetchMock.mockResolvedValueOnce(json({ company_id: "operator", display_name: "Operator (demo)", role: "operator" }));
    await liveTransport.signIn("operator", "pw");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/session");
    expect(JSON.parse(init.body)).toEqual({ user: "operator", passcode: "pw" });

    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await liveTransport.signOut();
    expect(fetchMock.mock.calls[1][1].method).toBe("DELETE");
    expect(fetchMock.mock.calls[1][1].credentials).toBe("same-origin");
  });
});

describe("liveTransport conversation history", () => {
  it("lists with a limit, reads one by id and deletes one", async () => {
    const summaries = [{ conversation_id: "c1", title: "Low disk", updated_at: "2026-11-21T10:00:00Z", turns: 2 }];
    fetchMock.mockResolvedValueOnce(json(summaries));
    await expect(liveTransport.conversations(30)).resolves.toEqual(summaries);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/conversations?limit=30");

    const stored = { conversation_id: "c 1", title: "Low disk", turns: [] };
    fetchMock.mockResolvedValueOnce(json(stored));
    await expect(liveTransport.conversation("c 1")).resolves.toEqual(stored);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/conversations/c%201");

    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(liveTransport.deleteConversation("c1")).resolves.toBeUndefined();
    expect(fetchMock.mock.calls[2][0]).toBe("/api/conversations/c1");
    expect(fetchMock.mock.calls[2][1].method).toBe("DELETE");
  });

  it("lists without a query when no limit is given, and surfaces a 404 on reopen", async () => {
    fetchMock.mockResolvedValueOnce(json([]));
    await liveTransport.conversations();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/conversations");
    fetchMock.mockResolvedValueOnce(json({ detail: "not found" }, 404));
    const error = await rejection(liveTransport.conversation("gone"));
    expect(error.status).toBe(404);
  });
});
