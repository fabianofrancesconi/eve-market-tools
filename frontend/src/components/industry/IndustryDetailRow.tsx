import { useState, useEffect } from 'react'
import { apiGet } from '../../lib/api-client'
import { fmtIsk, fmtNum, fmtDuration } from '../../lib/format'

interface Material {
  name: string
  quantity: number
  unit_price: number
  line_cost: number
  volume: number
}

interface DetailData {
  product_name: string
  tech_level: number
  materials: Material[]
  material_cost: number
  job_install: number
  total_cost: number
  profit_patient: number
  profit_instant: number
  batch_total_cost: number
  batch_profit: number
  batch_build_time: number
  cargo_in: number
  cargo_out: number
  bp_price: number
  me_level: number
  te_level: number
  runs: number
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
  blueprintId, stationId, jobRate, salesTax, broker, runs, colSpan, onClose,
}: Props) {
  const [detail, setDetail] = useState<DetailData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    apiGet<DetailData>('/api/industry/detail', {
      blueprint_id: blueprintId,
      station: stationId,
      job_rate: jobRate,
      sales_tax: salesTax,
      broker,
      runs,
    })
      .then(data => { if (!cancelled) setDetail(data) })
      .catch(e => { if (!cancelled) setError(e.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [blueprintId, stationId, jobRate, salesTax, broker, runs])

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
              {/* Header */}
              <div className="flex items-center gap-2">
                <span className="font-medium text-foreground">{detail.product_name}</span>
                <span className={`text-xs px-1.5 py-0.5 rounded ${
                  detail.tech_level === 2 ? 'bg-accent-gold/20 text-accent-gold' : 'bg-border text-foreground-muted'
                }`}>
                  T{detail.tech_level}
                </span>
              </div>

              {/* Materials table */}
              <div>
                <h4 className="text-xs uppercase text-foreground-muted mb-1 tracking-wide">Materials</h4>
                <div className="overflow-x-auto rounded border border-border">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="bg-background-elevated border-b border-border">
                        <th className="px-2 py-1.5 text-left font-medium text-foreground-muted">Material</th>
                        <th className="px-2 py-1.5 text-right font-medium text-foreground-muted">Qty</th>
                        <th className="px-2 py-1.5 text-right font-medium text-foreground-muted">Unit price</th>
                        <th className="px-2 py-1.5 text-right font-medium text-foreground-muted">Line cost</th>
                        <th className="px-2 py-1.5 text-right font-medium text-foreground-muted">Cargo m3</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.materials.map((m, i) => (
                        <tr key={i} className="border-b border-border/30">
                          <td className="px-2 py-1">{m.name}</td>
                          <td className="px-2 py-1 text-right">{fmtNum(m.quantity)}</td>
                          <td className="px-2 py-1 text-right">{fmtIsk(m.unit_price)}</td>
                          <td className="px-2 py-1 text-right">{fmtIsk(m.line_cost)}</td>
                          <td className="px-2 py-1 text-right">{fmtNum(m.volume, 1)} m&sup3;</td>
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
                      <dt className="text-foreground-muted">Job install</dt>
                      <dd>{fmtIsk(detail.job_install)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-foreground-muted">Total cost</dt>
                      <dd>{fmtIsk(detail.total_cost)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-foreground-muted">Profit (list)</dt>
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
                  </dl>
                </div>

                {/* Batch */}
                <div>
                  <h4 className="text-xs uppercase text-foreground-muted mb-1 tracking-wide">
                    Batch ({detail.runs} run{detail.runs !== 1 ? 's' : ''})
                  </h4>
                  <dl className="space-y-0.5">
                    <div className="flex justify-between">
                      <dt className="text-foreground-muted">Total cost</dt>
                      <dd>{fmtIsk(detail.batch_total_cost)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-foreground-muted">Total profit</dt>
                      <dd className={detail.batch_profit > 0 ? 'text-positive' : 'text-negative'}>
                        {fmtIsk(detail.batch_profit)}
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-foreground-muted">Build time</dt>
                      <dd>{fmtDuration(detail.batch_build_time)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-foreground-muted">Cargo in</dt>
                      <dd>{fmtNum(detail.cargo_in, 1)} m&sup3;</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-foreground-muted">Cargo out</dt>
                      <dd>{fmtNum(detail.cargo_out, 1)} m&sup3;</dd>
                    </div>
                  </dl>
                </div>
              </div>

              {/* Blueprint info */}
              <div>
                <h4 className="text-xs uppercase text-foreground-muted mb-1 tracking-wide">Blueprint</h4>
                <div className="flex gap-4 text-sm">
                  <span className="text-foreground-muted">BPO price: <span className="text-foreground">{fmtIsk(detail.bp_price)}</span></span>
                  <span className="text-foreground-muted">ME: <span className="text-foreground">{detail.me_level}</span></span>
                  <span className="text-foreground-muted">TE: <span className="text-foreground">{detail.te_level}</span></span>
                </div>
              </div>
            </div>
          )}
        </div>
      </td>
    </tr>
  )
}
