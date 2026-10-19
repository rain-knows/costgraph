import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiRequest, ForbiddenError, isForbidden, isNotFound, isUnauthorized, NotFoundError, UnauthorizedError } from './http'
import { clearAccessToken, getAccessToken, setAccessToken } from '../auth/tokenStore'

function respond(status: number, body: unknown = {}) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

beforeEach(() => {
  clearAccessToken()
  localStorage.clear()
  sessionStorage.clear()
})

describe('HTTP auth boundary', () => {
  it('attaches the bearer token from memory', async () => {
    setAccessToken('token-1')
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(respond(200, { ok: true }))

    await apiRequest('/api/me')

    const headers = fetchMock.mock.calls[0][1]?.headers as Headers
    expect(headers.get('Authorization')).toBe('Bearer token-1')
  })

  it('does not persist the token outside module memory', () => {
    setAccessToken('token-1')
    expect(getAccessToken()).toBe('token-1')
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    expect(document.cookie).toBe('')
  })

  it('clears the token and throws an unauthorized error on 401', async () => {
    setAccessToken('token-1')
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(respond(401, { detail: { code: 'token_expired', message: '令牌已过期' } }))

    const error = await apiRequest('/api/me').catch((reason: unknown) => reason)

    expect(error).toBeInstanceOf(UnauthorizedError)
    expect(isUnauthorized(error)).toBe(true)
    expect(error).toMatchObject({ status: 401, code: 'token_expired', message: '令牌已过期' })
    expect(getAccessToken()).toBeNull()
  })

  it('keeps the login state and throws a forbidden error on 403', async () => {
    setAccessToken('token-1')
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(respond(403, { detail: { code: 'data_management_access_denied', message: '无数据治理权限' } }))

    const error = await apiRequest('/api/v1/data-admin/batches').catch((reason: unknown) => reason)

    expect(error).toBeInstanceOf(ForbiddenError)
    expect(isForbidden(error)).toBe(true)
    expect(error).toMatchObject({ status: 403, code: 'data_management_access_denied' })
    expect(getAccessToken()).toBe('token-1')
  })

  it('throws a not-found error on 404 without clearing the token', async () => {
    setAccessToken('token-1')
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(respond(404, { detail: '批次不存在' }))

    const error = await apiRequest('/api/v1/data-admin/batches/missing').catch((reason: unknown) => reason)

    expect(error).toBeInstanceOf(NotFoundError)
    expect(isNotFound(error)).toBe(true)
    expect(error).toMatchObject({ status: 404, message: '批次不存在' })
    expect(getAccessToken()).toBe('token-1')
  })

  it('keeps other statuses distinguishable from the three auth outcomes', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(respond(503, { detail: { code: 'unavailable', message: '服务不可用' } }))

    const error = await apiRequest('/api/health').catch((reason: unknown) => reason)

    expect(error).toBeInstanceOf(ApiError)
    expect(isUnauthorized(error)).toBe(false)
    expect(isForbidden(error)).toBe(false)
    expect(isNotFound(error)).toBe(false)
  })
})
