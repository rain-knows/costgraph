import { clearAccessToken, getAccessToken } from '../auth/tokenStore'

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

/** 401：令牌缺失、过期或无效；令牌已在 http 边界清空，调用方应回到未登录态。 */
export class UnauthorizedError extends ApiError {
  constructor(message: string, code = 'unauthorized', requestId?: string) {
    super(message, 401, code, requestId)
    this.name = 'UnauthorizedError'
  }
}

/** 403：已认证但缺少权限；登录态保留。 */
export class ForbiddenError extends ApiError {
  constructor(message: string, code = 'forbidden', requestId?: string) {
    super(message, 403, code, requestId)
    this.name = 'ForbiddenError'
  }
}

/** 404：资源不存在，与空列表和无权限严格区分。 */
export class NotFoundError extends ApiError {
  constructor(message: string, code = 'not_found', requestId?: string) {
    super(message, 404, code, requestId)
    this.name = 'NotFoundError'
  }
}

export function isUnauthorized(error: unknown): error is UnauthorizedError {
  return error instanceof UnauthorizedError
}

export function isForbidden(error: unknown): error is ForbiddenError {
  return error instanceof ForbiddenError
}

export function isNotFound(error: unknown): error is NotFoundError {
  return error instanceof NotFoundError
}

/** 供自行调用 fetch 的边界（如 SSE 订阅）复用同一个 Authorization 头形状。 */
export function authorizationHeader(): Record<string, string> {
  const token = getAccessToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export async function apiRequest<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (!headers.has('Authorization')) {
    const token = getAccessToken()
    if (token) headers.set('Authorization', `Bearer ${token}`)
  }
  const response = await fetch(input, { credentials: 'same-origin', ...init, headers })
  if (!response.ok) {
    const error = await apiError(response)
    if (response.status === 401) clearAccessToken()
    throw error
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function apiError(response: Response, fallback = '请求未能完成'): Promise<ApiError> {
  let message = `${fallback}：HTTP ${response.status}`
  let code: string | undefined
  let requestId: string | undefined
  try {
    const payload = await response.json() as {
      detail?: { code?: string; message?: string; request_id?: string } | string
      message?: string
      request_id?: string
      code?: string
    }
    const detail = typeof payload.detail === 'string' ? payload.detail : payload.detail?.message
    requestId = typeof payload.detail === 'object' ? payload.detail?.request_id : payload.request_id
    code = typeof payload.detail === 'object' ? payload.detail?.code : payload.code
    message = `${detail || payload.message || message}${requestId ? `（请求 ${requestId}）` : ''}`
  } catch {
    message = `${fallback}：HTTP ${response.status}`
  }
  if (response.status === 401) return new UnauthorizedError(message, code ?? 'unauthorized', requestId)
  if (response.status === 403) return new ForbiddenError(message, code ?? 'forbidden', requestId)
  if (response.status === 404) return new NotFoundError(message, code ?? 'not_found', requestId)
  return new ApiError(message, response.status, code, requestId)
}
