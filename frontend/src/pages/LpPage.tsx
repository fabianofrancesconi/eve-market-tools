import { useState, useMemo, useCallback, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiGet, apiPost } from '../lib/api-client'
import { fmtIsk, fmtPct, fmtNum, fmtAge } from '../lib/format'
import { DataTable } from '../components/shared/DataTable'
import { LpDetailPanel } from '../components/lp/LpDetailPanel'
import { useUiStore } from '../stores/ui-store'
import { useAuth } from '../hooks/use-auth'

const TRADE_HUBS: Record<number, string> = {
  60003760: 'Jita 4-4',
  60008494: 'Amarr VIII',
  60011866: 'Dodixie IX',
  60005686: 'Hek VIII',
  60004588: 'Rens VI',
}

interface NpcCorp {
  id: number
  name: string
}

interface LoyaltyEntry {
  corporation_id: number
  loyalty_points: number
}

interface CharData {
  character_id: number
  is_active: boolean
  loyalty_points?: LoyaltyEntry[]
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
  illiquid?: boolean
  output_volume?: number | null
  // Enriched from liquidity
  tradeability?: number | null
  daily_vol?: number | null
  days_to_clear?: number | null
  list_price?: number | null
  floor_age?: number | null
}

interface LiquidityEntry {
  daily_vol: number | null
  days_to_clear: number | null
  list_price: number | null
  floor_age: number | null
  tradeability: number | null
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

// Tradeability = a 0–100 blend of two signals, each scored by its rank against
// the other offers in this store: liquidity (higher daily_vol = better) and low
// competition (lower days_to_clear = better). `weight` sets the proportion
// (0 = all competition, 1 = all liquidity). Mirrors the master UI so the
// Favor-quiet / Balanced / Favor-liquidity toggle actually changes the ranking.
function withTradeability<T extends LpRow>(rows: T[], weight: number): T[] {
  const loaded = rows.filter(r => r.daily_vol != null)
  if (loaded.length === 0) return rows.map(r => ({ ...r, tradeability: null }))
  const sortedVols = loaded.map(r => r.daily_vol as number).sort((a, b) => a - b)
  const sortedDays = loaded
    .map(r => (r.days_to_clear == null ? Infinity : r.days_to_clear))
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
    const dayVal = r.days_to_clear == null ? Infinity : r.days_to_clear
    const liq = pctRank(sortedVols, r.daily_vol as number, true)
    const comp = pctRank(sortedDays, dayVal, false)
    return { ...r, tradeability: Math.round(weight * liq + (1 - weight) * comp) }
  })
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
  const sortKey = useUiStore(s => s.lpSortKey)
  const setSortKey = useUiStore(s => s.setLpSortKey)
  const sortDir = useUiStore(s => s.lpSortDir)
  const setSortDir = useUiStore(s => s.setLpSortDir)
  const colOrder = useUiStore(s => s.lpColOrder)
  const setColOrder = useUiStore(s => s.setLpColOrder)
  const colWidths = useUiStore(s => s.lpColWidths)
  const setColWidths = useUiStore(s => s.setLpColWidths)

  // Local state
  const [scanTrigger, setScanTrigger] = useState(0)
  const [selectedRow, setSelectedRow] = useState<LpRow | null>(null)
  const [enrichedRows, setEnrichedRows] = useState<Map<number, LiquidityEntry>>(new Map())
  const [liquidityLoading, setLiquidityLoading] = useState(false)
  const [corpDropdownOpen, setCorpDropdownOpen] = useState(false)
  const [restoredScan, setRestoredScan] = useState<ScanResult | null>(null)
  const [restoreChecked, setRestoreChecked] = useState(false)
  const forceRefreshRef = useRef(false)
  const corpInputRef = useRef<HTMLInputElement>(null)

  // Corps list for autocomplete
  const { data: corpsData } = useQuery<NpcCorp[]>({
    queryKey: ['npc-corps'],
    queryFn: () => apiGet('/api/lp/corps'),
    staleTime: 24 * 60 * 60 * 1000,
  })

  const corpSuggestions = useMemo(() => {
    if (!corpsData || !corp.trim()) return []
    const q = corp.toLowerCase()
    return corpsData
      .filter(c => c.name.toLowerCase().includes(q))
      .slice(0, 10)
  }, [corpsData, corp])

  // Restore the last saved scan on mount (instant, no ESI hit). Only fall back
  // to an auto-scan if there was nothing to restore.
  useEffect(() => {
    apiGet<{ lp: ScanResult | null }>('/api/lp/last-scan')
      .then(res => { if (res.lp?.sellable) setRestoredScan(res.lp) })
      .catch(() => {})
      .finally(() => setRestoreChecked(true))
  }, [])

  useEffect(() => {
    if (!restoreChecked) return
    if (corp.trim() && scanTrigger === 0 && !restoredScan) {
      setScanTrigger(1)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restoreChecked])

  // Escape key to close detail panel
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && selectedRow) setSelectedRow(null)
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [selectedRow])

  // Build scan URL
  const scanEnabled = scanTrigger > 0 && corp.trim().length > 0
  const scanQueryKey = useMemo(
    () => ['lp-scan', corp, budget, stationId, salesTax, brokerFee, scanTrigger],
    [corp, budget, stationId, salesTax, brokerFee, scanTrigger]
  )

  const { data, isFetching } = useQuery<ScanResult>({
    queryKey: scanQueryKey,
    queryFn: () => {
      const refresh = forceRefreshRef.current
      forceRefreshRef.current = false
      return apiGet<ScanResult>(
        `/api/lp/scan?corp=${encodeURIComponent(corp)}&lp_budget=${budget}&station_id=${stationId}&sales_tax=${salesTax / 100}&broker_fee=${brokerFee / 100}${refresh ? '&refresh=true' : ''}`
      )
    },
    enabled: scanEnabled,
  })

  // Once a live scan resolves, drop the restored snapshot so it can't shadow
  // subsequent scans.
  useEffect(() => {
    if (data) setRestoredScan(null)
  }, [data])

  const activeScan = data ?? restoredScan

  // When logged in, drive the LP budget from the character's actual loyalty
  // points for the viewed corp and lock the field (mirrors master).
  const { isLoggedIn } = useAuth()
  const { data: charData } = useQuery<CharData[]>({
    queryKey: ['character-data'],
    queryFn: () => apiGet('/api/character/data'),
    enabled: isLoggedIn,
    refetchInterval: 300_000,
  })
  const resolvedCorpId = useMemo(() => {
    if (activeScan?.corp_id) return activeScan.corp_id
    const q = corp.trim().toLowerCase()
    return corpsData?.find(c => c.name.toLowerCase() === q)?.id ?? null
  }, [activeScan?.corp_id, corp, corpsData])
  const lpInfo = useMemo(() => {
    if (!isLoggedIn || !charData || resolvedCorpId == null) return null
    const active = charData.find(c => c.is_active) ?? charData[0]
    const entry = active?.loyalty_points?.find(l => l.corporation_id === resolvedCorpId)
    return { points: entry?.loyalty_points ?? 0, matched: !!entry }
  }, [isLoggedIn, charData, resolvedCorpId])
  useEffect(() => {
    if (lpInfo && lpInfo.points !== budget) setBudget(lpInfo.points)
  }, [lpInfo, budget, setBudget])

  const handleScan = useCallback(() => {
    if (!corp.trim()) return
    setEnrichedRows(new Map())
    setScanTrigger(c => c + 1)
  }, [corp])

  const handleRefresh = useCallback(() => {
    if (!corp.trim()) return
    forceRefreshRef.current = true
    setEnrichedRows(new Map())
    setScanTrigger(c => c + 1)
  }, [corp])

  // Liquidity enrichment: fetch after scan data arrives
  useEffect(() => {
    if (!activeScan?.sellable || activeScan.sellable.length === 0) return
    const typeIds = [...new Set(activeScan.sellable.map(r => r.name_id))].filter(Boolean)
    if (typeIds.length === 0) return

    setLiquidityLoading(true)
    apiGet<Record<string, LiquidityEntry>>(`/api/lp/liquidity?type_ids=${typeIds.join(',')}&station_id=${stationId}`)
      .then((resp) => {
        const map = new Map<number, LiquidityEntry>()
        for (const [tid, entry] of Object.entries(resp)) {
          map.set(Number(tid), entry)
        }
        setEnrichedRows(map)
      })
      .catch(() => {
        // Silently fail — cells stay as spinners
      })
      .finally(() => setLiquidityLoading(false))
  }, [activeScan?.sellable, stationId])

  // Save scan results to backend for restore on next page load
  useEffect(() => {
    if (data && !data.error && data.sellable) {
      apiPost('/api/lp/save-scan', { tab: 'lp', data }).catch(() => {})
    }
  }, [data])

  // Merge enrichment into rows, compute weighted tradeability, then append the
  // unsellable offers (no Jita market) dimmed at the bottom, as master did.
  const enriched = useMemo(() => {
    if (!activeScan?.sellable) return []
    const sellable = activeScan.sellable.map((row) => {
      const liq = enrichedRows.get(row.name_id)
      if (!liq) return { ...row, unsellable: false }
      return {
        ...row,
        unsellable: false,
        daily_vol: liq.daily_vol,
        days_to_clear: liq.days_to_clear,
        list_price: liq.list_price,
        floor_age: liq.floor_age,
      }
    })
    const scored = withTradeability(sellable, tradeWeight)
    const unsellable = (activeScan.unsellable ?? []).map(r => ({
      ...r, unsellable: true, tradeability: null,
    }))
    return [...scored, ...unsellable]
  }, [activeScan, enrichedRows, tradeWeight])

  // Filtering
  const filtered = useMemo(() => {
    let rows = enriched
    const hasSearch = search.trim().length > 0

    // Search filter (overrides max-spread)
    if (hasSearch) {
      const q = search.toLowerCase()
      rows = rows.filter(r => r.name?.toLowerCase().includes(q))
    } else {
      // Max spread filter (only when not searching) — unsellable rows are exempt
      if (maxSpread < 100) {
        rows = rows.filter(r => r.unsellable || r.spread_pct == null || r.spread_pct <= maxSpread)
      }
    }

    // Hide illiquid: spread >= 25% (unsellable rows are always kept)
    if (hideIlliquid) {
      rows = rows.filter(r => r.unsellable || r.spread_pct == null || r.spread_pct < 25)
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
        render: (r: LpRow) => {
          const isWin = r.isk_per_lp_patient != null && r.isk_per_lp_instant != null && r.isk_per_lp_patient >= r.isk_per_lp_instant
          return (
            <span className={`${(r.isk_per_lp_patient ?? 0) > 0 ? 'text-positive' : 'text-negative'} ${isWin ? 'font-semibold' : ''}`}>
              {fmtIsk(r.isk_per_lp_patient)}
            </span>
          )
        },
      },
      {
        key: 'isk_per_lp_instant',
        header: 'Instant ISK/LP',
        align: 'right' as const,
        width: '110px',
        render: (r: LpRow) => {
          const isWin = r.isk_per_lp_instant != null && r.isk_per_lp_patient != null && r.isk_per_lp_instant > r.isk_per_lp_patient
          return (
            <span className={`${(r.isk_per_lp_instant ?? 0) > 0 ? 'text-positive' : 'text-negative'} ${isWin ? 'font-semibold' : ''}`}>
              {fmtIsk(r.isk_per_lp_instant)}
            </span>
          )
        },
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
        key: 'list_price',
        header: 'List @',
        align: 'right' as const,
        width: '90px',
        render: (r: LpRow) =>
          r.list_price != null ? fmtIsk(r.list_price) : liquidityLoading ? <Spinner /> : '-',
      },
      {
        key: 'floor_age',
        header: 'Floor age',
        align: 'right' as const,
        width: '70px',
        render: (r: LpRow) =>
          r.floor_age != null ? fmtAge(r.floor_age) : liquidityLoading ? <Spinner /> : '-',
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
      {
        key: 'buy_volume',
        header: 'Buy Demand',
        align: 'right' as const,
        width: '90px',
        hiddenByDefault: true,
        render: (r: LpRow) => fmtNum(r.buy_volume, 0),
      },
      {
        key: 'qty',
        header: 'Units',
        align: 'right' as const,
        width: '70px',
        hiddenByDefault: true,
        render: (r: LpRow) => fmtNum(r.qty, 0),
      },
      {
        key: 'output_volume',
        header: 'Vol m³',
        align: 'right' as const,
        width: '75px',
        hiddenByDefault: true,
        render: (r: LpRow) => r.output_volume != null ? fmtNum(r.output_volume, 1) : '-',
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
        <div className="w-48 relative">
          <label className="block text-xs text-foreground-muted mb-1 uppercase">Corporation</label>
          <input
            ref={corpInputRef}
            type="text"
            value={corp}
            onChange={e => { setCorp(e.target.value); setCorpDropdownOpen(true) }}
            onFocus={() => setCorpDropdownOpen(true)}
            onBlur={() => setTimeout(() => setCorpDropdownOpen(false), 150)}
            onKeyDown={e => { if (e.key === 'Enter') { setCorpDropdownOpen(false); handleScan() } }}
            placeholder="e.g. Sisters of EVE"
            className="w-full px-2 py-1.5 rounded bg-background-elevated border border-border text-foreground text-sm placeholder:text-foreground-muted/50 focus:outline-none focus:border-accent-cyan"
          />
          {corpDropdownOpen && corpSuggestions.length > 0 && (
            <ul className="absolute z-50 mt-1 w-64 max-h-52 overflow-y-auto bg-background-panel border border-border rounded shadow-lg text-sm">
              {corpSuggestions.map(c => (
                <li
                  key={c.id}
                  onMouseDown={() => { setCorp(c.name); setCorpDropdownOpen(false) }}
                  className="px-3 py-1.5 cursor-pointer hover:bg-background-elevated truncate"
                >
                  {c.name}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="w-24">
          <label className="block text-xs text-foreground-muted mb-1 uppercase">
            LP Budget
            {lpInfo && (
              <span
                className="ml-1 text-accent-gold normal-case"
                title={lpInfo.matched
                  ? "Locked to your character's loyalty points for this corp"
                  : 'Your character has no LP with this corp'}
              >
                {lpInfo.matched ? '🔒' : '🔒 0'}
              </span>
            )}
          </label>
          <input
            type="number"
            value={budget}
            onChange={e => setBudget(Number(e.target.value))}
            readOnly={lpInfo != null}
            className={`w-full px-2 py-1.5 rounded bg-background-elevated border border-border text-foreground text-sm focus:outline-none focus:border-accent-cyan ${lpInfo != null ? 'opacity-70 cursor-not-allowed' : ''}`}
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
          disabled={isFetching || !corp.trim()}
          title="Force a fresh pull of this corp's LP offers (bypasses the 24h cache)"
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
      {activeScan?.error && (
        <p className="text-negative text-sm">{activeScan.error}</p>
      )}

      {/* Status bar */}
      {activeScan?.corp_name && !activeScan.error && (
        <p className="text-sm text-foreground-muted">
          {activeScan.corp_name} &mdash; {filtered.length} offers shown &middot; list vs instant sell
          {activeScan.unsellable?.length ? ` · ${activeScan.unsellable.length} unsellable` : ''}
        </p>
      )}

      {/* Table */}
      {activeScan?.sellable && (
        <DataTable
          data={filtered}
          columns={columns}
          keyFn={r => r.offer_id}
          onRowClick={(row) => setSelectedRow(row)}
          emptyMessage="No profitable offers found"
          showColumnPicker={true}
          defaultSortKey={sortKey || null}
          defaultSortDir={sortDir}
          onSortChange={(k, d) => { setSortKey(k ?? ''); setSortDir(d ?? 'desc') }}
          defaultColOrder={colOrder}
          onColOrderChange={setColOrder}
          defaultColWidths={colWidths}
          onColWidthsChange={setColWidths}
          rowClassName={(r) =>
            r.unsellable
              ? 'opacity-55 italic'
              : r.illiquid
                ? 'opacity-60'
                : r.max_units === 0
                  ? 'opacity-40 italic'
                  : ''
          }
        />
      )}

      {/* Detail Panel */}
      {selectedRow && activeScan && (
        <LpDetailPanel
          offerId={selectedRow.offer_id}
          corpId={activeScan.corp_id}
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
