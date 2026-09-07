/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    './index.html',
    './src/**/*.{ts,tsx}',
    './packages/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        // The one accent. Reserved for the user's action and the user's
        // current selection. Never for data, direction, or decoration.
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
          deep: 'hsl(var(--primary-deep))',
        },
        // The dark console rail: navigation, and the single hero stat tile.
        rail: {
          DEFAULT: 'hsl(var(--rail))',
          2: 'hsl(var(--rail-2))',
          foreground: 'hsl(var(--rail-foreground))',
          muted: 'hsl(var(--rail-muted))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
        },
        success: {
          DEFAULT: 'hsl(var(--success))',
          foreground: 'hsl(var(--success-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        // Market direction. Western convention: green rises, red falls.
        // Factual, regional, and separate from every other colour system.
        stock: {
          up: 'hsl(var(--stock-up))',
          down: 'hsl(var(--stock-down))',
        },
        // Market identity only. Validated for colour-vision deficiency and
        // contrast in both themes; never used to mean up, down, good or bad.
        mkt: {
          us: 'hsl(var(--mkt-us))',
          ca: 'hsl(var(--mkt-ca))',
          crypto: 'hsl(var(--mkt-crypto))',
          gold: 'hsl(var(--mkt-gold))',
          // Filled-badge variants, contrast-checked against `mkt.ink`.
          'us-solid': 'hsl(var(--mkt-us-solid))',
          'ca-solid': 'hsl(var(--mkt-ca-solid))',
          'crypto-solid': 'hsl(var(--mkt-crypto-solid))',
          'gold-solid': 'hsl(var(--mkt-gold-solid))',
          ink: 'hsl(var(--mkt-ink))',
        },
        stockink: {
          up: 'hsl(var(--stock-up-ink))',
          down: 'hsl(var(--stock-down-ink))',
        },
        skeleton: 'hsl(var(--skeleton))',
        // Stat-tile grounds only. A bounded fourth colour role: it labels
        // which metric you are reading and never appears on a control.
        tile: {
          mint: 'hsl(var(--tile-mint))',
          lilac: 'hsl(var(--tile-lilac))',
          peach: 'hsl(var(--tile-peach))',
          sky: 'hsl(var(--tile-sky))',
        },
        // Marketing surface only. These read the `.mk` root class tokens
        // (src/marketing/marketing.css), never :root, so the app is untouched.
        mk: {
          canvas: 'hsl(var(--mk-canvas) / <alpha-value>)',
          surface: 'hsl(var(--mk-surface) / <alpha-value>)',
          surface2: 'hsl(var(--mk-surface-2) / <alpha-value>)',
          line: 'hsl(var(--mk-line) / <alpha-value>)',
          lineStrong: 'hsl(var(--mk-line-strong) / <alpha-value>)',
          ink: 'hsl(var(--mk-ink) / <alpha-value>)',
          inkSoft: 'hsl(var(--mk-ink-soft) / <alpha-value>)',
          inkMute: 'hsl(var(--mk-ink-mute) / <alpha-value>)',
          accent: 'hsl(var(--mk-accent) / <alpha-value>)',
          accentInk: 'hsl(var(--mk-accent-ink) / <alpha-value>)',
          amber: 'hsl(var(--mk-amber) / <alpha-value>)',
          rose: 'hsl(var(--mk-rose) / <alpha-value>)',
          up: 'hsl(var(--mk-up) / <alpha-value>)',
          down: 'hsl(var(--mk-down) / <alpha-value>)',
        },
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
        card: 'var(--radius-card)',
        rail: 'var(--radius-rail)',
      },
      fontFamily: {
        sans: ['Satoshi', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto',
          'PingFang SC', 'Microsoft YaHei', 'sans-serif'],
        mono: ['"JetBrains Mono Variable"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      boxShadow: {
        instrument: '0 1px 2px hsl(228 17% 9% / 0.05), 0 8px 24px -14px hsl(228 17% 9% / 0.22)',
        hero: '0 1px 2px hsl(228 17% 9% / 0.06), 0 14px 30px -18px hsl(228 17% 9% / 0.5)',
      },
      transitionTimingFunction: {
        settle: 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
    },
  },
  plugins: [require('tailwindcss-animate'), require('@tailwindcss/typography')],
}
