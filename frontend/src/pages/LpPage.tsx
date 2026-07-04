import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../lib/api-client'
import { fmtIsk, fmtPct, fmtNum } from '../lib/format'
import { DataTable } from '../components/shared/DataTable'
import { useUiStore } from '../stores/ui-store'
import { DEFAULTS } from '../lib/constants'

interface LpRow {
  offer_id: number
  name: string
  name_id: number
  qty: number
  lp_cost: number
  isk_per_lp_best: number | null
  profit_best: number | null
  spread_pct: number | null
  ask: number | null
  bid: number | null
  sell_volume: number
}

interface ScanResult {
  corp_id: number
  corp_name: string
  sellable: LpRow[]
  unsellable: LpRow[]
  error?: string
}

export function LpPage() {
  const corp = useUiStore(s => s.lpCorp)
  const setCorp = useUiStore(s => s.setLpCorp)
  const budget = useUiStore(s => s.lpBudget)
  const setBudget = useUiStore(s => s.setLpBudget)
  const [scanUrl, setScanUrl] = useState<string | null>(null)
  const [scanCount, setScanCount] = useState(0)

  const { data, isFetching } = useQuery<ScanResult>({
    queryKey: ['lp-scan', scanUrl, scanCount],
    queryFn: () => apiGet<ScanResult>(scanUrl!),
    enabled: !!scanUrl,
  })

  const handleScan = () => {
    if (!corp.trim()) return
    const url = `/api/lp/scan?corp=${encodeURIComponent(corp)}&lp_budget=${budget}&sales_tax=${DEFAULTS.salesTax}&broker_fee=${DEFAULTS.brokerFee}`
    setScanUrl(url)
    setScanCount(c => c + 1)
  }

  const columns = [
    { key: 'name', header: 'Item', width: '250px' },
    {
      key: 'isk_per_lp_best',
      header: 'ISK/LP',
      align: 'right' as const,
      render: (r: LpRow) => (
        <span className={r.isk_per_lp_best && r.isk_per_lp_best > 0 ? 'text-positive' : 'text-negative'}>
          {fmtIsk(r.isk_per_lp_best)}
        </span>
      ),
    },
    { key: 'profit_best', header: 'Profit', align: 'right' as const, render: (r: LpRow) => fmtIsk(r.profit_best) },
    { key: 'lp_cost', header: 'LP Cost', align: 'right' as const, render: (r: LpRow) => fmtNum(r.lp_cost) },
    { key: 'spread_pct', header: 'Spread', align: 'right' as const, render: (r: LpRow) => fmtPct(r.spread_pct) },
    { key: 'ask', header: 'Ask', align: 'right' as const, render: (r: LpRow) => fmtIsk(r.ask) },
    { key: 'sell_volume', header: 'Sell Vol', align: 'right' as const, render: (r: LpRow) => fmtNum(r.sell_volume) },
  ]

  return (
    <div className="space-y-4">
      <div className="flex gap-3 items-end">
        <div className="flex-1 max-w-xs">
          <label className="block text-xs text-foreground-muted mb-1">Corporation</label>
          <input
            type="text"
            value={corp}
            onChange={e => setCorp(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleScan()}
            placeholder="e.g. Sisters of EVE"
            className="w-full px-3 py-2 rounded bg-background-elevated border border-border text-foreground placeholder:text-foreground-muted/50 focus:outline-none focus:border-accent-cyan"
          />
        </div>
        <div className="w-32">
          <label className="block text-xs text-foreground-muted mb-1">LP Budget</label>
          <input
            type="number"
            value={budget}
            onChange={e => setBudget(Number(e.target.value))}
            className="w-full px-3 py-2 rounded bg-background-elevated border border-border text-foreground focus:outline-none focus:border-accent-cyan"
          />
        </div>
        <button
          onClick={handleScan}
          disabled={isFetching || !corp.trim()}
          className="px-4 py-2 rounded bg-accent-cyan text-primary-foreground font-medium hover:bg-accent-cyan/80 disabled:opacity-50 transition-colors"
        >
          {isFetching ? 'Scanning...' : 'Scan'}
        </button>
      </div>

      {data?.error && (
        <p className="text-negative text-sm">{data.error}</p>
      )}

      {data?.corp_name && (
        <p className="text-sm text-foreground-muted">
          {data.corp_name} — {data.sellable.length} sellable offers
        </p>
      )}

      {data?.sellable && (
        <DataTable
          data={data.sellable}
          columns={columns}
          keyFn={r => r.offer_id}
          emptyMessage="No profitable offers found"
        />
      )}
    </div>
  )
}
