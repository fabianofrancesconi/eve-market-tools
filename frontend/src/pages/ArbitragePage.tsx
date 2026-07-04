import { useState } from 'react'
import { useSse } from '../hooks/use-sse'
import { sseUrl } from '../lib/api-client'
import { fmtIsk, fmtPct, fmtNum } from '../lib/format'
import { DataTable } from '../components/shared/DataTable'
import { ProgressBar } from '../components/shared/ProgressBar'

const REGIONS: Record<number, string> = {
  10000002: 'The Forge (Jita)',
  10000043: 'Domain (Amarr)',
  10000032: 'Sinq Laison (Dodixie)',
  10000042: 'Metropolis (Hek)',
  10000030: 'Heimatar (Rens)',
}

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
  const [regionId, setRegionId] = useState(10000002)
  const [salesTax, setSalesTax] = useState(7.5)
  const [crossStation, setCrossStation] = useState(true)
  const [minIsk, setMinIsk] = useState(0)
  const [maxJumps, setMaxJumps] = useState(6)
  const [avoidLowsec, setAvoidLowsec] = useState(false)
  const [routeFlag, setRouteFlag] = useState('shortest')

  const [url, setUrl] = useState<string | null>(null)
  const [trigger, setTrigger] = useState(0)
  const { progress, message, result, isStreaming } = useSse<ArbRow[]>(url, trigger)

  const handleScan = () => {
    setUrl(sseUrl('/api/arbitrage/scan', {
      region_id: regionId,
      sales_tax: salesTax / 100,
      cross_station: crossStation,
      min_isk: minIsk,
      max_jumps: maxJumps,
      avoid_lowsec: avoidLowsec,
      route_flag: routeFlag,
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
    <div className="space-y-3">
      {/* Top bar: Sales Tax */}
      <div className="flex items-center gap-4 text-xs uppercase tracking-wide text-foreground-muted">
        <label className="flex items-center gap-1">
          Sales Tax %
          <input
            type="number"
            step="0.01"
            value={salesTax}
            onChange={e => setSalesTax(Number(e.target.value))}
            className="w-16 px-2 py-1 rounded bg-background-elevated border border-border text-foreground text-sm"
          />
        </label>
      </div>

      {/* Main controls row */}
      <div className="flex flex-wrap gap-2 items-end">
        <div className="w-44">
          <label className="block text-xs text-foreground-muted mb-1 uppercase">Region</label>
          <select
            value={regionId}
            onChange={e => setRegionId(Number(e.target.value))}
            className="w-full px-2 py-1.5 rounded bg-background-elevated border border-border text-foreground text-sm focus:outline-none focus:border-accent-cyan"
          >
            {Object.entries(REGIONS).map(([id, name]) => (
              <option key={id} value={id}>{name}</option>
            ))}
          </select>
        </div>
        <div className="w-28">
          <label className="block text-xs text-foreground-muted mb-1 uppercase">Min ISK</label>
          <input
            type="number"
            value={minIsk}
            onChange={e => setMinIsk(Number(e.target.value))}
            className="w-full px-2 py-1.5 rounded bg-background-elevated border border-border text-foreground text-sm focus:outline-none focus:border-accent-cyan"
          />
        </div>
        <div className="w-20">
          <label className="block text-xs text-foreground-muted mb-1 uppercase">Max Jumps</label>
          <input
            type="number"
            value={maxJumps}
            onChange={e => setMaxJumps(Number(e.target.value))}
            className="w-full px-2 py-1.5 rounded bg-background-elevated border border-border text-foreground text-sm focus:outline-none focus:border-accent-cyan"
          />
        </div>
        <div className="w-28">
          <label className="block text-xs text-foreground-muted mb-1 uppercase">Route</label>
          <select
            value={routeFlag}
            onChange={e => setRouteFlag(e.target.value)}
            className="w-full px-2 py-1.5 rounded bg-background-elevated border border-border text-foreground text-sm focus:outline-none focus:border-accent-cyan"
          >
            <option value="shortest">Shortest</option>
            <option value="secure">Safest</option>
          </select>
        </div>
        <button
          onClick={handleScan}
          disabled={isStreaming}
          className="px-4 py-1.5 rounded bg-accent-cyan text-primary-foreground font-medium text-sm hover:bg-accent-cyan/80 disabled:opacity-50 transition-colors"
        >
          {isStreaming ? 'Scanning...' : 'Scan Region'}
        </button>
      </div>

      {/* Filter toggles */}
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <label className="flex items-center gap-1.5 text-foreground-muted cursor-pointer">
          <input
            type="checkbox"
            checked={crossStation}
            onChange={e => setCrossStation(e.target.checked)}
            className="rounded border-border"
          />
          Cross-station
        </label>
        <label className="flex items-center gap-1.5 text-foreground-muted cursor-pointer">
          <input
            type="checkbox"
            checked={avoidLowsec}
            onChange={e => setAvoidLowsec(e.target.checked)}
            className="rounded border-border"
          />
          Avoid lowsec
        </label>
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
