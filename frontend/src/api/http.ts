export class ApiError extends Error {
  code: string
  status: number
  requestId?: string

  constructor(message: string, status: number, code = 'http_error', requestId?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.requestId = requestId
  }
}

export async function apiRequest<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, { credentials: 'same-origin', ...init })
  if (!response.ok) throw await apiError(response)
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function apiError(response: Response, fallback = '请求未能完成'): Promise<Error> {
  try {
    const payload = await response.json() as {
      detail?: { code?: string; message?: string; request_id?: string } | string
      message?: string
      request_id?: string
      code?: string
    }
    const detail = typeof payload.detail === 'string' ? payload.detail : payload.detail?.message
    const requestId = typeof payload.detail === 'object' ? payload.detail?.request_id : payload.request_id
    const code = typeof payload.detail === 'object' ? payload.detail?.code : payload.code
    return new ApiError(`${detail || payload.message || `${fallback}：HTTP ${response.status}`}${requestId ? `（请求 ${requestId}）` : ''}`, response.status, code, requestId)
  } catch {
    return new ApiError(`${fallback}：HTTP ${response.status}`, response.status)
  }
}
