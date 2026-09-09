export function cn(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(' ')
}

export const formatPreciseMoney = (value: number | string, digits = 2) =>
  new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY', minimumFractionDigits: digits, maximumFractionDigits: digits }).format(Number(value))

export const formatNumber = (value: number, digits = 0) =>
  new Intl.NumberFormat('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value)

export const formatDecimal = (value: number | string, digits = 0) =>
  new Intl.NumberFormat('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(Number(value))

export const formatPercent = (value: number | string) => `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(1)}%`
