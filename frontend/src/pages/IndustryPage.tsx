import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSse } from '../hooks/use-sse'
import { apiGet, sseUrl } from '../lib/api-client'
import { fmtIsk, fmtPct, fmtDuration } from '../lib/format'
import { DataTable } from '../components/shared/DataTable'
import { ProgressBar } from '../components/shared/ProgressBar'
import { DEFAULTS } from '../lib/constants'

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
}

interface MarketGroup {
  id: number
  name: string
}

export function IndustryPage() {
  const [groupId, setGroupId] = useState('')
  const [url, setUrl] = useState<string | null>(null)
  const [trigger, setTrigger] = useState(0)
  const { progress, message, result, isStreaming } = useSse<IndRow[]>(url, trigger)

  const { data: groups } = useQuery<MarketGroup[]>({
    queryKey: ['ind-groups'],
    queryFn: () => apiGet('/api/industry/groups'),
  })

  const handleScan = () => {
    setUrl(sseUrl('/api/industry/scan', {
      group_ids: groupId,
      me: 10,
      te: 20,
      job_rate: 0.05,
      sales_tax: DEFAULTS.salesTax,
      broker_fee: DEFAULTS.brokerFee,
      runs: 1,
      skills_level: 4,
    }))
    setTrigger(t => t + 1)
  }

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
  ]

  return (
    <div className="space-y-4">
      <div className="flex gap-3 items-end">
        <div className="w-64">
          <label className="block text-xs text-foreground-muted mb-1">Category</label>
          <select
            value={groupId}
            onChange={e => setGroupId(e.target.value)}
            className="w-full px-3 py-2 rounded bg-background-elevated border border-border text-foreground focus:outline-none focus:border-accent-cyan"
          >
            <option value="">All categories</option>
            {groups?.map(g => (
              <option key={g.id} value={g.id}>{g.name}</option>
            ))}
          </select>
        </div>
        <button
          onClick={handleScan}
          disabled={isStreaming}
          className="px-4 py-2 rounded bg-accent-cyan text-primary-foreground font-medium hover:bg-accent-cyan/80 disabled:opacity-50 transition-colors"
        >
          {isStreaming ? 'Scanning...' : 'Scan'}
        </button>
      </div>

      {isStreaming && <ProgressBar progress={progress} message={message} />}

      {result && (
        <>
          <p className="text-sm text-foreground-muted">{result.length} items evaluated</p>
          <DataTable
            data={result}
            columns={columns}
            keyFn={r => r.blueprint_id}
            emptyMessage="No items found"
          />
        </>
      )}
    </div>
  )
}
