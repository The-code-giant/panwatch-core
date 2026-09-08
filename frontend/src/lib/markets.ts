/**
 * The one source of truth for which markets this app knows about.
 *
 * The definitions live in `@tickerkeep/api/markets` so the api and biz-ui
 * packages can share them without importing from the app; this module is the
 * app-side name every page and panel imports.
 */
export * from '@tickerkeep/api/markets'
