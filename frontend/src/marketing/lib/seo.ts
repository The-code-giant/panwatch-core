import { useEffect } from 'react'

/**
 * tickerkeep-core stub. Same option shape as the marketing site's useSeo so that
 * src/ProductApp.tsx compiles unchanged; only the document title is applied.
 */
export interface SeoOptions {
  title: string
  description: string
  path: string
  image?: string
  article?: { published: string }
  noindex?: boolean
}

export function useSeo(opts: SeoOptions): void {
  const { title } = opts
  useEffect(() => {
    const previousTitle = document.title
    document.title = title
    return () => {
      document.title = previousTitle
    }
  }, [title])
}
