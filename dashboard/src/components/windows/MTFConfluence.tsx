'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { Layers, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { cn } from '@/lib/utils';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

interface TFData {
  bias: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  close: number;
  rsi: number;
  adx: number;
  macd_hist: number;
  atr: number;
  bb_pct_b: number;
  ema_stack: { ema9: number; ema21: number; ema50: number };
}

interface MTFData {
  symbol: string;
  timeframes: Record<string, TFData>;
  confluence: {
    alignment_score: number;
    bull_count: number;
    bear_count: number;
    total_tfs: number;
    overall_bias: string;
  };
}

const TF_ORDER = ['D1', 'H4', 'H1', 'M15', 'M5'];
const TF_LABELS: Record<string, string> = { D1: 'Daily', H4: '4H', H1: '1H', M15: '15M', M5: '5M' };
const TF_WEIGHT: Record<string, string> = { D1: 'Macro', H4: 'Sesión', H1: 'Intraday', M15: 'Momentum', M5: 'Ejecución' };

const BIAS_CONFIG = {
  BULLISH: { color: 'text-neon-green', bg: 'bg-neon-green/10', icon: TrendingUp, dot: 'bg-neon-green' },
  BEARISH: { color: 'text-neon-red',   bg: 'bg-neon-red/10',   icon: TrendingDown, dot: 'bg-neon-red' },
  NEUTRAL: { color: 'text-text-muted', bg: 'bg-bg-tertiary',   icon: Minus,        dot: 'bg-text-muted' },
};

export default function MTFConfluence({ symbol = 'XAUUSD' }: { symbol?: string }) {
  const [data, setData] = useState<MTFData | null>(null);

  const fetch_ = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/intelligence/mtf-context/${symbol}`);
      if (res.ok) setData(await res.json());
    } catch {}
  }, [symbol]);

  useEffect(() => { fetch_(); const t = setInterval(fetch_, 5000); return () => clearInterval(t); }, [fetch_]);

  const conf = data?.confluence;
  const score = conf ? conf.alignment_score : 0;
  const overallBias = conf?.overall_bias || 'NEUTRAL';
  const overallCfg = BIAS_CONFIG[overallBias as keyof typeof BIAS_CONFIG] || BIAS_CONFIG.NEUTRAL;
  const OverallIcon = overallCfg.icon;

  // Score bar: -4 to +4 → 0-100%.
  const scorePct = ((score + 4) / 8) * 100;
  const scoreColor = score >= 2 ? 'bg-neon-green' : score <= -2 ? 'bg-neon-red' : 'bg-yellow-500';

  return (
    <div className="flex flex-col gap-0 h-full">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border-primary/50">
        <Layers className="h-3.5 w-3.5 text-neon-cyan" />
        <span className="text-[11px] font-mono font-bold text-text-secondary tracking-wider">MTF CONFLUENCE</span>
        <span className="ml-2 text-[9px] font-mono text-text-muted">{symbol}</span>
        <div className="ml-auto flex items-center gap-2">
          <OverallIcon className={cn('h-3 w-3', overallCfg.color)} />
          <span className={cn('text-[10px] font-mono font-bold', overallCfg.color)}>{overallBias}</span>
        </div>
      </div>

      {/* Alignment bar */}
      <div className="px-3 py-2">
        <div className="flex items-center justify-between mb-1">
          <span className="text-[9px] font-mono text-text-muted">BEAR</span>
          <span className="text-[9px] font-mono text-text-secondary font-bold">Score {score > 0 ? '+' : ''}{score} / 4</span>
          <span className="text-[9px] font-mono text-text-muted">BULL</span>
        </div>
        <div className="relative h-2 bg-bg-tertiary rounded-full overflow-hidden">
          {/* Center marker */}
          <div className="absolute top-0 bottom-0 left-1/2 w-px bg-border-primary" />
          <div
            className={cn('absolute top-0 bottom-0 rounded-full transition-all duration-700', scoreColor)}
            style={{
              left: score >= 0 ? '50%' : `${scorePct}%`,
              right: score < 0 ? '50%' : `${100 - scorePct}%`,
            }}
          />
        </div>
        <div className="flex justify-between mt-1">
          <span className="text-[9px] font-mono text-neon-red">{conf?.bear_count || 0} TF bear</span>
          <span className="text-[9px] font-mono text-neon-green">{conf?.bull_count || 0} TF bull</span>
        </div>
      </div>

      {/* Per-TF rows */}
      <div className="flex flex-col gap-0 flex-1 overflow-y-auto px-3 pb-2">
        {TF_ORDER.map((tf) => {
          const tfData = data?.timeframes[tf];
          const bias = tfData?.bias || 'NEUTRAL';
          const bCfg = BIAS_CONFIG[bias];
          const BiasIcon = bCfg.icon;

          return (
            <div key={tf} className="flex items-center gap-2 py-1.5 border-b border-border-primary/20 last:border-0">
              {/* TF label */}
              <div className="w-10 shrink-0">
                <div className="text-[10px] font-mono font-bold text-text-secondary">{TF_LABELS[tf]}</div>
                <div className="text-[8px] font-mono text-text-muted">{TF_WEIGHT[tf]}</div>
              </div>

              {/* Bias badge */}
              <div className={cn('flex items-center gap-1 px-1.5 py-0.5 rounded border text-[9px] font-mono w-20 shrink-0', bCfg.bg, 'border-current')}>
                <div className={cn('w-1.5 h-1.5 rounded-full', bCfg.dot)} />
                <span className={bCfg.color}>{bias}</span>
              </div>

              {/* Mini stats */}
              {tfData && (
                <div className="flex gap-3 text-[9px] font-mono text-text-muted flex-1 min-w-0 overflow-hidden">
                  <span>RSI <span className={tfData.rsi > 60 ? 'text-neon-green' : tfData.rsi < 40 ? 'text-neon-red' : 'text-text-secondary'}>{tfData.rsi.toFixed(0)}</span></span>
                  <span>ADX <span className={tfData.adx > 25 ? 'text-neon-cyan' : 'text-text-secondary'}>{tfData.adx.toFixed(0)}</span></span>
                  <span className="hidden sm:inline">ATR <span className="text-text-secondary">{tfData.atr.toFixed(2)}</span></span>
                </div>
              )}

              {!tfData && <span className="text-[9px] font-mono text-text-muted">sin datos</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
