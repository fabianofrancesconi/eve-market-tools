import { useState, useEffect, useCallback } from 'react'
import { apiGet } from '../../lib/api-client'
import { fmtIsk, fmtNum, fmtDuration, fmtPct } from '../../lib/format'

interface Material {
  type_id: number
  name: string
  base_qty: number
  eff_qty: number
  unit_price: number | null
  line_cost: number | null
  volume_each?: number | null
}

interface Datacore {
  name: string
  quantity: number
  unit_price: number | null
}

interface InventionData {
  probability: number
  runs_per_bpc: number
  datacores: Datacore[]
  cost_per_run: number
}

interface MissingSkill {
  skill_id: number
  name: string
  required: number
  current: number
  train_hours: number
  prereq: boolean
}

interface ProductInfo {
  type_id: number
  name: string
  quantity: number
  volume_each: number | null
}

interface DetailData {
  product: ProductInfo
  tech_level: number
  required_items: Material[]
  material_cost: number
  eiv: number
  job_cost: number
  total_cost: number
  profit_patient: number
  profit_instant: number
  build_time: number
  ask: number | null
  bid: number | null
  bp_price: number | null
  bp_source: string
  bp_available: boolean
  payback_runs: number | null
  me_used: number
  te_used: number
  owned_bp_me_te: string | null
  invention: InventionData | null
  missing_skills: MissingSkill[] | null
}

interface Props {
  blueprintId: number
  stationId: number
  jobRate: number
  salesTax: number
  broker: number
  runs: number
  colSpan: number
  onClose: () => void
}

