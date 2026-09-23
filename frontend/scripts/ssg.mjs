// Static-site-generate the pre-rendered routes using React's real server
// renderer (via a dedicated `vite build --ssr` bundle), then splice each
// result into the client build's dist/index.html.
//
// Replaces the old Playwright-based prerender.mjs: no headless browser is
// launched, so there is no local preview-server origin for asset URLs to
// leak from (that's what caused http://localhost:PORT/... to end up in
// production <link rel="modulepreload"> tags), and renderToString() emits
// the genuine React hydration markers that a DOM snapshot cannot.
import { execSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { readFile, writeFile, rm } from 'node:fs/promises'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const ROOT = join(__dirname, '..')
const DIST = join(ROOT, 'dist')
const SSR_OUT = join(ROOT, 'dist-ssr')
const ROUTES = ['/'] // keep in sync with entry-server.tsx's <Routes>

// Build the SSR bundle for entry-server.tsx (separate from the client
// build already sitting in dist/ — this one runs in Node, never shipped).
execSync('npx vite build --ssr src/entry-server.tsx --outDir dist-ssr --ssrManifest=false', {
  cwd: ROOT,
  stdio: 'inherit',
})

// Vite outputs .mjs when package.json has no "type":"module", .js otherwise.
const ssrEntry = existsSync(join(SSR_OUT, 'entry-server.js'))
  ? join(SSR_OUT, 'entry-server.js')
  : join(SSR_OUT, 'entry-server.mjs')
const { render } = await import(ssrEntry)

const template = await readFile(join(DIST, 'index.html'), 'utf-8')
if (!/<div id="root"><\/div>/.test(template)) {
  throw new Error(
    'dist/index.html#root is not empty — this looks like a stale/already-' +
    'rendered dist/. Run `vite build` fresh before scripts/ssg.mjs.'
  )
}

// Static SEO defaults in index.html (see the comment above <title> there) —
// stripped per-route so the Helmet-produced tags below fully replace them
// instead of duplicating alongside them.
const STATIC_HEAD_TAGS = [
  /<title>.*?<\/title>\s*/s,
  /<meta name="description"[^>]*>\s*/,
  /<link rel="canonical"[^>]*>\s*/,
  /<meta property="og:[a-z:]+"[^>]*>\s*/g,
  /<meta name="twitter:[a-z]+"[^>]*>\s*/g,
]

// React 19's renderToString (legacy, non-streaming) does NOT hoist <title>,
// <meta>, and <link rel="canonical"> to <head>; it emits them inline in the
// component HTML. Strip them from the #root content here — ssg.mjs already
// injects the authoritative versions via helmetContext into <head> below.
const META_INLINE_PATTERNS = [
  /<title[^>]*>[\s\S]*?<\/title>/g,
  /<meta\s[^>]*(?:name=["'](?:description|robots|twitter:[^"']+)["']|property=["']og:[^"']+["'])[^>]*\/?>/g,
  /<link\s[^>]*rel=["']canonical["'][^>]*\/?>/g,
]

for (const route of ROUTES) {
  const { html: rawHtml, head } = render(route)

  const html = META_INLINE_PATTERNS.reduce((h, pat) => h.replace(pat, ''), rawHtml)

  let out = template.replace('<div id="root"></div>', `<div id="root">${html}</div>`)

  if (head) {
    for (const pattern of STATIC_HEAD_TAGS) out = out.replace(pattern, '')
    out = out.replace('</head>', `${head}\n</head>`)
  }

  const out2Path = route === '/' ? join(DIST, 'index.html') : join(DIST, route.slice(1), 'index.html')
  await writeFile(out2Path, out)
  console.log(`✓ SSR-rendered ${route} → ${out2Path.replace(DIST, 'dist')}`)
}

await rm(SSR_OUT, { recursive: true, force: true })
