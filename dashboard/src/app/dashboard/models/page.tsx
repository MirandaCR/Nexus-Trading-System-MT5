'use client';

import React, { useEffect, useState, useCallback } from 'react';
import {
  Cpu,
  Network,
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
  Layers,
  Brain,
  Gamepad2,
  Activity,
  GitBranch,
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

interface AllTraders {
  ml?: { model_type: string; model_name: string; is_fitted: boolean; performance: ModelPerformance };
  dl?: { model_type: string; model_name: string; is_fitted: boolean; performance: ModelPerformance };
  rl?: { model_type: string; model_name: string; is_fitted: boolean; performance: ModelPerformance };
  training_in_progress?: Record<string, boolean>;
}

const TABS = [
  { id: 'ml', label: 'ML', icon: Cpu, color: '#00f5ff', desc: 'XGBoost + LightGBM' },
  { id: 'dl', label: 'DL', icon: Network, color: '#bf40ff', desc: 'Transformer Encoder' },
  { id: 'rl', label: 'RL', icon: Bot, color: '#39ff14', desc: 'PPO Agent' },
  { id: 'hmm', label: 'HMM', icon: GitBranch, color: '#ff9500', desc: 'Regime Detector' },
] as const;

type TabId = (typeof TABS)[number]['id'];

function MetricCard({ label, value, format = 'number', positive, color }: {
  label: string; value: number; format?: 'number' | 'pct' | 'ratio' | 'pips';
  positive?: boolean; color?: string;
}) {
  const formatted = format === 'pct' ? `${(value * 100).toFixed(1)}%`
    : format === 'ratio' ? value.toFixed(2)
    : format === 'pips' ? `${value.toFixed(1)}`
    : value.toFixed(0);
  return (
    <div className="flex flex-col gap-0.5 p-2.5 rounded-lg bg-bg-card border border-border-primary/20">
      <span className="text-[9px] text-text-muted font-mono uppercase">{label}</span>
      <span className={cn('text-base font-mono font-bold',
        positive === undefined ? 'text-text-primary' : positive ? 'text-neon-green' : 'text-neon-red'
      )} style={color ? { color } : undefined}>
        {formatted}
      </span>
    </div>
  );
}

function MiniEquityChart({ data, color = '#00f5ff' }: { data: number[]; color?: string }) {
  if (!data || data.length < 2) return <div className="text-[10px] text-text-muted text-center py-4">No equity data</div>;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const w = 400; const h = 80;
  const points = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - min) / range) * h}`);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-20">
      <polyline points={points.join(' ')} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
      <polyline points={`0,${h} ${points.join(' ')} ${w},${h}`} fill={`${color}15`} stroke="none" />
    </svg>
  );
}

function ModelPanel({ perf, accentColor, modelKey, training, onTrain }: {
  perf: ModelPerformance | null;
  accentColor: string;
  modelKey: string;
  training: boolean;
  onTrain: () => void;
}) {
  if (!perf) return (
    <div className="flex items-center justify-center py-12 text-text-muted text-xs gap-2">
      <AlertCircle className="h-4 w-4" /> No data for {modelKey.toUpperCase()} model
    </div>
  );

  return (
    <div className="space-y-3">
      {/* Status + Train button */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] font-mono text-text-muted bg-bg-card px-2 py-0.5 rounded border border-border-primary/20">
          {perf.model_name}
        </span>
        {perf.is_fitted ? (
          <span className="flex items-center gap-1 text-[10px] text-neon-green">
            <CheckCircle2 className="h-3 w-3" /> Trained
          </span>
        ) : (
          <span className="flex items-center gap-1 text-[10px] text-neon-gold">
            <AlertCircle className="h-3 w-3" /> Not Trained
          </span>
        )}
        <div className="flex-1" />
        <button
          onClick={onTrain}
          disabled={training}
          className="flex items-center gap-1 px-2.5 py-1 rounded text-[10px] font-medium border transition-all disabled:opacity-50"
          style={{ borderColor: `${accentColor}40`, color: accentColor }}
        >
          {training ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
          {training ? 'Training...' : 'Train'}
        </button>
      </div>

      {/* Metrics Grid */}
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-1.5">
        <MetricCard label="Win Rate" value={perf.win_rate} format="pct" positive={perf.win_rate > 0.5} />
        <MetricCard label="PF" value={perf.profit_factor} format="ratio" positive={perf.profit_factor > 1} />
        <MetricCard label="Sharpe" value={perf.sharpe_ratio} format="ratio" positive={perf.sharpe_ratio > 0} />
        <MetricCard label="Max DD" value={perf.max_drawdown} format="pct" positive={perf.max_drawdown > -0.1} />
        <MetricCard label="Avg RR" value={perf.avg_rr} format="ratio" positive={perf.avg_rr > 1} />
        <MetricCard label="Trades" value={perf.total_trades} />
      </div>

      {/* Equity + Details */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
        <div className="rounded-lg border border-border-primary/20 bg-bg-primary p-2.5">
          <div className="flex items-center gap-2 mb-1.5">
            <TrendingUp className="h-3 w-3" style={{ color: accentColor }} />
            <span className="text-[10px] font-mono font-semibold text-text-secondary">Equity Curve</span>
            <span className="text-[9px] text-text-muted ml-auto">
              Return: {(perf.total_return * 100).toFixed(2)}%
            </span>
          </div>
          <MiniEquityChart data={perf.equity_curve} color={accentColor} />
        </div>

        <div className="rounded-lg border border-border-primary/20 bg-bg-primary p-2.5">
          <div className="flex items-center gap-2 mb-1.5">
            <Brain className="h-3 w-3" style={{ color: accentColor }} />
            <span className="text-[10px] font-mono font-semibold text-text-secondary">Details</span>
          </div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[10px] font-mono">
            <div className="flex justify-between"><span className="text-text-muted">Samples</span><span style={{ color: accentColor }}>{perf.train_samples.toLocaleString()}</span></div>
            <div className="flex justify-between"><span className="text-text-muted">Train Time</span><span className="text-text-primary">{perf.training_time_sec.toFixed(1)}s</span></div>
            <div className="flex justify-between"><span className="text-text-muted">Avg Win</span><span className="text-neon-green">{perf.avg_win_pips.toFixed(1)}p</span></div>
            <div className="flex justify-between"><span className="text-text-muted">Avg Loss</span><span className="text-neon-red">{perf.avg_loss_pips.toFixed(1)}p</span></div>
            <div className="flex justify-between"><span className="text-text-muted">Expectancy</span><span className={perf.expectancy > 0 ? 'text-neon-green' : 'text-neon-red'}>{perf.expectancy.toFixed(4)}</span></div>
            <div className="flex justify-between"><span className="text-text-muted">Sortino</span><span className={perf.sortino_ratio > 0 ? 'text-neon-cyan' : 'text-text-muted'}>{perf.sortino_ratio.toFixed(2)}</span></div>
            <div className="col-span-2 flex justify-between"><span className="text-text-muted">Last Trained</span><span className="text-text-secondary">{perf.last_trained ? new Date(perf.last_trained).toLocaleString() : 'Never'}</span></div>
          </div>
        </div>
      </div>

      {/* Recent Signals */}
      <div className="rounded-lg border border-border-primary/20 bg-bg-primary p-2.5">
        <div className="flex items-center gap-2 mb-1.5">
          <Zap className="h-3 w-3 text-neon-gold" />
          <span className="text-[10px] font-mono font-semibold text-text-secondary">Recent Signals</span>
          <span className="text-[9px] text-text-muted ml-auto">{perf.recent_signals?.length ?? 0}</span>
        </div>
        <div className="max-h-[200px] overflow-y-auto scrollbar-hide">
          {(!perf.recent_signals || perf.recent_signals.length === 0) ? (
            <div className="text-[10px] text-text-muted text-center py-3">No signals yet</div>
          ) : (
            <table className="w-full text-[9px] font-mono table-fixed">
              <thead>
                <tr className="text-text-muted border-b border-border-primary/10">
                  <th className="text-left w-[40px] py-1">Dir</th>
                  <th className="text-left w-[60px] py-1">Symbol</th>
                  <th className="text-right w-[40px] py-1">Conf</th>
                  <th className="text-right w-[32px] py-1">SL</th>
                  <th className="text-right w-[32px] py-1">TP</th>
                  <th className="text-left py-1">Reasoning</th>
                  <th className="text-right w-[56px] py-1">Time</th>
                </tr>
              </thead>
              <tbody>
                {perf.recent_signals.map((sig, i) => (
                  <tr key={i} className="border-b border-border-primary/5">
                    <td className={cn('py-1 font-bold', sig.direction === 'BUY' ? 'text-neon-green' : sig.direction === 'SELL' ? 'text-neon-red' : 'text-text-muted')}>
                      {sig.direction}
                    </td>
                    <td className="py-1 text-neon-cyan">{sig.symbol}</td>
                    <td className="py-1 text-right text-text-secondary">{sig.confidence.toFixed(2)}</td>
                    <td className="py-1 text-right text-neon-red/70">{sig.sl_pips.toFixed(0)}</td>
                    <td className="py-1 text-right text-neon-green/70">{sig.tp_pips.toFixed(0)}</td>
                    <td className="py-1 text-text-muted truncate">{sig.reasoning?.slice(0, 60)}</td>
                    <td className="py-1 text-right text-text-muted">{sig.timestamp ? new Date(sig.timestamp).toLocaleTimeString() : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

function HMMPanel() {
  const [regime, setRegime] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchRegime = async () => {
      try {
        const res = await fetch(`${API}/api/regime/current`);
        if (res.ok) {
          const data = await res.json();
          if (data && !data.error) setRegime(data);
        }
      } catch {/* silent */}
      setLoading(false);
    };
    fetchRegime();
    const id = setInterval(fetchRegime, 15000);
    return () => clearInterval(id);
  }, []);

  const states = ['TRENDING_UP', 'TRENDING_DOWN', 'RANGING', 'HIGH_VOLATILITY'];
  const stateColors: Record<string, string> = {
    TRENDING_UP: '#39ff14', TRENDING_DOWN: '#ff2255',
    RANGING: '#ffcc00', HIGH_VOLATILITY: '#bf40ff',
  };

  const currentState = String(regime?.regime ?? regime?.hmm_regime ?? 'UNKNOWN');
  const confidence = Number(regime?.hmm_confidence ?? 0);
  const probs = (regime?.hmm_state_probs ?? {}) as Record<string, number>;

  const nodePositions: Record<string, { x: number; y: number }> = {
    TRENDING_UP: { x: 160, y: 40 },
    TRENDING_DOWN: { x: 320, y: 40 },
    RANGING: { x: 100, y: 140 },
    HIGH_VOLATILITY: { x: 380, y: 140 },
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-mono text-text-muted">Hidden Markov Model — 4-state Regime Detection</span>
        {loading && <RefreshCw className="h-3 w-3 animate-spin text-text-muted" />}
      </div>

      {/* Current regime */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg border" style={{ borderColor: `${stateColors[currentState] ?? '#666'}40`, backgroundColor: `${stateColors[currentState] ?? '#666'}10` }}>
          <span className="h-2 w-2 rounded-full animate-pulse" style={{ backgroundColor: stateColors[currentState] ?? '#666' }} />
          <span className="text-sm font-mono font-bold" style={{ color: stateColors[currentState] ?? '#aaa' }}>
            {currentState.replace(/_/g, ' ')}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-text-muted font-mono">Confidence</span>
          <div className="h-2 w-24 rounded-full bg-bg-tertiary overflow-hidden">
            <div className="h-full rounded-full" style={{ width: `${confidence * 100}%`, backgroundColor: stateColors[currentState] ?? '#666' }} />
          </div>
          <span className="text-[10px] font-mono font-bold" style={{ color: stateColors[currentState] ?? '#aaa' }}>
            {(confidence * 100).toFixed(0)}%
          </span>
        </div>
      </div>

      {/* SVG State Transition Graph */}
      <div className="rounded-lg border border-border-primary/20 bg-bg-primary p-3">
        <span className="text-[10px] font-mono text-text-muted uppercase mb-2 block">State Transition Graph</span>
        <svg viewBox="0 0 480 190" className="w-full max-w-lg">
          <defs>
            <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0, 8 3, 0 6" fill="#555" />
            </marker>
          </defs>
          {/* Edges between states */}
          {states.map((from, fi) =>
            states.filter(to => to !== from).map((to, ti) => {
              const f = nodePositions[from];
              const t = nodePositions[to];
              const mx = (f.x + t.x) / 2;
              const my = (f.y + t.y) / 2;
              const dx = t.x - f.x;
              const dy = t.y - f.y;
              const len = Math.sqrt(dx * dx + dy * dy);
              const ox = -dy / len * 8;
              const oy = dx / len * 8;
              return (
                <line
                  key={`${from}-${to}`}
                  x1={f.x + dx / len * 28} y1={f.y + dy / len * 28}
                  x2={t.x - dx / len * 28} y2={t.y - dy / len * 28}
                  stroke="#333" strokeWidth="0.8" markerEnd="url(#arrowhead)" opacity={0.3}
                />
              );
            })
          )}
          {/* Nodes */}
          {states.map((state) => {
            const pos = nodePositions[state];
            const isCurrent = state === currentState;
            const prob = probs[state] ?? 0;
            return (
              <g key={state}>
                <circle
                  cx={pos.x} cy={pos.y} r={isCurrent ? 26 : 22}
                  fill={isCurrent ? `${stateColors[state]}20` : '#111'}
                  stroke={stateColors[state]}
                  strokeWidth={isCurrent ? 2 : 0.8}
                  opacity={isCurrent ? 1 : 0.5}
                />
                {isCurrent && (
                  <circle cx={pos.x} cy={pos.y} r={28} fill="none" stroke={stateColors[state]} strokeWidth="0.5" opacity={0.4}>
                    <animate attributeName="r" from="26" to="34" dur="2s" repeatCount="indefinite" />
                    <animate attributeName="opacity" from="0.4" to="0" dur="2s" repeatCount="indefinite" />
                  </circle>
                )}
                <text x={pos.x} y={pos.y - 4} textAnchor="middle" fill={stateColors[state]} fontSize="7" fontFamily="monospace" fontWeight="bold">
                  {state.replace('_', ' ').replace('TRENDING ', '').replace('HIGH_', 'HI-')}
                </text>
                <text x={pos.x} y={pos.y + 8} textAnchor="middle" fill="#888" fontSize="7" fontFamily="monospace">
                  {(prob * 100).toFixed(0)}%
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      {/* State Probabilities */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5">
        {states.map((state) => {
          const prob = probs[state] ?? 0;
          const isCurrent = state === currentState;
          return (
            <div key={state} className={cn(
              'rounded-lg border p-2',
              isCurrent ? 'border-opacity-40' : 'border-border-primary/15'
            )} style={isCurrent ? { borderColor: stateColors[state] } : undefined}>
              <div className="flex items-center gap-1.5 mb-1">
                <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: stateColors[state] }} />
                <span className="text-[9px] font-mono text-text-muted uppercase">{state.replace(/_/g, ' ')}</span>
              </div>
              <div className="h-1.5 rounded-full bg-bg-tertiary overflow-hidden">
                <div className="h-full rounded-full transition-all" style={{ width: `${prob * 100}%`, backgroundColor: stateColors[state] }} />
              </div>
              <span className="text-[10px] font-mono font-bold mt-0.5 block" style={{ color: stateColors[state] }}>
                {(prob * 100).toFixed(1)}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function ModelsPage() {
  const [activeTab, setActiveTab] = useState<TabId>('ml');
  const [traders, setTraders] = useState<AllTraders | null>(null);
  const [loading, setLoading] = useState(true);
  const [trainingModel, setTrainingModel] = useState<string | null>(null);
  const [error, setError] = useState('');

  const fetchAll = useCallback(async () => {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 8000);
    try {
      const res = await fetch(`${API}/api/ml/traders/status`, { signal: ctrl.signal });
      if (res.ok) { setTraders(await res.json()); setError(''); }
      else setError('Service unavailable');
    } catch { setError('ML service busy — retrying…'); }
    finally { clearTimeout(timer); setLoading(false); }
  }, []);

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, 12_000);
    return () => clearInterval(id);
  }, [fetchAll]);

  const handleTrain = async (modelType: string) => {
    setTrainingModel(modelType);
    try {
      await fetch(`${API}/api/ml/traders/train`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_types: [modelType] }),
      });
    } catch {/* silent */}
    setTimeout(() => { setTrainingModel(null); fetchAll(); }, 3000);
  };

  const currentTab = TABS.find(t => t.id === activeTab)!;

  return (
    <div className="p-2 sm:p-3 w-full max-w-[1800px] mx-auto space-y-3">
      {/* Tab bar */}
      <div className="flex items-center gap-1 border-b border-border-primary/20 pb-1">
        {TABS.map((tab) => {
          const Icon = tab.icon;
          const isActive = tab.id === activeTab;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 rounded-t text-[11px] font-mono font-medium transition-all border-b-2',
                isActive
                  ? 'border-current text-text-primary bg-bg-card'
                  : 'border-transparent text-text-muted hover:text-text-secondary hover:bg-bg-tertiary/30'
              )}
              style={isActive ? { color: tab.color, borderColor: tab.color } : undefined}
            >
              <Icon className="h-3.5 w-3.5" />
              {tab.label}
              <span className="hidden sm:inline text-[9px] text-text-muted ml-1">{tab.desc}</span>
            </button>
          );
        })}
        <div className="flex-1" />
        <button onClick={fetchAll} disabled={loading} className="text-text-muted hover:text-text-secondary p-1">
          <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 px-3 py-1.5 rounded bg-neon-red/10 border border-neon-red/20 text-[10px] text-neon-red">
          <AlertCircle className="h-3 w-3" /> {error}
        </div>
      )}

      {/* Tab content */}
      {activeTab === 'hmm' ? (
        <HMMPanel />
      ) : (
        <ModelPanel
          perf={traders?.[activeTab as 'ml' | 'dl' | 'rl']?.performance ?? null}
          accentColor={currentTab.color}
          modelKey={activeTab}
          training={trainingModel === activeTab}
          onTrain={() => handleTrain(activeTab)}
        />
      )}

      {loading && !traders && (
        <div className="flex items-center justify-center py-12 text-text-muted text-xs">
          <RefreshCw className="h-4 w-4 animate-spin mr-2" /> Loading model status...
        </div>
      )}
    </div>
  );
}
