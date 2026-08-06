'use client';

import { useState } from 'react';
import NeonCard from '../shared/NeonCard';
import type { Timeframe } from '../../types';
import { useDashboardStore } from '@/stores/dashboardStore';

const TIMEFRAMES: Timeframe[] = ['M1', 'M5', 'M15', 'H1', 'H4'];

function toTVSymbol(sym: string): string {
  const MAP: Record<string, string> = {
    XAUUSD: 'OANDA:XAUUSD',
    BTCUSD: 'BITSTAMP:BTCUSD',
  };
  return MAP[sym.toUpperCase()] ?? `FX:${sym}`;
}

function toTVInterval(tf: Timeframe): string {
  const MAP: Record<Timeframe, string> = {
    M1: '1', M5: '5', M15: '15', H1: '60', H4: '240',
  };
  return MAP[tf] ?? '15';
}

const SYMBOLS = ['XAUUSD', 'BTCUSD', 'EURUSD'];

export default function LiveChart() {
  const selectedSymbol = useDashboardStore((s) => s.symbol);
  const activeTimeframe = useDashboardStore((s) => s.timeframe);
  const setSymbol = useDashboardStore((s) => s.setSymbol);
  const setTimeframe = useDashboardStore((s) => s.setTimeframe);

  const [showSymbolPicker, setShowSymbolPicker] = useState(false);

  return (
    <NeonCard
      title="Live Chart"
      glow="cyan"
      noPadding
      icon={
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M3 3v18h18" />
          <path d="M7 16l4-8 4 4 4-12" />
        </svg>
      }
      headerRight={
        <div className="flex items-center gap-3">
          <div className="relative">
            <button
              type="button"
              onClick={() => setShowSymbolPicker(!showSymbolPicker)}
              className="flex items-center gap-1 rounded bg-bg-secondary px-2 py-1 text-xs font-mono text-neon-cyan hover:bg-bg-tertiary transition-colors"
            >
              {selectedSymbol}
              <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M7 10l5 5 5-5z" /></svg>
            </button>
            {showSymbolPicker && (
              <div className="absolute left-0 top-full z-50 mt-1 rounded border border-border-primary/20 bg-bg-primary shadow-lg">
                {SYMBOLS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => {
                      setSymbol(s);
                      setShowSymbolPicker(false);
                    }}
                    className={`block w-full px-3 py-1.5 text-left text-xs font-mono hover:bg-bg-tertiary transition-colors ${
                      s === selectedSymbol ? 'text-neon-cyan' : 'text-text-secondary'
                    }`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      }
      className="h-full min-h-0 flex flex-col"
    >
      <div className="flex items-center gap-1 border-b border-border-primary/10 px-3 py-1 shrink-0">
        {TIMEFRAMES.map((tf) => (
          <button
            key={tf}
            type="button"
            onClick={() => setTimeframe(tf)}
            className={`rounded px-2.5 py-1 text-[11px] font-mono font-semibold transition-all ${
              tf === activeTimeframe
                ? 'bg-neon-purple/15 text-neon-purple'
                : 'text-text-muted hover:text-text-secondary hover:bg-bg-tertiary'
            }`}
          >
            {tf}
          </button>
        ))}
      </div>

      <div className="relative flex-1 min-h-0 w-full">
        <iframe
          key={`${selectedSymbol}-${activeTimeframe}`}
          src={`https://www.tradingview.com/widgetembed/?frameElementId=tv_chart&symbol=${encodeURIComponent(toTVSymbol(selectedSymbol))}&interval=${toTVInterval(activeTimeframe)}&theme=dark&style=1&locale=en&toolbar_bg=%230a0a0f&enable_publishing=0&hide_side_toolbar=1&allow_symbol_change=0&save_image=0&studies=RSI%40tv-basicstudies&studies=MACD%40tv-basicstudies`}
          className="absolute inset-0 w-full h-full border-0 block"
          title="TradingView Chart"
          allow="clipboard-write"
        />
      </div>
    </NeonCard>
  );
}
