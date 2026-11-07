export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export class Unauthorized extends ApiError {
  constructor(message = "Not logged in.") {
    super(401, message);
  }
}

let onUnauthorized: (() => void) | null = null;

/** Called before any request throws Unauthorized; the layout registers a redirect to /login. */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

async function detailOf(response: Response): Promise<string | undefined> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (Array.isArray(body.detail))
      // FastAPI validation errors: one entry per field
      return body.detail.map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : String(d))).join("; ");
    return body.detail === undefined ? undefined : String(body.detail);
  } catch {
    return undefined; // body was not JSON
  }
}

export async function api<T>(
  path: string,
  init: RequestInit = {},
  fetchImpl: typeof fetch = fetch,
): Promise<T> {
  const response = await fetchImpl(path, {
    credentials: "same-origin",
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  if (response.status === 401) {
    const detail = await detailOf(response);
    onUnauthorized?.();
    throw new Unauthorized(detail);
  }
  if (!response.ok) {
    throw new ApiError(response.status, (await detailOf(response)) ?? response.statusText);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const withBody = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const get = <T>(path: string) => api<T>(path);
export const post = <T>(path: string, body?: unknown) => api<T>(path, withBody("POST", body));
export const put = <T>(path: string, body?: unknown) => api<T>(path, withBody("PUT", body));
export const patch = <T>(path: string, body?: unknown) => api<T>(path, withBody("PATCH", body));
export const del = <T>(path: string, body?: unknown) => api<T>(path, withBody("DELETE", body));
