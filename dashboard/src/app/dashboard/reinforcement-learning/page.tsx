'use client';

import React, { useEffect, useState, useCallback } from 'react';
import {
  Bot,
  RefreshCw,
  TrendingUp,
  TrendingDown,
  Target,
  BarChart3,
  Zap,
  AlertCircle,
  CheckCircle2,
  Play,
  Gamepad2,
  Award,
  Activity,
} from 'lucide-react';
import { cn } from '@/lib/utils';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

interface ModelPerformance {
  model_type: string;
  model_name: string;
  is_fitted: boolean;
  total_trades: number;
  win_rate: number;
  profit_factor: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  max_drawdown: number;
  total_return: number;
  avg_rr: number;
  avg_win_pips: number;
  avg_loss_pips: number;
  expectancy: number;
  train_samples: number;
  last_trained: string;
  training_time_sec: number;
  equity_curve: number[];
  recent_signals: Array<{
    direction: string;
    confidence: number;
    sl_pips: number;
    tp_pips: number;
    symbol: string;
    timestamp: string;
    reasoning: string;
  }>;
}

function MetricCard({ label, value, format = 'number', positive }: {
  label: string; value: number; format?: 'number' | 'pct' | 'ratio'; positive?: boolean;
}) {
  const formatted = format === 'pct' ? `${(value * 100).toFixed(1)}%` : format === 'ratio' ? value.toFixed(2) : value.toFixed(4);
  return (
    <div className="flex flex-col gap-1 p-3 rounded-lg bg-bg-card border border-border-primary/20">
      <span className="text-[10px] text-text-muted font-mono uppercase">{label}</span>
      <span className={cn('text-lg font-mono font-bold', positive === undefined ? 'text-text-primary' : positive ? 'text-neon-green' : 'text-neon-red')}>
        {formatted}
      </span>
    </div>
  );
}

