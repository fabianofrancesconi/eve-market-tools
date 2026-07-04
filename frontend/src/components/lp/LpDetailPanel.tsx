import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../../lib/api-client'
import { fmtIsk, fmtNum, fmtAge, fmtPct } from '../../lib/format'
import { PriceChart, stationToRegion } from '../shared/PriceChart'

interface RequiredItem {
  name: string
  type_id: number
  qty: number
  unit_price: number
  line_cost: number
}

interface SellOrderStats {
  best_price: number
  age_seconds: number
  orders_at_best: number
  sell_orders_total: number
}

interface DetailData {
  offer_id: number
  name: string
  name_id: number
  qty: number
  lp_cost: number
  isk_cost: number
  required_items: RequiredItem[]
  acquisition_cost: number
  sell_value_patient: number
  sell_value_instant: number
  tax_patient: number
  tax_instant: number
  broker_patient: number
  broker_instant: number
  net_patient: number
  net_instant: number
  profit_patient: number
  profit_instant: number
  // New fields from enhanced detail API
  daily_vol: number | null
  days_to_clear: number | null
  fair_price: number | null
  suggested_list: number | null
  sell_order_stats: SellOrderStats | null
  required_books: Record<string, [number, number][]>
  output_buy_book: [number, number][]
  high_spread_pct: number
}

interface LpDetailPanelProps {
  offerId: number
  corpId: number
  lpBudget: number
  stationId: number
  salesTax: number
  brokerFee: number
  onClose: () => void
}

function Section({ title, defaultOpen, children }: { title: string; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen ?? true)
  return (
    <div className="border-t border-border/50 pt-2">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-sm font-medium text-foreground-muted hover:text-foreground w-full text-left"
      >
        <span className="text-xs">{open ? '▼' : '▶'}</span>
        {title}
      </button>
      {open && <div className="mt-2">{children}</div>}
    </div>
  )
}

function KpiCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-background-elevated rounded px-3 py-2 flex-1 min-w-0">
      <div className="text-[10px] uppercase tracking-wide text-foreground-muted truncate">{label}</div>
      <div className={`text-sm font-semibold truncate ${color ?? 'text-foreground'}`}>{value}</div>
    </div>
  )
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // fallback
      const el = document.createElement('textarea')
      el.value = text
      document.body.appendChild(el)
      el.select()
      document.execCommand('copy')
      document.body.removeChild(el)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }

  return (
    <button
      onClick={handleCopy}
      className="ml-2 p-0.5 rounded text-foreground-muted hover:text-accent-cyan transition-colors"
      title="Copy item name"
    >
      {copied ? (
        <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M4 10l4 4 8-8" />
        </svg>
      ) : (
        <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="6" y="6" width="10" height="12" rx="1" />
          <path d="M4 14V4a1 1 0 011-1h8" />
        </svg>
      )}
    </button>
  )
}

