import { useState, useMemo, useEffect, useCallback, useRef, Fragment } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSse } from '../hooks/use-sse'
import { apiGet, sseUrl } from '../lib/api-client'
import { fmtIsk, fmtPct, fmtNum, fmtDuration } from '../lib/format'
import { ProgressBar } from '../components/shared/ProgressBar'
import { IndustryDetailRow } from '../components/industry/IndustryDetailRow'
import { useUiStore } from '../stores/ui-store'

/* ---------- constants ---------- */

const TRADE_HUBS: Record<number, string> = {
  60003760: 'Jita 4-4',
  60008494: 'Amarr VIII',
  60011866: 'Dodixie IX',
  60005686: 'Hek VIII',
  60004588: 'Rens VI',
}

type TradeWeight = 'balanced' | 'liquidity' | 'quiet'

/* ---------- types ---------- */

interface IndRow {
  blueprint_id: number
  product_id: number
  out_qty: number
  product_name: string
  tech_level: number
  daily_vol?: number | null
  isk_per_hour_patient: number | null
  isk_per_hour_instant: number | null
  profit_patient: number | null
  profit_instant: number | null
  margin_patient: number | null
  margin_instant: number | null
  build_time: number | null
  total_cost: number | null
  material_cost: number | null
  bp_price: number | null
  ask: number | null
  bid: number | null
  in_vol_run: number | null
  out_vol_run: number | null
  days_to_sell: number | null
  tradeability: number | null
  buildable: boolean
  group_name: string
  market_group_id: number
  payback_runs: number | null
  train_hours: number | null
  bp_available: boolean
  bp_source: string
}

interface MarketGroup {
  id: number
  name: string
}

interface LiquidityResult {
  [typeId: string]: { daily_vol: number; tradeability: number }
}

/* ---------- helpers ---------- */

type SortDir = 'asc' | 'desc' | null

function colorForValue(val: number | null | undefined): string {
  if (val == null) return ''
  return val > 0 ? 'text-positive' : val < 0 ? 'text-negative' : ''
}

function Spinner() {
  return (
    <svg className="animate-spin h-3 w-3 inline-block text-foreground-muted" viewBox="0 0 24 24">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
    </svg>
  )
}

// Tradeability = 0–100 blend of liquidity (higher daily_vol better) and low
// competition (lower days_to_sell better), each ranked against the other rows.
// weight 0 = all competition, 1 = all liquidity. Mirrors the LP tab so the
// Favor-quiet / Balanced / Favor-liquidity toggle actually changes the ranking.
function withTradeability<T extends IndRow>(rows: T[], weight: number): T[] {
  const loaded = rows.filter(r => r.daily_vol != null)
  if (loaded.length === 0) return rows.map(r => ({ ...r, tradeability: null }))
  const sortedVols = loaded.map(r => r.daily_vol as number).sort((a, b) => a - b)
  const sortedDays = loaded
    .map(r => (r.days_to_sell == null ? Infinity : r.days_to_sell))
    .sort((a, b) => a - b)
  const bisect = (arr: number[], v: number) => {
    let lo = 0, hi = arr.length
    while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] < v) lo = m + 1; else hi = m }
    return lo
  }
  const pctRank = (sorted: number[], v: number, higherBetter: boolean) => {
    const n = sorted.length
    if (n <= 1) return 100
    const pos = bisect(sorted, v)
    const beats = higherBetter ? pos : n - pos - (sorted[pos] === v ? 1 : 0)
    return (beats / (n - 1)) * 100
  }
  return rows.map(r => {
    if (r.daily_vol == null) return { ...r, tradeability: null }
    const dayVal = r.days_to_sell == null ? Infinity : r.days_to_sell
    const liq = pctRank(sortedVols, r.daily_vol as number, true)
    const comp = pctRank(sortedDays, dayVal, false)
    return { ...r, tradeability: Math.round(weight * liq + (1 - weight) * comp) }
  })
}

/* ---------- page component ---------- */

