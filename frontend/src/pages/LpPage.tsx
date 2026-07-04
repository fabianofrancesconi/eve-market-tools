import { useState, useMemo, useCallback, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../lib/api-client'
import { fmtIsk, fmtPct, fmtNum } from '../lib/format'
import { DataTable } from '../components/shared/DataTable'
import { LpDetailPanel } from '../components/lp/LpDetailPanel'
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
  isk_cost: number
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
  max_units: number
  cost_ea: number | null
  unsellable: boolean
  // Enriched from liquidity
  tradeability?: number | null
  daily_vol?: number | null
  days_to_clear?: number | null
}

interface LiquidityRow {
  type_id: number
  tradeability: number
  daily_vol: number
  days_to_clear: number
}

interface ScanResult {
  corp_id: number
  corp_name: string
  sellable: LpRow[]
  unsellable: LpRow[]
  error?: string
}

function Spinner() {
  return (
    <span className="inline-block w-3 h-3 border border-foreground-muted/50 border-t-foreground-muted rounded-full animate-spin" />
  )
}

function spreadColor(pct: number | null): string {
  if (pct == null) return ''
  if (pct < 10) return 'text-positive'
  if (pct < 25) return 'text-yellow-400'
  return 'text-negative'
}

export function LpPage() {
  // Store state
  const corp = useUiStore(s => s.lpCorp)
  const setCorp = useUiStore(s => s.setLpCorp)
  const budget = useUiStore(s => s.lpBudget)
  const setBudget = useUiStore(s => s.setLpBudget)
  const search = useUiStore(s => s.lpSearch)
  const setSearch = useUiStore(s => s.setLpSearch)
  const salesTax = useUiStore(s => s.lpSalesTax)
  const setSalesTax = useUiStore(s => s.setLpSalesTax)
  const brokerFee = useUiStore(s => s.lpBrokerFee)
  const setBrokerFee = useUiStore(s => s.setLpBrokerFee)
  const maxSpread = useUiStore(s => s.lpMaxSpread)
  const setMaxSpread = useUiStore(s => s.setLpMaxSpread)
  const stationId = useUiStore(s => s.lpStationId)
  const setStationId = useUiStore(s => s.setLpStationId)
  const hideIlliquid = useUiStore(s => s.lpHideIlliquid)
  const setHideIlliquid = useUiStore(s => s.setLpHideIlliquid)
  const hideUnaffordable = useUiStore(s => s.lpHideUnaffordable)
  const setHideUnaffordable = useUiStore(s => s.setLpHideUnaffordable)
  const tradeWeight = useUiStore(s => s.lpTradeWeight)
  const setTradeWeight = useUiStore(s => s.setLpTradeWeight)

  // Local state
  const [scanTrigger, setScanTrigger] = useState(0)
  const [selectedRow, setSelectedRow] = useState<LpRow | null>(null)
  const [enrichedRows, setEnrichedRows] = useState<Map<number, LiquidityRow>>(new Map())
  const [liquidityLoading, setLiquidityLoading] = useState(false)

  // Build scan URL
  const scanEnabled = scanTrigger > 0 && corp.trim().length > 0
  const scanQueryKey = useMemo(
    () => ['lp-scan', corp, budget, stationId, salesTax, brokerFee, scanTrigger],
    [corp, budget, stationId, salesTax, brokerFee, scanTrigger]
  )

  const { data, isFetching, refetch } = useQuery<ScanResult>({
    queryKey: scanQueryKey,
    queryFn: () =>
      apiGet<ScanResult>(
        `/api/lp/scan?corp=${encodeURIComponent(corp)}&lp_budget=${budget}&station_id=${stationId}&sales_tax=${salesTax / 100}&broker_fee=${brokerFee / 100}`
      ),
    enabled: scanEnabled,
  })

  const handleScan = useCallback(() => {
    if (!corp.trim()) return
    setEnrichedRows(new Map())
    setScanTrigger(c => c + 1)
  }, [corp])

  const handleRefresh = useCallback(() => {
    if (!corp.trim()) return
    setEnrichedRows(new Map())
    refetch()
  }, [corp, refetch])

  // Liquidity enrichment: fetch after scan data arrives
  useEffect(() => {
    if (!data?.sellable || data.sellable.length === 0) return
    const typeIds = [...new Set(data.sellable.map(r => r.name_id))].filter(Boolean)
    if (typeIds.length === 0) return

    setLiquidityLoading(true)
    apiGet<LiquidityRow[]>(`/api/lp/liquidity?type_ids=${typeIds.join(',')}&station_id=${stationId}`)
      .then((rows) => {
        const map = new Map<number, LiquidityRow>()
        for (const r of rows) {
          map.set(r.type_id, r)
        }
        setEnrichedRows(map)
      })
      .catch(() => {
        // Silently fail — cells stay as spinners
      })
      .finally(() => setLiquidityLoading(false))
  }, [data?.sellable, stationId])

  // Merge enrichment into rows
  const enriched = useMemo(() => {
    if (!data?.sellable) return []
    return data.sellable.map((row) => {
      const liq = enrichedRows.get(row.name_id)
      if (!liq) return row
      return {
        ...row,
        tradeability: liq.tradeability,
        daily_vol: liq.daily_vol,
        days_to_clear: liq.days_to_clear,
      }
    })
  }, [data?.sellable, enrichedRows])

  // Filtering
  const filtered = useMemo(() => {
    let rows = enriched
    const hasSearch = search.trim().length > 0

    // Search filter (overrides max-spread)
    if (hasSearch) {
      const q = search.toLowerCase()
      rows = rows.filter(r => r.name?.toLowerCase().includes(q))
    } else {
      // Max spread filter (only when not searching)
      if (maxSpread < 100) {
        rows = rows.filter(r => r.spread_pct == null || r.spread_pct <= maxSpread)
      }
    }

    // Hide illiquid: spread >= 25%
    if (hideIlliquid) {
      rows = rows.filter(r => r.spread_pct == null || r.spread_pct < 25)
    }

    // Hide unaffordable: max_units === 0
    if (hideUnaffordable) {
      rows = rows.filter(r => r.max_units !== 0)
    }

    return rows
  }, [enriched, maxSpread, hideIlliquid, hideUnaffordable, search])

  const columns = useMemo(
    () => [
      {
        key: 'name',
        header: 'Item',
        width: '220px',
        render: (r: LpRow) => <span className="truncate block max-w-[220px]" title={r.name}>{r.name}</span>,
      },
      {
        key: 'isk_per_lp_patient',
        header: 'List ISK/LP',
        align: 'right' as const,
        width: '100px',
        render: (r: LpRow) => (
          <span className={(r.isk_per_lp_patient ?? 0) > 0 ? 'text-positive' : 'text-negative'}>
            {fmtIsk(r.isk_per_lp_patient)}
          </span>
        ),
      },
      {
        key: 'isk_per_lp_instant',
        header: 'Instant ISK/LP',
        align: 'right' as const,
        width: '110px',
        render: (r: LpRow) => (
          <span className={(r.isk_per_lp_instant ?? 0) > 0 ? 'text-positive' : 'text-negative'}>
            {fmtIsk(r.isk_per_lp_instant)}
          </span>
        ),
      },
      {
        key: 'profit_patient',
        header: 'List profit',
        align: 'right' as const,
        width: '105px',
        render: (r: LpRow) => fmtIsk(r.profit_patient),
      },
      {
        key: 'profit_instant',
        header: 'Instant profit',
        align: 'right' as const,
        width: '110px',
        render: (r: LpRow) => fmtIsk(r.profit_instant),
      },
      {
        key: 'tradeability',
        header: 'Tradeability',
        align: 'right' as const,
        width: '90px',
        render: (r: LpRow) =>
          r.tradeability != null ? fmtNum(r.tradeability, 1) : liquidityLoading ? <Spinner /> : '-',
      },
      {
        key: 'daily_vol',
        header: 'Daily Vol',
        align: 'right' as const,
        width: '85px',
        render: (r: LpRow) =>
          r.daily_vol != null ? fmtNum(r.daily_vol, 0) : liquidityLoading ? <Spinner /> : '-',
      },
      {
        key: 'days_to_clear',
        header: 'Days to Clear',
        align: 'right' as const,
        width: '90px',
        render: (r: LpRow) =>
          r.days_to_clear != null ? fmtNum(r.days_to_clear, 1) : liquidityLoading ? <Spinner /> : '-',
      },
      {
        key: 'spread_pct',
        header: 'Spread',
        align: 'right' as const,
        width: '70px',
        render: (r: LpRow) => (
          <span className={spreadColor(r.spread_pct)}>{fmtPct(r.spread_pct)}</span>
        ),
      },
      {
        key: 'max_units',
        header: 'Max Runs',
        align: 'right' as const,
        width: '75px',
        render: (r: LpRow) => fmtNum(r.max_units),
      },
      {
        key: 'lp_cost',
        header: 'LP/Run',
        align: 'right' as const,
        width: '75px',
        render: (r: LpRow) => fmtNum(r.lp_cost),
      },
      {
        key: 'cost_ea',
        header: 'ISK/Run',
        align: 'right' as const,
        width: '90px',
        render: (r: LpRow) => fmtIsk(r.cost_ea),
      },
      {
        key: 'ask',
        header: 'Ask',
        align: 'right' as const,
        width: '90px',
        render: (r: LpRow) => fmtIsk(r.ask),
      },
      {
        key: 'bid',
        header: 'Bid',
        align: 'right' as const,
        width: '90px',
        render: (r: LpRow) => fmtIsk(r.bid),
      },
    ],
    [liquidityLoading]
  )

  return (
    <div className="space-y-3">
      {/* Row 1: Sales Tax, Broker Fee */}
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

      {/* Row 2: Main controls */}
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
        <div className="w-24">
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
            placeholder="off"
            className="w-full px-2 py-1.5 rounded bg-background-elevated border border-border text-foreground text-sm placeholder:text-foreground-muted/50 focus:outline-none focus:border-accent-cyan"
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
        <div className="w-40">
          <label className="block text-xs text-foreground-muted mb-1 uppercase">Search</label>
          <div className="relative">
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="item name..."
              className="w-full px-2 py-1.5 pr-7 rounded bg-background-elevated border border-border text-foreground text-sm placeholder:text-foreground-muted/50 focus:outline-none focus:border-accent-cyan"
            />
            {search && (
              <button
                onClick={() => setSearch('')}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-foreground-muted hover:text-foreground text-sm leading-none"
                aria-label="Clear search"
              >
                &#x2715;
              </button>
            )}
          </div>
        </div>
        <button
          onClick={handleScan}
          disabled={isFetching || !corp.trim()}
          className="px-4 py-1.5 rounded bg-accent-cyan text-primary-foreground font-medium text-sm hover:bg-accent-cyan/80 disabled:opacity-50 transition-colors"
        >
          {isFetching ? 'Scanning...' : 'Scan'}
        </button>
        <button
          onClick={handleRefresh}
          disabled={isFetching || !scanEnabled}
          className="px-3 py-1.5 rounded border border-border text-foreground-muted text-sm hover:text-foreground hover:border-foreground-muted disabled:opacity-50 transition-colors"
        >
          &#x21bb; Refresh
        </button>
      </div>

      {/* Row 3: Filter toggles + Tradeability */}
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

        {/* Tradeability weight segmented buttons */}
        <div className="flex items-center gap-1 ml-4">
          <span className="text-xs text-foreground-muted uppercase mr-1">Tradeability:</span>
          {[
            { value: 0.25, label: 'Favor quiet' },
            { value: 0.5, label: 'Balanced' },
            { value: 0.75, label: 'Favor liquidity' },
          ].map(opt => (
            <button
              key={opt.value}
              onClick={() => setTradeWeight(opt.value)}
              className={`px-2.5 py-1 text-xs rounded border transition-colors ${
                tradeWeight === opt.value
                  ? 'border-accent-cyan text-accent-cyan bg-accent-cyan/10'
                  : 'border-border text-foreground-muted hover:text-foreground hover:border-foreground-muted'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Error */}
      {data?.error && (
        <p className="text-negative text-sm">{data.error}</p>
      )}

      {/* Status bar */}
      {data?.corp_name && !data.error && (
        <p className="text-sm text-foreground-muted">
          {data.corp_name} &mdash; {filtered.length} offers shown &middot; list vs instant sell
        </p>
      )}

      {/* Table */}
      {data?.sellable && (
        <DataTable
          data={filtered}
          columns={columns}
          keyFn={r => r.offer_id}
          onRowClick={(row) => setSelectedRow(row)}
          emptyMessage="No profitable offers found"
        />
      )}

      {/* Detail Panel */}
      {selectedRow && data && (
        <LpDetailPanel
          offerId={selectedRow.offer_id}
          corpId={data.corp_id}
          lpBudget={budget}
          stationId={stationId}
          salesTax={salesTax}
          brokerFee={brokerFee}
          onClose={() => setSelectedRow(null)}
        />
      )}
    </div>
  )
}
