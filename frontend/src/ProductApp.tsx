import { useEffect } from 'react'
import App from './App'
import { ToastProvider } from '@panwatch/base-ui/components/ui/toast'
import { useLocation } from 'react-router-dom'
import { useSeo } from './marketing/lib/seo'

/** App-only dependency: the public site uses small native SVG research diagrams. */
function loadChartLibrary() {
  if (document.querySelector('script[data-panwatch-charts]')) return
  const script = document.createElement('script')
  script.dataset.panwatchCharts = 'true'
  script.async = true
  script.src = 'https://unpkg.com/lightweight-charts@5.2.1/dist/lightweight-charts.standalone.production.js'
  script.onerror = () => {
    script.onerror = null
    script.src = 'https://cdn.jsdelivr.net/npm/lightweight-charts@5.2.1/dist/lightweight-charts.standalone.production.js'
  }
  document.head.appendChild(script)
}

export default function ProductApp() {
  const { pathname } = useLocation()
  useSeo({ title: 'PanWatch workspace', description: 'Sign in to your self-hosted PanWatch research workspace.', path: pathname, noindex: true })
  useEffect(loadChartLibrary, [])
  return <ToastProvider><App /></ToastProvider>
}
