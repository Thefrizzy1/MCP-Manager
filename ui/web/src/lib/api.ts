/** Typed fetch wrapper. Same-origin; the browser sends Basic-auth creds and the
 *  CSRF Origin header automatically. Throws ApiError on non-2xx. */
export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  const hasBody = opts?.body !== undefined && opts?.body !== null
  const res = await fetch(path, {
    ...opts,
    headers: {
      ...(hasBody ? { 'Content-Type': 'application/json' } : {}),
      ...opts?.headers,
    },
  })
  const text = await res.text()
  let data: unknown = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!res.ok) {
    // A 401 on any dashboard API means the session is gone (expired, logged out,
    // or never established). Send the browser to the login page rather than
    // surfacing a raw error the SPA can't recover from. The login page is a
    // separate server-rendered route, so this cannot loop.
    if (res.status === 401 && !window.location.pathname.startsWith('/login')) {
      window.location.assign('/login')
    }
    const d = data as { detail?: unknown; error?: unknown } | null
    const raw = (d && (d.detail ?? d.error)) ?? `HTTP ${res.status}`
    throw new ApiError(typeof raw === 'string' ? raw : JSON.stringify(raw), res.status)
  }
  return data as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body !== undefined ? JSON.stringify(body) : undefined }),
  del: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'DELETE', body: body !== undefined ? JSON.stringify(body) : undefined }),
}
