import type { ReactNode } from 'react'
import { useEffect } from 'react'
import { LoginPage } from './LoginPage'
import { isLocalDemoAuthEnabled, LOCAL_DEMO_ACCESS_TOKEN } from './localDemo'
import { getAccessToken, setAccessToken } from './tokenStore'
import { useAccessToken } from './useAccessToken'

/** 未登录时整页登录，不渲染任何工作区内容。 */
export function RequireSignIn({ children }: { children: ReactNode }) {
  const localDemo = isLocalDemoAuthEnabled()
  // 在挂载受保护子树前同步注入演示令牌，避免子树首个 effect 先发出无令牌请求。
  if (localDemo && !getAccessToken()) setAccessToken(LOCAL_DEMO_ACCESS_TOKEN)
  const accessToken = useAccessToken()
  useEffect(() => {
    if (localDemo && !accessToken) setAccessToken(LOCAL_DEMO_ACCESS_TOKEN)
  }, [accessToken, localDemo])
  if (localDemo && !accessToken) {
    return <main className="grid min-h-screen place-items-center bg-[var(--bg)] px-6 text-[var(--fg)]"><p className="label" role="status">正在进入本地演示工作区</p></main>
  }
  if (!accessToken) return <LoginPage />
  return <>{children}</>
}
