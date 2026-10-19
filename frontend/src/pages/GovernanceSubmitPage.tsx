import { useState } from 'react'
import { Link, useNavigate, useOutletContext } from 'react-router-dom'
import { isForbidden } from '../api/http'
import { submitImportJob, type ImportJobSubmission } from '../api/importJobs'
import { hasPermission } from '../auth/permissions'
import type { AppOutletContext } from '../components/AppShell'
import { Badge, Button, PageHeader, StatePanel } from '../components/ui'
import { formatDateTime, formatNumber } from '../lib/utils'

type Payload = Record<string, Array<Record<string, unknown>>>

const DEFAULT_RULE_VERSION = 'cost-v1'
const STEPS = ['来源', '载荷', '预览与提交'] as const

type ParsedPayload = { payload: Payload; byteLength: number; sha256: string }

/** 只校验结构，不渲染行内容；服务端才是权威边界。 */
function parsePayload(text: string): Payload {
  let value: unknown
  try {
    value = JSON.parse(text)
  } catch {
    throw new Error('文件不是合法的 JSON，请检查格式。')
  }
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('载荷必须是「表名 → 记录数组」的 JSON 对象。')
  }
  const payload: Payload = {}
  for (const [table, rows] of Object.entries(value as Record<string, unknown>)) {
    if (!Array.isArray(rows)) throw new Error(`表 ${table} 的取值不是数组。`)
    if (rows.some((row) => typeof row !== 'object' || row === null || Array.isArray(row))) {
      throw new Error(`表 ${table} 中存在不是对象的记录。`)
    }
    payload[table] = rows as Array<Record<string, unknown>>
  }
  if (!Object.keys(payload).length) throw new Error('载荷中至少需要一个表。')
  return payload
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const buffer = new ArrayBuffer(bytes.byteLength)
  new Uint8Array(buffer).set(bytes)
  const digest = await crypto.subtle.digest('SHA-256', buffer)
  return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, '0')).join('')
}

