import { NavLink } from 'react-router-dom'
import { useAuth } from '../../hooks/use-auth'

const navItems = [
  { to: '/lp', label: 'LP Store' },
  { to: '/arb', label: 'Arbitrage' },
  { to: '/ind', label: 'Industry' },
  { to: '/character', label: 'Character' },
]

export function Header() {
  const { isLoggedIn, activeCharacter, login } = useAuth()

  return (
    <header className="border-b border-border bg-background-panel">
      <div className="container mx-auto px-4 flex items-center h-14 gap-8">
        <div className="flex items-center gap-2">
          <svg viewBox="0 0 32 32" className="w-7 h-7">
            <rect width="32" height="32" rx="4" fill="#080d11" />
            <rect x="3" y="21" width="7" height="8" rx="1" fill="#4fc3f7" />
            <rect x="12.5" y="15" width="7" height="14" rx="1" fill="#4fc3f7" />
            <rect x="22" y="8" width="7" height="21" rx="1" fill="#c8a040" />
            <polyline
              points="6.5,19 16,13 25.5,6"
              stroke="#4caf76"
              strokeWidth="2.5"
              fill="none"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span className="font-semibold text-foreground">EVE Market Tools</span>
        </div>

        <nav className="flex gap-1">
          {navItems.map(({ to, label }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-accent-cyan/15 text-accent-cyan'
                    : 'text-foreground-muted hover:text-foreground hover:bg-background-elevated'
                }`
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="ml-auto">
          {isLoggedIn && activeCharacter ? (
            <div className="flex items-center gap-2">
              <img
                src={`https://images.evetech.net/characters/${activeCharacter.character_id}/portrait?size=32`}
                className="w-6 h-6 rounded"
                alt=""
              />
              <span className="text-sm text-foreground">{activeCharacter.name}</span>
            </div>
          ) : (
            <button
              onClick={login}
              className="px-3 py-1.5 rounded text-sm font-medium bg-accent-cyan/15 text-accent-cyan hover:bg-accent-cyan/25 transition-colors"
            >
              Log in with EVE
            </button>
          )}
        </div>
      </div>
    </header>
  )
}
