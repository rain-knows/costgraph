import type {
  DecimalValue,
  FinishedBatchCostSummary,
  ManufacturingCostView,
  MaterialLaborOverheadView,
  VariableFixedCostView,
} from './costData'

export type CapabilityId = 'system_help' | 'cost_calculation'
export type RouteId = CapabilityId | 'blocked'
export type RoutingMode = 'auto' | 'manual'
export type RunOutcome = 'completed' | 'needs_clarification' | 'blocked' | 'failed'

export type Clarification = {
  id: string
  question: string
  missing_slots: string[]
  invalid_slots?: string[]
  options: Array<{ label: string; value: string }>
}

export type AgentEvent = {
  node: string
  status: string
  summary: string
  started_at?: string | null
  finished_at?: string | null
}

export type ReportJson = {
  report_schema_version: '2.0'
  rule_version: string
  prompt_version: string
  code_version: string
  data_snapshot_id: string
  run_id: string
  part: {
    part_id: string
    part_number: string
    part_description: string
    part_type: 'raw_material' | 'purchased_semi_finished' | 'work_in_progress' | 'finished_good'
    product_family: string | null
  }
  period: string
  date_range?: { start_date: string; end_date: string } | null
  batch_summary: {
    batch_count: number
    completed_quantity: DecimalValue
    qualified_quantity: DecimalValue
    defective_quantity: DecimalValue
    quality_rate: DecimalValue
  }
  summary_cards: Array<{ label: string; value: string | number; unit: string }>
  manufacturing_view: ManufacturingCostView
  material_labor_overhead_view: MaterialLaborOverheadView
  variable_fixed_view: VariableFixedCostView
  finished_batches: FinishedBatchCostSummary[]
  insight_cards?: Array<{ label: string; value: string; description: string }>
  calculation_formula: string[]
  calculation_policy: Record<string, string>
  analysis_text: string
  source_summary: string
  lineage: {
    schema_version?: '2.0'
    source: string
    query_scope?: Record<string, unknown>
    data_snapshot_id: string
    tables: Array<{ name: string; record_count: number; record_id_sample: string[] }>
  }
  model_info: Record<string, unknown>
  ai_trace: Record<string, unknown>
  agent_steps: AgentEvent[]
}

export type AgentStatusBar = {
  schema_version: number
  run_id: string
  run_state: 'running' | 'waiting_for_user' | 'completed' | 'blocked' | 'failed' | 'cancelled'
  phase: string
  goal: string
  current_step: string
  completed_steps: number
  total_steps: number
  todo: Array<{ id: string; label: string; status: 'done' | 'current' | 'pending' }>
  capabilities: Array<{ id: string; label: string; enabled: boolean; mode: string }>
  routing_mode: RoutingMode
  requested_route: RouteId
  effective_route: RouteId
  route_reason: string
  outcome?: RunOutcome | null
  missing_slots?: string[]
  clarification?: Clarification | null
  environment: Record<string, string>
  authorization: {
    principal_id: string
    tenant_id: string
    requested_capabilities: CapabilityId[]
    authorized_capabilities: CapabilityId[]
    server_allowed_capabilities: CapabilityId[]
    effective_capabilities: CapabilityId[]
    data_scope: {
      source: string
      allowed_part_ids: string[]
      allowed_period_start?: string | null
      allowed_period_end?: string | null
    }
  }
  last_event?: AgentEvent | null
  updated_at: string
}

export type AgentRunResponse = {
  run_id: string
  conversation_id?: string | null
  turn_id?: string | null
  message_id?: string | null
  final_message: string
  report_json: ReportJson | null
  events: AgentEvent[]
  status_bar?: AgentStatusBar | null
  outcome: RunOutcome
  clarification?: Clarification | null
  context_used?: Record<string, unknown>
}

export type RuntimeSemanticEvent = AgentEvent & {
  schema_version?: string
  sequence?: number
  kind?: string
  name?: string | null
  error_code?: string | null
  budget?: Record<string, unknown> | null
  status_bar?: AgentStatusBar | null
  result?: AgentRunResponse | null
  clarification?: Clarification | null
  request_id?: string | null
}

export type AgentStreamMessage =
  | { type: 'runtime_event'; event: RuntimeSemanticEvent }
  | { type: 'result'; result: AgentRunResponse }
  | { type: 'error'; code?: string; message: string; request_id?: string }

export type HealthStatus = {
  status: 'ok' | 'degraded'
  service: string
  app_env: string
  app_version: string
  database: string
  worker: string
  execution_backend: string
  durable_runs: boolean
  runtime_ready: boolean
}
