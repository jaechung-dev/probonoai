import { StrictMode } from 'react'
import { createRoot, hydrateRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { HelmetProvider } from 'react-helmet-async'
import '@fontsource/playfair-display/400.css'
import '@fontsource/playfair-display/600.css'
import '@fontsource/playfair-display/700.css'
import '@fontsource/playfair-display/900.css'
import '@fontsource-variable/inter'
import './index.css'
import App from './App'

const rootEl = document.getElementById('root')!
const app = (
  <StrictMode>
    <HelmetProvider>
      <App />
    </HelmetProvider>
  </StrictMode>
)

// Only the landing route ("/") is pre-rendered, so only hydrate there. On any
// other route the baked-in markup is the wrong (landing) page — discard it and
// client-render cleanly to avoid hydration mismatches.
if (rootEl.hasChildNodes() && window.location.pathname === '/') {
  hydrateRoot(rootEl, app)
  document.documentElement.classList.remove('booting')
} else {
  rootEl.innerHTML = ''
  // flushSync ensures React commits before we reveal #root, preventing a
  // brief blank-screen flash between unbooting and React's first paint.
  flushSync(() => createRoot(rootEl).render(app))
  document.documentElement.classList.remove('booting')
}
