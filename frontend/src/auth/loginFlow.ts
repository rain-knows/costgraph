import { createCodeChallenge, createCodeVerifier, createState } from './pkce'
import { discoverEndpoints, exchangeCodeForAccessToken, buildAuthorizationUrl, OidcError, type OidcConfig } from './oidc'
import { setAccessToken } from './tokenStore'

/**
 * 授权码 + PKCE 的登录事务需要跨越一次整页跳转，因此 ``state`` 与 ``code_verifier``
 * 暂存于 sessionStorage；它们是短时一次性随机值，不是访问令牌，完成后立即删除。
 * 访问令牌本身只写入内存（见 tokenStore）。
 */
const TRANSACTION_KEY = 'costgraph.oidc.transaction'
const TRANSACTION_TTL_MS = 10 * 60 * 1000

type PkceTransaction = {
  state: string
  codeVerifier: string
  returnTo: string
  createdAt: number
}

export class AuthorizationError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'AuthorizationError'
  }
}

export async function beginAuthorization(returnTo: string, config: OidcConfig): Promise<string> {
  const state = createState()
  const codeVerifier = createCodeVerifier()
  const codeChallenge = await createCodeChallenge(codeVerifier)
  const endpoints = await discoverEndpoints(config)
  writeTransaction({ state, codeVerifier, returnTo: safeReturnPath(returnTo), createdAt: Date.now() })
  return buildAuthorizationUrl(config, endpoints, { state, codeChallenge })
}

export function redirectToAuthorization(url: string): void {
  window.location.assign(url)
}

/** 校验 state 后用授权码换取访问令牌；返回值是登录前希望继续访问的应用内路径。 */
export async function completeAuthorization(search: string, config: OidcConfig): Promise<string> {
  const params = new URLSearchParams(search)
  const providerError = params.get('error')
  if (providerError) {
    throw new AuthorizationError(`身份提供方返回错误：${params.get('error_description') || providerError}`)
  }
  const code = params.get('code')
  const state = params.get('state')
  if (!code || !state) throw new AuthorizationError('回调缺少授权码或 state 参数。')

  const transaction = takeTransaction()
  if (!transaction) throw new AuthorizationError('登录事务已过期，请重新登录。')
  if (transaction.state !== state) throw new AuthorizationError('state 校验失败，请重新登录。')

  const endpoints = await discoverEndpoints(config)
  setAccessToken(await exchangeCodeForAccessToken(config, endpoints, { code, codeVerifier: transaction.codeVerifier }))
  return transaction.returnTo
}

/** 只接受应用内绝对路径，避免开放重定向和登录回调自环。 */
export function safeReturnPath(value: string | null | undefined): string {
  if (!value || !value.startsWith('/') || value.startsWith('//')) return '/'
  if (value === '/auth' || value.startsWith('/auth/') || value.startsWith('/auth?')) return '/'
  return value
}

function writeTransaction(transaction: PkceTransaction): void {
  try {
    sessionStorage.setItem(TRANSACTION_KEY, JSON.stringify(transaction))
  } catch {
    throw new AuthorizationError('无法保存登录事务，请检查浏览器存储权限。')
  }
}

function takeTransaction(): PkceTransaction | null {
  let raw: string | null = null
  try {
    raw = sessionStorage.getItem(TRANSACTION_KEY)
    sessionStorage.removeItem(TRANSACTION_KEY)
  } catch {
    return null
  }
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<PkceTransaction>
    if (typeof parsed.state !== 'string' || typeof parsed.codeVerifier !== 'string') return null
    if (typeof parsed.createdAt !== 'number' || Date.now() - parsed.createdAt > TRANSACTION_TTL_MS) return null
    return {
      state: parsed.state,
      codeVerifier: parsed.codeVerifier,
      returnTo: safeReturnPath(parsed.returnTo),
      createdAt: parsed.createdAt,
    }
  } catch {
    return null
  }
}

export { OidcError }
