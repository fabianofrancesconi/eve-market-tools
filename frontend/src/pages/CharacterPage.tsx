import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../lib/api-client'
import { fmtIsk, fmtNum } from '../lib/format'
import { useAuth } from '../hooks/use-auth'

interface CharData {
  character_id: number
  name: string
  is_active: boolean
  wallet?: number
  total_sp?: number
  skillqueue?: Array<{ skill_id: number; finished_level: number; finish_date?: string }>
  loyalty_points?: Array<{ corporation_id: number; loyalty_points: number }>
  industry_jobs?: Array<{ job_id: number; activity_id: number; status: string; end_date: string }>
  market_orders?: Array<{ order_id: number; type_id: number; price: number; volume_remain: number; is_buy_order: boolean }>
  error?: string
}

export function CharacterPage() {
  const { isLoggedIn, login } = useAuth()

  const { data: characters } = useQuery<CharData[]>({
    queryKey: ['character-data'],
    queryFn: () => apiGet('/api/character/data'),
    enabled: isLoggedIn,
    refetchInterval: 300_000,
  })

  if (!isLoggedIn) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[50vh] gap-4">
        <p className="text-foreground-muted">Log in with EVE Online to see your character data.</p>
        <button
          onClick={login}
          className="px-4 py-2 rounded bg-accent-cyan text-primary-foreground font-medium hover:bg-accent-cyan/80 transition-colors"
        >
          Log in with EVE
        </button>
      </div>
    )
  }

  if (!characters) {
    return <p className="text-foreground-muted">Loading character data...</p>
  }

  return (
    <div className="space-y-6">
      {characters.map(char => (
        <div key={char.character_id} className="rounded-lg border border-border bg-background-panel p-4">
          <div className="flex items-center gap-3 mb-4">
            <img
              src={`https://images.evetech.net/characters/${char.character_id}/portrait?size=64`}
              alt={char.name}
              className="w-12 h-12 rounded"
            />
            <div>
              <h2 className="text-lg font-semibold">{char.name}</h2>
              {char.is_active && <span className="text-xs text-accent-cyan">Active</span>}
            </div>
          </div>

          {char.error ? (
            <p className="text-negative text-sm">{char.error}</p>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <StatCard label="Wallet" value={fmtIsk(char.wallet)} />
              <StatCard label="Total SP" value={fmtNum(char.total_sp)} />
              <StatCard label="Skill Queue" value={`${char.skillqueue?.length ?? 0} skills`} />
              <StatCard label="Active Orders" value={`${char.market_orders?.length ?? 0}`} />
            </div>
          )}

          {char.industry_jobs && char.industry_jobs.length > 0 && (
            <div className="mt-4">
              <h3 className="text-sm font-medium text-foreground-muted mb-2">Industry Jobs</h3>
              <div className="space-y-1">
                {char.industry_jobs.filter(j => j.status === 'active').slice(0, 5).map(job => (
                  <div key={job.job_id} className="flex justify-between text-sm py-1 border-b border-border/30">
                    <span>Job #{job.job_id}</span>
                    <span className="text-foreground-muted">{new Date(job.end_date).toLocaleString()}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {char.market_orders && char.market_orders.length > 0 && (
            <div className="mt-4">
              <h3 className="text-sm font-medium text-foreground-muted mb-2">Market Orders</h3>
              <div className="space-y-1">
                {char.market_orders.slice(0, 5).map(order => (
                  <div key={order.order_id} className="flex justify-between text-sm py-1 border-b border-border/30">
                    <span className={order.is_buy_order ? 'text-positive' : 'text-accent-cyan'}>
                      {order.is_buy_order ? 'Buy' : 'Sell'} x {order.volume_remain}
                    </span>
                    <span>{fmtIsk(order.price)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded bg-background-elevated p-3">
      <p className="text-xs text-foreground-muted">{label}</p>
      <p className="text-lg font-semibold">{value}</p>
    </div>
  )
}
