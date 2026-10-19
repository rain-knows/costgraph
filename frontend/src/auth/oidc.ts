export type OidcConfig = {
  issuer: string
  clientId: string
  redirectUri: string
  scope: string
}

export type OidcEndpoints = {
  authorizationEndpoint: string
  tokenEndpoint: string
}

export class OidcError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'OidcError'
  }
}

/** 配置缺失时返回 null，由登录页呈现“身份服务未配置”，不静默降级为匿名访问。 */
export function readOidcConfig(): OidcConfig | null {
  const env = import.meta.env
  const issuer = (env.VITE_OIDC_ISSUER ?? '').trim()
  const clientId = (env.VITE_OIDC_CLIENT_ID ?? '').trim()
  const redirectUri = (env.VITE_OIDC_REDIRECT_URI ?? '').trim()
  if (!issuer || !clientId || !redirectUri) return null
  return {
    issuer,
    clientId,
    redirectUri,
    scope: (env.VITE_OIDC_SCOPE ?? '').trim() || 'openid profile email',
  }
}

let cachedEndpoints: { issuer: string; endpoints: OidcEndpoints } | null = null

export async function discoverEndpoints(config: OidcConfig, signal?: AbortSignal): Promise<OidcEndpoints> {
  if (cachedEndpoints?.issuer === config.issuer) return cachedEndpoints.endpoints
  const discoveryUrl = `${config.issuer.replace(/\/+$/, '')}/.well-known/openid-configuration`
  let response: Response
  try {
    response = await fetch(discoveryUrl, { headers: { Accept: 'application/json' }, signal })
  } catch {
    throw new OidcError('无法连接身份服务，请稍后重试。')
  }
  if (!response.ok) throw new OidcError(`身份服务发现失败：HTTP ${response.status}`)
  const payload = await response.json() as { authorization_endpoint?: unknown; token_endpoint?: unknown }
  const authorizationEndpoint = typeof payload.authorization_endpoint === 'string' ? payload.authorization_endpoint : ''
  const tokenEndpoint = typeof payload.token_endpoint === 'string' ? payload.token_endpoint : ''
  if (!authorizationEndpoint || !tokenEndpoint) throw new OidcError('身份服务发现响应缺少授权或令牌端点。')
  const endpoints = { authorizationEndpoint, tokenEndpoint }
  cachedEndpoints = { issuer: config.issuer, endpoints }
  return endpoints
}

export function buildAuthorizationUrl(
  config: OidcConfig,
  endpoints: OidcEndpoints,
  pkce: { state: string; codeChallenge: string },
): string {
  const url = new URL(endpoints.authorizationEndpoint)
  url.searchParams.set('response_type', 'code')
  url.searchParams.set('client_id', config.clientId)
  url.searchParams.set('redirect_uri', config.redirectUri)
  url.searchParams.set('scope', config.scope)
  url.searchParams.set('state', pkce.state)
  url.searchParams.set('code_challenge', pkce.codeChallenge)
  url.searchParams.set('code_challenge_method', 'S256')
  return url.toString()
}

export async function exchangeCodeForAccessToken(
  config: OidcConfig,
  endpoints: OidcEndpoints,
  request: { code: string; codeVerifier: string },
): Promise<string> {
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    code: request.code,
    redirect_uri: config.redirectUri,
    client_id: config.clientId,
    code_verifier: request.codeVerifier,
  })
  let response: Response
  try {
    response = await fetch(endpoints.tokenEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', Accept: 'application/json' },
      body,
    })
  } catch {
    throw new OidcError('令牌交换失败：无法连接身份服务。')
  }
  if (!response.ok) throw new OidcError(`令牌交换失败：HTTP ${response.status}`)
  const payload = await response.json() as { access_token?: unknown }
  if (typeof payload.access_token !== 'string' || !payload.access_token.trim()) {
    throw new OidcError('令牌交换响应缺少访问令牌。')
  }
  return payload.access_token
}
