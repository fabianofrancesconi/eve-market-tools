import { useState, useMemo } from 'react'
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

type Mode = 'cross' | 'same'

interface ArbRow {
  type_id: number
  name: string
  sell_price: number
  buy_price: number
  net_per_unit: number
  margin_pct: number
  flippable_qty: number
  isk_opportunity: number
  total_volume: number
  sell_station_name: string
  buy_station_name: string
  sell_station_id: number
  buy_station_id: number
  from_sec: number | null
  to_sec: number | null
  jumps_total: number
  risk: string
}

function secColor(sec: number | null): string {
  if (sec == null) return 'text-foreground-muted'
  if (sec >= 0.5) return 'text-positive'
  if (sec > 0) return 'text-yellow-400'
  return 'text-negative'
}

function riskColor(risk: string): string {
  const r = risk?.toUpperCase() || ''
  if (r === 'SAFE') return 'text-positive'
  if (r === 'LOWSEC') return 'text-yellow-400'
  return 'text-negative'
}

export function ArbitragePage() {
  const [regionId, setRegionId] = useState(10000002)
  const [salesTax, setSalesTax] = useState(7.5)
  const [mode, setMode] = useState<Mode>('cross')
  const [minIsk, setMinIsk] = useState<string>('')
  const [maxJumps, setMaxJumps] = useState(6)
  const [avoidLowsec, setAvoidLowsec] = useState(false)
  const [routeFlag, setRouteFlag] = useState('shortest')

  const [url, setUrl] = useState<string | null>(null)
  const [trigger, setTrigger] = useState(0)
  const { progress, message, subMessage, result, isStreaming } = useSse<ArbRow[]>(url, trigger)

  const isCross = mode === 'cross'

  const handleScan = () => {
    const params: Record<string, string | number | boolean> = {
      region_id: regionId,
      sales_tax: salesTax / 100,
      cross_station: isCross,
      top: 100,
      refresh: false,
    }
    if (minIsk) params.min_isk = Number(minIsk)
    if (isCross) {
      params.max_jumps = maxJumps
      params.avoid_lowsec = avoidLowsec
      params.route_flag = routeFlag
    } else {
      params.avoid_lowsec = avoidLowsec
    }
    setUrl(sseUrl('/api/arbitrage/scan', params))
    setTrigger(t => t + 1)
  }

  const columns = useMemo(() => {
    const cols = [
      {
        key: 'name',
        header: 'Item',
        width: '240px',
        align: 'left' as const,
      },
      {
        key: 'sell_price',
        header: 'Ask',
        width: '110px',
        align: 'right' as const,
        render: (r: ArbRow) => fmtIsk(r.sell_price),
      },
      {
        key: 'buy_price',
        header: 'Bid',
        width: '110px',
        align: 'right' as const,
        render: (r: ArbRow) => fmtIsk(r.buy_price),
      },
      {
        key: 'net_per_unit',
        header: 'Net/u',
        width: '100px',
        align: 'right' as const,
        render: (r: ArbRow) => (
          <span className={r.net_per_unit >= 0 ? 'text-positive' : 'text-negative'}>
            {fmtIsk(r.net_per_unit)}
          </span>
        ),
      },
      {
        key: 'margin_pct',
        header: 'Margin %',
        width: '75px',
        align: 'right' as const,
        render: (r: ArbRow) => (
          <span className={r.margin_pct >= 0 ? 'text-positive' : 'text-negative'}>
            {fmtPct(r.margin_pct)}
          </span>
        ),
      },
      {
        key: 'flippable_qty',
        header: 'Qty',
        width: '70px',
        align: 'right' as const,
        render: (r: ArbRow) => fmtNum(r.flippable_qty),
      },
      {
        key: 'isk_opportunity',
        header: 'ISK Opp',
        width: '110px',
        align: 'right' as const,
        render: (r: ArbRow) => (
          <span className="text-positive">{fmtIsk(r.isk_opportunity)}</span>
        ),
      },
      {
        key: 'total_volume',
        header: 'Vol m³',
        width: '85px',
        align: 'right' as const,
        render: (r: ArbRow) => fmtNum(r.total_volume, 1),
      },
      {
        key: 'sell_station_name',
        header: 'From',
        width: '200px',
        align: 'left' as const,
        render: (r: ArbRow) => (
          <span className="truncate block max-w-[200px]" title={r.sell_station_name}>
            {r.sell_station_name}
          </span>
        ),
      },
      {
        key: 'from_sec',
        header: 'Sec',
        width: '50px',
        align: 'right' as const,
        render: (r: ArbRow) => (
          <span className={secColor(r.from_sec)}>
            {r.from_sec != null ? r.from_sec.toFixed(1) : '-'}
          </span>
        ),
      },
      {
        key: 'buy_station_name',
        header: 'To',
        width: '200px',
        align: 'left' as const,
        render: (r: ArbRow) => (
          <span className="truncate block max-w-[200px]" title={r.buy_station_name}>
            {r.buy_station_name}
          </span>
        ),
      },
      {
        key: 'to_sec',
        header: 'Sec',
        width: '50px',
        align: 'right' as const,
        render: (r: ArbRow) => (
          <span className={secColor(r.to_sec)}>
            {r.to_sec != null ? r.to_sec.toFixed(1) : '-'}
          </span>
        ),
      },
    ]

    if (isCross) {
      cols.push({
        key: 'jumps_total',
        header: 'Jumps',
        width: '60px',
        align: 'right' as const,
        render: (r: ArbRow) => String(r.jumps_total ?? '-'),
      })
    }

    cols.push({
      key: 'risk',
      header: 'Risk',
      width: '75px',
      align: 'left' as const,
      render: (r: ArbRow) => (
        <span className={riskColor(r.risk)}>{r.risk?.toUpperCase() || '-'}</span>
      ),
    })

    return cols
  }, [isCross])

  const regionName = REGIONS[regionId] || 'Unknown'
  const modeLabel = isCross ? 'Cross-station (haul)' : 'Same-station (flip)'

  return (
    <div className="space-y-3">
      {/* Row 1: Fee settings */}
      <div className="flex items-center gap-4 text-xs uppercase tracking-wide text-foreground-muted">
        <label className="flex items-center gap-1">
          Sales Tax %
          <input
            type="number"
            step="0.01"
            min="0"
            value={salesTax}
            onChange={e => setSalesTax(Number(e.target.value))}
            className="w-16 px-2 py-1 rounded bg-background-elevated border border-border text-foreground text-sm"
          />
        </label>
      </div>

      {/* Row 2: Main controls */}
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

        <div className="w-44">
          <label className="block text-xs text-foreground-muted mb-1 uppercase">Mode</label>
          <select
            value={mode}
            onChange={e => setMode(e.target.value as Mode)}
            className="w-full px-2 py-1.5 rounded bg-background-elevated border border-border text-foreground text-sm focus:outline-none focus:border-accent-cyan"
          >
            <option value="cross">Cross-station (haul)</option>
            <option value="same">Same-station (flip)</option>
          </select>
        </div>

        <div className="w-24">
          <label className="block text-xs text-foreground-muted mb-1 uppercase">Min ISK</label>
          <input
            type="number"
            value={minIsk}
            onChange={e => setMinIsk(e.target.value)}
            placeholder="any"
            className="w-full px-2 py-1.5 rounded bg-background-elevated border border-border text-foreground text-sm focus:outline-none focus:border-accent-cyan placeholder:text-foreground-muted/50"
          />
        </div>

        {isCross && (
          <>
            <div className="w-20">
              <label className="block text-xs text-foreground-muted mb-1 uppercase">Max Jumps</label>
              <input
                type="number"
                min="1"
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
                <option value="secure">Secure</option>
              </select>
            </div>
          </>
        )}

        <button
          onClick={handleScan}
          disabled={isStreaming}
          className="px-4 py-1.5 rounded bg-accent-cyan text-primary-foreground font-medium text-sm hover:bg-accent-cyan/80 disabled:opacity-50 transition-colors"
        >
          {isStreaming ? 'Scanning...' : 'Scan'}
        </button>
      </div>

      {/* Row 3: Toggles */}
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <label className="flex items-center gap-1.5 text-foreground-muted cursor-pointer">
          <input
            type="checkbox"
            checked={avoidLowsec}
            onChange={e => setAvoidLowsec(e.target.checked)}
            className="rounded border-border"
          />
          Highsec only
        </label>
      </div>

      {/* Progress overlay */}
      {isStreaming && (
        <div className="flex flex-col items-center justify-center py-16 space-y-4">
          <div className="w-full max-w-md">
            <ProgressBar progress={progress} message={message} />
          </div>
          {subMessage && (
            <p className="text-xs text-foreground-muted/70">{subMessage}</p>
          )}
        </div>
      )}

      {/* Status bar + results */}
      {!isStreaming && result && (
        <>
          <p className="text-sm text-foreground-muted">
            {regionName} — {result.length} deal{result.length !== 1 ? 's' : ''} · {modeLabel}
          </p>
          <DataTable
            data={result}
            columns={columns}
            keyFn={r => `${r.type_id}-${r.sell_station_id}-${r.buy_station_id}`}
            emptyMessage="No arbitrage opportunities found"
          />
        </>
      )}
    </div>
  )
}
