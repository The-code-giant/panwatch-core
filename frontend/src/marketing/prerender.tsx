/**
 * panwatch-core stub consumed by scripts/prerender.mjs. With no marketing pages
 * there is nothing to prerender: `paths` is empty and `render` returns an empty
 * document body, so the build step still produces dist/app-shell.html and the
 * gzip variants the backend (src/web/static_site.py) negotiates.
 */
export const paths: string[] = []

export function render(_path: string, _origin: string): { html: string; head: string } {
  return { html: '', head: '' }
}
