'use client';

import React, { useState, useCallback, useEffect } from 'react';
import {
  FlaskConical, Play, Loader2, TrendingUp, TrendingDown,
  BarChart2, AlertTriangle, CheckCircle2, Clock,
} from 'lucide-react';
import { cn } from '@/lib/utils';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

// ── Types ─────────────────────────────────────────────────────────────────────

interface BacktestConfig {
  symbol: string;
  date_from: string;
  date_to: string;
  min_conviction: number;
  sessions: string[];
  strategies: string[];
  directions: string[];
  initial_equity: number;
}

interface Metrics {
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  profit_factor: number;
  net_pnl: number;
  avg_pnl: number;
  avg_win: number;
  avg_loss: number;
  max_drawdown: number;
  max_dd_pct: number;
  sharpe: number;
}

interface CurvePoint { n: number; ts: string; pnl: number; equity: number }
interface GroupMetrics { trades: number; win_rate: number; net_pnl: number; avg_pnl: number }

interface BacktestResult {
  config: BacktestConfig;
  raw_trades_fetched: number;
  trades_after_filter: number;
  metrics: Metrics;
  equity_curve: CurvePoint[];
  by_session: Record<string, GroupMetrics>;
  by_strategy: Record<string, GroupMetrics>;
  by_direction: Record<string, GroupMetrics>;
  by_hour: Record<string, { trades: number; win_rate: number; net_pnl: number }>;
  run_at: string;
}

const SESSIONS = ['LONDON', 'NEW_YORK', 'TOKYO', 'OVERLAP'];
const DIRECTIONS = ['BUY', 'SELL'];

// ── Equity Curve SVG ──────────────────────────────────────────────────────────

function EquityCurve({ data, initial }: { data: CurvePoint[]; initial: number }) {
  if (!data.length) return null;
  const W = 600, H = 140, PAD = 8;
  const equities = data.map(d => d.equity);
  const minEq = Math.min(initial, ...equities);
  const maxEq = Math.max(initial, ...equities);
  const range = maxEq - minEq || 1;
  const finalEq = data[data.length - 1]?.equity ?? initial;
  const lineColor = finalEq >= initial ? '#22c55e' : '#ef4444';

  const toX = (i: number) => PAD + (i / Math.max(data.length - 1, 1)) * (W - PAD * 2);
  const toY = (eq: number) => H - PAD - ((eq - minEq) / range) * (H - PAD * 2);

  const pts = data.map((d, i) => `${toX(i).toFixed(1)},${toY(d.equity).toFixed(1)}`).join(' ');
  const zeroY = toY(initial);

  // Area fill
  const areaPath = `M${toX(0)},${zeroY} ${data.map((d, i) => `L${toX(i)},${toY(d.equity)}`).join(' ')} L${toX(data.length - 1)},${zeroY} Z`;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 140 }} preserveAspectRatio="none">
      {/* Zero baseline */}
      <line x1={PAD} y1={zeroY} x2={W - PAD} y2={zeroY} stroke="#ffffff15" strokeDasharray="4 3" />
      {/* Area */}
      <path d={areaPath} fill={lineColor} fillOpacity="0.08" />
      {/* Line */}
      <polyline points={pts} fill="none" stroke={lineColor} strokeWidth="1.5" strokeLinejoin="round" />
      {/* Endpoint dot */}
      <circle cx={toX(data.length - 1)} cy={toY(finalEq)} r="3" fill={lineColor} />
    </svg>
  );
}

// ── Metric card ────────────────────────────────────────────────────────────────

function MetricCard({ label, value, sub, positive }: { label: string; value: string; sub?: string; positive?: boolean | null }) {
  const color = positive === null || positive === undefined ? 'text-text-primary' : positive ? 'text-neon-green' : 'text-neon-red';
  return (
    <div className="bg-bg-tertiary/30 border border-border-primary/40 rounded-lg px-3 py-2.5 flex flex-col gap-0.5">
      <div className="text-[8px] font-mono text-text-muted tracking-wider">{label}</div>
      <div className={cn('text-[15px] font-mono font-black', color)}>{value}</div>
      {sub && <div className="text-[8px] font-mono text-text-muted">{sub}</div>}
    </div>
  );
}

