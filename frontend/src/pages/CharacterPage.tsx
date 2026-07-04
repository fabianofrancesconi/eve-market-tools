import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../lib/api-client'
import { fmtIsk, fmtNum } from '../lib/format'
import { useAuth } from '../hooks/use-auth'

interface SkillQueueEntry {
  skill_id: number
  skill_name?: string
  finished_level: number
  finish_date?: string
}

interface LoyaltyEntry {
  corporation_id: number
  corporation_name?: string
  loyalty_points: number
}

interface IndustryJob {
  job_id: number
  activity_id: number
  blueprint_type_id: number
  product_type_id: number
  product_name?: string
  status: string
  runs: number
  end_date: string
}

interface MarketOrder {
  order_id: number
  type_id: number
  type_name?: string
  price: number
  volume_remain: number
  volume_total: number
  is_buy_order: boolean
  issued: string
  duration: number
  location_id: number
}

interface CharData {
  character_id: number
  name: string
  is_active: boolean
  wallet?: number | null
  total_sp?: number | null
  unallocated_sp?: number | null
  skillqueue?: SkillQueueEntry[]
  loyalty_points?: LoyaltyEntry[]
  industry_jobs?: IndustryJob[]
  market_orders?: MarketOrder[]
  error?: string
}

const ACTIVITIES: Record<number, string> = {
  1: 'Manufacturing',
  3: 'TE Research',
  4: 'ME Research',
  5: 'Copying',
  8: 'Invention',
  9: 'Reactions',
}

function timeUntil(dateStr: string): string {
  const diff = new Date(dateStr).getTime() - Date.now()
  if (diff <= 0) return '✓ Ready'
  const h = Math.floor(diff / 3600000)
  const m = Math.floor((diff % 3600000) / 60000)
  if (h >= 24) {
    const d = Math.floor(h / 24)
    return `${d}d ${h % 24}h`
  }
  return `${h}h ${m}m`
}

function postedAge(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime()
  if (diff < 0) return '-'
  const h = Math.floor(diff / 3600000)
  if (h < 1) return '<1h'
  if (h < 24) return `${h}h`
  const d = Math.floor(h / 24)
  return `${d}d`
}

export function CharacterPage() {
  const { isLoggedIn, login } = useAuth()

  const { data: characters, isLoading } = useQuery<CharData[]>({
    queryKey: ['character-data'],
    queryFn: () => apiGet('/api/character/data'),
    enabled: isLoggedIn,
    refetchInterval: 300_000,
  })

  if (!isLoggedIn) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[50vh] gap-4">
        <p className="text-foreground-muted text-center max-w-md">
          Log in with EVE Online to see your wallet, skills, loyalty points,
          running industry jobs and active market orders here.
        </p>
        <button
          onClick={login}
          className="px-4 py-2 rounded bg-accent-cyan text-primary-foreground font-medium hover:bg-accent-cyan/80 transition-colors"
        >
          Log in with EVE
        </button>
      </div>
    )
  }

  if (isLoading || !characters) {
    return <p className="text-foreground-muted">Loading character data...</p>
  }

  return <CharacterTabView characters={characters} />
}

function CharacterTabView({ characters }: { characters: CharData[] }) {
  const [activeTab, setActiveTab] = useState<number | 'all'>(
    characters.length > 1 ? 'all' : characters[0]?.character_id ?? 'all'
  )

  const visibleChars = activeTab === 'all'
    ? characters
    : characters.filter(c => c.character_id === activeTab)

  return (
    <div className="space-y-4">
      {characters.length > 1 && (
        <div className="flex gap-1 border-b border-border pb-1">
          <button
            onClick={() => setActiveTab('all')}
            className={`px-3 py-1.5 text-sm rounded-t transition-colors ${
              activeTab === 'all'
                ? 'bg-accent-cyan/15 text-accent-cyan border-b-2 border-accent-cyan'
                : 'text-foreground-muted hover:text-foreground'
            }`}
          >
            All
          </button>
          {characters.map(c => (
            <button
              key={c.character_id}
              onClick={() => setActiveTab(c.character_id)}
              className={`px-3 py-1.5 text-sm rounded-t flex items-center gap-1.5 transition-colors ${
                activeTab === c.character_id
                  ? 'bg-accent-cyan/15 text-accent-cyan border-b-2 border-accent-cyan'
                  : 'text-foreground-muted hover:text-foreground'
              }`}
            >
              <img
                src={`https://images.evetech.net/characters/${c.character_id}/portrait?size=32`}
                className="w-4 h-4 rounded"
                alt=""
              />
              {c.name}
            </button>
          ))}
        </div>
      )}

      <div className="space-y-8">
        {visibleChars.map(char => (
          <CharacterPanel key={char.character_id} char={char} />
        ))}
      </div>
    </div>
  )
}

