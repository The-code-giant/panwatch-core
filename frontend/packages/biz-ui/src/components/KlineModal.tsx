import { DEFAULT_MARKET } from '@panwatch/api/markets'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import InteractiveKline from '@panwatch/biz-ui/components/InteractiveKline'

export default function KlineModal(props: {
  open: boolean
  onOpenChange: (open: boolean) => void
  symbol: string
  market: string
  title?: string
  description?: string
  initialInterval?: '1d' | '1w' | '1m'
  initialDays?: '60' | '120' | '250'
}) {
  const symbol = String(props.symbol || '').trim()
  const market = String(props.market || '').trim() || DEFAULT_MARKET

  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent className="max-w-5xl">
        <DialogHeader>
          <DialogTitle>{props.title || (symbol ? `K-line: ${symbol}` : 'K-line')}</DialogTitle>
          <DialogDescription>
            {props.description || 'Switch between daily/weekly/monthly K-line, with MA/volume/MACD.'}
          </DialogDescription>
        </DialogHeader>
        {symbol ? (
          <InteractiveKline
            symbol={symbol}
            market={market}
            initialInterval={props.initialInterval}
            initialDays={props.initialDays}
          />
        ) : (
          <div className="text-[12px] text-muted-foreground py-8 text-center">No stock selected</div>
        )}
      </DialogContent>
    </Dialog>
  )
}