export function GovernanceSubmitPage() {
  const { period, me, meLoading, meError } = useOutletContext<AppOutletContext>()
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [sourceSystem, setSourceSystem] = useState('')
  const [fileName, setFileName] = useState('')
  const [ruleVersion, setRuleVersion] = useState(DEFAULT_RULE_VERSION)
  const [parsed, setParsed] = useState<ParsedPayload | null>(null)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const [submission, setSubmission] = useState<ImportJobSubmission | null>(null)
  const [idempotencyKey, setIdempotencyKey] = useState('')
  const permitted = hasPermission(me?.permissions, 'data_import.submit')

  if (meLoading) return <StatePanel state="loading" detail="正在读取当前主体权限。" />
  if (meError) return <StatePanel state="unavailable" detail="无法读取当前主体的权限摘要。" />
  if (!permitted) return <StatePanel state="unauthorized" title="无权提交导入任务" detail="当前主体没有 data_import.submit 权限。" />

  const sourceValid = sourceSystem.trim().length > 0 && fileName.trim().length > 0 && ruleVersion.trim().length > 0

  async function chooseFile(file: File | undefined) {
    setError('')
    setParsed(null)
    // 换了载荷就是新的一次提交尝试，幂等键必须重新生成。
    setIdempotencyKey('')
    if (!file) { setFileName(''); return }
    setFileName(file.name)
    try {
      const bytes = new Uint8Array(await file.arrayBuffer())
      const payload = parsePayload(new TextDecoder().decode(bytes))
      setParsed({ payload, byteLength: bytes.byteLength, sha256: await sha256Hex(bytes) })
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '读取文件失败。')
    }
  }

  async function submit() {
    if (!parsed) return
    const key = idempotencyKey || crypto.randomUUID()
    setIdempotencyKey(key)
    setPending(true)
    setError('')
    try {
      const result = await submitImportJob({
        source_system: sourceSystem.trim(),
        source_file_name: fileName.trim(),
        calculation_rule_version: ruleVersion.trim(),
        payload: parsed.payload,
      }, key)
      setSubmission(result)
    } catch (failure: unknown) {
      if (isForbidden(failure)) setError('当前主体没有提交导入任务的权限。')
      else setError(failure instanceof Error ? failure.message : '提交导入任务失败')
    } finally {
      setPending(false)
    }
  }

  if (submission) {
    return <div>
      <PageHeader title="导入任务已提交" />
      <section className="px-4 py-6" aria-label="提交结果">
        <div className="max-w-2xl">
          <Badge tone={submission.duplicate ? 'warning' : 'success'}>{submission.duplicate ? '已存在相同快照的任务' : '已创建新任务'}</Badge>
          <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-sm mobile-stack">
            <div><dt className="label">任务 ID</dt><dd className="num break-all">{submission.job_id}</dd></div>
            <div><dt className="label">状态</dt><dd>{submission.status}</dd></div>
            <div><dt className="label">快照哈希</dt><dd className="num break-all">{submission.snapshot_hash}</dd></div>
            <div><dt className="label">对象 SHA-256</dt><dd className="num break-all">{submission.source_object_sha256}</dd></div>
            <div><dt className="label">对象大小</dt><dd className="num">{formatNumber(submission.source_object_size)} 字节</dd></div>
            <div><dt className="label">总行数</dt><dd className="num">{formatNumber(submission.total_rows)}</dd></div>
            <div><dt className="label">提交时间</dt><dd className="num">{formatDateTime(submission.created_at)}</dd></div>
          </dl>
          {submission.duplicate ? <p className="mt-4 text-sm">已存在相同快照的任务，返回既有任务；本次未重复落盘。</p> : null}
          <div className="mt-5 flex flex-wrap gap-2">
            <Button variant="primary" onClick={() => navigate(`/data-admin/imports/${encodeURIComponent(submission.job_id)}?period=${period}`)}>查看任务详情</Button>
            <Button variant="ghost" onClick={() => navigate(`/data-admin/imports?period=${period}`)}>返回任务列表</Button>
          </div>
          <p className="meta mt-3">详情页：<Link className="underline" to={`/data-admin/imports/${encodeURIComponent(submission.job_id)}?period=${period}`}>/data-admin/imports/{submission.job_id}</Link></p>
        </div>
      </section>
    </div>
  }

  return <div>
    <PageHeader title="提交导入任务" actions={<Button variant="ghost" onClick={() => navigate(`/data-admin/imports?period=${period}`)}>返回列表</Button>} />
    <nav className="flex min-h-14 items-center gap-2 border-b border-[var(--border-soft)] px-4" aria-label="提交步骤">
      {STEPS.map((label, index) => <span key={label} className="flex items-center gap-2">
        {index > 0 ? <span className="meta">/</span> : null}
        <span aria-current={index === step ? 'step' : undefined} className={index === step ? 'text-sm font-semibold' : 'meta'}>{index + 1}. {label}</span>
      </span>)}
    </nav>
    <section className="px-4 py-5" aria-label={`步骤 ${STEPS[step]}`}>
      {step === 0 ? <div className="grid max-w-xl gap-4">
        <label className="grid gap-1"><span className="label">来源系统</span><input value={sourceSystem} onChange={(event) => setSourceSystem(event.target.value)} maxLength={64} placeholder="例如 erp" className="h-10 bg-[var(--surface)] px-3 text-sm" /></label>
        <label className="grid gap-1"><span className="label">源文件名</span><input value={fileName} onChange={(event) => setFileName(event.target.value)} maxLength={128} placeholder="例如 snapshot.json" className="h-10 bg-[var(--surface)] px-3 text-sm" /></label>
        <label className="grid gap-1"><span className="label">计算规则版本</span><input value={ruleVersion} onChange={(event) => setRuleVersion(event.target.value)} maxLength={64} className="h-10 bg-[var(--surface)] px-3 text-sm" /></label>
        <div className="flex gap-2"><Button variant="primary" disabled={!sourceValid} onClick={() => setStep(1)}>下一步</Button></div>
      </div> : null}

      {step === 1 ? <div className="grid max-w-xl gap-3">
        <label className="grid gap-1"><span className="label">载荷文件（JSON）</span>
          <input type="file" accept=".json,application/json" onChange={(event) => void chooseFile(event.target.files?.[0])} className="text-sm" />
        </label>
        <p className="meta">文件仅用于提交，页面不会展示任何原始行内容。</p>
        {error ? <p className="text-sm text-[var(--danger)]" role="alert">{error}</p> : null}
        <div className="flex gap-2">
          <Button variant="ghost" onClick={() => setStep(0)}>上一步</Button>
          <Button variant="primary" disabled={!parsed} onClick={() => setStep(2)}>下一步</Button>
        </div>
      </div> : null}

      {step === 2 ? <div className="grid max-w-xl gap-3">
        {parsed ? <>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm mobile-stack">
            <div><dt className="label">来源</dt><dd>{sourceSystem} · {fileName}</dd></div>
            <div><dt className="label">规则版本</dt><dd className="num">{ruleVersion}</dd></div>
            <div><dt className="label">字节数</dt><dd className="num">{formatNumber(parsed.byteLength)}</dd></div>
            <div><dt className="label">前端计算 SHA-256</dt><dd className="num break-all">{parsed.sha256}</dd></div>
          </dl>
          <table className="data-table" aria-label="载荷表预览"><thead><tr><th>表名</th><th>行数</th></tr></thead>
            <tbody>{Object.entries(parsed.payload).map(([table, rows]) => <tr key={table}><td>{table}</td><td className="num">{formatNumber(rows.length)}</td></tr>)}</tbody></table>
          <p className="meta">前端哈希仅作展示，服务端按落盘对象重新计算并作为权威值。</p>
        </> : <p className="text-sm" role="alert">尚未读取到合法载荷，请返回上一步重新选择文件。</p>}
        {error ? <p className="text-sm text-[var(--danger)]" role="alert">{error}</p> : null}
        <div className="flex gap-2">
          <Button variant="ghost" onClick={() => setStep(1)}>上一步</Button>
          <Button variant="primary" disabled={!parsed || pending} onClick={() => void submit()}>{pending ? '正在提交' : '提交'}</Button>
        </div>
      </div> : null}
    </section>
  </div>
}
