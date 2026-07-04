import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
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

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <div className="min-h-screen bg-background text-foreground">
          <Header />
          <main className="container mx-auto px-4 py-6">
            <Routes>
              <Route path="/" element={<Navigate to="/lp" replace />} />
              <Route path="/lp" element={<LpPage />} />
              <Route path="/arb" element={<ArbitragePage />} />
              <Route path="/ind" element={<IndustryPage />} />
              <Route path="/character" element={<CharacterPage />} />
              <Route path="/auth/callback" element={<AuthCallbackPage />} />
            </Routes>
          </main>
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App