function CharacterPanel({ char }: { char: CharData }) {
  return (
    <div className="space-y-4">
      {/* Character header */}
      <div className="flex items-center gap-3">
        <img
          src={`https://images.evetech.net/characters/${char.character_id}/portrait?size=64`}
          alt={char.name}
          className="w-12 h-12 rounded"
        />
        <div>
          <h2 className="text-lg font-semibold text-accent-cyan">{char.name}</h2>
          <div className="flex items-center gap-3 text-sm text-foreground-muted">
            {char.wallet != null && <span className="text-accent-gold">{fmtIsk(char.wallet)} ISK</span>}
            {char.total_sp != null && <span>{fmtNum(char.total_sp)} SP</span>}
            {char.is_active && <span className="text-accent-cyan text-xs">★ Active</span>}
          </div>
        </div>
      </div>

      {char.error && (
        <p className="text-negative text-sm">{char.error}</p>
      )}

      {!char.error && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Industry Jobs */}
          <Card title={`Industry Jobs (${char.industry_jobs?.filter(j => j.status === 'active').length ?? 0})`} wide={true}>
            {char.industry_jobs && char.industry_jobs.filter(j => j.status === 'active').length > 0 ? (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-foreground-muted border-b border-border/50">
                    <th className="text-left py-1.5">Product</th>
                    <th className="text-left py-1.5">Activity</th>
                    <th className="text-right py-1.5">Runs</th>
                    <th className="text-right py-1.5">Time Left</th>
                  </tr>
                </thead>
                <tbody>
                  {char.industry_jobs.filter(j => j.status === 'active').map(job => (
                    <tr key={job.job_id} className="border-b border-border/30">
                      <td className="py-1.5">{job.product_name || `#${job.product_type_id}`}</td>
                      <td className="py-1.5 text-foreground-muted">{ACTIVITIES[job.activity_id] || `Activity ${job.activity_id}`}</td>
                      <td className="py-1.5 text-right">{job.runs}</td>
                      <td className={`py-1.5 text-right ${timeUntil(job.end_date) === '✓ Ready' ? 'text-positive' : ''}`}>
                        {timeUntil(job.end_date)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="text-foreground-muted text-xs">No active jobs.</p>
            )}
          </Card>

          {/* Skill Queue */}
          <Card title={`Skill Queue (${char.skillqueue?.length ?? 0})`}>
            {char.skillqueue && char.skillqueue.length > 0 ? (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-foreground-muted border-b border-border/50">
                    <th className="text-left py-1.5">Skill</th>
                    <th className="text-center py-1.5">Level</th>
                    <th className="text-right py-1.5">Finishes</th>
                  </tr>
                </thead>
                <tbody>
                  {char.skillqueue.slice(0, 10).map((sq, i) => (
                    <tr key={i} className="border-b border-border/30">
                      <td className="py-1.5">{sq.skill_name || `Skill ${sq.skill_id}`}</td>
                      <td className="py-1.5 text-center">{['I','II','III','IV','V'][sq.finished_level - 1] || sq.finished_level}</td>
                      <td className="py-1.5 text-right text-foreground-muted">
                        {sq.finish_date ? new Date(sq.finish_date).toLocaleDateString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="text-foreground-muted text-xs">Skill queue is empty.</p>
            )}
          </Card>

          {/* Loyalty Points */}
          <Card title="Loyalty Points">
            {char.loyalty_points && char.loyalty_points.length > 0 ? (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-foreground-muted border-b border-border/50">
                    <th className="text-left py-1.5">Corporation</th>
                    <th className="text-right py-1.5">LP</th>
                  </tr>
                </thead>
                <tbody>
                  {char.loyalty_points.map(lp => (
                    <tr key={lp.corporation_id} className="border-b border-border/30">
                      <td className="py-1.5">{lp.corporation_name || `Corp #${lp.corporation_id}`}</td>
                      <td className="py-1.5 text-right">{fmtNum(lp.loyalty_points)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="text-foreground-muted text-xs">No loyalty points.</p>
            )}
          </Card>

          {/* Market Orders */}
          <Card title={`Market Orders (${char.market_orders?.length ?? 0})`} wide={true}>
            {char.market_orders && char.market_orders.length > 0 ? (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-foreground-muted border-b border-border/50">
                    <th className="text-left py-1.5">Item</th>
                    <th className="text-center py-1.5">Side</th>
                    <th className="text-right py-1.5">Remaining</th>
                    <th className="text-right py-1.5">Price</th>
                    <th className="text-right py-1.5">Total Value</th>
                    <th className="text-right py-1.5">Posted</th>
                  </tr>
                </thead>
                <tbody>
                  {char.market_orders.map(order => (
                    <tr key={order.order_id} className="border-b border-border/30">
                      <td className="py-1.5">{order.type_name || `Type #${order.type_id}`}</td>
                      <td className={`py-1.5 text-center ${order.is_buy_order ? 'text-negative' : 'text-positive'}`}>
                        {order.is_buy_order ? 'Buy' : 'Sell'}
                      </td>
                      <td className="py-1.5 text-right">{order.volume_remain}/{order.volume_total}</td>
                      <td className="py-1.5 text-right">{fmtIsk(order.price)}</td>
                      <td className="py-1.5 text-right">{fmtIsk(order.price * order.volume_remain)}</td>
                      <td className="py-1.5 text-right text-foreground-muted">{postedAge(order.issued)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t border-border font-medium">
                    <td className="py-1.5" colSpan={5}>Total</td>
                    <td className="py-1.5 text-right">
                      {fmtIsk(char.market_orders.reduce((s, o) => s + o.price * o.volume_remain, 0))}
                    </td>
                  </tr>
                </tfoot>
              </table>
            ) : (
              <p className="text-foreground-muted text-xs">No open orders.</p>
            )}
          </Card>
        </div>
      )}
    </div>
  )
}

function Card({ title, children, wide }: { title: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <div className={`rounded border border-border bg-background-elevated p-3 ${wide ? 'md:col-span-2' : ''}`}>
      <h3 className="text-sm font-medium text-foreground-muted mb-2">{title}</h3>
      {children}
    </div>
  )
}
