/** Typed same-origin fetch helpers. The session is the HttpOnly cookie; nothing is stored in JS. */

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** Shown when the request never reached the server (status 0). */
export const UNREACHABLE = "The copilot could not be reached.";

const STATUS_WORDS = "The copilot returned an unexpected response.";

function detailOf(body: unknown): string | null {
  if (typeof body !== "object" || body === null || !("detail" in body)) return null;
  const detail = (body as { detail: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (typeof item === "object" && item !== null ? (item as { msg?: unknown }).msg : null))
      .filter((msg): msg is string => typeof msg === "string");
    return messages.length > 0 ? messages.join("; ") : null;
  }
  return null;
}

export async function errorFrom(response: Response): Promise<ApiError> {
  let detail: string | null = null;
  try {
    detail = detailOf(await response.json());
  } catch {
    /* not JSON: keep the generic sentence, never echo a body */
  }
  return new ApiError(response.status, detail ?? STATUS_WORDS);
}

/** Send a same-origin request; a network failure becomes `ApiError(0)`, a non-2xx becomes the server's detail. */
export async function send(
  path: string,
  init: { method?: string; body?: unknown; headers?: Record<string, string> } = {},
): Promise<Response> {
  const headers: Record<string, string> = { ...init.headers };
  let body: string | undefined;
  if (init.body !== undefined) {
    headers["content-type"] = "application/json";
    body = JSON.stringify(init.body);
  }
  let response: Response;
  try {
    response = await fetch(path, {
      method: init.method ?? "GET",
      credentials: "same-origin",
      headers,
      body,
    });
  } catch {
    throw new ApiError(0, UNREACHABLE);
  }
  if (!response.ok) throw await errorFrom(response);
  return response;
}

export async function getJson<T>(path: string, query?: Record<string, string | undefined>): Promise<T> {
  return (await send(path + queryString(query))).json() as Promise<T>;
}

export async function postJson<T>(path: string, body?: unknown): Promise<T> {
  return (await send(path, { method: "POST", body })).json() as Promise<T>;
}

export async function del(path: string): Promise<void> {
  await send(path, { method: "DELETE" });
}

export function queryString(query?: Record<string, string | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== "") params.set(key, value);
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}
