import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import { App } from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import { ToastProvider } from './components/ui/Toast'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 10_000 },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        {/* The backstop, for a throw in the shell itself — the per-page boundary
            inside App cannot catch one raised above it. */}
        <ErrorBoundary>
          <App />
        </ErrorBoundary>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
)
