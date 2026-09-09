export type Money = number | string
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

export type ProcessCost = {
  process_name: string
  total_cost: Money
  material_cost: Money
  labor_cost: Money
  equipment_cost: Money
  energy_cost: Money
  overhead_cost: Money
}

export type ReportJson = {
  report_schema_version: string
  rule_version: string
  prompt_version: string
  code_version: string
  data_snapshot_id: string
  run_id: string
  product: { product_id: string; product_name: string; spec?: string | null }
  period: string
  date_range?: { start_date: string; end_date: string } | null
  summary_cards: Array<{ label: string; value: Money; unit: string }>
  process_cost_breakdown: ProcessCost[]
  cost_composition_chart: Array<{ name: string; value: Money }>
  insight_cards?: Array<{ label: string; value: string; description: string }>
  comparison?: {
    previous_period: string
    current_unit_cost: Money
    previous_unit_cost: Money
    unit_cost_delta: Money
    unit_cost_delta_rate: Money
  } | null
  calculation_formula: string[]
  calculation_policy: Record<string, string>
  analysis_text: string
  source_summary: string
  lineage: {
    source: string
    data_snapshot_id: string
    tables: Array<{ name: string; record_count: number; record_id_sample: string[] }>
  }
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
      allowed_product_ids: string[]
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
