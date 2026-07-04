import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { useAuth } from '../../hooks/use-auth'
import { fmtIsk } from '../../lib/format'

const navItems = [
  { to: '/lp', label: 'LP Store' },
  { to: '/arb', label: 'Arbitrage' },
  { to: '/ind', label: 'Industry' },
  { to: '/character', label: 'Overview' },
]

export function Header() {
  const { isLoggedIn, activeCharacter, characters, login, logout, switchCharacter } = useAuth()
  const [dropdownOpen, setDropdownOpen] = useState(false)

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

        <div className="ml-auto relative">
          {isLoggedIn && activeCharacter ? (
            <>
              <button
                onClick={() => setDropdownOpen(!dropdownOpen)}
                onBlur={() => setTimeout(() => setDropdownOpen(false), 150)}
                className="flex items-center gap-2 px-2 py-1 rounded hover:bg-background-elevated transition-colors"
              >
                <img
                  src={`https://images.evetech.net/characters/${activeCharacter.character_id}/portrait?size=32`}
                  className="w-7 h-7 rounded"
                  alt=""
                />
                <div className="text-left">
                  <span className="text-sm text-foreground block leading-tight">{activeCharacter.name}</span>
                  {activeCharacter.wallet != null && (
                    <span className="text-xs text-accent-gold leading-tight">{fmtIsk(activeCharacter.wallet)}</span>
                  )}
                </div>
                <svg className="w-3 h-3 text-foreground-muted" viewBox="0 0 12 12">
                  <path d="M3 5l3 3 3-3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </button>

              {dropdownOpen && (
                <div className="absolute right-0 mt-1 w-52 bg-background-panel border border-border rounded shadow-lg z-50 py-1">
                  {characters && characters.length > 1 && (
                    <>
                      <div className="px-3 py-1 text-[10px] uppercase text-foreground-muted">Switch Character</div>
                      {characters.filter(c => c.character_id !== activeCharacter.character_id).map(c => (
                        <button
                          key={c.character_id}
                          onMouseDown={() => { switchCharacter(c.character_id); setDropdownOpen(false) }}
                          className="w-full text-left px-3 py-1.5 flex items-center gap-2 hover:bg-background-elevated text-sm"
                        >
                          <img
                            src={`https://images.evetech.net/characters/${c.character_id}/portrait?size=32`}
                            className="w-5 h-5 rounded"
                            alt=""
                          />
                          {c.name}
                        </button>
                      ))}
                      <div className="border-t border-border my-1" />
                    </>
                  )}
                  <button
                    onMouseDown={() => { login(); setDropdownOpen(false) }}
                    className="w-full text-left px-3 py-1.5 text-sm text-foreground-muted hover:text-foreground hover:bg-background-elevated"
                  >
                    + Add character
                  </button>
                  <button
                    onMouseDown={() => { logout(activeCharacter.character_id); setDropdownOpen(false) }}
                    className="w-full text-left px-3 py-1.5 text-sm text-negative hover:bg-background-elevated"
                  >
                    Remove {activeCharacter.name}
                  </button>
                </div>
              )}
            </>
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