function MiniEquityChart({ data }: { data: number[] }) {
  if (data.length < 2) return <div className="text-[10px] text-text-muted">No equity data</div>;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const w = 400; const h = 100;
  const points = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - min) / range) * h}`);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-24">
      <polyline points={points.join(' ')} fill="none" stroke="#39ff14" strokeWidth="1.5" strokeLinejoin="round" />
      <polyline points={`0,${h} ${points.join(' ')} ${w},${h}`} fill="#39ff1415" stroke="none" />
    </svg>
  );
}

export default function ReinforcementLearningPage() {
  const [perf, setPerf] = useState<ModelPerformance | null>(null);
  const [loading, setLoading] = useState(true);
  const [training, setTraining] = useState(false);
  const [error, setError] = useState('');

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/ml/traders/status`);
      if (res.ok) {
        const data = await res.json();
        setPerf(data.rl?.performance || null);
        setError('');
      } else {
        setError('Service unavailable');
      }
    } catch {
      setError('Cannot connect');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const id = setInterval(fetchStatus, 10_000);
    return () => clearInterval(id);
  }, [fetchStatus]);

  const handleTrain = async () => {
    setTraining(true);
    try {
      await fetch(`${API}/api/ml/traders/train`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_types: ['rl'] }),
      });
    } catch {}
    setTimeout(() => { setTraining(false); fetchStatus(); }, 3000);
  };

  return (
    <div className="p-2 sm:p-4 w-full max-w-[1800px] mx-auto space-y-3 sm:space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <Bot className="h-5 w-5 text-neon-green" />
          <h1 className="text-sm font-bold text-text-primary">Reinforcement Learning Trader</h1>
        </div>
        <span className="text-[10px] font-mono text-text-muted bg-bg-card px-2 py-0.5 rounded border border-border-primary/20">
          PPO Agent | Actor-Critic | Continuous SL/TP
        </span>
        <div className="flex-1" />
        {perf?.is_fitted ? (
          <span className="flex items-center gap-1 text-[10px] text-neon-green"><CheckCircle2 className="h-3 w-3" /> Trained</span>
        ) : (
          <span className="flex items-center gap-1 text-[10px] text-neon-gold"><AlertCircle className="h-3 w-3" /> Not Trained</span>
        )}
        <button
          onClick={handleTrain}
          disabled={training}
          className={cn(
            'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-medium border transition-all',
            training ? 'border-border-primary text-text-muted cursor-not-allowed' : 'border-neon-green/30 text-neon-green bg-neon-green/5 hover:bg-neon-green/10'
          )}
        >
          {training ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
          {training ? 'Training...' : 'Train Agent'}
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-neon-red/10 border border-neon-red/20 text-[11px] text-neon-red">
          <AlertCircle className="h-3 w-3" /> {error}
        </div>
      )}

      {perf && (
        <>
          {/* PPO Architecture */}
          <div className="rounded-xl border border-neon-green/20 bg-bg-primary p-4">
            <div className="flex items-center gap-2 mb-3">
              <Gamepad2 className="h-4 w-4 text-neon-green" />
              <span className="text-[11px] font-mono font-semibold text-text-secondary">PPO Agent Architecture</span>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div className="rounded-lg bg-bg-card border border-neon-green/10 p-3">
                <div className="text-[10px] font-mono font-bold text-neon-green mb-1">Environment</div>
                <div className="space-y-0.5 text-[9px] text-text-muted font-mono">
                  <div>State: Features + Position + PnL</div>
                  <div>Actions: HOLD / BUY / SELL</div>
                  <div>Continuous: SL (5-50) / TP (10-100)</div>
                  <div>Reward: Risk-adjusted RR</div>
                </div>
              </div>
              <div className="rounded-lg bg-bg-card border border-neon-green/10 p-3">
                <div className="text-[10px] font-mono font-bold text-neon-green mb-1">Actor (Policy)</div>
                <div className="space-y-0.5 text-[9px] text-text-muted font-mono">
                  <div>Shared: 128 &rarr; 128 (GELU)</div>
                  <div>Action Head: Categorical(3)</div>
                  <div>SL Head: Normal(&mu;, &sigma;)</div>
                  <div>TP Head: Normal(&mu;, &sigma;)</div>
                </div>
              </div>
              <div className="rounded-lg bg-bg-card border border-neon-green/10 p-3">
                <div className="text-[10px] font-mono font-bold text-neon-green mb-1">Critic (Value)</div>
                <div className="space-y-0.5 text-[9px] text-text-muted font-mono">
                  <div>GAE &lambda; = 0.95</div>
                  <div>&gamma; = 0.99</div>
                  <div>Clip ratio: 0.2</div>
                  <div>Entropy bonus: 0.01</div>
                </div>
              </div>
            </div>
          </div>

          {/* Metrics */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
            <MetricCard label="Win Rate" value={perf.win_rate} format="pct" positive={perf.win_rate > 0.5} />
            <MetricCard label="Profit Factor" value={perf.profit_factor} format="ratio" positive={perf.profit_factor > 1} />
            <MetricCard label="Sharpe" value={perf.sharpe_ratio} format="ratio" positive={perf.sharpe_ratio > 0} />
            <MetricCard label="Max DD" value={perf.max_drawdown} format="pct" positive={perf.max_drawdown > -0.1} />
            <MetricCard label="Avg RR" value={perf.avg_rr} format="ratio" positive={perf.avg_rr > 1} />
            <MetricCard label="Trades" value={perf.total_trades} />
          </div>

          {/* Equity + Signals */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div className="rounded-xl border border-border-primary/20 bg-bg-primary p-3">
              <div className="flex items-center gap-2 mb-2">
                <TrendingUp className="h-3.5 w-3.5 text-neon-green" />
                <span className="text-[11px] font-mono font-semibold text-text-secondary">Episode Equity</span>
                <span className="text-[10px] text-text-muted ml-auto">Return: {(perf.total_return * 100).toFixed(2)}%</span>
              </div>
              <MiniEquityChart data={perf.equity_curve} />
            </div>

            <div className="rounded-xl border border-border-primary/20 bg-bg-primary p-3">
              <div className="flex items-center gap-2 mb-2">
                <Activity className="h-3.5 w-3.5 text-neon-green" />
                <span className="text-[11px] font-mono font-semibold text-text-secondary">Training Details</span>
              </div>
              <div className="space-y-1.5 text-[11px] font-mono">
                <div className="flex justify-between"><span className="text-text-muted">Algorithm</span><span className="text-neon-green">PPO (Clip)</span></div>
                <div className="flex justify-between"><span className="text-text-muted">Observations</span><span className="text-text-primary">{perf.train_samples.toLocaleString()}</span></div>
                <div className="flex justify-between"><span className="text-text-muted">Train Time</span><span className="text-text-primary">{perf.training_time_sec.toFixed(1)}s</span></div>
                <div className="flex justify-between"><span className="text-text-muted">Expectancy</span><span className={perf.expectancy > 0 ? 'text-neon-green' : 'text-neon-red'}>{perf.expectancy.toFixed(4)}</span></div>
                <div className="flex justify-between"><span className="text-text-muted">Avg Win</span><span className="text-neon-green">{perf.avg_win_pips.toFixed(1)} pips</span></div>
                <div className="flex justify-between"><span className="text-text-muted">Avg Loss</span><span className="text-neon-red">{perf.avg_loss_pips.toFixed(1)} pips</span></div>
                <div className="flex justify-between"><span className="text-text-muted">Last Trained</span><span className="text-text-secondary">{perf.last_trained ? new Date(perf.last_trained).toLocaleString() : 'Never'}</span></div>
              </div>
            </div>
          </div>

          {/* Recent Signals */}
          <div className="rounded-xl border border-border-primary/20 bg-bg-primary p-3">
            <div className="flex items-center gap-2 mb-2">
              <Zap className="h-3.5 w-3.5 text-neon-gold" />
              <span className="text-[11px] font-mono font-semibold text-text-secondary">Agent Decisions</span>
              <span className="text-[10px] text-text-muted ml-auto">{perf.recent_signals.length} signals</span>
            </div>
            <div className="max-h-[300px] overflow-y-auto scrollbar-hide">
              {perf.recent_signals.length === 0 ? (
                <div className="text-[10px] text-text-muted text-center py-4">No decisions yet — train the agent first</div>
              ) : (
                perf.recent_signals.map((sig, i) => (
                  <div key={i} className="flex items-center gap-2 py-1.5 border-b border-border-primary/10 text-[10px] font-mono last:border-0">
                    <span className={cn('font-bold w-10', sig.direction === 'BUY' ? 'text-neon-green' : sig.direction === 'SELL' ? 'text-neon-red' : 'text-text-muted')}>{sig.direction}</span>
                    <span className="text-neon-cyan w-14">{sig.symbol}</span>
                    <span className="text-text-secondary">conf={sig.confidence.toFixed(2)}</span>
                    <span className="text-neon-gold">SL={sig.sl_pips.toFixed(0)} TP={sig.tp_pips.toFixed(0)}</span>
                    <span className="flex-1 text-text-muted truncate">{sig.reasoning?.slice(0, 60)}</span>
                    <span className="text-[9px] text-text-muted shrink-0">{sig.timestamp ? new Date(sig.timestamp).toLocaleTimeString() : ''}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        </>
      )}

      {loading && !perf && (
        <div className="flex items-center justify-center py-12 text-text-muted text-xs">
          <RefreshCw className="h-4 w-4 animate-spin mr-2" /> Loading RL agent status...
        </div>
      )}
    </div>
  );
}
