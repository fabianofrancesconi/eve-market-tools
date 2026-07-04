import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../../lib/api-client'
import { fmtIsk, fmtNum } from '../../lib/format'
import { PriceChart, stationToRegion } from '../shared/PriceChart'

interface RequiredItem {
  name: string
  type_id: number
  qty: number
  unit_price: number
  line_cost: number
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

export function LpDetailPanel({ offerId, corpId, lpBudget, stationId, salesTax, brokerFee, onClose }: LpDetailPanelProps) {
  const { data, isLoading, error } = useQuery<DetailData>({
    queryKey: ['lp-detail', offerId, corpId, lpBudget, stationId, salesTax, brokerFee],
    queryFn: () =>
      apiGet<DetailData>(
        `/api/lp/detail?offer_id=${offerId}&corp_id=${corpId}&lp_budget=${lpBudget}&station_id=${stationId}&sales_tax=${salesTax / 100}&broker_fee=${brokerFee / 100}`
      ),
    enabled: offerId > 0 && corpId > 0,
  })

  const regionId = stationToRegion(stationId)

  return (
    <div className="fixed inset-y-0 right-0 w-[580px] max-w-full bg-background border-l border-border shadow-2xl z-50 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-start justify-between p-4 border-b border-border">
        <div className="min-w-0 flex-1">
          {isLoading ? (
            <div className="h-6 w-48 bg-background-elevated animate-pulse rounded" />
          ) : (
            <>
              <h2 className="text-lg font-semibold text-accent-cyan truncate">
                {data?.name ?? 'Loading...'}
              </h2>
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
                        <td className="py-1 text-right text-foreground-muted">{fmtNum(item.qty)}</td>
                        <td className="py-1 text-right text-foreground-muted">{fmtIsk(item.unit_price)}</td>
                        <td className="py-1 text-right">{fmtIsk(item.line_cost)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="text-xs text-foreground-muted">No required items (LP + ISK only)</p>
              )}
            </Section>

            {/* Cost Breakdown */}
            <Section title="Cost Breakdown" defaultOpen={true}>
              <div className="space-y-1 text-xs">
                <div className="flex justify-between">
                  <span className="text-foreground-muted">LP cost</span>
                  <span>{fmtNum(data.lp_cost)} LP</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-foreground-muted">ISK cost (base)</span>
                  <span>{fmtIsk(data.isk_cost)}</span>
                </div>
                {data.required_items && data.required_items.length > 0 && (
                  <div className="flex justify-between">
                    <span className="text-foreground-muted">Required items</span>
                    <span>{fmtIsk(data.required_items.reduce((s, i) => s + i.line_cost, 0))}</span>
                  </div>
                )}
                <div className="flex justify-between font-medium border-t border-border/50 pt-1">
                  <span className="text-foreground-muted">Total acquisition</span>
                  <span>{fmtIsk(data.acquisition_cost)}</span>
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
                    <td className="py-1 text-right">{fmtIsk(data.sell_value_patient)}</td>
                    <td className="py-1 text-right">{fmtIsk(data.sell_value_instant)}</td>
                  </tr>
                  <tr>
                    <td className="py-1 text-foreground-muted">- Sales tax</td>
                    <td className="py-1 text-right text-negative">{fmtIsk(data.tax_patient)}</td>
                    <td className="py-1 text-right text-negative">{fmtIsk(data.tax_instant)}</td>
                  </tr>
                  <tr>
                    <td className="py-1 text-foreground-muted">- Broker fee</td>
                    <td className="py-1 text-right text-negative">{fmtIsk(data.broker_patient)}</td>
                    <td className="py-1 text-right text-negative">{fmtIsk(data.broker_instant)}</td>
                  </tr>
                  <tr>
                    <td className="py-1 text-foreground-muted">Net revenue</td>
                    <td className="py-1 text-right">{fmtIsk(data.net_patient)}</td>
                    <td className="py-1 text-right">{fmtIsk(data.net_instant)}</td>
                  </tr>
                  <tr>
                    <td className="py-1 text-foreground-muted">- Acquisition</td>
                    <td className="py-1 text-right text-negative">{fmtIsk(data.acquisition_cost)}</td>
                    <td className="py-1 text-right text-negative">{fmtIsk(data.acquisition_cost)}</td>
                  </tr>
                  <tr className="font-medium">
                    <td className="py-1 text-foreground-muted">= Profit</td>
                    <td className={`py-1 text-right ${(data.profit_patient ?? 0) >= 0 ? 'text-positive' : 'text-negative'}`}>
                      {fmtIsk(data.profit_patient)}
                    </td>
                    <td className={`py-1 text-right ${(data.profit_instant ?? 0) >= 0 ? 'text-positive' : 'text-negative'}`}>
                      {fmtIsk(data.profit_instant)}
                    </td>
                  </tr>
                </tbody>
              </table>
            </Section>
          </>
        )}
      </div>
    </div>
  )
}
