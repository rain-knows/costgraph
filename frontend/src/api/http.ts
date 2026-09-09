export async function apiRequest<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init)
  if (!response.ok) throw await apiError(response)
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function apiError(response: Response, fallback = '请求未能完成'): Promise<Error> {
  try {
    const payload = await response.json() as {
      detail?: { message?: string; request_id?: string } | string
      message?: string
      request_id?: string
    }
    const detail = typeof payload.detail === 'string' ? payload.detail : payload.detail?.message
    const requestId = typeof payload.detail === 'object' ? payload.detail?.request_id : payload.request_id
    return new Error(`${detail || payload.message || `${fallback}：HTTP ${response.status}`}${requestId ? `（请求 ${requestId}）` : ''}`)
  } catch {
    return new Error(`${fallback}：HTTP ${response.status}`)
  }
}
