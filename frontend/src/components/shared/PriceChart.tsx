import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../../lib/api-client'
import {
  ComposedChart,
  Line,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts'

interface HistoryPoint {
  date: string
  average: number
  highest: number
  lowest: number
  volume: number
  order_count: number
}

interface PriceChartProps {
  typeId: number
  regionId: number
}

const STATION_TO_REGION: Record<number, number> = {
  60003760: 10000002,
  60008494: 10000043,
  60011866: 10000032,
  60005686: 10000042,
  60004588: 10000030,
}

export function stationToRegion(stationId: number): number {
  return STATION_TO_REGION[stationId] ?? 10000002
}

export function PriceChart({ typeId, regionId }: PriceChartProps) {
  const { data, isLoading, error } = useQuery<HistoryPoint[]>({
    queryKey: ['lp-history', typeId, regionId],
    queryFn: () => apiGet<HistoryPoint[]>(`/api/lp/history?type_id=${typeId}&region_id=${regionId}`),
    enabled: typeId > 0 && regionId > 0,
    staleTime: 5 * 60 * 1000,
  })

  if (isLoading) {
    return (
      <div className="h-48 flex items-center justify-center text-foreground-muted text-sm">
        Loading price history...
      </div>
    )
  }

  if (error || !data || data.length === 0) {
    return (
      <div className="h-48 flex items-center justify-center text-foreground-muted text-sm">
        No price history available
      </div>
    )
  }

  const maxVol = Math.max(...data.map(d => d.volume))

  return (
    <div className="h-48 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 5, right: 5, left: 5, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10, fill: '#888' }}
            tickFormatter={(v: string) => v.slice(5)}
            interval="preserveStartEnd"
          />
          <YAxis
            yAxisId="price"
            orientation="right"
            tick={{ fontSize: 10, fill: '#888' }}
            tickFormatter={(v: number) => v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(0) + 'K' : String(v)}
            domain={['auto', 'auto']}
          />
          <YAxis
            yAxisId="volume"
            orientation="left"
            tick={{ fontSize: 10, fill: '#888' }}
            domain={[0, maxVol * 3]}
            hide
          />
          <Tooltip
            contentStyle={{
              backgroundColor: '#1a1a2e',
              border: '1px solid #333',
              borderRadius: '4px',
              fontSize: '11px',
            }}
            labelStyle={{ color: '#aaa' }}
            formatter={(value, name) => {
              const v = Number(value)
              if (name === 'volume') return [v.toLocaleString(), 'Volume']
              return [v.toLocaleString(undefined, { maximumFractionDigits: 2 }), name === 'average' ? 'Avg Price' : String(name)]
            }}
          />
          <Bar yAxisId="volume" dataKey="volume" fill="rgba(100,149,237,0.2)" isAnimationActive={false} />
          <Line
            yAxisId="price"
            type="monotone"
            dataKey="average"
            stroke="#22d3ee"
            dot={false}
            strokeWidth={1.5}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}
