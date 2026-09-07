import { readFile, writeFile, readdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import { createServer } from 'vite'
import { gzipSync } from 'node:zlib'

// Core has no public marketing pages. Exercise the extraction stub through
// Vite's SSR loader, then emit the product shell used by the static resolver.
const root = process.cwd()
const dist = resolve(root, 'dist')
const server = await createServer({
  server: { middlewareMode: true, watch: null, ws: false },
  optimizeDeps: { noDiscovery: true, include: [] },
  appType: 'custom',
})
try {
  const { render, paths } = await server.ssrLoadModule('/src/marketing/prerender.tsx')
  const page = render('/', '')
  if (paths.length !== 0 || page.html !== '' || page.head !== '') {
    throw new Error('Core prerender expects an empty marketing boundary')
  }
  const template = await readFile(resolve(dist, 'index.html'), 'utf8')
  for (const name of ['app-shell.html', '404.html']) {
    await writeFile(resolve(dist, name), template)
  }
  for (const file of await readdir(dist, { recursive: true })) {
    if (!/\.(html|js|css|json|xml|txt)$/.test(file)) continue
    const target = resolve(dist, file)
    await writeFile(target + '.gz', gzipSync(await readFile(target), { level: 9 }))
  }
  console.log('Core product shell and 404 fallback compressed; 0 marketing pages.')
} finally {
  await server.close()
}
