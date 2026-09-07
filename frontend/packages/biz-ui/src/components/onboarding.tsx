import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { TrendingUp, Bot, Bell, CheckCircle2, ChevronRight, Sparkles } from 'lucide-react'
import { Dialog, DialogContent } from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'

interface OnboardingProps {
  open: boolean
  onComplete: () => void
  hasStocks: boolean
}

type Step = 'welcome' | 'ai' | 'notify' | 'complete'

export function Onboarding({ open, onComplete, hasStocks }: OnboardingProps) {
  const navigate = useNavigate()
  const [step, setStep] = useState<Step>('welcome')

  const handleNext = () => {
    if (step === 'welcome') {
      setStep('ai')
    } else if (step === 'ai') {
      setStep('notify')
    } else if (step === 'notify') {
      setStep('complete')
    } else {
      onComplete()
    }
  }

  const handleSkip = () => {
    onComplete()
  }

  const handleGoToSettings = () => {
    onComplete()
    navigate('/settings')
  }

  const handleGoToPortfolio = () => {
    onComplete()
    navigate('/portfolio')
  }

  return (
    <Dialog open={open} onOpenChange={(open) => !open && onComplete()}>
      <DialogContent className="max-w-md p-0 overflow-hidden">
        {/* Progress Indicator */}
        <div className="flex items-center gap-1.5 px-6 pt-6">
          {(['welcome', 'ai', 'notify', 'complete'] as Step[]).map((s, i) => (
            <div
              key={s}
              className={`flex-1 h-1 rounded-full transition-colors ${
                i <= ['welcome', 'ai', 'notify', 'complete'].indexOf(step)
                  ? 'bg-primary'
                  : 'bg-accent/50'
              }`}
            />
          ))}
        </div>

        <div className="p-6 pt-4">
          {step === 'welcome' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-primary flex items-center justify-center mx-auto mb-4">
                <TrendingUp className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                Welcome to PanWatch
              </h2>
              <p className="text-[14px] text-muted-foreground mb-6">
                {hasStocks
                  ? 'Your watchlist is ready, you can get started now'
                  : "We've added 5 popular stocks as examples, so you can check out live quotes right away"
                }
              </p>

              <div className="space-y-3 text-left mb-6">
                <div className="flex items-start gap-3 p-3 rounded-xl bg-accent/30">
                  <div className="w-8 h-8 rounded-lg bg-blue-500/10 flex items-center justify-center flex-shrink-0">
                    <TrendingUp className="w-4 h-4 text-blue-500" />
                  </div>
                  <div>
                    <p className="text-[13px] font-medium text-foreground">Real-time quote monitoring</p>
                    <p className="text-[12px] text-muted-foreground">Track price moves on your watchlist and catch anomalies fast</p>
                  </div>
                </div>
                <div className="flex items-start gap-3 p-3 rounded-xl bg-accent/30">
                  <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center flex-shrink-0">
                    <Bot className="w-4 h-4 text-muted-foreground" />
                  </div>
                  <div>
                    <p className="text-[13px] font-medium text-foreground">AI-powered analysis</p>
                    <p className="text-[12px] text-muted-foreground">Daily reports, anomaly suggestions, technical analysis</p>
                  </div>
                </div>
                <div className="flex items-start gap-3 p-3 rounded-xl bg-accent/30">
                  <div className="w-8 h-8 rounded-lg bg-amber-500/10 flex items-center justify-center flex-shrink-0">
                    <Bell className="w-4 h-4 text-amber-500" />
                  </div>
                  <div>
                    <p className="text-[13px] font-medium text-foreground">Smart notifications</p>
                    <p className="text-[12px] text-muted-foreground">Push via Telegram, Discord, Slack, and more</p>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <Button className="flex-1" onClick={handleNext}>
                  Get Started <ChevronRight className="w-4 h-4" />
                </Button>
              </div>
              <button
                onClick={handleSkip}
                className="mt-3 text-[12px] text-muted-foreground hover:text-foreground transition-colors"
              >
                Skip tour
              </button>
            </div>
          )}

          {step === 'ai' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-primary flex items-center justify-center mx-auto mb-4">
                <Bot className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                Set Up AI Analysis
              </h2>
              <p className="text-[14px] text-muted-foreground mb-4">
                Connect an AI service to unlock smart analysis features
              </p>

              <div className="space-y-2 text-left mb-6 p-4 rounded-xl bg-accent/30">
                <div className="flex items-center gap-2 text-[13px]">
                  <Sparkles className="w-4 h-4 text-muted-foreground" />
                  <span className="text-foreground">Automated daily report analysis</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Sparkles className="w-4 h-4 text-muted-foreground" />
                  <span className="text-foreground">AI suggestions on anomalies</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Sparkles className="w-4 h-4 text-muted-foreground" />
                  <span className="text-foreground">Technical chart analysis</span>
                </div>
              </div>

              <p className="text-[12px] text-muted-foreground mb-4">
                Supports OpenAI, Anthropic, DeepSeek, and other providers
              </p>

              <div className="flex items-center gap-3">
                <Button variant="secondary" className="flex-1" onClick={handleNext}>
                  Maybe later
                </Button>
                <Button className="flex-1" onClick={handleGoToSettings}>
                  Go to Settings
                </Button>
              </div>
            </div>
          )}

          {step === 'notify' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-amber-500 flex items-center justify-center mx-auto mb-4">
                <Bell className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                Set Up Notification Channels
              </h2>
              <p className="text-[14px] text-muted-foreground mb-4">
                Get real-time push notifications once configured
              </p>

              <div className="space-y-2 text-left mb-6 p-4 rounded-xl bg-accent/30">
                <div className="flex items-center gap-2 text-[13px]">
                  <Bell className="w-4 h-4 text-amber-500" />
                  <span className="text-foreground">Intraday anomaly alerts</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Bell className="w-4 h-4 text-amber-500" />
                  <span className="text-foreground">AI analysis report push</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Bell className="w-4 h-4 text-amber-500" />
                  <span className="text-foreground">Take Profit / Stop Loss alerts</span>
                </div>
              </div>

              <p className="text-[12px] text-muted-foreground mb-4">
                Supports Telegram, Discord, Slack, and other channels
              </p>

              <div className="flex items-center gap-3">
                <Button variant="secondary" className="flex-1" onClick={handleNext}>
                  Maybe later
                </Button>
                <Button className="flex-1" onClick={handleGoToSettings}>
                  Go to Settings
                </Button>
              </div>
            </div>
          )}

          {step === 'complete' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-emerald-500 flex items-center justify-center mx-auto mb-4">
                <CheckCircle2 className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                All Set
              </h2>
              <p className="text-[14px] text-muted-foreground mb-6">
                You can change these settings anytime from the Settings page
              </p>

              <div className="space-y-3">
                <Button className="w-full" onClick={() => onComplete()}>
                  Go to Dashboard
                </Button>
                <Button variant="secondary" className="w-full" onClick={handleGoToPortfolio}>
                  Manage Watchlist
                </Button>
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
