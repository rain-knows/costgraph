/**
 * 访问令牌只存在于模块级内存：不写 localStorage、sessionStorage、cookie 或 URL。
 * 刷新页面必然回到未登录态，这是有意行为，不要为体验增加持久化。
 */
let accessToken: string | null = null
const listeners = new Set<() => void>()

export function getAccessToken(): string | null {
  return accessToken
}

export function setAccessToken(token: string): void {
  const next = token.trim()
  if (!next) {
    clearAccessToken()
    return
  }
  accessToken = next
  notify()
}

export function clearAccessToken(): void {
  if (accessToken === null) return
  accessToken = null
  notify()
}

export function subscribeAccessToken(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function notify() {
  for (const listener of listeners) listener()
}
