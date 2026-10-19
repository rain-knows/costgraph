import type { ReactNode } from 'react'
import { LoginPage } from './LoginPage'
import { useAccessToken } from './useAccessToken'

/** 未登录时整页登录，不渲染任何工作区内容。 */
export function RequireSignIn({ children }: { children: ReactNode }) {
  const accessToken = useAccessToken()
  if (!accessToken) return <LoginPage />
  return <>{children}</>
}
