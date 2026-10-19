import { useSyncExternalStore } from 'react'
import { getAccessToken, subscribeAccessToken } from './tokenStore'

export function useAccessToken(): string | null {
  return useSyncExternalStore(subscribeAccessToken, getAccessToken, getAccessToken)
}
