import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  // 宿主 shell 若继承 NODE_ENV=production，React 会解析到 production 构建，
  // 导致 @testing-library/react 的 act() 直接失败。测试产物必须与宿主环境无关。
  define: { 'process.env.NODE_ENV': '"test"' },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    restoreMocks: true,
  },
})
