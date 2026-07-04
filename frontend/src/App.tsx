import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useQuery } from '@tanstack/react-query'
import { Header } from './components/layout/Header'
import { LpPage } from './pages/LpPage'
import { ArbitragePage } from './pages/ArbitragePage'
import { IndustryPage } from './pages/IndustryPage'
import { CharacterPage } from './pages/CharacterPage'
import { AuthCallbackPage } from './pages/AuthCallbackPage'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      retry: 1,
    },
  },
})

function Footer() {
  const { data } = useQuery<{ version: string }>({
    queryKey: ['health'],
    queryFn: () => fetch('/api/health').then(r => r.json()),
    staleTime: Infinity,
  })
  return (
    <footer className="border-t border-border py-3 text-center text-xs text-foreground-muted">
      EVE Market Tools {data?.version ? `v${data.version}` : ''}
    </footer>
  )
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <div className="min-h-screen bg-background text-foreground flex flex-col">
          <Header />
          <main className="container mx-auto px-4 py-6 flex-1">
            <Routes>
              <Route path="/" element={<Navigate to="/lp" replace />} />
              <Route path="/lp" element={<LpPage />} />
              <Route path="/arb" element={<ArbitragePage />} />
              <Route path="/ind" element={<IndustryPage />} />
              <Route path="/character" element={<CharacterPage />} />
              <Route path="/auth/callback" element={<AuthCallbackPage />} />
            </Routes>
          </main>
          <Footer />
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App
