'use client';

import React, { useEffect, useState, useCallback } from 'react';
import {
  Trophy,
  RefreshCw,
  TrendingUp,
  Crown,
  Medal,
  BarChart3,
  Play,
  Cpu,
  Network,
  Bot,
  BrainCircuit,
  ArrowUpRight,
  ArrowDownRight,
  Minus,
  Zap,
} from 'lucide-react';
import { cn } from '@/lib/utils';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

interface LeaderboardEntry {
  name: string;
  model_type: string;
  win_rate: number;
  profit_factor: number;
  sharpe_ratio: number;
  max_drawdown: number;
  total_return: number;
  total_trades: number;
  avg_rr: number;
  is_fitted: boolean;
  last_trained: string;
}

interface ComparisonData {
  leaderboard: LeaderboardEntry[];
  best_model: string;
  equity_curves: Record<string, number[]>;
}

const MODEL_ICONS: Record<string, React.ElementType> = {
  genai: BrainCircuit,
  ml: Cpu,
  dl: Network,
  rl: Bot,
  consensus: Zap,
};

const MODEL_COLORS: Record<string, string> = {
  genai: '#ffd700',
  ml: '#00f5ff',
  dl: '#bf40ff',
  rl: '#39ff14',
  consensus: '#ff6b35',
};

const RANK_STYLES = [
  { bg: 'bg-neon-gold/10', border: 'border-neon-gold/30', text: 'text-neon-gold', icon: Crown },
  { bg: 'bg-gray-400/10', border: 'border-gray-400/30', text: 'text-gray-400', icon: Medal },
  { bg: 'bg-amber-700/10', border: 'border-amber-700/30', text: 'text-amber-600', icon: Medal },
  { bg: 'bg-bg-card', border: 'border-border-primary/20', text: 'text-text-muted', icon: BarChart3 },
];