export function IndustryPage() {
  /* --- state: controls (persisted) --- */
  const salesTax = useUiStore(s => s.indSalesTax)
  const setSalesTax = useUiStore(s => s.setIndSalesTax)
  const brokerFee = useUiStore(s => s.indBrokerFee)
  const setBrokerFee = useUiStore(s => s.setIndBrokerFee)
  const jobRate = useUiStore(s => s.indJobRate)
  const setJobRate = useUiStore(s => s.setIndJobRate)
  const stationId = useUiStore(s => s.indStationId)
  const setStationId = useUiStore(s => s.setIndStationId)
  const runs = useUiStore(s => s.indRuns)
  const setRuns = useUiStore(s => s.setIndRuns)
  const minTradeability = useUiStore(s => s.indMinTradeability)
  const setMinTradeability = useUiStore(s => s.setIndMinTradeability)
  const tradeWeight = useUiStore(s => s.indTradeWeight) as TradeWeight
  const setTradeWeight = (v: TradeWeight) => useUiStore.getState().setIndTradeWeight(v)
  const buildableOnly = useUiStore(s => s.indBuildableOnly)
  const setBuildableOnly = useUiStore(s => s.setIndBuildableOnly)
  const hideT2 = useUiStore(s => s.indHideT2)
  const setHideT2 = useUiStore(s => s.setIndHideT2)
  const includeUnobtainable = useUiStore(s => s.indIncludeUnobtainable)
  const setIncludeUnobtainable = useUiStore(s => s.setIndIncludeUnobtainable)
  const [groupId, setGroupId] = useState('all')
  const [search, setSearch] = useState('')

  /* --- state: scan --- */
  const [url, setUrl] = useState<string | null>(null)
  const [trigger, setTrigger] = useState(0)
  const { progress, message, subMessage, result, isStreaming } = useSse<IndRow[]>(url, trigger)

  /* --- state: enrichment (product_id -> mean daily volume) --- */
  const [dailyVols, setDailyVols] = useState<Record<number, number>>({})
  const [enriching, setEnriching] = useState(false)
  const [enrichDone, setEnrichDone] = useState(0)
  const [enrichTotal, setEnrichTotal] = useState(0)
  const enrichAbortRef = useRef(false)

  /* --- state: detail expansion --- */
  const [expandedId, setExpandedId] = useState<number | null>(null)

  // Escape key to close detail
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && expandedId != null) setExpandedId(null)
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [expandedId])

  /* --- state: sorting --- */
  const [sortKey, setSortKey] = useState<string | null>(null)
  const [sortDir, setSortDir] = useState<SortDir>(null)

  /* --- fetch groups --- */
  const { data: groups } = useQuery<MarketGroup[]>({
    queryKey: ['ind-groups'],
    queryFn: () => apiGet('/api/industry/groups'),
  })

  /* --- scan handler --- */
  const handleScan = () => {
    setExpandedId(null)
    setDailyVols({})
    setEnriching(false)
    enrichAbortRef.current = true
    setUrl(sseUrl('/api/industry/scan', {
      group_ids: groupId === 'all' ? '' : groupId,
      station_id: stationId,
      job_rate: jobRate / 100,
      sales_tax: salesTax / 100,
      broker_fee: brokerFee / 100,
      runs,
    }))
    setTrigger(t => t + 1)
  }

  /* --- background enrichment: fill mean daily volume per product --- */
  const enrichRows = useCallback(async (rows: IndRow[]) => {
    enrichAbortRef.current = false
    const typeIds = rows.map(r => r.product_id)
    if (typeIds.length === 0) return

    setEnriching(true)
    setEnrichDone(0)

    const batchSize = 50
    const batches: number[][] = []
    for (let i = 0; i < typeIds.length; i += batchSize) {
      batches.push(typeIds.slice(i, i + batchSize))
    }
    setEnrichTotal(typeIds.length)

    const map: Record<number, number> = {}
    let done = 0

    for (const batch of batches) {
      if (enrichAbortRef.current) break
      try {
        const resp = await apiGet<LiquidityResult>('/api/industry/liquidity', {
          type_ids: batch.join(','),
          station_id: stationId,
        })
        for (const [tid, vals] of Object.entries(resp)) {
          map[Number(tid)] = vals.daily_vol
        }
      } catch {
        // silently skip failed batches
      }
      done += batch.length
      setEnrichDone(done)
      setDailyVols({ ...map })
    }
    setEnriching(false)
  }, [stationId])

  useEffect(() => {
    if (result && result.length > 0) {
      setDailyVols({})
      enrichRows(result)
    }
  }, [result, enrichRows])

  /* --- derived data: merge daily vol, derive days-to-sell + weighted tradeability --- */
  const weightNum = tradeWeight === 'liquidity' ? 0.75 : tradeWeight === 'quiet' ? 0.25 : 0.5

  const enriched = useMemo(() => {
    if (!result) return []
    const merged = result.map(r => {
      const dv = dailyVols[r.product_id]
      if (dv == null) return { ...r, daily_vol: null }
      return { ...r, daily_vol: dv, days_to_sell: r.out_qty ? (r.out_qty * runs) / dv : null }
    })
    return withTradeability(merged, weightNum)
  }, [result, dailyVols, runs, weightNum])

  const filtered = useMemo(() => {
    let rows = enriched

    if (search.trim()) {
      const q = search.toLowerCase()
      rows = rows.filter(r => r.product_name?.toLowerCase().includes(q))
      return rows
    }

    if (buildableOnly) {
      rows = rows.filter(r => r.buildable)
    }
    if (hideT2) {
      rows = rows.filter(r => r.tech_level !== 2)
    }
    if (!includeUnobtainable) {
      rows = rows.filter(r => r.bp_available)
    }

    const minTrade = minTradeability !== '' ? Number(minTradeability) : null
    if (minTrade != null && !isNaN(minTrade)) {
      rows = rows.filter(r => r.tradeability == null || r.tradeability >= minTrade)
    }

    return rows
  }, [enriched, search, minTradeability, buildableOnly, hideT2, includeUnobtainable])

  const sorted = useMemo(() => {
    if (!sortKey || !sortDir) return filtered
    return [...filtered].sort((a, b) => {
      const av = (a as unknown as Record<string, unknown>)[sortKey]
      const bv = (b as unknown as Record<string, unknown>)[sortKey]
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      const cmp = (av as number) < (bv as number) ? -1 : (av as number) > (bv as number) ? 1 : 0
      return sortDir === 'desc' ? -cmp : cmp
    })
  }, [filtered, sortKey, sortDir])

  const handleSort = (key: string) => {
    if (sortKey !== key) { setSortKey(key); setSortDir('desc') }
    else if (sortDir === 'desc') { setSortDir('asc') }
    else { setSortKey(null); setSortDir(null) }
  }

  const handleRowClick = (row: IndRow) => {
    setExpandedId(expandedId === row.blueprint_id ? null : row.blueprint_id)
  }

  /* --- column definitions --- */
  const columns: {
    key: string; header: string; width: string; align: 'left' | 'right' | 'center';
    render: (r: IndRow) => React.ReactNode
  }[] = [
    {
      key: 'product_name', header: 'Item', width: '210px', align: 'left',
      render: r => <span className="font-medium">{r.product_name}</span>,
    },
    {
      key: 'tech_level', header: 'Tech', width: '46px', align: 'center',
      render: r => <span className={r.tech_level === 2 ? 'text-accent-gold font-medium' : ''}>T{r.tech_level}</span>,
    },
    {
      key: 'isk_per_hour_patient', header: 'ISK/hr list', width: '105px', align: 'right',
      render: r => <span className={colorForValue(r.isk_per_hour_patient)}>{fmtIsk(r.isk_per_hour_patient)}</span>,
    },
    {
      key: 'isk_per_hour_instant', header: 'ISK/hr instant', width: '105px', align: 'right',
      render: r => <span className={colorForValue(r.isk_per_hour_instant)}>{fmtIsk(r.isk_per_hour_instant)}</span>,
    },
    {
      key: 'profit_patient', header: 'Profit list', width: '100px', align: 'right',
      render: r => <span className={colorForValue(r.profit_patient)}>{fmtIsk(r.profit_patient)}</span>,
    },
    {
      key: 'profit_instant', header: 'Profit instant', width: '100px', align: 'right',
      render: r => <span className={colorForValue(r.profit_instant)}>{fmtIsk(r.profit_instant)}</span>,
    },
    {
      key: 'margin_patient', header: 'Margin list', width: '70px', align: 'right',
      render: r => <span className={colorForValue(r.margin_patient != null ? r.margin_patient * 100 : null)}>
        {fmtPct(r.margin_patient != null ? r.margin_patient * 100 : null)}
      </span>,
    },
    {
      key: 'margin_instant', header: 'Margin instant', width: '70px', align: 'right',
      render: r => <span className={colorForValue(r.margin_instant != null ? r.margin_instant * 100 : null)}>
        {fmtPct(r.margin_instant != null ? r.margin_instant * 100 : null)}
      </span>,
    },
    {
      key: 'build_time', header: 'Build time', width: '72px', align: 'right',
      render: r => <>{fmtDuration(r.build_time)}</>,
    },
    {
      key: 'total_cost', header: 'Cost/run', width: '95px', align: 'right',
      render: r => <>{fmtIsk(r.total_cost)}</>,
    },
    {
      key: 'bp_price', header: 'BP price', width: '100px', align: 'right',
      render: r => <>{fmtIsk(r.bp_price)}</>,
    },
    {
      key: 'payback_runs', header: 'Payback', width: '80px', align: 'right',
      render: r => <>{r.payback_runs != null ? `${fmtNum(r.payback_runs)} runs` : '-'}</>,
    },
    {
      key: 'ask', header: 'Sell price', width: '95px', align: 'right',
      render: r => <>{fmtIsk(r.ask)}</>,
    },
    {
      key: 'in_vol_run', header: 'Cargo in', width: '80px', align: 'right',
      render: r => <>{r.in_vol_run != null ? `${fmtNum(r.in_vol_run, 1)} m³` : '-'}</>,
    },
    {
      key: 'out_vol_run', header: 'Cargo out', width: '80px', align: 'right',
      render: r => <>{r.out_vol_run != null ? `${fmtNum(r.out_vol_run, 1)} m³` : '-'}</>,
    },
    {
      key: 'days_to_sell', header: 'Days to sell', width: '82px', align: 'right',
      render: r => {
        if (enriching && r.days_to_sell == null) return <Spinner />
        return <>{r.days_to_sell != null ? fmtNum(r.days_to_sell, 1) : '-'}</>
      },
    },
    {
      key: 'tradeability', header: 'Tradeability', width: '90px', align: 'right',
      render: r => {
        if (enriching && r.tradeability == null) return <Spinner />
        if (r.tradeability == null) return <>-</>
        const color = r.tradeability >= 70 ? 'text-positive' : r.tradeability >= 40 ? 'text-accent-gold' : 'text-negative'
        return <span className={color}>{fmtNum(r.tradeability)}</span>
      },
    },
    {
      key: 'buildable', header: 'Buildable?', width: '70px', align: 'center',
      render: r => r.buildable
        ? <span className="text-positive">{'✓'}</span>
        : <span className="text-negative">{'✗'}</span>,
    },
    {
      key: 'train_hours', header: 'Train time', width: '80px', align: 'right',
      render: r => {
        if (r.train_hours == null) return <>-</>
        if (r.train_hours === 0) return <span className="text-positive">Ready</span>
        const days = r.train_hours / 24
        return <span className="text-accent-gold">{days >= 1 ? `${fmtNum(days, 1)}d` : `${fmtNum(r.train_hours, 1)}h`}</span>
      },
    },
    {
      key: 'group_name', header: 'Category', width: '130px', align: 'left',
      render: r => <>{r.group_name || '-'}</>,
    },
  ]

  const totalCols = columns.length

  /* --- render --- */
  return (
    <div className="space-y-3">
      {/* Control bar */}
      <div className="flex flex-wrap gap-6 items-end text-sm">
        {/* Group 1: Costs & Fees */}
        <fieldset className="flex items-end gap-2">
          <legend className="text-[10px] uppercase tracking-wide text-foreground-muted mb-1">Costs &amp; Fees</legend>
          <label className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase text-foreground-muted">Sales Tax %</span>
            <input
              type="number" step="0.01" value={salesTax}
              onChange={e => setSalesTax(Number(e.target.value))}
              className="w-16 px-2 py-1 rounded bg-background-elevated border border-border text-foreground text-sm"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase text-foreground-muted">Broker Fee %</span>
            <input
              type="number" step="0.01" value={brokerFee}
              onChange={e => setBrokerFee(Number(e.target.value))}
              className="w-16 px-2 py-1 rounded bg-background-elevated border border-border text-foreground text-sm"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase text-foreground-muted">Job Rate %</span>
            <input
              type="number" step="0.01" value={jobRate}
              onChange={e => setJobRate(Number(e.target.value))}
              className="w-16 px-2 py-1 rounded bg-background-elevated border border-border text-foreground text-sm"
            />
          </label>
        </fieldset>

        {/* Group 2: Scope */}
        <fieldset className="flex items-end gap-2">
          <legend className="text-[10px] uppercase tracking-wide text-foreground-muted mb-1">Scope</legend>
          <label className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase text-foreground-muted">Category</span>
            <select
              value={groupId} onChange={e => setGroupId(e.target.value)}
              className="w-44 px-2 py-1 rounded bg-background-elevated border border-border text-foreground text-sm focus:outline-none focus:border-accent-cyan"
            >
              <option value="all">All</option>
              {groups?.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase text-foreground-muted">Market</span>
            <select
              value={stationId} onChange={e => setStationId(Number(e.target.value))}
              className="w-32 px-2 py-1 rounded bg-background-elevated border border-border text-foreground text-sm focus:outline-none focus:border-accent-cyan"
            >
              {Object.entries(TRADE_HUBS).map(([id, name]) => (
                <option key={id} value={id}>{name}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase text-foreground-muted">Runs</span>
            <input
              type="number" value={runs} min={1}
              onChange={e => setRuns(Math.max(1, Number(e.target.value)))}
              className="w-14 px-2 py-1 rounded bg-background-elevated border border-border text-foreground text-sm focus:outline-none focus:border-accent-cyan"
            />
          </label>
        </fieldset>

        {/* Group 3: Filter */}
        <fieldset className="flex items-end gap-2">
          <legend className="text-[10px] uppercase tracking-wide text-foreground-muted mb-1">Filter</legend>
          <label className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase text-foreground-muted">Search</span>
            <div className="relative">
              <input
                type="text" value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="product name..."
                className="w-36 px-2 py-1 pr-6 rounded bg-background-elevated border border-border text-foreground text-sm placeholder:text-foreground-muted/50 focus:outline-none focus:border-accent-cyan"
              />
              {search && (
                <button
                  onClick={() => setSearch('')}
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 text-foreground-muted hover:text-foreground text-sm leading-none"
                  aria-label="Clear search"
                >
                  &times;
                </button>
              )}
            </div>
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase text-foreground-muted">Min Tradeability</span>
            <input
              type="number" min={0} max={100} value={minTradeability}
              onChange={e => setMinTradeability(e.target.value)}
              placeholder="0-100"
              className="w-16 px-2 py-1 rounded bg-background-elevated border border-border text-foreground text-sm placeholder:text-foreground-muted/50 focus:outline-none focus:border-accent-cyan"
            />
          </label>
          <div className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase text-foreground-muted">Tradeability</span>
            <div className="flex rounded overflow-hidden border border-border text-xs">
              {([['balanced', 'Balanced'], ['liquidity', 'Favor liquidity'], ['quiet', 'Favor quiet']] as const).map(([val, label]) => (
                <button
                  key={val}
                  onClick={() => setTradeWeight(val)}
                  className={`px-2 py-1 transition-colors ${
                    tradeWeight === val
                      ? 'bg-accent-cyan text-primary-foreground'
                      : 'bg-background-elevated text-foreground-muted hover:text-foreground'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </fieldset>

        {/* Group 4: Actions */}
        <fieldset className="flex items-end gap-2">
          <legend className="text-[10px] uppercase tracking-wide text-foreground-muted mb-1">Actions</legend>
          <button
            onClick={handleScan}
            disabled={isStreaming}
            className="px-4 py-1 rounded bg-accent-cyan text-primary-foreground font-medium text-sm hover:bg-accent-cyan/80 disabled:opacity-50 transition-colors"
          >
            {isStreaming ? 'Scanning...' : 'Scan'}
          </button>
          <label className="flex items-center gap-1 text-foreground-muted cursor-pointer whitespace-nowrap">
            <input type="checkbox" checked={buildableOnly} onChange={e => setBuildableOnly(e.target.checked)} className="rounded border-border" />
            Buildable only
          </label>
          <label className="flex items-center gap-1 text-foreground-muted cursor-pointer whitespace-nowrap">
            <input type="checkbox" checked={hideT2} onChange={e => setHideT2(e.target.checked)} className="rounded border-border" />
            Hide T2
          </label>
          <label className="flex items-center gap-1 text-foreground-muted cursor-pointer whitespace-nowrap">
            <input type="checkbox" checked={includeUnobtainable} onChange={e => setIncludeUnobtainable(e.target.checked)} className="rounded border-border" />
            Include unobtainable
          </label>
        </fieldset>
      </div>

      {/* Progress overlay */}
      {isStreaming && (
        <div className="flex flex-col items-center justify-center py-16 space-y-3">
          <ProgressBar progress={progress} message={message} />
          {subMessage && <p className="text-xs text-foreground-muted">{subMessage}</p>}
        </div>
      )}

      {/* Results table */}
      {!isStreaming && result && (
        <>
          {/* Status bar */}
          <div className="flex items-center gap-3 text-sm text-foreground-muted">
            <span>{sorted.length} items &middot; source {TRADE_HUBS[stationId]}</span>
            {enriching && (
              <span className="flex items-center gap-1.5">
                <Spinner />
                scoring tradeability {enrichDone}/{enrichTotal}
              </span>
            )}
          </div>

          {/* Table */}
          {sorted.length === 0 ? (
            <p className="text-center text-foreground-muted py-8">No items found</p>
          ) : (
            <div className="overflow-x-auto rounded border border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-background-elevated border-b border-border">
                    {columns.map(col => (
                      <th
                        key={col.key}
                        className={`px-3 py-2 font-medium text-foreground-muted whitespace-nowrap cursor-pointer hover:text-foreground select-none ${
                          col.align === 'right' ? 'text-right' : col.align === 'center' ? 'text-center' : 'text-left'
                        }`}
                        style={{ width: col.width }}
                        onClick={() => handleSort(col.key)}
                      >
                        {col.header}
                        {sortKey === col.key && (
                          <span className="ml-1">{sortDir === 'asc' ? '↑' : '↓'}</span>
                        )}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sorted.map(row => (
                    <Fragment key={row.blueprint_id}>
                      <tr
                        className={`border-b border-border/50 hover:bg-background-elevated/50 cursor-pointer ${
                          expandedId === row.blueprint_id ? 'bg-background-elevated/40' : ''
                        }`}
                        onClick={() => handleRowClick(row)}
                      >
                        {columns.map(col => (
                          <td
                            key={col.key}
                            className={`px-3 py-1.5 ${
                              col.align === 'right' ? 'text-right' : col.align === 'center' ? 'text-center' : 'text-left'
                            }`}
                          >
                            {col.render(row)}
                          </td>
                        ))}
                      </tr>
                      {expandedId === row.blueprint_id && (
                        <IndustryDetailRow
                          key={`detail-${row.blueprint_id}`}
                          blueprintId={row.blueprint_id}
                          stationId={stationId}
                          jobRate={jobRate}
                          salesTax={salesTax}
                          broker={brokerFee}
                          runs={runs}
                          colSpan={totalCols}
                          onClose={() => setExpandedId(null)}
                        />
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
