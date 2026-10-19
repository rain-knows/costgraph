import { beforeEach, describe, expect, it, vi } from 'vitest'
import { submitImportJob, listImportJobs, decideImportJob } from './importJobs'

function respond(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

beforeEach(() => vi.restoreAllMocks())

describe('import job client', () => {
  it('sends the idempotency key header and the exact request body', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(respond({ job_id: 'job-1', duplicate: false }))
    const body = { source_system: 'erp', source_file_name: 'snapshot.json', calculation_rule_version: 'cost-v1', payload: { parts: [{ part_id: 'P-1' }] } }

    await submitImportJob(body, 'key-1')

    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toBe('/api/data-admin/imports')
    const headers = init?.headers as Headers
    expect(headers.get('Idempotency-Key')).toBe('key-1')
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(JSON.parse(String(init?.body))).toEqual(body)
  })

  it('encodes list filters and paging into the query string', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(respond({ items: [], page: 2, page_size: 50, total: 0 }))

    await listImportJobs({ status: 'published', page: 2, pageSize: 50 })

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/data-admin/imports?page=2&page_size=50&status=published')
  })

  it('omits the status filter when it is not selected', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(respond({ items: [], page: 1, page_size: 20, total: 0 }))

    await listImportJobs()

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/data-admin/imports?page=1&page_size=20')
  })

  it('posts a rejection with its reason and URL-encodes the job id', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(respond({ job_id: 'job/1' }))

    await decideImportJob('job/1', { decision: 'reject', reason: '行数不匹配' })

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/data-admin/imports/job%2F1/decision')
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ decision: 'reject', reason: '行数不匹配' })
  })
})
