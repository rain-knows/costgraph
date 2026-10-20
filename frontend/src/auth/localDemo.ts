export const LOCAL_DEMO_ACCESS_TOKEN = 'costgraph-local-demo'

/** 只有构建环境明确开启时才允许本地 demo 自动进入工作区。 */
export function isLocalDemoAuthEnabled(): boolean {
  return (import.meta.env.VITE_LOCAL_DEMO_AUTH ?? '').trim().toLowerCase() === 'true'
}
