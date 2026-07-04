import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../lib/api-client'
import { fmtIsk, fmtPct, fmtNum } from '../lib/format'
import { DataTable } from '../components/shared/DataTable'
import { useUiStore } from '../stores/ui-store'

const TRADE_HUBS: Record<number, string> = {
  60003760: 'Jita 4-4',
  60008494: 'Amarr VIII',
  60011866: 'Dodixie IX',
  60005686: 'Hek VIII',
  60004588: 'Rens VI',
}

interface LpRow {
  offer_id: number
  name: string
  name_id: number
  qty: number
  lp_cost: number
  isk_per_lp_best: number | null
  isk_per_lp_patient: number | null
  isk_per_lp_instant: number | null
  profit_best: number | null
  profit_patient: number | null
  profit_instant: number | null
  spread_pct: number | null
  ask: number | null
  bid: number | null
  sell_volume: number
  buy_volume: number
  unsellable: boolean
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
  const search = useUiStore(s => s.lpSearch)
  const setSearch = useUiStore(s => s.setLpSearch)

  const [salesTax, setSalesTax] = useState(7.50)
  const [brokerFee, setBrokerFee] = useState(3.00)
  const [maxSpread, setMaxSpread] = useState(100)
  const [stationId, setStationId] = useState(60003760)
  const [hideIlliquid, setHideIlliquid] = useState(false)
  const [hideUnaffordable, setHideUnaffordable] = useState(false)
  const [scanUrl, setScanUrl] = useState<string | null>(null)
  const [scanCount, setScanCount] = useState(0)

  const { data, isFetching, refetch } = useQuery<ScanResult>({
    queryKey: ['lp-scan', scanUrl, scanCount],
    queryFn: () => apiGet<ScanResult>(scanUrl!),
    enabled: !!scanUrl,
  })

  const handleScan = () => {
    if (!corp.trim()) return
    const url = `/api/lp/scan?corp=${encodeURIComponent(corp)}&lp_budget=${budget}&station_id=${stationId}&sales_tax=${salesTax / 100}&broker_fee=${brokerFee / 100}`
    setScanUrl(url)
    setScanCount(c => c + 1)
  }

  const filtered = useMemo(() => {
    if (!data?.sellable) return []
    let rows = data.sellable
    if (maxSpread < 100) {
      rows = rows.filter(r => r.spread_pct != null && r.spread_pct <= maxSpread)
    }
    if (hideIlliquid) {
      rows = rows.filter(r => r.sell_volume > 0)
    }
    if (hideUnaffordable) {
      rows = rows.filter(r => r.lp_cost <= budget)
    }
    if (search.trim()) {
      const q = search.toLowerCase()
      rows = rows.filter(r => r.name?.toLowerCase().includes(q))
    }
    return rows
  }, [data?.sellable, maxSpread, hideIlliquid, hideUnaffordable, search, budget])

  const columns = [
    { key: 'name', header: 'Item', width: '220px' },
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
    { key: 'qty', header: 'Qty', align: 'right' as const, render: (r: LpRow) => fmtNum(r.qty) },
    { key: 'spread_pct', header: 'Spread %', align: 'right' as const, render: (r: LpRow) => fmtPct(r.spread_pct) },
    { key: 'ask', header: 'Ask', align: 'right' as const, render: (r: LpRow) => fmtIsk(r.ask) },
    { key: 'bid', header: 'Bid', align: 'right' as const, render: (r: LpRow) => fmtIsk(r.bid) },
    { key: 'sell_volume', header: 'Sell Vol', align: 'right' as const, render: (r: LpRow) => fmtNum(r.sell_volume) },
    { key: 'buy_volume', header: 'Buy Vol', align: 'right' as const, render: (r: LpRow) => fmtNum(r.buy_volume) },
  ]