// ── Breakdown table ────────────────────────────────────────────────────────────

function BreakdownTable({ data, title }: { data: Record<string, GroupMetrics>; title: string }) {
  const rows = Object.entries(data).sort((a, b) => b[1].net_pnl - a[1].net_pnl);
  if (!rows.length) return null;
  return (
    <div>
      <div className="text-[9px] font-mono text-text-muted mb-1 tracking-wider">{title}</div>
      <div className="flex flex-col gap-0.5">
        {rows.map(([key, m]) => (
          <div key={key} className="flex items-center gap-2 text-[9px] font-mono">
            <span className="w-20 text-text-secondary font-bold truncate">{key}</span>
            <span className="w-10 text-text-muted">{m.trades}t</span>
            <span className={cn('w-12', (m.win_rate || 0) >= 0.5 ? 'text-neon-green' : 'text-neon-red')}>
              {((m.win_rate || 0) * 100).toFixed(0)}%WR
            </span>
            <div className="flex-1 h-1.5 bg-bg-tertiary/50 rounded-full overflow-hidden">
              <div
                className={cn('h-full rounded-full', m.net_pnl >= 0 ? 'bg-neon-green/60' : 'bg-neon-red/60')}
                style={{ width: `${Math.min(100, Math.abs(m.net_pnl) / (rows[0][1]?.net_pnl || 1) * 100)}%` }}
              />
            </div>
            <span className={cn('w-16 text-right', m.net_pnl >= 0 ? 'text-neon-green' : 'text-neon-red')}>
              {m.net_pnl >= 0 ? '+' : ''}${m.net_pnl.toFixed(0)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────────

const TODAY = new Date().toISOString().slice(0, 10);
const MONTH_AGO = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);

export default function BacktestPanel() {
  const [cfg, setCfg] = useState<BacktestConfig>({
    symbol: 'XAUUSD',
    date_from: MONTH_AGO,
    date_to: TODAY,
    min_conviction: 0.0,
    sessions: [],
    directions: [],
    strategies: [],
    initial_equity: 5000,
  });
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [breakdownTab, setBreakdownTab] = useState<'session' | 'strategy' | 'direction' | 'hour'>('session');
  const [strategies, setStrategies] = useState<string[]>([]);

  // Load available strategies + last result + user defaults on mount
  useEffect(() => {
    fetch(`${API}/api/backtest/strategies`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.strategies) setStrategies(d.strategies); })
      .catch(() => {});
    fetch(`${API}/api/backtest/summary`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.metrics) setResult(d as BacktestResult); })
      .catch(() => {});
    fetch(`${API}/api/intelligence/settings`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        const s = d?.settings ?? {};
        setCfg(c => ({
          ...c,
          ...(s.backtest_default_symbol ? { symbol: s.backtest_default_symbol } : {}),
          ...(s.backtest_initial_equity ? { initial_equity: Number(s.backtest_initial_equity) } : {}),
        }));
      })
      .catch(() => {});
  }, []);

  const toggleItem = (arr: string[], item: string) =>
    arr.includes(item) ? arr.filter(x => x !== item) : [...arr, item];

  const runBacktest = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API}/api/backtest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cfg),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setResult(data as BacktestResult);
    } catch (e: any) {
      setError(e.message ?? 'Error al ejecutar backtest');
    } finally {
      setLoading(false);
    }
  }, [cfg]);

  const m = result?.metrics;
  const finalEq = result?.equity_curve.at(-1)?.equity ?? cfg.initial_equity;
  const netPnlPos = (m?.net_pnl ?? 0) >= 0;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-border-primary/60 shrink-0">
        <FlaskConical className="h-4 w-4 text-neon-purple" />
        <span className="text-[12px] font-mono font-bold text-text-secondary tracking-wider">BACKTESTING</span>
        {result && (
          <span className="text-[9px] font-mono text-text-muted ml-2">
            Último: {new Date(result.run_at).toLocaleTimeString('es')}
          </span>
        )}
      </div>

      <div className="flex-1 min-h-0 flex gap-3 p-3 overflow-hidden">
        {/* ── Config panel ── */}
        <div className="w-56 shrink-0 flex flex-col gap-2 overflow-y-auto">
          <div className="border border-border-primary/50 rounded-lg bg-bg-secondary/40 p-3 flex flex-col gap-2.5">
            {/* Symbol */}
            <div>
              <label className="text-[8px] font-mono text-text-muted tracking-wider block mb-1">SÍMBOLO</label>
              <select
                value={cfg.symbol}
                onChange={e => setCfg(c => ({ ...c, symbol: e.target.value }))}
                className="w-full bg-bg-tertiary/60 border border-border-primary/40 rounded px-2 py-1 text-[10px] font-mono text-text-primary"
              >
                {['XAUUSD', 'EURUSD', 'BTCUSD', 'US500'].map(s => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>

            {/* Date range */}
            <div>
              <label className="text-[8px] font-mono text-text-muted tracking-wider block mb-1">DESDE</label>
              <input
                type="date" value={cfg.date_from}
                onChange={e => setCfg(c => ({ ...c, date_from: e.target.value }))}
                className="w-full bg-bg-tertiary/60 border border-border-primary/40 rounded px-2 py-1 text-[10px] font-mono text-text-primary"
              />
            </div>
            <div>
              <label className="text-[8px] font-mono text-text-muted tracking-wider block mb-1">HASTA</label>
              <input
                type="date" value={cfg.date_to}
                onChange={e => setCfg(c => ({ ...c, date_to: e.target.value }))}
                className="w-full bg-bg-tertiary/60 border border-border-primary/40 rounded px-2 py-1 text-[10px] font-mono text-text-primary"
              />
            </div>

            {/* Min conviction */}
            <div>
              <label className="text-[8px] font-mono text-text-muted tracking-wider block mb-1">
                CONVICCIÓN MÍNIMA <span className="text-neon-purple">{cfg.min_conviction.toFixed(2)}</span>
              </label>
              <input
                type="range" min="0" max="0.9" step="0.05"
                value={cfg.min_conviction}
                onChange={e => setCfg(c => ({ ...c, min_conviction: parseFloat(e.target.value) }))}
                className="w-full accent-neon-purple"
              />
              <div className="flex justify-between text-[8px] font-mono text-text-muted mt-0.5">
                <span>0.00</span><span>0.45</span><span>0.90</span>
              </div>
            </div>

            {/* Initial equity */}
            <div>
              <label className="text-[8px] font-mono text-text-muted tracking-wider block mb-1">EQUITY INICIAL $</label>
              <input
                type="number" value={cfg.initial_equity} min="100" step="500"
                onChange={e => setCfg(c => ({ ...c, initial_equity: parseFloat(e.target.value) || 5000 }))}
                className="w-full bg-bg-tertiary/60 border border-border-primary/40 rounded px-2 py-1 text-[10px] font-mono text-text-primary"
              />
            </div>

            {/* Sessions */}
            <div>
              <label className="text-[8px] font-mono text-text-muted tracking-wider block mb-1">SESIONES (vacío = todas)</label>
              <div className="flex flex-wrap gap-1">
                {SESSIONS.map(s => (
                  <button
                    key={s}
                    onClick={() => setCfg(c => ({ ...c, sessions: toggleItem(c.sessions, s) }))}
                    className={cn(
                      'px-1.5 py-0.5 rounded text-[8px] font-mono font-bold border transition-all',
                      cfg.sessions.includes(s)
                        ? 'bg-neon-purple/20 text-neon-purple border-neon-purple/40'
                        : 'text-text-muted border-border-primary/30 hover:text-text-secondary'
                    )}
                  >
                    {s.slice(0, 3)}
                  </button>
                ))}
              </div>
            </div>

            {/* Directions */}
            <div>
              <label className="text-[8px] font-mono text-text-muted tracking-wider block mb-1">DIRECCIÓN</label>
              <div className="flex gap-1">
                {DIRECTIONS.map(d => (
                  <button
                    key={d}
                    onClick={() => setCfg(c => ({ ...c, directions: toggleItem(c.directions, d) }))}
                    className={cn(
                      'flex-1 py-0.5 rounded text-[8px] font-mono font-bold border transition-all',
                      cfg.directions.includes(d)
                        ? d === 'BUY'
                          ? 'bg-neon-green/20 text-neon-green border-neon-green/40'
                          : 'bg-neon-red/20 text-neon-red border-neon-red/40'
                        : 'text-text-muted border-border-primary/30 hover:text-text-secondary'
                    )}
                  >
                    {d}
                  </button>
                ))}
              </div>
            </div>

            {/* Strategies */}
            {strategies.length > 0 && (
              <div>
                <label className="text-[8px] font-mono text-text-muted tracking-wider block mb-1">ESTRATEGIAS</label>
                <div className="flex flex-wrap gap-1">
                  {strategies.map(s => (
                    <button
                      key={s}
                      onClick={() => setCfg(c => ({ ...c, strategies: toggleItem(c.strategies, s) }))}
                      className={cn(
                        'px-1.5 py-0.5 rounded text-[8px] font-mono border transition-all truncate max-w-full',
                        cfg.strategies.includes(s)
                          ? 'bg-neon-purple/20 text-neon-purple border-neon-purple/40'
                          : 'text-text-muted border-border-primary/30 hover:text-text-secondary'
                      )}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Run button */}
            <button
              onClick={runBacktest}
              disabled={loading}
              className={cn(
                'flex items-center justify-center gap-2 w-full py-2 rounded-lg border font-mono font-bold text-[11px] transition-all mt-1',
                loading
                  ? 'bg-neon-purple/10 text-neon-purple/60 border-neon-purple/20 cursor-not-allowed'
                  : 'bg-neon-purple/15 text-neon-purple border-neon-purple/30 hover:bg-neon-purple/25'
              )}
            >
              {loading
                ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />Ejecutando…</>
                : <><Play className="h-3.5 w-3.5" />Ejecutar Backtest</>
              }
            </button>

            {error && (
              <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-neon-red/10 border border-neon-red/20 text-[9px] font-mono text-neon-red">
                <AlertTriangle className="h-3 w-3 shrink-0" />{error}
              </div>
            )}
          </div>
        </div>

        {/* ── Results panel ── */}
        <div className="flex-1 min-w-0 flex flex-col gap-2 overflow-y-auto">
          {!result ? (
            <div className="flex-1 flex flex-col items-center justify-center gap-3 text-text-muted">
              <FlaskConical className="h-12 w-12 opacity-15" />
              <span className="text-[11px] font-mono">Configura los parámetros y ejecuta el backtest</span>
              <span className="text-[9px] font-mono opacity-60">Filtrará trades históricos reales del sistema</span>
            </div>
          ) : (
            <>
              {/* Filter summary */}
              <div className="flex items-center gap-3 text-[9px] font-mono text-text-muted bg-bg-tertiary/20 rounded px-3 py-1.5">
                <span>{result.raw_trades_fetched} trades totales</span>
                <span className="text-border-primary">→</span>
                <span className="text-neon-purple font-bold">{result.trades_after_filter} tras filtros</span>
                {result.raw_trades_fetched > 0 && (
                  <span className="ml-auto opacity-60">
                    {((result.trades_after_filter / result.raw_trades_fetched) * 100).toFixed(0)}% seleccionados
                  </span>
                )}
              </div>

              {/* Metric cards */}
              {m && (
                <div className="grid grid-cols-3 xl:grid-cols-6 gap-2">
                  <MetricCard
                    label="TRADES"
                    value={String(m.total_trades)}
                    sub={`${m.wins}W / ${m.losses}L`}
                    positive={null}
                  />
                  <MetricCard
                    label="WIN RATE"
                    value={`${(m.win_rate * 100).toFixed(1)}%`}
                    sub={m.win_rate >= 0.5 ? 'positivo' : 'negativo'}
                    positive={m.win_rate >= 0.5}
                  />
                  <MetricCard
                    label="PROFIT FACTOR"
                    value={m.profit_factor >= 999 ? '∞' : m.profit_factor.toFixed(2)}
                    sub={m.profit_factor >= 1.5 ? 'excelente' : m.profit_factor >= 1.0 ? 'positivo' : 'negativo'}
                    positive={m.profit_factor >= 1.0}
                  />
                  <MetricCard
                    label="NET PnL"
                    value={`${m.net_pnl >= 0 ? '+' : ''}$${m.net_pnl.toFixed(0)}`}
                    sub={`avg $${m.avg_pnl.toFixed(1)}/trade`}
                    positive={m.net_pnl >= 0}
                  />
                  <MetricCard
                    label="MAX DRAWDOWN"
                    value={`$${Math.abs(m.max_drawdown).toFixed(0)}`}
                    sub={`${m.max_dd_pct.toFixed(1)}%`}
                    positive={m.max_dd_pct > -10}
                  />
                  <MetricCard
                    label="SHARPE"
                    value={m.sharpe.toFixed(2)}
                    sub={m.sharpe >= 1 ? 'bueno' : m.sharpe >= 0 ? 'marginal' : 'negativo'}
                    positive={m.sharpe >= 1}
                  />
                </div>
              )}

              {/* Equity curve */}
              {result.equity_curve.length > 1 && (
                <div className="border border-border-primary/40 rounded-lg bg-bg-secondary/30 overflow-hidden">
                  <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border-primary/30">
                    <BarChart2 className="h-3 w-3 text-neon-purple" />
                    <span className="text-[9px] font-mono text-text-muted tracking-wider">EQUITY CURVE</span>
                    <span className={cn('ml-auto text-[10px] font-mono font-bold', netPnlPos ? 'text-neon-green' : 'text-neon-red')}>
                      ${finalEq.toFixed(0)} final
                    </span>
                  </div>
                  <div className="px-2 py-1">
                    <EquityCurve data={result.equity_curve} initial={cfg.initial_equity} />
                  </div>
                </div>
              )}

              {/* Breakdown */}
              <div className="border border-border-primary/40 rounded-lg bg-bg-secondary/30 overflow-hidden">
                <div className="flex items-center border-b border-border-primary/30">
                  {(['session', 'strategy', 'direction', 'hour'] as const).map(tab => (
                    <button
                      key={tab}
                      onClick={() => setBreakdownTab(tab)}
                      className={cn(
                        'px-3 py-1.5 text-[9px] font-mono font-bold transition-all border-r border-border-primary/20 last:border-0',
                        breakdownTab === tab
                          ? 'bg-neon-purple/10 text-neon-purple'
                          : 'text-text-muted hover:text-text-secondary'
                      )}
                    >
                      {tab.toUpperCase()}
                    </button>
                  ))}
                </div>
                <div className="p-3">
                  {breakdownTab === 'session' && (
                    <BreakdownTable data={result.by_session} title="POR SESIÓN" />
                  )}
                  {breakdownTab === 'strategy' && (
                    <BreakdownTable data={result.by_strategy} title="POR ESTRATEGIA" />
                  )}
                  {breakdownTab === 'direction' && (
                    <BreakdownTable data={result.by_direction} title="POR DIRECCIÓN" />
                  )}
                  {breakdownTab === 'hour' && (
                    <div>
                      <div className="text-[9px] font-mono text-text-muted mb-2 tracking-wider">POR HORA UTC</div>
                      <div className="flex gap-0.5 items-end h-16">
                        {Object.entries(result.by_hour).map(([h, data]) => {
                          const maxT = Math.max(...Object.values(result.by_hour).map(d => d.trades));
                          const pct = (data.trades / Math.max(maxT, 1)) * 100;
                          const isPos = data.net_pnl >= 0;
                          return (
                            <div key={h} className="flex flex-col items-center gap-0.5 flex-1" title={`${h}:00 UTC — ${data.trades}t, ${(data.win_rate*100).toFixed(0)}%WR`}>
                              <div
                                className={cn('w-full rounded-t-sm transition-all', isPos ? 'bg-neon-green/50' : 'bg-neon-red/50')}
                                style={{ height: `${Math.max(4, pct * 0.9)}%` }}
                              />
                              <span className="text-[7px] font-mono text-text-muted">{h}</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
