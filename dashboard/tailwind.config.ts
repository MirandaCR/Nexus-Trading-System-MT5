import type { Config } from 'tailwindcss';

const config: Config = {
  darkMode: 'class',
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        bg: {
          primary: 'var(--bg-primary)',
          secondary: 'var(--bg-secondary)',
          tertiary: 'var(--bg-tertiary)',
          card: 'var(--bg-card)',
          'card-hover': 'var(--bg-card-hover)',
          input: 'var(--bg-input)',
          overlay: 'var(--bg-overlay)',
        },
        neon: {
          green: 'var(--neon-green)',
          'green-dim': 'var(--neon-green-dim)',
          purple: 'var(--neon-purple)',
          'purple-dim': 'var(--neon-purple-dim)',
          cyan: 'var(--neon-cyan)',
          'cyan-dim': 'var(--neon-cyan-dim)',
          gold: 'var(--neon-gold)',
          'gold-dim': 'var(--neon-gold-dim)',
          red: 'var(--neon-red)',
          'red-dim': 'var(--neon-red-dim)',
          blue: 'var(--neon-blue)',
          'blue-dim': 'var(--neon-blue-dim)',
          orange: 'var(--neon-orange)',
          pink: 'var(--neon-pink)',
        },
        text: {
          primary: 'var(--text-primary)',
          secondary: 'var(--text-secondary)',
          muted: 'var(--text-muted)',
          accent: 'var(--text-accent)',
        },
        border: {
          primary: 'var(--border-primary)',
          secondary: 'var(--border-secondary)',
          accent: 'var(--border-accent)',
          glow: 'var(--border-glow)',
        },
        chart: {
          bullish: 'var(--chart-bullish)',
          bearish: 'var(--chart-bearish)',
          volume: 'var(--chart-volume)',
          grid: 'var(--chart-grid)',
          crosshair: 'var(--chart-crosshair)',
          ma20: 'var(--chart-ma20)',
          ma50: 'var(--chart-ma50)',
          bb: 'var(--chart-bb)',
        },
        agent: {
          orchestrator: 'var(--agent-orchestrator)',
          analyst: 'var(--agent-analyst)',
          risk: 'var(--agent-risk)',
          executor: 'var(--agent-executor)',
          monitor: 'var(--agent-monitor)',
          journal: 'var(--agent-journal)',
          news: 'var(--agent-news)',
          strategy: 'var(--agent-strategy)',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      boxShadow: {
        /* Clean shadows — no glow, just depth */
        'neon-green': '0 0 0 1px var(--neon-green)',
        'neon-purple': '0 0 0 1px var(--neon-purple)',
        'neon-cyan': '0 0 0 1px var(--neon-cyan)',
        'neon-red': '0 0 0 1px var(--neon-red)',
        'neon-gold': '0 0 0 1px var(--neon-gold)',
        'card': '0 1px 3px rgba(0, 0, 0, 0.3)',
        'card-hover': '0 4px 12px rgba(0, 0, 0, 0.4)',
      },
      backgroundImage: {
        'gradient-radial': 'radial-gradient(var(--tw-gradient-stops))',
        'gradient-card': 'linear-gradient(180deg, var(--bg-card) 0%, var(--bg-secondary) 100%)',
        'gradient-neon': 'linear-gradient(135deg, var(--neon-purple) 0%, var(--neon-cyan) 100%)',
        'gradient-gold': 'linear-gradient(135deg, var(--neon-gold) 0%, var(--neon-orange) 100%)',
      },
      animation: {
        'neon-pulse': 'neon-pulse 2.5s ease-in-out infinite',
        'fade-in': 'fade-in 0.3s ease-out',
        'slide-up': 'slide-up 0.3s ease-out',
        'slide-in-right': 'slide-in-right 0.3s ease-out',
        'pulse-dot': 'pulse-dot 2s ease-in-out infinite',
      },
      keyframes: {
        'neon-pulse': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.5' },
        },
        'fade-in': {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        'slide-up': {
          '0%': { opacity: '0', transform: 'translateY(10px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'slide-in-right': {
          '0%': { opacity: '0', transform: 'translateX(10px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        'pulse-dot': {
          '0%, 100%': { transform: 'scale(1)', opacity: '1' },
          '50%': { transform: 'scale(1.3)', opacity: '0.6' },
        },
      },
      borderRadius: {
        'xl': '0.75rem',
        '2xl': '1rem',
        '3xl': '1.5rem',
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
};

export default config;
