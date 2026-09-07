import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { TrendingUp, Lock, Eye, EyeOff, User } from 'lucide-react'
import { authApi } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

export default function LoginPage() {
  const navigate = useNavigate()
  const { toast } = useToast()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [isSetup, setIsSetup] = useState(false)
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    // Check auth status
    authApi.status()
      .then(data => {
        setIsSetup(!data.initialized)
        setChecking(false)
      })
      .catch(() => setChecking(false))
  }, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username || !password) return

    if (isSetup) {
      if (password !== confirmPassword) {
        toast('Passwords do not match - re-enter both fields', 'error')
        return
      }
      if (password.length < 6) {
        toast('Password must be at least 6 characters - enter a longer password', 'error')
        return
      }
    }

    setLoading(true)
    try {
      const data = isSetup
        ? await authApi.setup({ username, password })
        : await authApi.login({ username, password })

      // Save token
      localStorage.setItem('token', data.token)
      localStorage.setItem('token_expires', data.expires_at)

      toast(isSetup ? 'Password set successfully' : 'Logged in successfully', 'success')
      navigate('/today')
    } catch (e) {
      const fallback = isSetup
        ? 'Setup failed - check your details and try again'
        : 'Login failed - check your username and password and try again'
      toast(e instanceof Error ? e.message : fallback, 'error')
    } finally {
      setLoading(false)
    }
  }

  if (checking) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <span className="w-6 h-6 border-2 border-border border-t-foreground rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div className="w-16 h-16 rounded-2xl bg-rail flex items-center justify-center mb-4">
            <TrendingUp className="w-8 h-8 text-rail-foreground" />
          </div>
          <h1 className="page-title !text-[28px]">PanWatch</h1>
          <p className="text-sm text-muted-foreground mt-1">AI Stock Watch Assistant</p>
        </div>

        {/* Form */}
        <div className="card p-6">
          <div className="flex items-center gap-2 mb-6">
            <Lock className="w-5 h-5 text-foreground" />
            <h2 className="text-lg font-semibold">
              {isSetup ? 'Set Access Password' : 'Log In'}
            </h2>
          </div>

          {isSetup && (
            <p className="helper-text mb-4">
              First time here - set an access password to protect your data
            </p>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <Label>Username</Label>
              <div className="relative">
                <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <Input
                  type="text"
                  value={username}
                  onChange={e => setUsername(e.target.value)}
                  placeholder="Enter your username"
                  className="pl-10"
                  autoFocus
                />
              </div>
            </div>

            <div>
              <Label>{isSetup ? 'Set Password' : 'Password'}</Label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <Input
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder={isSetup ? 'At least 6 characters' : 'Enter your password'}
                  className="pl-10 pr-10"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-1 top-1/2 -translate-y-1/2 h-8 w-8"
                  onClick={() => setShowPassword(!showPassword)}
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </Button>
              </div>
            </div>

            {isSetup && (
              <div>
                <Label>Confirm Password</Label>
                <Input
                  type={showPassword ? 'text' : 'password'}
                  value={confirmPassword}
                  onChange={e => setConfirmPassword(e.target.value)}
                  placeholder="Re-enter your password"
                />
              </div>
            )}

            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? (
                <span className="w-4 h-4 border-2 border-current/30 border-t-current rounded-full animate-spin" />
              ) : isSetup ? (
                'Set Password & Continue'
              ) : (
                'Log In'
              )}
            </Button>
          </form>
        </div>

        <p className="text-center text-xs text-muted-foreground mt-6">
          AI-powered stock monitoring assistant
        </p>
      </div>
    </div>
  )
}
