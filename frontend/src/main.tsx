import React, { lazy, Suspense } from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, useLocation } from 'react-router-dom'
import MarketingRoutes from './marketing'
import { isMarketingPath } from './marketing/routes'
import './index.css'

const ProductApp = lazy(() => import('./ProductApp'))

function SiteRouter() {
  const { pathname } = useLocation()
  if (isMarketingPath(pathname)) return <MarketingRoutes />
  return <Suspense fallback={<div className="min-h-screen bg-background" role="status" aria-label="Loading application" />}><ProductApp /></Suspense>
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <SiteRouter />
    </BrowserRouter>
  </React.StrictMode>
)
