import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { listArtifacts, restoreArtifact, getArtifact } from '../api/artifacts'
import type { ReportJson } from '../api/agent'
import { finishedBatch } from '../test/costFixtures'
import { formatChartMoney, ReportsPage } from './ReportsPage'

vi.mock('../api/artifacts', () => ({ getArtifact: vi.fn(), listArtifacts: vi.fn(), restoreArtifact: vi.fn(), trashArtifact: vi.fn() }))
vi.mock('../components/Charts', () => ({ axis: () => ({}), tooltip: () => ({}), EChart: ({ ariaLabel }: { ariaLabel: string }) => <div role="img" aria-label={ariaLabel} /> }))
const listArtifactsMock = vi.mocked(listArtifacts)
const getArtifactMock = vi.mocked(getArtifact)
const restoreArtifactMock = vi.mocked(restoreArtifact)
const artifact = {
  artifact_id: 'artifact-1',
  artifact_type: 'cost_report' as const,
  conversation_id: 'conv-1',
  turn_id: 'turn-1',
  message_id: 'message-1',
  run_id: 'run-1',
  conversation_title: '成品外壳成本',
  part_id: 'part-1',
  part_number: 'FG-001',
  part_description: '成品外壳总成',
  period: '2026-06',
  report_sha256: 'hash',
  data_snapshot_id: 'snapshot',
  report_schema_version: '3.0',
  rule_version: '1',
  prompt_version: '1',
  code_version: '1',
  created_at: '2026-07-01T00:00:00Z',
  deleted_at: '2026-07-02T00:00:00Z',
}

const report: ReportJson = {
  report_schema_version: '3.0',
  report_style: 'presentation',
  rule_version: 'rule-1',
  prompt_version: 'prompt-1',
  code_version: 'code-1',
  data_snapshot_id: 'snapshot',
  run_id: 'run-1',
  part: finishedBatch.part,
  period: '2026-06',
  comparison: null,
  batch_summary: { batch_count: 1, completed_quantity: '1000.0000', qualified_quantity: '950.0000', defective_quantity: '50.0000', quality_rate: '95.00' },
  summary_cards: [
    { label: '合计单位成本2', value: '53.50', unit: '元/件' },
    { label: '制造成本', value: '50000.00', unit: '元' },
    { label: '合格数量', value: '950.0000', unit: '件' },
  ],
  manufacturing_view: finishedBatch.manufacturing_view,
  material_labor_overhead_view: finishedBatch.material_labor_overhead_view,
  variable_fixed_view: finishedBatch.variable_fixed_view,
  finished_batches: [finishedBatch],
  insight_cards: [{ label: '材料成本', value: '20.00 元/件', description: '材料成本占制造成本40.00%。' }],
  calculation_formula: ['total = material + labor + overhead'],
  calculation_policy: { rounding: 'ROUND_HALF_UP' },
  analysis_text: '本期完成1个批次，合格率为95.00%。',
  source_summary: '来自当前已发布成本快照。',
  lineage: { source: 'postgresql_cost_data', data_snapshot_id: 'snapshot', tables: [{ name: 'cost_records', record_count: 8, record_id_sample: ['record-1'] }] },
  model_info: {},
  ai_trace: {},
  agent_steps: [],
}

const baselineBatch = { ...finishedBatch, finished_batch_id: 'batch-baseline-1', period: '2026-05' }
const comparisonReport: ReportJson = {
  ...report,
  report_style: 'period_comparison',
  comparison: {
    baseline_period: '2026-05',
    current_period: '2026-06',
    baseline_batch_summary: report.batch_summary,
    baseline_manufacturing_view: report.manufacturing_view,
    baseline_material_labor_overhead_view: report.material_labor_overhead_view,
    baseline_variable_fixed_view: report.variable_fixed_view,
    baseline_finished_batches: [baselineBatch],
    headline_metrics: [{ metric_id: 'total_unit_cost_2', label: '合计单位成本2', unit: '元/件', baseline_value: '50.00', current_value: '53.50', delta: '3.50', change_rate: '7.00' }],
    manufacturing_groups: [{ metric_id: 'direct_material', label: '直接材料', unit: '元/件', baseline_value: '18.00', current_value: '20.00', delta: '2.00', change_rate: '11.11' }],
  },
}

describe('ReportsPage v3', () => {
  it('keeps chart cost labels at the same two-decimal precision as cost values', () => {
    expect(formatChartMoney(20)).toBe('20.00')
    expect(formatChartMoney('53.5')).toBe('53.50')
  })

  beforeEach(() => {
    listArtifactsMock.mockResolvedValue({ items: [artifact], total: 1, limit: 20, offset: 0 })
    getArtifactMock.mockResolvedValue({ ...artifact, report_json: report })
    restoreArtifactMock.mockResolvedValue({ ...artifact, deleted_at: null })
  })

  it('renders the presentation report hierarchy and server-provided values', async () => {
    render(<MemoryRouter initialEntries={['/reports/artifact-1?period=2026-06']}><Routes><Route element={<Outlet context={{ period: '2026-06' }} />}><Route path="/reports/:artifactId" element={<ReportsPage />} /></Route></Routes></MemoryRouter>)

    expect(await screen.findByRole('region', { name: '展示型报表核心指标' })).toBeInTheDocument()
    expect(screen.getByText('合计单位成本2')).toBeInTheDocument()
    expect(screen.getByText('53.50 元/件')).toBeInTheDocument()
    expect(screen.getByText('本期完成1个批次，合格率为95.00%。')).toBeInTheDocument()
    expect(screen.getByText('分析结论')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: '成品外壳总成 批次料工费构成' })).toBeInTheDocument()
    expect(screen.getByText('950')).toBeInTheDocument()
    expect(screen.getByText('可审计元数据')).toBeInTheDocument()
  })

  it('keeps the period comparison presentation path intact', async () => {
    getArtifactMock.mockResolvedValueOnce({ ...artifact, report_json: comparisonReport })
    render(<MemoryRouter initialEntries={['/reports/artifact-1?period=2026-06']}><Routes><Route element={<Outlet context={{ period: '2026-06' }} />}><Route path="/reports/:artifactId" element={<ReportsPage />} /></Route></Routes></MemoryRouter>)

    expect(await screen.findByRole('heading', { name: '成品外壳总成 周期对比报表' })).toBeInTheDocument()
    expect(screen.getByText('六类制造成本单位对比')).toBeInTheDocument()
    expect(screen.getByText('制造成本差额明细')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: '成品外壳总成 六类制造成本周期对比' })).toBeInTheDocument()
  })

  it('restores an artifact from the real trash view', async () => {
    render(<MemoryRouter initialEntries={['/reports?period=2026-06&scope=trashed']}><Routes><Route element={<Outlet context={{ period: '2026-06' }} />}><Route path="/reports" element={<ReportsPage />} /></Route></Routes></MemoryRouter>)
    await userEvent.click(await screen.findByRole('button', { name: '恢复 成品外壳总成 报表' }))
    await waitFor(() => expect(restoreArtifactMock).toHaveBeenCalledWith('artifact-1'))
    expect(listArtifactsMock).toHaveBeenCalledWith(expect.objectContaining({ state: 'trashed', period: '2026-06' }))
  })
})
