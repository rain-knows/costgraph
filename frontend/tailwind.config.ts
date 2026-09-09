import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['IBM Plex Sans', 'Helvetica Neue', 'Arial', 'sans-serif'],
        mono: ['IBM Plex Mono', 'SF Mono', 'Menlo', 'monospace'],
      },
      transitionDuration: {
        fast: '110ms',
        base: '180ms',
      },
    },
  },
  plugins: [],
} satisfies Config
