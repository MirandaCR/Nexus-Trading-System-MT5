'use client';

import React, { useEffect, useState, useCallback } from 'react';
import {
  Cpu,
  RefreshCw,
  TrendingUp,
  TrendingDown,
  Target,
  BarChart3,
  Clock,
  Zap,
  AlertCircle,
  CheckCircle2,
  Play,
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

interface TraderStatus {
  model_type: string;
  model_name: string;
  is_fitted: boolean;
  performance: ModelPerformance;
}

function MetricCard({
  label,
  value,
  format = 'number',
  positive,
  icon: Icon,
}: {
  label: string;
  value: number;
  format?: 'number' | 'pct' | 'pips' | 'ratio';
  positive?: boolean;
  icon?: React.ElementType;
}) {
  const formatted =
    format === 'pct'
      ? `${(value * 100).toFixed(1)}%`
      : format === 'pips'
      ? `${value.toFixed(1)}`
      : format === 'ratio'
      ? value.toFixed(2)
      : value.toFixed(4);

  return (
    <div className="flex flex-col gap-1 p-3 rounded-lg bg-bg-card border border-border-primary/20">
      <div className="flex items-center gap-1.5">
        {Icon && <Icon className="h-3 w-3 text-text-muted" />}
        <span className="text-[10px] text-text-muted font-mono uppercase">{label}</span>
      </div>
      <span
        className={cn(
          'text-lg font-mono font-bold',
          positive === undefined
            ? 'text-text-primary'
            : positive
            ? 'text-neon-green'
            : 'text-neon-red'
        )}
      >
        {formatted}
      </span>
    </div>
  );
}

function MiniEquityChart({ data, color = '#00f5ff' }: { data: number[]; color?: string }) {
  if (data.length < 2) return <div className="text-[10px] text-text-muted">No equity data</div>;

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const w = 280;
  const h = 80;

  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w;
    const y = h - ((v - min) / range) * h;
    return `${x},${y}`;
  });

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-20">
      <polyline
        points={points.join(' ')}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <polyline
        points={`0,${h} ${points.join(' ')} ${w},${h}`}
        fill={`${color}15`}
        stroke="none"
      />
    </svg>
  );
}

function SignalRow({
  signal,
}: {
  signal: ModelPerformance['recent_signals'][0];
}) {
  const dirColor =
    signal.direction === 'BUY'
      ? 'text-neon-green'
      : signal.direction === 'SELL'
      ? 'text-neon-red'
      : 'text-text-muted';

  return (
    <div className="flex items-center gap-2 py-1.5 border-b border-border-primary/10 last:border-0">
      <span className={cn('text-[10px] font-mono font-bold w-12', dirColor)}>
        {signal.direction}
      </span>
      <span className="text-[10px] font-mono text-neon-cyan w-16">{signal.symbol}</span>
      <span className="text-[10px] font-mono text-text-secondary">
        conf={signal.confidence.toFixed(2)}
      </span>
      <span className="text-[10px] font-mono text-neon-gold">
        SL={signal.sl_pips.toFixed(0)} TP={signal.tp_pips.toFixed(0)}
      </span>
      <span className="flex-1 text-[10px] font-mono text-text-muted truncate">
        {signal.reasoning?.slice(0, 60)}
      </span>
      <span className="text-[9px] text-text-muted shrink-0">
        {signal.timestamp ? new Date(signal.timestamp).toLocaleTimeString() : ''}
      </span>
    </div>
  );
}