export function IndustryDetailRow({
  blueprintId, stationId, jobRate, salesTax, broker, runs: initialRuns, colSpan, onClose,
}: Props) {
  const [detail, setDetail] = useState<DetailData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [localRuns, setLocalRuns] = useState(initialRuns)
  const [copied, setCopied] = useState(false)

  const fetchDetail = useCallback((runsValue: number) => {
    setLoading(true)
    setError(null)
    apiGet<DetailData>('/api/industry/detail', {
      blueprint_id: blueprintId,
      station_id: stationId,
      me: 0,
      te: 0,
      job_rate: jobRate / 100,
      sales_tax: salesTax / 100,
      broker_fee: broker / 100,
      runs: runsValue,
    })
      .then(data => setDetail(data))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [blueprintId, stationId, jobRate, salesTax, broker])

  useEffect(() => {
    fetchDetail(localRuns)
  }, [fetchDetail, localRuns])

  const handleRunsChange = (value: number) => {
    const clamped = Math.max(1, Math.min(value, 10000))
    setLocalRuns(clamped)
  }

  const copyProductName = () => {
    if (!detail) return
    navigator.clipboard.writeText(detail.product.name).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  // Batch calculations
  const batchTotalCost = detail ? detail.total_cost * localRuns : 0
  const batchProfitPatient = detail ? detail.profit_patient * localRuns : 0
  const batchBuildTime = detail ? detail.build_time * localRuns : 0

  return (
    <tr className="border-b border-border/50 bg-background-elevated/30">
      <td colSpan={colSpan} className="px-4 py-3">
        <div className="relative">
          <button
            onClick={onClose}
            className="absolute top-0 right-0 text-foreground-muted hover:text-foreground text-lg leading-none"
            aria-label="Close detail"
          >
            &times;
          </button>

          {loading && (
            <div className="flex items-center gap-2 text-sm text-foreground-muted py-4">
              <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
              </svg>
              Loading detail...
            </div>
          )}

          {error && <p className="text-sm text-negative py-2">{error}</p>}

          {detail && (
            <div className="space-y-4">
              {/* Header with copy button */}
              <div className="flex items-center gap-2">
                <span className="font-medium text-foreground">{detail.product.name}</span>
                <button
                  onClick={copyProductName}
                  className="text-foreground-muted hover:text-accent-cyan transition-colors"
                  title="Copy product name"
                  aria-label="Copy product name"
                >
                  {copied ? (
                    <svg className="h-4 w-4 text-positive" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  ) : (
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                    </svg>
                  )}
                </button>
                <span className={`text-xs px-1.5 py-0.5 rounded ${
                  detail.tech_level === 2 ? 'bg-accent-gold/20 text-accent-gold' : 'bg-border text-foreground-muted'
                }`}>
                  T{detail.tech_level}
                </span>
              </div>

              {/* Runs input with presets */}
              <div className="flex items-center gap-3">
                <label className="text-xs uppercase text-foreground-muted tracking-wide">Runs</label>
                <div className="flex items-center gap-1">
                  {[1, 10, 100].map(preset => (
                    <button
                      key={preset}
                      onClick={() => handleRunsChange(preset)}
                      className={`px-2 py-0.5 text-xs rounded border transition-colors ${
                        localRuns === preset
                          ? 'border-accent-cyan bg-accent-cyan/10 text-accent-cyan'
                          : 'border-border text-foreground-muted hover:border-foreground-muted hover:text-foreground'
                      }`}
                    >
                      {preset}
                    </button>
                  ))}
                </div>
                <input
                  type="number"
                  min={1}
                  max={10000}
                  value={localRuns}
                  onChange={e => handleRunsChange(parseInt(e.target.value) || 1)}
                  className="w-20 px-2 py-0.5 text-xs rounded border border-border bg-background-elevated text-foreground text-right focus:outline-none focus:border-accent-cyan"
                />
              </div>

              {/* Materials table */}
              <div>
                <h4 className="text-xs uppercase text-foreground-muted mb-1 tracking-wide">Materials</h4>
                <div className="overflow-x-auto rounded border border-border">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="bg-background-elevated border-b border-border">
                        <th className="px-2 py-1.5 text-left font-medium text-foreground-muted">Material</th>
                        <th className="px-2 py-1.5 text-right font-medium text-foreground-muted">Base qty</th>
                        <th className="px-2 py-1.5 text-right font-medium text-foreground-muted">Eff qty</th>
                        <th className="px-2 py-1.5 text-right font-medium text-foreground-muted">Batch qty</th>
                        <th className="px-2 py-1.5 text-right font-medium text-foreground-muted">Unit price</th>
                        <th className="px-2 py-1.5 text-right font-medium text-foreground-muted">Line cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.required_items.map((m, i) => (
                        <tr key={i} className="border-b border-border/30">
                          <td className="px-2 py-1">{m.name}</td>
                          <td className="px-2 py-1 text-right text-foreground-muted">{fmtNum(m.base_qty)}</td>
                          <td className="px-2 py-1 text-right">{fmtNum(m.eff_qty)}</td>
                          <td className="px-2 py-1 text-right text-accent-cyan">{fmtNum(m.eff_qty * localRuns)}</td>
                          <td className="px-2 py-1 text-right">{fmtIsk(m.unit_price)}</td>
                          <td className="px-2 py-1 text-right">{fmtIsk(m.line_cost)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Stats grid */}
              <div className="grid grid-cols-2 gap-4 text-sm">
                {/* Per-run */}
                <div>
                  <h4 className="text-xs uppercase text-foreground-muted mb-1 tracking-wide">Per run</h4>
                  <dl className="space-y-0.5">
                    <div className="flex justify-between">
                      <dt className="text-foreground-muted">Material cost</dt>
                      <dd>{fmtIsk(detail.material_cost)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-foreground-muted">Job cost</dt>
                      <dd>{fmtIsk(detail.job_cost)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-foreground-muted">Total cost</dt>
                      <dd>{fmtIsk(detail.total_cost)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-foreground-muted">Profit (patient)</dt>
                      <dd className={detail.profit_patient > 0 ? 'text-positive' : 'text-negative'}>
                        {fmtIsk(detail.profit_patient)}
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-foreground-muted">Profit (instant)</dt>
                      <dd className={detail.profit_instant > 0 ? 'text-positive' : 'text-negative'}>
                        {fmtIsk(detail.profit_instant)}
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-foreground-muted">Build time</dt>
                      <dd>{fmtDuration(detail.build_time)}</dd>
                    </div>
                  </dl>
                </div>

                {/* Batch */}
                <div>
                  <h4 className="text-xs uppercase text-foreground-muted mb-1 tracking-wide">
                    Batch ({localRuns} run{localRuns !== 1 ? 's' : ''})
                  </h4>
                  <dl className="space-y-0.5">
                    <div className="flex justify-between">
                      <dt className="text-foreground-muted">Total cost</dt>
                      <dd>{fmtIsk(batchTotalCost)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-foreground-muted">Total profit (patient)</dt>
                      <dd className={batchProfitPatient > 0 ? 'text-positive' : 'text-negative'}>
                        {fmtIsk(batchProfitPatient)}
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-foreground-muted">Build time</dt>
                      <dd>{fmtDuration(batchBuildTime)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-foreground-muted">Ask (sell)</dt>
                      <dd>{fmtIsk(detail.ask)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-foreground-muted">Bid (buy)</dt>
                      <dd>{fmtIsk(detail.bid)}</dd>
                    </div>
                  </dl>
                </div>
              </div>

              {/* Blueprint info */}
              <div>
                <h4 className="text-xs uppercase text-foreground-muted mb-1 tracking-wide">Blueprint</h4>
                <div className="grid grid-cols-2 gap-x-6 gap-y-0.5 text-sm">
                  <div className="flex justify-between">
                    <span className="text-foreground-muted">Source</span>
                    <span className={`capitalize ${
                      detail.bp_source === 'market' ? 'text-foreground' :
                      detail.bp_source === 'invention' ? 'text-accent-gold' :
                      'text-foreground-muted'
                    }`}>
                      {detail.bp_source || 'none'}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-foreground-muted">Owned</span>
                    <span className={detail.bp_available ? 'text-positive' : 'text-foreground-muted'}>
                      {detail.bp_available ? 'Yes' : 'No'}
                      {detail.owned_bp_me_te && ` (${detail.owned_bp_me_te})`}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-foreground-muted">ME / TE</span>
                    <span className="text-foreground">{detail.me_used} / {detail.te_used}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-foreground-muted">BPO price</span>
                    <span className="text-foreground">{fmtIsk(detail.bp_price)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-foreground-muted">Payback runs</span>
                    <span className={`${
                      (detail.payback_runs ?? 0) > 0 && (detail.payback_runs ?? 0) < 50
                        ? 'text-positive'
                        : (detail.payback_runs ?? 0) >= 50
                          ? 'text-accent-gold'
                          : 'text-foreground-muted'
                    }`}>
                      {detail.payback_runs ? fmtNum(detail.payback_runs) : 'N/A'}
                    </span>
                  </div>
                </div>
              </div>

              {/* Invention section (T2 only) */}
              {detail.invention && (
                <div>
                  <h4 className="text-xs uppercase text-foreground-muted mb-1 tracking-wide">Invention</h4>
                  <div className="grid grid-cols-2 gap-x-6 gap-y-0.5 text-sm mb-2">
                    <div className="flex justify-between">
                      <span className="text-foreground-muted">Success probability</span>
                      <span className="text-accent-cyan">{fmtPct(detail.invention.probability * 100)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-foreground-muted">Runs per BPC</span>
                      <span className="text-foreground">{detail.invention.runs_per_bpc}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-foreground-muted">Cost per run</span>
                      <span className="text-foreground">{fmtIsk(detail.invention.cost_per_run)}</span>
                    </div>
                  </div>
                  {detail.invention.datacores.length > 0 && (
                    <div className="overflow-x-auto rounded border border-border">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="bg-background-elevated border-b border-border">
                            <th className="px-2 py-1.5 text-left font-medium text-foreground-muted">Datacore</th>
                            <th className="px-2 py-1.5 text-right font-medium text-foreground-muted">Qty</th>
                            <th className="px-2 py-1.5 text-right font-medium text-foreground-muted">Unit price</th>
                            <th className="px-2 py-1.5 text-right font-medium text-foreground-muted">Total</th>
                          </tr>
                        </thead>
                        <tbody>
                          {detail.invention.datacores.map((dc, i) => (
                            <tr key={i} className="border-b border-border/30">
                              <td className="px-2 py-1">{dc.name}</td>
                              <td className="px-2 py-1 text-right">{dc.quantity}</td>
                              <td className="px-2 py-1 text-right">{fmtIsk(dc.unit_price)}</td>
                              <td className="px-2 py-1 text-right">{fmtIsk(dc.quantity * (dc.unit_price ?? 0))}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}

              {/* Missing skills */}
              {detail.missing_skills && detail.missing_skills.length > 0 && (
                <div>
                  <h4 className="text-xs uppercase text-foreground-muted mb-1 tracking-wide">
                    Missing Skills
                    <span className="ml-2 text-negative font-normal normal-case">
                      ({detail.missing_skills.length} skill{detail.missing_skills.length !== 1 ? 's' : ''} needed)
                    </span>
                  </h4>
                  <div className="overflow-x-auto rounded border border-border">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="bg-background-elevated border-b border-border">
                          <th className="px-2 py-1.5 text-left font-medium text-foreground-muted">Skill</th>
                          <th className="px-2 py-1.5 text-right font-medium text-foreground-muted">Required</th>
                          <th className="px-2 py-1.5 text-right font-medium text-foreground-muted">Current</th>
                          <th className="px-2 py-1.5 text-right font-medium text-foreground-muted">Train time</th>
                          <th className="px-2 py-1.5 text-center font-medium text-foreground-muted">Type</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detail.missing_skills.map((skill, i) => (
                          <tr key={i} className="border-b border-border/30">
                            <td className="px-2 py-1 text-foreground">{skill.name}</td>
                            <td className="px-2 py-1 text-right text-negative">Lv {skill.required}</td>
                            <td className="px-2 py-1 text-right text-foreground-muted">
                              {skill.current > 0 ? `Lv ${skill.current}` : '-'}
                            </td>
                            <td className="px-2 py-1 text-right">
                              {skill.train_hours > 0 ? `${fmtNum(skill.train_hours, 1)}h` : '-'}
                            </td>
                            <td className="px-2 py-1 text-center">
                              <span className={`text-xs px-1 py-0.5 rounded ${
                                skill.prereq
                                  ? 'bg-accent-gold/20 text-accent-gold'
                                  : 'bg-border text-foreground-muted'
                              }`}>
                                {skill.prereq ? 'prereq' : 'direct'}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </td>
    </tr>
  )
}