  return (
    <div className="space-y-3">
      {/* Top bar: Sales Tax & Broker Fee */}
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
        <label className="flex items-center gap-1">
          Broker Fee %
          <input
            type="number"
            step="0.01"
            value={brokerFee}
            onChange={e => setBrokerFee(Number(e.target.value))}
            className="w-16 px-2 py-1 rounded bg-background-elevated border border-border text-foreground text-sm"
          />
        </label>
      </div>

      {/* Main controls row */}
      <div className="flex flex-wrap gap-2 items-end">
        <div className="w-48">
          <label className="block text-xs text-foreground-muted mb-1 uppercase">Corporation</label>
          <input
            type="text"
            value={corp}
            onChange={e => setCorp(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleScan()}
            placeholder="e.g. Sisters of EVE"
            className="w-full px-2 py-1.5 rounded bg-background-elevated border border-border text-foreground text-sm placeholder:text-foreground-muted/50 focus:outline-none focus:border-accent-cyan"
          />
        </div>
        <div className="w-20">
          <label className="block text-xs text-foreground-muted mb-1 uppercase">LP Budget</label>
          <input
            type="number"
            value={budget}
            onChange={e => setBudget(Number(e.target.value))}
            className="w-full px-2 py-1.5 rounded bg-background-elevated border border-border text-foreground text-sm focus:outline-none focus:border-accent-cyan"
          />
        </div>
        <div className="w-20">
          <label className="block text-xs text-foreground-muted mb-1 uppercase">Max Spread %</label>
          <input
            type="number"
            value={maxSpread}
            onChange={e => setMaxSpread(Number(e.target.value))}
            className="w-full px-2 py-1.5 rounded bg-background-elevated border border-border text-foreground text-sm focus:outline-none focus:border-accent-cyan"
          />
        </div>
        <div className="w-32">
          <label className="block text-xs text-foreground-muted mb-1 uppercase">Market</label>
          <select
            value={stationId}
            onChange={e => setStationId(Number(e.target.value))}
            className="w-full px-2 py-1.5 rounded bg-background-elevated border border-border text-foreground text-sm focus:outline-none focus:border-accent-cyan"
          >
            {Object.entries(TRADE_HUBS).map(([id, name]) => (
              <option key={id} value={id}>{name}</option>
            ))}
          </select>
        </div>
        <div className="w-36">
          <label className="block text-xs text-foreground-muted mb-1 uppercase">Search</label>
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="item name..."
            className="w-full px-2 py-1.5 rounded bg-background-elevated border border-border text-foreground text-sm placeholder:text-foreground-muted/50 focus:outline-none focus:border-accent-cyan"
          />
        </div>
        <button
          onClick={handleScan}
          disabled={isFetching || !corp.trim()}
          className="px-4 py-1.5 rounded bg-accent-cyan text-primary-foreground font-medium text-sm hover:bg-accent-cyan/80 disabled:opacity-50 transition-colors"
        >
          {isFetching ? 'Scanning...' : 'Scan'}
        </button>
        <button
          onClick={() => refetch()}
          disabled={isFetching || !scanUrl}
          className="px-3 py-1.5 rounded border border-border text-foreground-muted text-sm hover:text-foreground hover:border-foreground-muted disabled:opacity-50 transition-colors"
        >
          ↻ Refresh
        </button>
      </div>

      {/* Filter toggles */}
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <label className="flex items-center gap-1.5 text-foreground-muted cursor-pointer">
          <input
            type="checkbox"
            checked={hideIlliquid}
            onChange={e => setHideIlliquid(e.target.checked)}
            className="rounded border-border"
          />
          Hide illiquid
        </label>
        <label className="flex items-center gap-1.5 text-foreground-muted cursor-pointer">
          <input
            type="checkbox"
            checked={hideUnaffordable}
            onChange={e => setHideUnaffordable(e.target.checked)}
            className="rounded border-border"
          />
          Hide unaffordable
        </label>
      </div>

      {data?.error && (
        <p className="text-negative text-sm">{data.error}</p>
      )}

      {data?.corp_name && !data.error && (
        <p className="text-sm text-foreground-muted">
          {data.corp_name} — {filtered.length} of {data.sellable.length} offers shown
        </p>
      )}

      {data?.sellable && (
        <DataTable
          data={filtered}
          columns={columns}
          keyFn={r => r.offer_id}
          emptyMessage="No profitable offers found"
        />
      )}
    </div>
  )
}