export default function MLModelsPage() {
  const [status, setStatus] = useState<TraderStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [training, setTraining] = useState(false);
  const [error, setError] = useState('');

  const fetchStatus = useCallback(async () => {
    // Abort after 8s so the panel never hangs on "Loading…" while the ML
    // service is busy retraining (its event loop blocks during training).
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 8000);
    try {
      const res = await fetch(`${API}/api/ml/traders/status`, { signal: ctrl.signal });
      if (res.ok) {
        const data = await res.json();
        setStatus(data.ml);
        setError('');
      } else {
        setError('Service unavailable');
      }
    } catch {
      setError('ML service busy (retraining) — retrying…');
    } finally {
      clearTimeout(timer);
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
        body: JSON.stringify({ model_types: ['ml'] }),
      });
    } catch {}
    setTimeout(() => {
      setTraining(false);
      fetchStatus();
    }, 3000);
  };

  const perf = status?.performance;

  return (
    <div className="p-2 sm:p-4 w-full max-w-[1800px] mx-auto space-y-3 sm:space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <Cpu className="h-5 w-5 text-neon-cyan" />
          <h1 className="text-sm font-bold text-text-primary">Machine Learning Trader</h1>
        </div>
        <span className="text-[10px] font-mono text-text-muted bg-bg-card px-2 py-0.5 rounded border border-border-primary/20">
          XGBoost + LightGBM | SL/TP Regression
        </span>
        <div className="flex-1" />
        {status?.is_fitted ? (
          <span className="flex items-center gap-1 text-[10px] text-neon-green">
            <CheckCircle2 className="h-3 w-3" /> Trained
          </span>
        ) : (
          <span className="flex items-center gap-1 text-[10px] text-neon-gold">
            <AlertCircle className="h-3 w-3" /> Not Trained
          </span>
        )}
        <button
          onClick={handleTrain}
          disabled={training}
          className={cn(
            'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-medium border transition-all',
            training
              ? 'border-border-primary text-text-muted cursor-not-allowed'
              : 'border-neon-cyan/30 text-neon-cyan bg-neon-cyan/5 hover:bg-neon-cyan/10'
          )}
        >
          {training ? (
            <RefreshCw className="h-3 w-3 animate-spin" />
          ) : (
            <Play className="h-3 w-3" />
          )}
          {training ? 'Training...' : 'Train Model'}
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-neon-red/10 border border-neon-red/20 text-[11px] text-neon-red">
          <AlertCircle className="h-3 w-3" /> {error}
        </div>
      )}

      {/* Metrics Grid */}
      {perf && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
            <MetricCard label="Win Rate" value={perf.win_rate} format="pct" positive={perf.win_rate > 0.5} icon={Target} />
            <MetricCard label="Profit Factor" value={perf.profit_factor} format="ratio" positive={perf.profit_factor > 1} icon={TrendingUp} />
            <MetricCard label="Sharpe" value={perf.sharpe_ratio} format="ratio" positive={perf.sharpe_ratio > 0} icon={BarChart3} />
            <MetricCard label="Max DD" value={perf.max_drawdown} format="pct" positive={perf.max_drawdown > -0.1} icon={TrendingDown} />
            <MetricCard label="Avg RR" value={perf.avg_rr} format="ratio" positive={perf.avg_rr > 1} icon={Zap} />
            <MetricCard label="Trades" value={perf.total_trades} icon={BarChart3} />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {/* Equity Curve */}
            <div className="rounded-xl border border-border-primary/20 bg-bg-primary p-3">
              <div className="flex items-center gap-2 mb-2">
                <TrendingUp className="h-3.5 w-3.5 text-neon-cyan" />
                <span className="text-[11px] font-mono font-semibold text-text-secondary">Equity Curve</span>
                <span className="text-[10px] text-text-muted ml-auto">
                  Return: {(perf.total_return * 100).toFixed(2)}%
                </span>
              </div>
              <MiniEquityChart data={perf.equity_curve} color="#00f5ff" />
            </div>

            {/* Model Info */}
            <div className="rounded-xl border border-border-primary/20 bg-bg-primary p-3">
              <div className="flex items-center gap-2 mb-2">
                <Cpu className="h-3.5 w-3.5 text-neon-cyan" />
                <span className="text-[11px] font-mono font-semibold text-text-secondary">Model Info</span>
              </div>
              <div className="grid grid-cols-2 gap-2 text-[11px] font-mono">
                <div className="flex justify-between">
                  <span className="text-text-muted">Direction</span>
                  <span className="text-text-primary">XGBoost (300 trees)</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-text-muted">SL/TP</span>
                  <span className="text-text-primary">LightGBM Regression</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-text-muted">Samples</span>
                  <span className="text-neon-cyan">{perf.train_samples.toLocaleString()}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-text-muted">Train Time</span>
                  <span className="text-text-primary">{perf.training_time_sec.toFixed(1)}s</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-text-muted">Avg Win</span>
                  <span className="text-neon-green">{perf.avg_win_pips.toFixed(1)} pips</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-text-muted">Avg Loss</span>
                  <span className="text-neon-red">{perf.avg_loss_pips.toFixed(1)} pips</span>
                </div>
                <div className="col-span-2 flex justify-between">
                  <span className="text-text-muted">Last Trained</span>
                  <span className="text-text-secondary">
                    {perf.last_trained ? new Date(perf.last_trained).toLocaleString() : 'Never'}
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* Recent Signals */}
          <div className="rounded-xl border border-border-primary/20 bg-bg-primary p-3">
            <div className="flex items-center gap-2 mb-2">
              <Zap className="h-3.5 w-3.5 text-neon-gold" />
              <span className="text-[11px] font-mono font-semibold text-text-secondary">Recent Signals</span>
              <span className="text-[10px] text-text-muted ml-auto">{perf.recent_signals.length} signals</span>
            </div>
            <div className="max-h-[300px] overflow-y-auto scrollbar-hide">
              {perf.recent_signals.length === 0 ? (
                <div className="text-[10px] text-text-muted text-center py-4">No signals yet</div>
              ) : (
                perf.recent_signals.map((sig, i) => <SignalRow key={i} signal={sig} />)
              )}
            </div>
          </div>
        </>
      )}

      {loading && !perf && (
        <div className="flex items-center justify-center py-12 text-text-muted text-xs">
          <RefreshCw className="h-4 w-4 animate-spin mr-2" /> Loading ML model status...
        </div>
      )}
    </div>
  );
}
