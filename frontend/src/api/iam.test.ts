import { beforeEach, describe, expect, it, vi } from 'vitest'
import { listIamPrincipals, setPrincipalRoles, setPrincipalScope } from './iam'

function respond(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

beforeEach(() => vi.restoreAllMocks())

describe('IAM client', () => {
  it('sends the three scope fields flat instead of a data_scope wrapper', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(respond({ principal_id: 'user-1' }))

    await setPrincipalScope('user-1', { allowed_part_ids: ['P-1', 'P-2'], allowed_period_start: '2026-01', allowed_period_end: '2026-06' })

    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toBe('/api/iam/principals/user-1/scope')
    expect(init?.method).toBe('PUT')
    const body = JSON.parse(String(init?.body))
    expect(body).toEqual({ allowed_part_ids: ['P-1', 'P-2'], allowed_period_start: '2026-01', allowed_period_end: '2026-06' })
    expect(body).not.toHaveProperty('data_scope')
  })

  it('sends null for an open period bound', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(respond({ principal_id: 'user-1' }))

    await setPrincipalScope('user-1', { allowed_part_ids: [], allowed_period_start: null, allowed_period_end: '2026-06' })

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ allowed_part_ids: [], allowed_period_start: null, allowed_period_end: '2026-06' })
  })

  it('puts the role list as-is', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(respond({ principal_id: 'user-1' }))

    await setPrincipalRoles('user-1', ['viewer', 'data_approver'])

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/iam/principals/user-1/roles')
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ roles: ['viewer', 'data_approver'] })
  })

  it('pages the principal list', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(respond({ items: [], page: 3, page_size: 20, total: 41 }))

    await listIamPrincipals({ page: 3, pageSize: 20 })

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/iam/principals?page=3&page_size=20')
  })
})