export function LpDetailPanel({ offerId, corpId, lpBudget, stationId, salesTax, brokerFee, onClose }: LpDetailPanelProps) {
  const [redemptions, setRedemptions] = useState(1)

  const { data, isLoading, error } = useQuery<DetailData>({
    queryKey: ['lp-detail', offerId, corpId, lpBudget, stationId, salesTax, brokerFee],
    queryFn: () =>
      apiGet<DetailData>(
        `/api/lp/detail?offer_id=${offerId}&corp_id=${corpId}&lp_budget=${lpBudget}&station_id=${stationId}&sales_tax=${salesTax / 100}&broker_fee=${brokerFee / 100}`
      ),
    enabled: offerId > 0 && corpId > 0,
  })

  const regionId = stationToRegion(stationId)

  // Calculate max affordable redemptions
  const maxByLp = data ? Math.floor(lpBudget / data.lp_cost) : 1
  const maxByBuyBook = data?.output_buy_book
    ? (() => {
        const totalVol = data.output_buy_book.reduce((sum, [, vol]) => sum + vol, 0)
        return data.qty > 0 ? Math.floor(totalVol / data.qty) : 999
      })()
    : 999

  return (
    <div className="fixed inset-y-0 right-0 w-[580px] max-w-full bg-background border-l border-border shadow-2xl z-50 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-start justify-between p-4 border-b border-border">
        <div className="min-w-0 flex-1">
          {isLoading ? (
            <div className="h-6 w-48 bg-background-elevated animate-pulse rounded" />
          ) : (
            <>
              <div className="flex items-center gap-1">
                <h2 className="text-lg font-semibold text-accent-cyan truncate">
                  {data?.name ?? 'Loading...'}
                </h2>
                {data?.name && <CopyButton text={data.name} />}
              </div>
              {data && (
                <p className="text-xs text-foreground-muted mt-0.5">
                  {data.qty}&times; per redemption &middot; offer #{data.offer_id}
                </p>
              )}
            </>
          )}
        </div>
        <button
          onClick={onClose}
          className="ml-2 p-1 rounded text-foreground-muted hover:text-foreground hover:bg-background-elevated transition-colors"
          aria-label="Close"
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M5 5l10 10M15 5L5 15" />
          </svg>
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {error && (
          <p className="text-negative text-sm">Failed to load detail: {String(error)}</p>
        )}

        {isLoading && (
          <div className="flex items-center justify-center py-12 text-foreground-muted text-sm">
            Loading offer detail...
          </div>
        )}

        {data && (
          <>
            {/* KPI Summary Cards */}
            <div className="flex gap-2">
              <KpiCard
                label="List Profit"
                value={fmtIsk(data.profit_patient)}
                color={(data.profit_patient ?? 0) >= 0 ? 'text-positive' : 'text-negative'}
              />
              <KpiCard
                label="Instant Profit"
                value={fmtIsk(data.profit_instant)}
                color={(data.profit_instant ?? 0) >= 0 ? 'text-positive' : 'text-negative'}
              />
              <KpiCard
                label="Suggested List"
                value={data.suggested_list != null ? fmtIsk(data.suggested_list) : '-'}
                color="text-accent-gold"
              />
            </div>

            {/* Freshness Info */}
            {data.sell_order_stats && (
              <div className="flex items-center gap-3 text-xs text-foreground-muted bg-background-elevated rounded px-3 py-2">
                <span>
                  Floor age: <span className="text-foreground font-medium">{fmtAge(data.sell_order_stats.age_seconds)}</span>
                </span>
                <span className="text-border">|</span>
                <span>
                  Orders at best: <span className="text-foreground font-medium">{data.sell_order_stats.orders_at_best}</span>
                </span>
                <span className="text-border">|</span>
                <span>
                  Total sell orders: <span className="text-foreground font-medium">{data.sell_order_stats.sell_orders_total}</span>
                </span>
                {data.high_spread_pct > 0 && (
                  <>
                    <span className="text-border">|</span>
                    <span>
                      Spread: <span className="text-accent-gold font-medium">{fmtPct(data.high_spread_pct)}</span>
                    </span>
                  </>
                )}
              </div>
            )}

            {/* Redemptions Input */}
            <div className="flex items-center gap-3 bg-background-elevated rounded px-3 py-2">
              <label className="text-xs text-foreground-muted whitespace-nowrap">Redemptions</label>
              <input
                type="number"
                min={1}
                value={redemptions}
                onChange={(e) => setRedemptions(Math.max(1, parseInt(e.target.value) || 1))}
                className="w-20 bg-background border border-border rounded px-2 py-1 text-sm text-foreground text-center focus:outline-none focus:border-accent-cyan"
              />
              <button
                onClick={() => setRedemptions(maxByLp)}
                className="text-[11px] text-accent-cyan hover:underline"
                title={`Max ${fmtNum(maxByLp)} redemptions from ${fmtNum(lpBudget)} LP`}
              >
                Max LP
              </button>
              <button
                onClick={() => setRedemptions(Math.min(maxByLp, maxByBuyBook))}
                className="text-[11px] text-accent-cyan hover:underline"
                title={`Max ${fmtNum(Math.min(maxByLp, maxByBuyBook))} redemptions from buy book depth`}
              >
                Max Book
              </button>
            </div>

            {/* Price Chart */}
            <Section title="Price History (90d)" defaultOpen={true}>
              <PriceChart typeId={data.name_id} regionId={regionId} />
            </Section>

            {/* Shopping List */}
            <Section title="Shopping List" defaultOpen={true}>
              {data.required_items && data.required_items.length > 0 ? (
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-foreground-muted">
                      <th className="text-left py-1">Item</th>
                      <th className="text-right py-1">Qty</th>
                      <th className="text-right py-1">Unit Price</th>
                      <th className="text-right py-1">Line Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.required_items.map((item) => (
                      <tr key={item.type_id} className="border-t border-border/30">
                        <td className="py-1 text-foreground">{item.name}</td>
                        <td className="py-1 text-right text-foreground-muted">{fmtNum(item.qty * redemptions)}</td>
                        <td className="py-1 text-right text-foreground-muted">{fmtIsk(item.unit_price)}</td>
                        <td className="py-1 text-right">{fmtIsk(item.line_cost * redemptions)}</td>
                      </tr>
                    ))}
                  </tbody>
                  {redemptions > 1 && (
                    <tfoot>
                      <tr className="border-t border-border/50 font-medium">
                        <td className="py-1 text-foreground-muted" colSpan={3}>Total ({redemptions}x)</td>
                        <td className="py-1 text-right">
                          {fmtIsk(data.required_items.reduce((s, i) => s + i.line_cost, 0) * redemptions)}
                        </td>
                      </tr>
                    </tfoot>
                  )}
                </table>
              ) : (
                <p className="text-xs text-foreground-muted">No required items (LP + ISK only)</p>
              )}
            </Section>

            {/* Order Book Depth */}
            {data.output_buy_book && data.output_buy_book.length > 0 && (
              <Section title="Buy Book (Output Item)" defaultOpen={false}>
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-foreground-muted">
                      <th className="text-left py-1">Price</th>
                      <th className="text-right py-1">Volume</th>
                      <th className="text-right py-1">Cumulative</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(() => {
                      let cumVol = 0
                      return data.output_buy_book.slice(0, 10).map(([price, vol], idx) => {
                        cumVol += vol
                        return (
                          <tr key={idx} className="border-t border-border/30">
                            <td className="py-1 text-foreground">{fmtIsk(price)}</td>
                            <td className="py-1 text-right text-foreground-muted">{fmtNum(vol)}</td>
                            <td className="py-1 text-right text-accent-cyan">{fmtNum(cumVol)}</td>
                          </tr>
                        )
                      })
                    })()}
                  </tbody>
                </table>
                {data.output_buy_book.length > 10 && (
                  <p className="text-[10px] text-foreground-muted mt-1">
                    Showing top 10 of {data.output_buy_book.length} price levels
                  </p>
                )}
              </Section>
            )}

            {/* Cost Breakdown */}
            <Section title="Cost Breakdown" defaultOpen={true}>
              <div className="space-y-1 text-xs">
                <div className="flex justify-between">
                  <span className="text-foreground-muted">LP cost</span>
                  <span>{fmtNum(data.lp_cost * redemptions)} LP</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-foreground-muted">ISK cost (base)</span>
                  <span>{fmtIsk(data.isk_cost * redemptions)}</span>
                </div>
                {data.required_items && data.required_items.length > 0 && (
                  <div className="flex justify-between">
                    <span className="text-foreground-muted">Required items</span>
                    <span>{fmtIsk(data.required_items.reduce((s, i) => s + i.line_cost, 0) * redemptions)}</span>
                  </div>
                )}
                <div className="flex justify-between font-medium border-t border-border/50 pt-1">
                  <span className="text-foreground-muted">Total acquisition</span>
                  <span>{fmtIsk(data.acquisition_cost * redemptions)}</span>
                </div>
              </div>
            </Section>

            {/* Profit Breakdown */}
            <Section title="Profit Breakdown" defaultOpen={true}>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-foreground-muted">
                    <th className="text-left py-1"></th>
                    <th className="text-right py-1">List (Patient)</th>
                    <th className="text-right py-1">Instant</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/30">
                  <tr>
                    <td className="py-1 text-foreground-muted">Sell value</td>
                    <td className="py-1 text-right">{fmtIsk(data.sell_value_patient * redemptions)}</td>
                    <td className="py-1 text-right">{fmtIsk(data.sell_value_instant * redemptions)}</td>
                  </tr>
                  <tr>
                    <td className="py-1 text-foreground-muted">- Sales tax</td>
                    <td className="py-1 text-right text-negative">{fmtIsk(data.tax_patient * redemptions)}</td>
                    <td className="py-1 text-right text-negative">{fmtIsk(data.tax_instant * redemptions)}</td>
                  </tr>
                  <tr>
                    <td className="py-1 text-foreground-muted">- Broker fee</td>
                    <td className="py-1 text-right text-negative">{fmtIsk(data.broker_patient * redemptions)}</td>
                    <td className="py-1 text-right text-negative">{fmtIsk(data.broker_instant * redemptions)}</td>
                  </tr>
                  <tr>
                    <td className="py-1 text-foreground-muted">Net revenue</td>
                    <td className="py-1 text-right">{fmtIsk(data.net_patient * redemptions)}</td>
                    <td className="py-1 text-right">{fmtIsk(data.net_instant * redemptions)}</td>
                  </tr>
                  <tr>
                    <td className="py-1 text-foreground-muted">- Acquisition</td>
                    <td className="py-1 text-right text-negative">{fmtIsk(data.acquisition_cost * redemptions)}</td>
                    <td className="py-1 text-right text-negative">{fmtIsk(data.acquisition_cost * redemptions)}</td>
                  </tr>
                  <tr className="font-medium">
                    <td className="py-1 text-foreground-muted">= Profit</td>
                    <td className={`py-1 text-right ${(data.profit_patient ?? 0) >= 0 ? 'text-positive' : 'text-negative'}`}>
                      {fmtIsk(data.profit_patient * redemptions)}
                    </td>
                    <td className={`py-1 text-right ${(data.profit_instant ?? 0) >= 0 ? 'text-positive' : 'text-negative'}`}>
                      {fmtIsk(data.profit_instant * redemptions)}
                    </td>
                  </tr>
                </tbody>
              </table>
              {data.daily_vol != null && (
                <div className="mt-2 flex gap-3 text-[11px] text-foreground-muted">
                  <span>Daily vol: <span className="text-foreground">{fmtNum(data.daily_vol)}</span></span>
                  {data.days_to_clear != null && (
                    <span>Days to clear: <span className="text-foreground">{data.days_to_clear.toFixed(1)}d</span></span>
                  )}
                </div>
              )}
            </Section>
          </>
        )}
      </div>
    </div>
  )
}
