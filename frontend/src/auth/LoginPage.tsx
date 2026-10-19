import { useState } from 'react'
import { KeyRound } from 'lucide-react'
import { Button } from '../components/ui'
import { beginAuthorization, redirectToAuthorization } from './loginFlow'
import { readOidcConfig } from './oidc'

/** 未登录态：整页登录入口，不使用占位数据伪装工作区。 */
export function LoginPage({ notice }: { notice?: string }) {
  const config = readOidcConfig()
  const [error, setError] = useState(notice ?? '')
  const [pending, setPending] = useState(false)

  async function signIn() {
    if (!config) return
    setPending(true)
    setError('')
    try {
      const returnTo = `${window.location.pathname}${window.location.search}`
      redirectToAuthorization(await beginAuthorization(returnTo, config))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '无法发起登录，请稍后重试。')
      setPending(false)
    }
  }

  return <main className="grid min-h-screen place-items-center bg-[var(--bg)] px-6 text-[var(--fg)]">
    <section className="w-full max-w-md border border-[var(--border)] bg-[var(--bg)] p-6" aria-labelledby="login-title">
      <p className="label">CostGraph 成本中台</p>
      <h1 id="login-title" className="page-title mt-2">登录以继续</h1>
      <p className="mt-3 text-sm leading-6 text-[var(--fg-2)]">
        使用统一身份提供方的 Authorization Code + PKCE 登录。访问令牌只保存在当前标签页内存中，刷新页面后需要重新登录。
      </p>
      {error ? <p className="mt-4 text-sm text-[var(--danger)]" role="alert">{error}</p> : null}
      <Button className="mt-6 w-full" variant="primary" disabled={!config || pending} onClick={() => void signIn()}>
        <KeyRound className="h-4 w-4" aria-hidden="true" />{pending ? '正在跳转身份服务' : '使用统一身份登录'}
      </Button>
      {config ? null : <p className="mt-3 text-sm text-[var(--fg-2)]" role="status">身份服务未配置：请在构建环境设置 VITE_OIDC_ISSUER、VITE_OIDC_CLIENT_ID 与 VITE_OIDC_REDIRECT_URI。</p>}
    </section>
  </main>
}
