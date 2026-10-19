import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { beginAuthorization, completeAuthorization, safeReturnPath } from './loginFlow'
import type { OidcConfig } from './oidc'
import { clearAccessToken, getAccessToken } from './tokenStore'

const TRANSACTION_KEY = 'costgraph.oidc.transaction'

function configFor(issuer: string): OidcConfig {
  return { issuer, clientId: 'workbench', redirectUri: 'http://127.0.0.1:5173/auth/callback', scope: 'openid profile email' }
}

function mockProvider(issuer: string, token: string) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    if (url.endsWith('/.well-known/openid-configuration')) {
      return new Response(JSON.stringify({ authorization_endpoint: `${issuer}/authorize`, token_endpoint: `${issuer}/token` }), { headers: { 'Content-Type': 'application/json' } })
    }
    if (url === `${issuer}/token`) {
      return new Response(JSON.stringify({ access_token: token }), { headers: { 'Content-Type': 'application/json' } })
    }
    throw new Error(`unexpected request: ${url}`)
  })
}

beforeEach(() => {
  clearAccessToken()
  sessionStorage.clear()
  localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('authorization code + PKCE flow', () => {
  it('builds the authorization URL and stashes only the one-time transaction', async () => {
    const issuer = 'https://id.example.com/flow-a'
    mockProvider(issuer, 'token-a')

    const url = new URL(await beginAuthorization('/cost-data?period=2026-06', configFor(issuer)))
    const transaction = JSON.parse(sessionStorage.getItem(TRANSACTION_KEY) ?? '{}')

    expect(url.searchParams.get('response_type')).toBe('code')
    expect(url.searchParams.get('client_id')).toBe('workbench')
    expect(url.searchParams.get('redirect_uri')).toBe('http://127.0.0.1:5173/auth/callback')
    expect(url.searchParams.get('scope')).toBe('openid profile email')
    expect(url.searchParams.get('code_challenge_method')).toBe('S256')
    expect(url.searchParams.get('code_challenge')).toMatch(/^[A-Za-z0-9_-]{43}$/)
    expect(url.searchParams.get('state')).toBe(transaction.state)
    expect(transaction.codeVerifier).toMatch(/^[A-Za-z0-9_-]{43}$/)
    expect(transaction.returnTo).toBe('/cost-data?period=2026-06')
  })

  it('exchanges the code for an in-memory token and consumes the transaction', async () => {
    const issuer = 'https://id.example.com/flow-b'
    mockProvider(issuer, 'token-b')
    const url = new URL(await beginAuthorization('/', configFor(issuer)))
    const state = url.searchParams.get('state') ?? ''

    await expect(completeAuthorization(`?code=code-1&state=${encodeURIComponent(state)}`, configFor(issuer))).resolves.toBe('/')

    expect(getAccessToken()).toBe('token-b')
    expect(sessionStorage.getItem(TRANSACTION_KEY)).toBeNull()
    expect(localStorage.length).toBe(0)
  })

  it('rejects a state mismatch and clears the stored transaction', async () => {
    const issuer = 'https://id.example.com/flow-c'
    mockProvider(issuer, 'token-c')
    await beginAuthorization('/', configFor(issuer))

    await expect(completeAuthorization('?code=code-1&state=forged', configFor(issuer))).rejects.toThrow('state 校验失败')
    expect(getAccessToken()).toBeNull()
    expect(sessionStorage.getItem(TRANSACTION_KEY)).toBeNull()
  })

  it('rejects an expired transaction', async () => {
    const issuer = 'https://id.example.com/flow-d'
    mockProvider(issuer, 'token-d')
    const url = new URL(await beginAuthorization('/', configFor(issuer)))
    const transaction = JSON.parse(sessionStorage.getItem(TRANSACTION_KEY) ?? '{}')
    sessionStorage.setItem(TRANSACTION_KEY, JSON.stringify({ ...transaction, createdAt: Date.now() - 11 * 60 * 1000 }))

    await expect(completeAuthorization(`?code=code-1&state=${encodeURIComponent(url.searchParams.get('state') ?? '')}`, configFor(issuer))).rejects.toThrow('登录事务已过期')
  })

  it('surfaces provider errors instead of navigating', async () => {
    await expect(completeAuthorization('?error=access_denied&error_description=denied', configFor('https://id.example.com/flow-e'))).rejects.toThrow('身份提供方返回错误：denied')
  })
})

describe('safeReturnPath', () => {
  it('only keeps in-app paths and never returns to the callback route', () => {
    expect(safeReturnPath('/cost-data?period=2026-06')).toBe('/cost-data?period=2026-06')
    expect(safeReturnPath('//evil.example.com')).toBe('/')
    expect(safeReturnPath('https://evil.example.com')).toBe('/')
    expect(safeReturnPath('/auth/callback?code=1')).toBe('/')
    expect(safeReturnPath(null)).toBe('/')
  })
})