function MultiEquityChart({ curves }: { curves: Record<string, number[]> }) {
  const entries = Object.entries(curves).filter(([, data]) => data.length > 1);
  if (entries.length === 0) {
    return <div className="text-[10px] text-text-muted text-center py-8">No equity data available. Train models to see curves.</div>;
  }

  const allValues = entries.flatMap(([, d]) => d);
  const globalMin = Math.min(...allValues);
  const globalMax = Math.max(...allValues);
  const range = globalMax - globalMin || 1;
  const w = 500;
  const h = 140;

  return (
    <div>
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-36">
        {entries.map(([name, data]) => {
          const color = MODEL_COLORS[
            Object.entries(MODEL_COLORS).find(([k]) =>
              name.toLowerCase().includes(k) || name.toLowerCase().includes(k === 'genai' ? 'gen' : k)
            )?.[0] || 'ml'
          ] || '#ffffff';

          const points = data.map(
            (v, i) =>
              `${(i / (data.length - 1)) * w},${h - ((v - globalMin) / range) * (h - 10) - 5}`
          );

          return (
            <polyline
              key={name}
              points={points.join(' ')}
              fill="none"
              stroke={color}
              strokeWidth="1.5"
              strokeLinejoin="round"
              opacity="0.85"
            />
          );
        })}
      </svg>
      <div className="flex items-center gap-4 justify-center mt-2">
        {entries.map(([name]) => {
          const color = MODEL_COLORS[
            Object.entries(MODEL_COLORS).find(([k]) =>
              name.toLowerCase().includes(k) || name.toLowerCase().includes(k === 'genai' ? 'gen' : k)
            )?.[0] || 'ml'
          ] || '#ffffff';
          return (
            <div key={name} className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
              <span className="text-[9px] font-mono text-text-muted">{name}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StatCompare({ label, values, format = 'number', higherIsBetter = true }: {
  label: string;
  values: { name: string; value: number; type: string }[];
  format?: 'number' | 'pct' | 'ratio';
  higherIsBetter?: boolean;
}) {
  const sorted = [...values].sort((a, b) => higherIsBetter ? b.value - a.value : a.value - b.value);
  const bestValue = sorted[0]?.value;

  const fmt = (v: number) =>
    format === 'pct' ? `${(v * 100).toFixed(1)}%` : format === 'ratio' ? v.toFixed(2) : v.toString();

  return (
    <div className="p-2 rounded-lg bg-bg-card/50">
      <span className="text-[9px] font-mono text-text-muted uppercase block mb-1.5">{label}</span>
      <div className="space-y-1">
        {sorted.map((item, i) => {
          const color = MODEL_COLORS[item.type] || '#ffffff';
          const isBest = item.value === bestValue && i === 0;
          return (
            <div key={item.name} className="flex items-center gap-2">
              <span className="h-1.5 w-1.5 rounded-full shrink-0" style={{ backgroundColor: color }} />
              <span className="text-[10px] font-mono text-text-secondary flex-1 truncate">{item.name}</span>
              <span className={cn('text-[11px] font-mono font-bold', isBest ? 'text-neon-gold' : 'text-text-primary')}>
                {fmt(item.value)}
              </span>
              {isBest && <Crown className="h-2.5 w-2.5 text-neon-gold shrink-0" />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function ModelComparisonPage() {
  const [data, setData] = useState<ComparisonData | null>(null);
  const [loading, setLoading] = useState(true);
  const [trainingAll, setTrainingAll] = useState(false);

  const fetchComparison = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/ml/traders/comparison`);
      if (res.ok) {
        setData(await res.json());
      }
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchComparison();
    const id = setInterval(fetchComparison, 15_000);
    return () => clearInterval(id);
  }, [fetchComparison]);

  const handleTrainAll = async () => {
    setTrainingAll(true);
    try {
      await fetch(`${API}/api/ml/traders/train`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_types: ['ml', 'dl', 'rl'] }),
      });
    } catch {}
    setTimeout(() => { setTrainingAll(false); fetchComparison(); }, 5000);
  };

  const lb = data?.leaderboard || [];

  return (
    <div className="p-2 sm:p-4 w-full max-w-[1800px] mx-auto space-y-3 sm:space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <Trophy className="h-5 w-5 text-neon-gold" />
          <h1 className="text-sm font-bold text-text-primary">Model Comparison</h1>
        </div>
        <span className="text-[10px] font-mono text-text-muted bg-bg-card px-2 py-0.5 rounded border border-border-primary/20">
          GenAI vs ML vs Deep Learning vs RL
        </span>
        <div className="flex-1" />
        {data?.best_model && (
          <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-neon-gold/10 border border-neon-gold/20">
            <Crown className="h-3 w-3 text-neon-gold" />
            <span className="text-[10px] font-mono font-bold text-neon-gold">{data.best_model}</span>
          </div>
        )}
        <button
          onClick={handleTrainAll}
          disabled={trainingAll}
          className={cn(
            'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-medium border transition-all',
            trainingAll
              ? 'border-border-primary text-text-muted cursor-not-allowed'
              : 'border-neon-gold/30 text-neon-gold bg-neon-gold/5 hover:bg-neon-gold/10'
          )}
        >
          {trainingAll ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
          {trainingAll ? 'Training All...' : 'Train All Models'}
        </button>
      </div>

      {/* Leaderboard */}
      <div className="rounded-xl border border-border-primary/20 bg-bg-primary overflow-hidden">
        <div className="flex items-center gap-2 px-3 py-2 border-b border-border-primary/10 bg-bg-secondary/40">
          <Trophy className="h-3.5 w-3.5 text-neon-gold" />
          <span className="text-[11px] font-mono font-semibold text-text-secondary">Leaderboard (by Sharpe Ratio)</span>
        </div>

        <div className="overflow-x-auto scrollbar-hide">
        {/* Table Header */}
        <div className="grid grid-cols-[32px_1fr_64px_56px_56px_56px_56px_56px_56px_48px] gap-1 px-3 py-1.5 border-b border-border-primary/10 text-[8px] font-mono text-text-muted uppercase min-w-[600px]">
          <span>#</span>
          <span>Model</span>
          <span className="text-right">Win Rate</span>
          <span className="text-right">PF</span>
          <span className="text-right">Sharpe</span>
          <span className="text-right">Sortino</span>
          <span className="text-right">Max DD</span>
          <span className="text-right">Return</span>
          <span className="text-right">Avg RR</span>
          <span className="text-right">Trades</span>
        </div>

        {lb.length === 0 ? (
          <div className="text-[10px] text-text-muted text-center py-8">
            No models trained yet. Click &quot;Train All Models&quot; to start.
          </div>
        ) : (
          lb.map((entry, i) => {
            const rank = RANK_STYLES[Math.min(i, RANK_STYLES.length - 1)];
            const IconComp = MODEL_ICONS[entry.model_type] || BarChart3;
            const color = MODEL_COLORS[entry.model_type] || '#ffffff';
            const RankIcon = rank.icon;

            return (
              <div
                key={entry.name}
                className={cn(
                  'grid grid-cols-[32px_1fr_64px_56px_56px_56px_56px_56px_56px_48px] gap-1 px-3 py-2 border-b border-border-primary/5 items-center transition-colors hover:bg-bg-card/50 min-w-[600px]',
                  i === 0 && 'bg-neon-gold/5'
                )}
              >
                <span className={cn('flex items-center justify-center', rank.text)}>
                  <RankIcon className="h-3.5 w-3.5" />
                </span>
                <div className="flex items-center gap-2">
                  <IconComp className="h-3.5 w-3.5 shrink-0" style={{ color }} />
                  <span className="text-[11px] font-mono font-medium text-text-primary truncate">{entry.name}</span>
                  {!entry.is_fitted && (
                    <span className="text-[8px] px-1 py-0.5 rounded bg-neon-gold/10 text-neon-gold font-mono">UNTRAINED</span>
                  )}
                </div>
                <span className={cn('text-[11px] font-mono text-right', entry.win_rate > 0.5 ? 'text-neon-green' : 'text-neon-red')}>
                  {(entry.win_rate * 100).toFixed(1)}%
                </span>
                <span className={cn('text-[11px] font-mono text-right', entry.profit_factor > 1 ? 'text-neon-green' : 'text-neon-red')}>
                  {entry.profit_factor.toFixed(2)}
                </span>
                <span className={cn('text-[11px] font-mono text-right font-bold', entry.sharpe_ratio > 0 ? 'text-neon-green' : 'text-neon-red')}>
                  {entry.sharpe_ratio.toFixed(2)}
                </span>
                <span className="text-[11px] font-mono text-right text-text-secondary">
                  {/* Sortino not in leaderboard data, show dash */}
                  -
                </span>
                <span className={cn('text-[11px] font-mono text-right', entry.max_drawdown > -0.1 ? 'text-neon-green' : 'text-neon-red')}>
                  {(entry.max_drawdown * 100).toFixed(1)}%
                </span>
                <span className={cn('text-[11px] font-mono text-right font-bold', entry.total_return > 0 ? 'text-neon-green' : 'text-neon-red')}>
                  {entry.total_return > 0 ? '+' : ''}{(entry.total_return * 100).toFixed(2)}%
                </span>
                <span className={cn('text-[11px] font-mono text-right', entry.avg_rr > 1 ? 'text-neon-green' : 'text-text-secondary')}>
                  {entry.avg_rr.toFixed(2)}
                </span>
                <span className="text-[11px] font-mono text-right text-text-secondary">
                  {entry.total_trades}
                </span>
              </div>
            );
          })
        )}
        </div>
      </div>

      {/* Equity Curves + Stat Comparison */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {/* Multi-model equity chart */}
        <div className="rounded-xl border border-border-primary/20 bg-bg-primary p-3">
          <div className="flex items-center gap-2 mb-2">
            <TrendingUp className="h-3.5 w-3.5 text-neon-gold" />
            <span className="text-[11px] font-mono font-semibold text-text-secondary">Equity Curves Overlay</span>
          </div>
          <MultiEquityChart curves={data?.equity_curves || {}} />
        </div>

        {/* Side-by-side stat comparison */}
        <div className="rounded-xl border border-border-primary/20 bg-bg-primary p-3">
          <div className="flex items-center gap-2 mb-2">
            <BarChart3 className="h-3.5 w-3.5 text-neon-gold" />
            <span className="text-[11px] font-mono font-semibold text-text-secondary">Metric Breakdown</span>
          </div>
          {lb.length > 0 ? (
            <div className="grid grid-cols-2 gap-2">
              <StatCompare
                label="Win Rate"
                format="pct"
                values={lb.map((e) => ({ name: e.name, value: e.win_rate, type: e.model_type }))}
              />
              <StatCompare
                label="Profit Factor"
                format="ratio"
                values={lb.map((e) => ({ name: e.name, value: e.profit_factor, type: e.model_type }))}
              />
              <StatCompare
                label="Sharpe Ratio"
                format="ratio"
                values={lb.map((e) => ({ name: e.name, value: e.sharpe_ratio, type: e.model_type }))}
              />
              <StatCompare
                label="Max Drawdown"
                format="pct"
                higherIsBetter={false}
                values={lb.map((e) => ({ name: e.name, value: Math.abs(e.max_drawdown), type: e.model_type }))}
              />
            </div>
          ) : (
            <div className="text-[10px] text-text-muted text-center py-8">Train models to compare</div>
          )}
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-4 gap-2">
        {[
          { label: 'GenAI Agents', icon: BrainCircuit, color: '#ffd700', desc: '12-agent pipeline con debate + arbitraje' },
          { label: 'ML (XGB+LGB)', icon: Cpu, color: '#00f5ff', desc: 'Gradient boosting con SL/TP regression' },
          { label: 'Deep Learning', icon: Network, color: '#bf40ff', desc: 'Transformer encoder con attention' },
          { label: 'RL (PPO)', icon: Bot, color: '#39ff14', desc: 'Policy gradient con reward RR-ajustado' },
        ].map(({ label, icon: Icon, color, desc }) => (
          <div key={label} className="rounded-xl border border-border-primary/20 bg-bg-primary p-3 flex flex-col gap-1.5">
            <div className="flex items-center gap-2">
              <Icon className="h-4 w-4" style={{ color }} />
              <span className="text-[11px] font-mono font-bold" style={{ color }}>{label}</span>
            </div>
            <span className="text-[9px] text-text-muted font-mono">{desc}</span>
          </div>
        ))}
      </div>

      {loading && lb.length === 0 && (
        <div className="flex items-center justify-center py-12 text-text-muted text-xs">
          <RefreshCw className="h-4 w-4 animate-spin mr-2" /> Loading comparison data...
        </div>
      )}
    </div>
  );
}
