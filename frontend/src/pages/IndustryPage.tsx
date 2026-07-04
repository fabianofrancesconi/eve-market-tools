import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSse } from '../hooks/use-sse'
import { apiGet, sseUrl } from '../lib/api-client'
import { fmtIsk, fmtPct, fmtDuration } from '../lib/format'
import { DataTable } from '../components/shared/DataTable'
import { ProgressBar } from '../components/shared/ProgressBar'

const TRADE_HUBS: Record<number, string> = {
  60003760: 'Jita 4-4',
  60008494: 'Amarr VIII',
  60011866: 'Dodixie IX',
  60005686: 'Hek VIII',
  60004588: 'Rens VI',
}

interface IndRow {
  blueprint_id: number
  product_id: number
  product_name: string
  isk_per_hour_best: number | null
  profit_best: number | null
  margin_best: number | null
  material_cost: number
  build_time: number | null
  tech_level: number
  buildable: boolean
  group_name?: string
}

interface MarketGroup {
  id: number
  name: string
}

export function IndustryPage() {
  const [stationId, setStationId] = useState(60003760)
  const [salesTax, setSalesTax] = useState(4.5)
  const [brokerFee, setBrokerFee] = useState(1.5)
  const [jobRate, setJobRate] = useState(6.0)
  const [runs, setRuns] = useState(1)
  const [groupId, setGroupId] = useState('all')
  const [search, setSearch] = useState('')
  const [buildableOnly, setBuildableOnly] = useState(false)
  const [hideT2, setHideT2] = useState(false)

  const [url, setUrl] = useState<string | null>(null)
  const [trigger, setTrigger] = useState(0)
  const { progress, message, result, isStreaming } = useSse<IndRow[]>(url, trigger)

  const { data: groups } = useQuery<MarketGroup[]>({
    queryKey: ['ind-groups'],
    queryFn: () => apiGet('/api/industry/groups'),
  })

  const handleScan = () => {
    setUrl(sseUrl('/api/industry/scan', {
      market_group: groupId,
      station: stationId,
      job_rate: jobRate,
      sales_tax: salesTax,
      broker: brokerFee,
      runs,
      buildable_only: buildableOnly,
      hide_t2: hideT2,
    }))
    setTrigger(t => t + 1)
  }

  const filtered = useMemo(() => {
    if (!result) return []
    let rows = result
    if (search.trim()) {
      const q = search.toLowerCase()
      rows = rows.filter(r => r.product_name?.toLowerCase().includes(q))
    }
    return rows
  }, [result, search])

  const columns = [
    { key: 'product_name', header: 'Product', width: '250px' },
    {
      key: 'isk_per_hour_best',
      header: 'ISK/Hour',
      align: 'right' as const,
      render: (r: IndRow) => (
        <span className={r.isk_per_hour_best && r.isk_per_hour_best > 0 ? 'text-positive' : 'text-negative'}>
          {fmtIsk(r.isk_per_hour_best)}
        </span>
      ),
    },
    { key: 'profit_best', header: 'Profit/Run', align: 'right' as const, render: (r: IndRow) => fmtIsk(r.profit_best) },
    { key: 'margin_best', header: 'Margin', align: 'right' as const, render: (r: IndRow) => fmtPct(r.margin_best ? r.margin_best * 100 : null) },
    { key: 'material_cost', header: 'Mat. Cost', align: 'right' as const, render: (r: IndRow) => fmtIsk(r.material_cost) },
    { key: 'build_time', header: 'Build Time', align: 'right' as const, render: (r: IndRow) => fmtDuration(r.build_time) },
    {
      key: 'tech_level',
      header: 'Tech',
      align: 'center' as const,
      render: (r: IndRow) => <span className={r.tech_level === 2 ? 'text-accent-gold' : ''}>T{r.tech_level}</span>,
    },
    { key: 'group_name', header: 'Category' },
  ]

  return (
    <div className="space-y-3">
      {/* Top bar: Sales Tax, Broker Fee, Job Rate */}
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
        <label className="flex items-center gap-1">
          Job Rate %
          <input
            type="number"
            step="0.01"
            value={jobRate}
            onChange={e => setJobRate(Number(e.target.value))}
            className="w-16 px-2 py-1 rounded bg-background-elevated border border-border text-foreground text-sm"
          />
        </label>
      </div>

      {/* Main controls row */}
      <div className="flex flex-wrap gap-2 items-end">
        <div className="w-52">
          <label className="block text-xs text-foreground-muted mb-1 uppercase">Category</label>
          <select
            value={groupId}
            onChange={e => setGroupId(e.target.value)}
            className="w-full px-2 py-1.5 rounded bg-background-elevated border border-border text-foreground text-sm focus:outline-none focus:border-accent-cyan"
          >
            <option value="all">All categories</option>
            {groups?.map(g => (
              <option key={g.id} value={g.id}>{g.name}</option>
            ))}
          </select>
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
        <div className="w-16">
          <label className="block text-xs text-foreground-muted mb-1 uppercase">Runs</label>
          <input
            type="number"
            value={runs}
            min={1}
            onChange={e => setRuns(Math.max(1, Number(e.target.value)))}
            className="w-full px-2 py-1.5 rounded bg-background-elevated border border-border text-foreground text-sm focus:outline-none focus:border-accent-cyan"
          />
        </div>
        <div className="w-36">
          <label className="block text-xs text-foreground-muted mb-1 uppercase">Search</label>
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="product name..."
            className="w-full px-2 py-1.5 rounded bg-background-elevated border border-border text-foreground text-sm placeholder:text-foreground-muted/50 focus:outline-none focus:border-accent-cyan"
          />
        </div>
        <button
          onClick={handleScan}
          disabled={isStreaming}
          className="px-4 py-1.5 rounded bg-accent-cyan text-primary-foreground font-medium text-sm hover:bg-accent-cyan/80 disabled:opacity-50 transition-colors"
        >
          {isStreaming ? 'Scanning...' : 'Scan'}
        </button>
      </div>

      {/* Filter toggles */}
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <label className="flex items-center gap-1.5 text-foreground-muted cursor-pointer">
          <input
            type="checkbox"
            checked={buildableOnly}
            onChange={e => setBuildableOnly(e.target.checked)}
            className="rounded border-border"
          />
          Buildable only
        </label>
        <label className="flex items-center gap-1.5 text-foreground-muted cursor-pointer">
          <input
            type="checkbox"
            checked={hideT2}
            onChange={e => setHideT2(e.target.checked)}
            className="rounded border-border"
          />
          Hide T2
        </label>
      </div>

      {isStreaming && <ProgressBar progress={progress} message={message} />}

      {result && (
        <>
          <p className="text-sm text-foreground-muted">{filtered.length} of {result.length} items shown</p>
          <DataTable
            data={filtered}
            columns={columns}
            keyFn={r => r.blueprint_id}
            emptyMessage="No items found"
          />
        </>
      )}
    </div>
  )
}
