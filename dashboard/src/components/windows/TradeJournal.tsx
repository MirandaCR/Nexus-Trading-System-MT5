'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { BookOpen, TrendingUp, TrendingDown, Clock } from 'lucide-react';
import { cn } from '@/lib/utils';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

interface TradeRecord {
  trade_id: string;
  symbol: string;
  side: string;
  entry_price: number;
  exit_price: number;
  pnl_pips: number;
  pnl_usd: number;
  session: string;
  regime: string;
  source: string;
  atr: number;
  conviction: number;
  closed_at: string;
  is_win: boolean;
  _hour_bucket: string;
}

interface JournalData {
  metrics: {
    overall_win_rate: number;
    total_trades: number;
    avg_pnl_pips: number;
    recent_streak: { type: string; count: number };
  };
  thresholds: Record<string, any>;
  recent_trades: TradeRecord[];
}

const SOURCE_LABELS: Record<string, string> = {
  autonomous_ml: 'AutoML',
  genai_agents: 'GenAI',
  manual: 'Manual',
};

const REGIME_COLOR: Record<string, string> = {
  TRENDING_BULL: 'text-neon-green', TRENDING_BEAR: 'text-neon-red',
  TRENDING: 'text-neon-green', RANGING: 'text-yellow-400',
  BREAKOUT: 'text-neon-purple', HIGH_VOL: 'text-orange-400',
  UNKNOWN: 'text-text-muted',
};

export default function TradeJournal() {
  const [data, setData] = useState<JournalData | null>(null);

  const fetch_ = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/intelligence/trade-memory`);
      if (res.ok) setData(await res.json());
    } catch {}
  }, []);

  useEffect(() => { fetch_(); const t = setInterval(fetch_, 10000); return () => clearInterval(t); }, [fetch_]);

  const trades = data?.recent_trades || [];
  const metrics = data?.metrics;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border-primary/50">
        <BookOpen className="h-3.5 w-3.5 text-neon-cyan" />
        <span className="text-[11px] font-mono font-bold text-text-secondary tracking-wider">TRADE JOURNAL</span>
        <span className="ml-auto text-[9px] font-mono text-text-muted">{metrics?.total_trades || 0} trades</span>
      </div>

      {/* Summary row */}
      {metrics && (
        <div className="grid grid-cols-3 gap-1 px-3 py-2 border-b border-border-primary/30">
          <Stat label="Win Rate" value={`${((metrics.overall_win_rate ?? 0) * 100).toFixed(1)}%`}
            color={(metrics.overall_win_rate ?? 0) >= 0.5 ? 'text-neon-green' : (metrics.overall_win_rate ?? 0) >= 0.4 ? 'text-yellow-400' : 'text-neon-red'} />
          <Stat label="Avg PnL" value={`${(metrics.avg_pnl_pips ?? 0) >= 0 ? '+' : ''}${(metrics.avg_pnl_pips ?? 0).toFixed(1)}p`}
            color={(metrics.avg_pnl_pips ?? 0) >= 0 ? 'text-neon-green' : 'text-neon-red'} />
          <Stat label="Streak"
            value={metrics.recent_streak?.count > 0 ? `${metrics.recent_streak.type === 'win' ? '▲' : '▼'} ${metrics.recent_streak.count}` : '—'}
            color={metrics.recent_streak?.type === 'win' ? 'text-neon-green' : metrics.recent_streak?.count > 0 ? 'text-neon-red' : 'text-text-muted'} />
        </div>
      )}

      {/* Trade list */}
      <div className="flex-1 overflow-y-auto px-3 py-1">
        {trades.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 h-full text-text-muted">
            <BookOpen className="h-8 w-8 opacity-20" />
            <span className="text-[10px] font-mono">Sin trades en memoria</span>
          </div>
        ) : (
          <div className="flex flex-col gap-1">
            {[...trades].reverse().map((t, i) => (
              <TradeRow key={t.trade_id || i} trade={t} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function TradeRow({ trade }: { trade: TradeRecord }) {
  const isWin = trade.is_win || trade.pnl_pips > 0;
  const pnlColor = isWin ? 'text-neon-green' : 'text-neon-red';
  const regimeColor = REGIME_COLOR[trade.regime?.toUpperCase()] || 'text-text-muted';
  const time = trade.closed_at
    ? new Date(trade.closed_at).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' })
    : '--:--';

  return (
    <div className={cn(
      'flex flex-col gap-1 px-2 py-1.5 rounded border text-[9px] font-mono',
      isWin ? 'border-neon-green/15 bg-neon-green/3' : 'border-neon-red/15 bg-neon-red/3'
    )}>
      <div className="flex items-center gap-2">
        {trade.side === 'BUY'
          ? <TrendingUp className="h-2.5 w-2.5 text-neon-green" />
          : <TrendingDown className="h-2.5 w-2.5 text-neon-red" />
        }
        <span className="font-bold text-text-primary">{trade.symbol}</span>
        <span className={cn('font-bold', trade.side === 'BUY' ? 'text-neon-green' : 'text-neon-red')}>{trade.side}</span>
        <span className="text-text-muted">@{trade.entry_price?.toFixed(3)}</span>
        <span className={cn('ml-auto font-bold', pnlColor)}>
          {trade.pnl_pips >= 0 ? '+' : ''}{trade.pnl_pips?.toFixed(1)} pips
        </span>
        {trade.pnl_usd !== undefined && (
          <span className={cn('font-bold', pnlColor)}>
            ${trade.pnl_usd >= 0 ? '+' : ''}{trade.pnl_usd?.toFixed(2)}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2 text-text-muted">
        <Clock className="h-2 w-2" />
        <span>{time}</span>
        <span className="w-px h-2 bg-border-primary" />
        <span className={regimeColor}>{trade.regime || 'N/A'}</span>
        <span className="w-px h-2 bg-border-primary" />
        <span>{trade.session || 'N/A'}</span>
        <span className="w-px h-2 bg-border-primary" />
        <span>{SOURCE_LABELS[trade.source] || trade.source || 'N/A'}</span>
        {trade.conviction > 0 && (
          <>
            <span className="w-px h-2 bg-border-primary" />
            <span>conv {(trade.conviction * 100).toFixed(0)}%</span>
          </>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="flex flex-col gap-0.5 bg-bg-tertiary/30 rounded px-1.5 py-1">
      <span className="text-[7px] font-mono text-text-muted uppercase tracking-wider">{label}</span>
      <span className={cn('text-[10px] font-mono font-bold', color)}>{value}</span>
    </div>
  );
}
