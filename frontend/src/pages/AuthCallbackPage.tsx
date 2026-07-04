import { useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'

export function AuthCallbackPage() {
  const [searchParams] = useSearchParams()

  useEffect(() => {
    const code = searchParams.get('code')
    const state = searchParams.get('state')

    if (code && state) {
      window.location.href = `/api/auth/callback?code=${encodeURIComponent(code)}&state=${encodeURIComponent(state)}`
    } else {
      window.location.href = '/lp'
    }
  }, [searchParams])

  return (
    <div className="flex items-center justify-center min-h-[50vh]">
      <p className="text-foreground-muted">Completing EVE SSO login...</p>
    </div>
  )
}
