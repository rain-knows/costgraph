  import { useEffect, useRef } from 'react'
import type { EChartsOption } from 'echarts'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { AriaComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([BarChart, LineChart, AriaComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent, CanvasRenderer])

type Palette = {
  fg: string
  muted: string
  border: string
  surface: string
  accent: string
  danger: string
  success: string
  bg: string
}

function readPalette(): Palette {
  const style = getComputedStyle(document.documentElement)
  const token = (name: string) => style.getPropertyValue(name).trim()
  return { fg: token('--fg'), muted: token('--muted'), border: token('--border-soft'), surface: token('--surface'), accent: token('--accent'), danger: token('--danger'), success: token('--success'), bg: token('--bg') }
}

export function EChart({ build, ariaLabel, className = 'chart-box' }: { build: (palette: Palette) => EChartsOption; ariaLabel: string; className?: string }) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = echarts.init(ref.current, undefined, { renderer: 'canvas' })
    const render = () => chart.setOption({
      ...build(readPalette()),
      aria: { enabled: true, description: ariaLabel, decal: { show: true } },
    }, true)
    render()
    const resize = new ResizeObserver(() => chart.resize())
    const theme = new MutationObserver(render)
    resize.observe(ref.current)
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => { resize.disconnect(); theme.disconnect(); chart.dispose() }
  }, [ariaLabel, build])

  return <div ref={ref} className={className} role="img" aria-label={ariaLabel} />
}

export const axis = (p: Palette) => ({
  axisLine: { lineStyle: { color: p.border } },
  axisTick: { show: false },
  axisLabel: { color: p.muted, fontFamily: 'IBM Plex Mono', fontSize: 11 },
  splitLine: { lineStyle: { color: p.border } },
})

export const tooltip = (p: Palette) => ({
  trigger: 'axis' as const,
  backgroundColor: p.fg,
  borderWidth: 0,
  textStyle: { color: p.bg, fontFamily: 'IBM Plex Sans', fontSize: 12 },
  axisPointer: { type: 'line' as const, lineStyle: { color: p.accent } },
})
