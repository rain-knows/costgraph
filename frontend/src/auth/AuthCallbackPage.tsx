import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { StatePanel } from '../components/ui'
import { completeAuthorization } from './loginFlow'
import { readOidcConfig } from './oidc'

/** 身份提供方回调：校验 state、用授权码换令牌，然后回到登录前路径（令牌不进 URL）。 */
export function AuthCallbackPage() {
  const navigate = useNavigate()
  const [error, setError] = useState('')

  useEffect(() => {
    const config = readOidcConfig()
    if (!config) {
      setError('身份服务未配置，无法完成登录。')
      return
    }
    let disposed = false
    completeAuthorization(window.location.search, config)
      .then((returnTo) => { if (!disposed) navigate(returnTo, { replace: true }) })
      .catch((reason: unknown) => { if (!disposed) setError(reason instanceof Error ? reason.message : '登录未完成，请重试。') })
    return () => { disposed = true }
  }, [navigate])

  if (error) {
    return <StatePanel state="error" title="登录未完成" detail={error} onRetry={() => navigate('/', { replace: true })} />
  }
  return <StatePanel state="loading" title="正在完成登录" detail="正在校验 state 并用授权码交换访问令牌。" />
}
