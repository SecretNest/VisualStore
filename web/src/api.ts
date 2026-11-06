export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export class Unauthorized extends ApiError {
  constructor() {
    super(401, "Not logged in.");
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
  if (response.status === 401) throw new Unauthorized();
  if (!response.ok) {
    let detail: string = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (body.detail !== undefined) detail = String(body.detail);
    } catch {
      /* body was not JSON; keep the status text */
    }
    throw new ApiError(response.status, detail);
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
