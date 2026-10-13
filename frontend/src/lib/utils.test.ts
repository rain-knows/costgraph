import { describe, expect, it } from 'vitest'
import { formatPercent, formatQuantity } from './utils'

describe('formatPercent', () => {
  it('renders backend percentage points without scaling them again', () => {
    expect(formatPercent('5.3')).toBe('+5.3%')
    expect(formatPercent('-1.25')).toBe('-1.3%')
  })
})

describe('formatQuantity', () => {
  it('rounds quantity metrics to integer display values', () => {
    expect(formatQuantity('2420.0000')).toBe('2,420')
    expect(formatQuantity('70.4')).toBe('70')
    expect(formatQuantity('70.5')).toBe('71')
  })
})
