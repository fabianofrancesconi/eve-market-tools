import { useQuery } from '@tanstack/react-query'
import { useCallback } from 'react'

interface AuthStatus {
  logged_in: boolean
  characters?: Array<{ character_id: number; name: string; is_active: boolean }>
}

export function useAuth() {
  const { data, refetch } = useQuery<AuthStatus>({
    queryKey: ['auth-status'],
    queryFn: () => fetch('/api/auth/status').then(r => r.json()),
    staleTime: 60_000,
  })

  const login = useCallback(async () => {
    try {
      const res = await fetch('/api/auth/login')
      if (!res.ok) throw new Error(`Login request failed: ${res.status}`)
      const { authorize_url } = await res.json()
      window.location.href = authorize_url
    } catch (err) {
      console.error('Login error:', err)
    }
  }, [])

  const logout = useCallback(async (characterId?: number) => {
    try {
      await fetch('/api/auth/logout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ character_id: characterId }),
      })
      refetch()
    } catch (err) {
      console.error('Logout error:', err)
    }
  }, [refetch])

  return {
    isLoggedIn: data?.logged_in ?? false,
    characters: data?.characters ?? [],
    activeCharacter: data?.characters?.find(c => c.is_active),
    login,
    logout,
    refetch,
  }
}
