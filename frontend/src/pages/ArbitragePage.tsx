import { useState } from 'react'
import { useSse } from '../hooks/use-sse'
import { sseUrl } from '../lib/api-client'
import { fmtIsk, fmtPct, fmtNum } from '../lib/format'
import { DataTable } from '../components/shared/DataTable'
import { ProgressBar } from '../components/shared/ProgressBar'
import { useUiStore } from '../stores/ui-store'
import { DEFAULTS } from '../lib/constants'

interface ArbRow {
  type_id: number
  name: string
  sell_price: number
  buy_price: number
  net_per_unit: number
  flippable_qty: number
  isk_opportunity: number
  margin_pct: number
  sell_station_name: string
  buy_station_name: string
  jumps_total: number
  from_sec: number | null
  to_sec: number | null
}

export function ArbitragePage() {
  const [url, setUrl] = useState<string | null>(null)
  const [trigger, setTrigger] = useState(0)
  const minIsk = useUiStore(s => s.arbMinIsk)
  const setMinIsk = useUiStore(s => s.setArbMinIsk)
  const { progress, message, result, isStreaming, cancel } = useSse<ArbRow[]>(url, trigger)

  const handleScan = () => {
    setUrl(sseUrl('/api/arbitrage/scan', {
      region_id: DEFAULTS.jitaRegionId,
      sales_tax: DEFAULTS.salesTax,
      min_isk: minIsk,
      max_jumps: 5,
      avoid_lowsec: true,
      top: 100,
      refresh: false,
    }))
    setTrigger(t => t + 1)
  }

  const columns = [
    { key: 'name', header: 'Item', width: '200px' },
    { key: 'isk_opportunity', header: 'ISK Opportunity', align: 'right' as const, render: (r: ArbRow) => <span className="text-positive">{fmtIsk(r.isk_opportunity)}</span> },
    { key: 'margin_pct', header: 'Margin', align: 'right' as const, render: (r: ArbRow) => fmtPct(r.margin_pct) },
    { key: 'sell_price', header: 'Buy At', align: 'right' as const, render: (r: ArbRow) => fmtIsk(r.sell_price) },
    { key: 'buy_price', header: 'Sell At', align: 'right' as const, render: (r: ArbRow) => fmtIsk(r.buy_price) },
    { key: 'flippable_qty', header: 'Qty', align: 'right' as const, render: (r: ArbRow) => fmtNum(r.flippable_qty) },
    { key: 'jumps_total', header: 'Jumps', align: 'right' as const },
    { key: 'sell_station_name', header: 'From' },
    { key: 'buy_station_name', header: 'To' },
  ]

  return (
    <div className="space-y-4">
      <div className="flex gap-3 items-end">
        <div className="w-40">
          <label className="block text-xs text-foreground-muted mb-1">Min ISK Opportunity</label>
          <input
            type="number"
            value={minIsk}
            onChange={e => setMinIsk(Number(e.target.value))}
            className="w-full px-3 py-2 rounded bg-background-elevated border border-border text-foreground focus:outline-none focus:border-accent-cyan"
          />
        </div>
        <button
          onClick={handleScan}
          disabled={isStreaming}
          className="px-4 py-2 rounded bg-accent-cyan text-primary-foreground font-medium hover:bg-accent-cyan/80 disabled:opacity-50 transition-colors"
        >
          {isStreaming ? 'Scanning...' : 'Scan Region'}
        </button>
      </div>

      {isStreaming && <ProgressBar progress={progress} message={message} />}

      {result && (
        <>
          <p className="text-sm text-foreground-muted">{result.length} opportunities found</p>
          <DataTable
            data={result}
            columns={columns}
            keyFn={r => `${r.type_id}-${r.sell_price}`}
            emptyMessage="No arbitrage opportunities found"
          />
        </>
      )}
    </div>
  )
}
