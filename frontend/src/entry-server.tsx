// SSR-only entry point (built separately via `vite build --ssr`, never
// shipped to the browser). Renders a route to a real HTML string with
// React's server renderer — no headless browser, so there is no "current
// page origin" for asset URLs to leak from, and the hydration markers
// (<!--$--> etc.) are the genuine ones React expects on the client.
import { StrictMode, Suspense } from 'react'
import { renderToString } from 'react-dom/server'
import { StaticRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { HelmetProvider, type HelmetServerState } from 'react-helmet-async'
import { AuthProvider } from '../context/auth'
import HomePage from './pages/HomePage'

// Only "/" is server-rendered today. Other routes are added here (as plain
// eager imports, same as HomePage) the day they also need a pre-rendered
// shell — keep this list in sync with the ROUTES array in scripts/ssg.mjs.
export function render(url: string): { html: string; head: string } {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
  })
  const helmetContext: { helmet?: HelmetServerState } = {}

  const html = renderToString(
    <StrictMode>
      <HelmetProvider context={helmetContext}>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <StaticRouter location={url}>
              {/* Mirror the <Suspense> in client App.tsx so the server HTML
                  has the <!--$--> boundary markers hydrateRoot expects. */}
              <Suspense>
                <Routes>
                  <Route path="/" element={<HomePage />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </Suspense>
            </StaticRouter>
          </AuthProvider>
        </QueryClientProvider>
      </HelmetProvider>
    </StrictMode>
  )

  const { helmet } = helmetContext
  const head = helmet
    ? [helmet.title.toString(), helmet.meta.toString(), helmet.link.toString()].join('\n')
    : ''

  return { html, head }
}
