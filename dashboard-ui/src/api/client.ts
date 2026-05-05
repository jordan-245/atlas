/**
 * Typed fetch wrapper for Atlas dashboard API.
 * Uses same-origin credentials (Basic Auth is handled by the browser on first 401).
 * 10 second timeout via AbortController.
 */

/**
 * ApiError — thrown by get() and post() on non-2xx responses.
 * Carries the HTTP status code AND the FastAPI `detail` string (when present in the JSON body).
 * Extends Error so that `e.message` still works for legacy callers;
 * new callers can check `e instanceof ApiError ? e.detail : e.message`.
 */
export class ApiError extends Error {
  status: number
  detail: string
  constructor(status: number, detail: string, statusText: string) {
    super(detail || statusText || `HTTP ${status}`)
    this.status = status
    this.detail = detail || statusText || `HTTP ${status}`
    this.name = 'ApiError'
  }
}

/** Parse a FastAPI error body, returning the `detail` string or empty string on failure. */
async function extractDetail(response: Response): Promise<string> {
  try {
    const body = await response.json()
    if (body && typeof body === 'object' && typeof body.detail === 'string') {
      return body.detail
    }
  } catch {
    // body not JSON or parse failed — fall through
  }
  return ''
}

export async function get<T>(url: string): Promise<T> {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), 10_000)
  try {
    const response = await fetch(url, {
      credentials: 'same-origin',
      signal: controller.signal,
      headers: { Accept: 'application/json' },
    })
    if (!response.ok) {
      const detail = await extractDetail(response)
      throw new ApiError(response.status, detail, response.statusText)
    }
    return (await response.json()) as T
  } finally {
    clearTimeout(timeoutId)
  }
}

/**
 * Typed POST wrapper — mirrors get<T> but sends JSON body.
 * Uses same-origin credentials. 10 second timeout.
 */
export async function post<T>(url: string, body: unknown = {}): Promise<T> {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), 10_000)
  try {
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      signal: controller.signal,
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!response.ok) {
      const detail = await extractDetail(response)
      throw new ApiError(response.status, detail, response.statusText)
    }
    return (await response.json()) as T
  } finally {
    clearTimeout(timeoutId)
  }
}
