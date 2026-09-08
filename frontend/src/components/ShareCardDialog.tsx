import { useRef, useState, type ReactNode } from 'react'
import { toPng } from 'html-to-image'
import { ImageDown, Loader2 } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@tickerkeep/base-ui/components/ui/dialog'
import { Button } from '@tickerkeep/base-ui/components/ui/button'

interface ShareCardDialogProps {
  open: boolean
  onClose: () => void
  /** Exported PNG filename (without extension). */
  filename: string
  /** Fixed card width, defaults to 640. */
  width?: number
  /** The card's face content, passed in by each business card. Colors must be explicit inline styles, not theme CSS variables. */
  children: ReactNode
}

/**
 * Common shell for share cards: a unified Dialog + fixed-width card container + brand footer + "Download Image" button.
 *
 * Design notes:
 * - The card container has a fixed width (640px by default), with its own white→#f8fafc gradient background, rounded
 *   corners, padding, system font, and explicit dark text, ensuring the exported PNG looks the same under any theme
 *   (light/dark). Each business card only needs to supply its "face" children.
 * - The footer (disclaimer + TickerKeep · github promo line) is rendered uniformly by the shell as the consistency
 *   anchor for all share cards.
 * - "Download Image" uses html-to-image's toPng (pixelRatio:2, cacheBust:true) to export as ${filename}.png.
 */
export default function ShareCardDialog({
  open,
  onClose,
  filename,
  width = 640,
  children,
}: ShareCardDialogProps) {
  const cardRef = useRef<HTMLDivElement>(null)
  const [busy, setBusy] = useState(false)

  const handleDownload = async () => {
    if (busy || !cardRef.current) return
    setBusy(true)
    try {
      const dataUrl = await toPng(cardRef.current, { pixelRatio: 2, cacheBust: true })
      const link = document.createElement('a')
      link.download = `${filename}.png`
      link.href = dataUrl
      link.click()
    } catch (e) {
      alert(e instanceof Error ? `Image generation failed: ${e.message}` : 'Image generation failed, please try again')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Share Image</DialogTitle>
          <DialogDescription>Export a clean card you can share on Xueqiu / WeChat groups.</DialogDescription>
        </DialogHeader>

        {/* Preview area: outer uses the theme background, inner card has its own explicit colors */}
        <div className="flex justify-center overflow-x-auto rounded-xl bg-accent/30 p-4 scrollbar">
          {/* Export card: fixed width, all colors explicit inline, not dependent on theme CSS variables */}
          <div
            ref={cardRef}
            style={{
              width,
              boxSizing: 'border-box',
              background: 'linear-gradient(180deg, #ffffff 0%, #f8fafc 100%)',
              borderRadius: 24,
              padding: '32px 36px',
              border: '1px solid #e2e8f0',
              fontFamily:
                '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif',
              color: '#0f172a',
            }}
          >
            {/* Business card face */}
            {children}

            {/* Divider */}
            <div style={{ height: 1, background: '#e2e8f0', margin: '24px 0 16px' }} />

            {/* Footer: disclaimer + brand promo line (consistent across all share cards) */}
            <div style={{ fontSize: 12, color: '#94a3b8', lineHeight: 1.6 }}>
              For reference only, not investment advice
            </div>
            <div
              style={{
                marginTop: 8,
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                fontSize: 13.5,
                fontWeight: 700,
                color: '#0f172a',
              }}
            >
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: 22,
                  height: 22,
                  borderRadius: 6,
                  background: '#0f172a',
                  color: '#ffffff',
                  fontSize: 13,
                  fontWeight: 900,
                  flexShrink: 0,
                }}
              >
                P
              </span>
              <span>TickerKeep</span>
            </div>
          </div>
        </div>

        {/* Actions */}
        <div className="mt-4 flex items-center justify-end gap-3">
          <Button variant="outline" size="sm" className="h-9" onClick={onClose} disabled={busy}>
            Close
          </Button>
          <Button size="sm" className="h-9" onClick={() => void handleDownload()} disabled={busy}>
            {busy ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <ImageDown className="w-3.5 h-3.5" />
            )}
            {busy ? 'Generating…' : 'Download Image'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
