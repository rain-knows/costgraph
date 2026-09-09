import { describe, expect, it } from 'vitest'
import { formatPercent } from './utils'

describe('formatPercent', () => {
  it('renders backend percentage points without scaling them again', () => {
    expect(formatPercent('5.3')).toBe('+5.3%')
    expect(formatPercent('-1.25')).toBe('-1.3%')
  })
})
