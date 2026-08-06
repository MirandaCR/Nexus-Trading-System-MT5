'use client';

import { useEffect, useState, useMemo, useCallback } from 'react';
import {
  Eye,
  RefreshCw,
  ArrowUp,
  ArrowDown,
  Minus,
} from 'lucide-react';
import NeonCard from '../shared/NeonCard';
import { useDashboardStore } from '@/stores/dashboardStore';
import { cn } from '@/lib/utils';

const POLL_MS = 12_000;
const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

// ── Types ───────────────────────────────────────────────────────────────

type HMMState = 'TRENDING_UP' | 'TRENDING_DOWN' | 'RANGING' | 'HIGH_VOLATILITY';
type Bias = 'LONG' | 'SHORT' | 'NEUTRAL';

interface RegimeAnalysis {
  regime: string;
  regime_strength: number;
  suggested_approach: string;
  vol_regime: string;
  adx: number;
  bb_width: number;
  hmm_regime?: string;
  hmm_confidence?: number;
  hmm_state_probs?: Record<string, number>;
  hmm_transition_matrix?: Record<string, Record<string, number>>;
  hmm_suggested_bias?: string;
}

interface ModeData {
  current_mode: string;
  session: string;
  config: {
    operational_tf: string;
    sl_range: string;
    tp_range: string;
    max_daily_trades: number;
    retrain_interval: string;
  };
}

// ── State layout & colors ───────────────────────────────────────────────

const HMM_STATES: HMMState[] = [
  'TRENDING_UP',
  'TRENDING_DOWN',
  'RANGING',
  'HIGH_VOLATILITY',
];

interface StateStyle {
  label: string;
  short: string;
  color: string;
  glow: string;
  dim: string;
  x: number;
  y: number;
}

const S: Record<HMMState, StateStyle> = {
  TRENDING_UP: {
    label: 'Trending Up',
    short: 'UP',
    color: '#22c55e',
    glow: 'rgba(34,197,94,0.35)',
    dim: 'rgba(34,197,94,0.12)',
    x: 160,
    y: 34,
  },
  RANGING: {
    label: 'Ranging',
    short: 'RNG',
    color: '#06b6d4',
    glow: 'rgba(6,182,212,0.35)',
    dim: 'rgba(6,182,212,0.12)',
    x: 42,
    y: 118,
  },
  HIGH_VOLATILITY: {
    label: 'High Vol',
    short: 'VOL',
    color: '#f97316',
    glow: 'rgba(249,115,22,0.35)',
    dim: 'rgba(249,115,22,0.12)',
    x: 278,
    y: 118,
  },
  TRENDING_DOWN: {
    label: 'Trending Down',
    short: 'DN',
    color: '#ef4444',
    glow: 'rgba(239,68,68,0.35)',
    dim: 'rgba(239,68,68,0.12)',
    x: 160,
    y: 202,
  },
};

const R = 26; // node radius
const MIN_PROB = 0.05;

// ── Arrow geometry ──────────────────────────────────────────────────────

function arrowGeom(from: HMMState, to: HMMState) {
  const f = S[from];
  const t = S[to];
  const dx = t.x - f.x;
  const dy = t.y - f.y;
  const dist = Math.hypot(dx, dy);
  if (dist === 0) return null;

  const ux = dx / dist;
  const uy = dy / dist;
  const nx = -uy;
  const ny = ux;

  // Opposing arrows curve in opposite directions
  const sign = from < to ? 1 : -1;
  const curve = Math.min(dist * 0.14, 24);
  const edgeOff = 3;

  const sx = f.x + ux * (R + 3) + nx * sign * edgeOff;
  const sy = f.y + uy * (R + 3) + ny * sign * edgeOff;
  const ex = t.x - ux * (R + 7) + nx * sign * edgeOff;
  const ey = t.y - uy * (R + 7) + ny * sign * edgeOff;

  const cx = (f.x + t.x) / 2 + nx * sign * curve;
  const cy = (f.y + t.y) / 2 + ny * sign * curve;

  const lx = cx + nx * sign * 10;
  const ly = cy + ny * sign * 10;

  return {
    d: `M${sx},${sy} Q${cx},${cy} ${ex},${ey}`,
    lx,
    ly,
  };
}

// ── Transition graph SVG ────────────────────────────────────────────────

function TransitionGraph({
  active,
  matrix,
}: {
  active: HMMState;
  matrix: Record<string, Record<string, number>>;
}) {
  const edges = useMemo(() => {
    const out: {
      from: HMMState;
      to: HMMState;
      prob: number;
      d: string;
      lx: number;
      ly: number;
    }[] = [];

    for (const from of HMM_STATES) {
      for (const to of HMM_STATES) {
        if (from === to) continue;
        const prob = matrix[from]?.[to] ?? 0;
        if (prob < MIN_PROB) continue;
        const g = arrowGeom(from, to);
        if (!g) continue;
        out.push({ from, to, prob, ...g });
      }
    }
    return out;
  }, [matrix]);

  return (
    <svg
      viewBox="0 0 320 236"
      className="w-full select-none"
      style={{ maxHeight: 220 }}
    >
      <defs>
        {HMM_STATES.map((s) => (
          <marker
            key={s}
            id={`ah-${s}`}
            markerWidth="8"
            markerHeight="6"
            refX="7"
            refY="3"
            orient="auto"
            markerUnits="userSpaceOnUse"
          >
            <path
              d="M0,0 L8,3 L0,6 L1.5,3Z"
              fill={S[s].color}
              opacity={0.65}
            />
          </marker>
        ))}

        <filter id="glow-active" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="7" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {/* ── Edges ── */}
      {edges.map(({ from, to, prob, d, lx, ly }) => (
        <g key={`${from}-${to}`}>
          <path
            d={d}
            fill="none"
            stroke={S[from].color}
            strokeWidth={Math.max(0.8, prob * 5)}
            strokeOpacity={0.25 + prob * 0.45}
            markerEnd={`url(#ah-${from})`}
          />
          <text
            x={lx}
            y={ly}
            textAnchor="middle"
            dominantBaseline="middle"
            fill="var(--text-muted)"
            fontSize="7.5"
            fontFamily="monospace"
          >
            {(prob * 100).toFixed(0)}%
          </text>
        </g>
      ))}

      {/* ── Nodes ── */}
      {HMM_STATES.map((state) => {
        const c = S[state];
        const on = state === active;
        const self = matrix[state]?.[state] ?? 0;

        return (
          <g key={state}>
            {on && (
              <circle
                cx={c.x}
                cy={c.y}
                r={R + 6}
                fill={c.glow}
                filter="url(#glow-active)"
              >
                <animate
                  attributeName="opacity"
                  values="0.6;1;0.6"
                  dur="2.4s"
                  repeatCount="indefinite"
                />
              </circle>
            )}

            <circle
              cx={c.x}
              cy={c.y}
              r={R}
              fill={on ? c.dim : 'rgba(17,19,24,0.92)'}
              stroke={c.color}
              strokeWidth={on ? 2 : 1}
              strokeOpacity={on ? 1 : 0.35}
            />

            <text
              x={c.x}
              y={c.y - 5}
              textAnchor="middle"
              dominantBaseline="middle"
              fill={c.color}
              fontSize="9"
              fontWeight="700"
              fontFamily="monospace"
              opacity={on ? 1 : 0.65}
            >
              {c.short}
            </text>

            <text
              x={c.x}
              y={c.y + 9}
              textAnchor="middle"
              dominantBaseline="middle"
              fill={on ? c.color : 'var(--text-muted)'}
              fontSize="7"
              fontFamily="monospace"
              opacity={on ? 0.85 : 0.45}
            >
              {(self * 100).toFixed(0)}%
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// ── State probability bars ──────────────────────────────────────────────

function ProbBars({
  probs,
  active,
}: {
  probs: Record<string, number>;
  active: HMMState;
}) {
  return (
    <div className="flex flex-col gap-1">
      {HMM_STATES.map((st) => {
        const c = S[st];
        const p = probs[st] ?? 0;
        const on = st === active;

        return (
          <div key={st} className="flex items-center gap-2">
            <span
              className={cn(
                'text-[9px] font-mono w-7 text-right shrink-0',
                on ? 'font-bold' : 'opacity-45',
              )}
              style={{ color: c.color }}
            >
              {c.short}
            </span>

            <div className="flex-1 h-[5px] rounded-full bg-bg-tertiary overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-500"
                style={{
                  width: `${p * 100}%`,
                  backgroundColor: c.color,
                  opacity: on ? 1 : 0.45,
                }}
              />
            </div>

            <span
              className={cn(
                'text-[9px] font-mono w-8 text-right shrink-0',
                on ? 'text-text-primary font-bold' : 'text-text-muted',
              )}
            >
              {(p * 100).toFixed(0)}%
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ── Rule-based fallback (no HMM data) ───────────────────────────────────

function RuleBasedFallback({ data, compact = false }: { data: RegimeAnalysis; compact?: boolean }) {
  const r = data.regime || 'UNKNOWN';
  const strength = data.regime_strength ?? 0;

  const palette: Record<string, { t: string; bg: string; b: string }> = {
    TRENDING: {
      t: 'text-neon-cyan',
      bg: 'bg-neon-cyan/10',
      b: 'border-neon-cyan/30',
    },
    RANGING: {
      t: 'text-neon-gold',
      bg: 'bg-neon-gold/10',
      b: 'border-neon-gold/30',
    },
    BREAKOUT: {
      t: 'text-neon-purple',
      bg: 'bg-neon-purple/10',
      b: 'border-neon-purple/30',
    },
  };
  const c = palette[r] ?? {
    t: 'text-text-muted',
    bg: 'bg-bg-tertiary',
    b: 'border-border-primary/30',
  };

  const barColor =
    r === 'TRENDING'
      ? '#06b6d4'
      : r === 'RANGING'
        ? '#eab308'
        : '#a855f7';

  return (
    <div className={`flex flex-col ${compact ? 'gap-1.5' : 'gap-2.5'}`}>
      <div className="flex items-center gap-2">
        <span
          className={cn(
            'inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-mono font-bold border',
            c.t,
            c.bg,
            c.b,
          )}
        >
          {r}
        </span>
        <span className="text-[9px] font-mono text-text-muted italic">
          rule-based
        </span>
      </div>

      <div>
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[10px] font-mono text-text-muted uppercase">
            Strength
          </span>
          <span className="text-xs font-mono text-text-primary">
            {strength}%
          </span>
          <span
            className={cn(
              'ml-auto text-[10px] font-mono',
              data.vol_regime === 'HIGH' || data.vol_regime === 'EXTREME'
                ? 'text-neon-orange'
                : data.vol_regime === 'MEDIUM'
                  ? 'text-neon-gold'
                  : 'text-neon-cyan',
            )}
          >
            Vol: {data.vol_regime || '?'}
          </span>
        </div>
        <div className="h-1.5 rounded-full bg-bg-tertiary overflow-hidden">
          <div
            className="h-full rounded-full transition-all"
            style={{ width: `${strength}%`, backgroundColor: barColor }}
          />
        </div>
      </div>

      {!compact && (
        <div className="grid grid-cols-3 gap-1.5">
          {[
            { label: 'ADX', value: data.adx?.toFixed(1) ?? '—' },
            { label: 'BB Width', value: data.bb_width?.toFixed(4) ?? '—' },
            {
              label: 'Approach',
              value: data.suggested_approach?.replace(/_/g, ' ') ?? '—',
              small: true,
            },
          ].map((m) => (
            <div
              key={m.label}
              className="rounded bg-bg-secondary/40 border border-border-primary/15 p-1.5"
            >
              <p className="text-[9px] font-mono text-text-muted uppercase">
                {m.label}
              </p>
              <p
                className={cn(
                  'font-bold font-mono text-text-primary truncate',
                  m.small ? 'text-[10px]' : 'text-xs',
                )}
              >
                {m.value}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────────

const DEFAULT_MATRIX: Record<string, Record<string, number>> = {
  TRENDING_UP: {
    TRENDING_UP: 0.85,
    TRENDING_DOWN: 0.02,
    RANGING: 0.1,
    HIGH_VOLATILITY: 0.03,
  },
  TRENDING_DOWN: {
    TRENDING_UP: 0.03,
    TRENDING_DOWN: 0.82,
    RANGING: 0.12,
    HIGH_VOLATILITY: 0.03,
  },
  RANGING: {
    TRENDING_UP: 0.12,
    TRENDING_DOWN: 0.1,
    RANGING: 0.73,
    HIGH_VOLATILITY: 0.05,
  },
  HIGH_VOLATILITY: {
    TRENDING_UP: 0.05,
    TRENDING_DOWN: 0.08,
    RANGING: 0.07,
    HIGH_VOLATILITY: 0.8,
  },
};

const DEFAULT_PROBS: Record<string, number> = {
  TRENDING_UP: 0.25,
  TRENDING_DOWN: 0.25,
  RANGING: 0.25,
  HIGH_VOLATILITY: 0.25,
};

export default function RegimeIndicator({ compact = false, className = '' }: { compact?: boolean; className?: string }) {
  const symbol = useDashboardStore((s) => s.symbol);
  const [regime, setRegime] = useState<RegimeAnalysis | null>(null);
  const [mode, setMode] = useState<ModeData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [decRes, modeRes] = await Promise.all([
        fetch(`${API_BASE}/api/agents/decision/${symbol}`).catch(() => null),
        fetch(`${API_BASE}/api/ml/traders/mode`).catch(() => null),
      ]);

      if (decRes?.ok) {
        const data = await decRes.json();
        if (data?.regime_analysis) {
          setRegime(data.regime_analysis);
          setError(false);
        }
      }

      if (modeRes?.ok) setMode(await modeRes.json());
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, POLL_MS);
    return () => clearInterval(id);
  }, [fetchAll]);

  const hasHMM = Boolean(
    regime?.hmm_regime &&
      regime?.hmm_transition_matrix &&
      regime?.hmm_state_probs,
  );

  const active = ((regime?.hmm_regime as HMMState) ?? 'TRENDING_UP') as HMMState;
  const confidence = regime?.hmm_confidence ?? 0;
  const bias = ((regime?.hmm_suggested_bias as Bias) ?? 'NEUTRAL') as Bias;
  const matrix = regime?.hmm_transition_matrix ?? DEFAULT_MATRIX;
  const probs = regime?.hmm_state_probs ?? DEFAULT_PROBS;

  return (
    <NeonCard
      title="Market Regime"
      icon={<Eye className="h-4 w-4" />}
      glow="purple"
      className={`${compact ? '' : 'h-full'} flex flex-col min-h-0 ${className}`}
      isLive={!error && !loading}
      accentColor={hasHMM ? S[active].color : undefined}
      headerRight={
        <button
          onClick={fetchAll}
          disabled={loading}
          className="text-text-muted hover:text-text-secondary transition-colors"
        >
          <RefreshCw className={cn('h-3 w-3', loading && 'animate-spin')} />
        </button>
      }
    >
      <div className={`flex flex-col ${compact ? 'gap-1.5' : 'gap-3'} ${compact ? '' : 'flex-1'}`}>
        {!compact && hasHMM ? (
          <>
            {/* ── HMM transition graph ── */}
            <TransitionGraph active={active} matrix={matrix} />

            {/* ── Active state / bias / volatility ── */}
            <div className="flex items-center gap-2 flex-wrap">
              <span
                className="inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-[10px] font-mono font-bold border"
                style={{
                  color: S[active].color,
                  backgroundColor: S[active].dim,
                  borderColor: `${S[active].color}40`,
                }}
              >
                {S[active].label}
              </span>

              <span className="flex items-center gap-0.5">
                {bias === 'LONG' ? (
                  <ArrowUp className="h-3 w-3 text-neon-green" />
                ) : bias === 'SHORT' ? (
                  <ArrowDown className="h-3 w-3 text-neon-red" />
                ) : (
                  <Minus className="h-3 w-3 text-text-muted" />
                )}
                <span
                  className={cn(
                    'text-[10px] font-mono font-bold',
                    bias === 'LONG'
                      ? 'text-neon-green'
                      : bias === 'SHORT'
                        ? 'text-neon-red'
                        : 'text-text-muted',
                  )}
                >
                  {bias}
                </span>
              </span>

              <div className="flex-1" />

              {regime?.vol_regime && (
                <span
                  className={cn(
                    'text-[9px] font-mono px-1.5 py-0.5 rounded border',
                    regime.vol_regime === 'HIGH' ||
                      regime.vol_regime === 'EXTREME'
                      ? 'text-neon-orange bg-neon-orange/10 border-neon-orange/30'
                      : regime.vol_regime === 'MEDIUM'
                        ? 'text-neon-gold bg-neon-gold/10 border-neon-gold/30'
                        : 'text-neon-cyan bg-neon-cyan/10 border-neon-cyan/30',
                  )}
                >
                  VOL: {regime.vol_regime}
                </span>
              )}
            </div>

            {/* ── Confidence bar ── */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[9px] font-mono text-text-muted uppercase">
                  HMM Confidence
                </span>
                <span className="text-[10px] font-mono text-text-primary font-bold">
                  {(confidence * 100).toFixed(0)}%
                </span>
              </div>
              <div className="h-1 rounded-full bg-bg-tertiary overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-500"
                  style={{
                    width: `${confidence * 100}%`,
                    backgroundColor: S[active].color,
                  }}
                />
              </div>
            </div>

            {/* ── State probabilities ── */}
            <div>
              <p className="text-[9px] font-mono text-text-muted uppercase mb-1.5">
                State Probabilities
              </p>
              <ProbBars probs={probs} active={active} />
            </div>

            {/* ── Key metrics ── */}
            <div className="grid grid-cols-3 gap-1.5">
              <div className="rounded bg-bg-secondary/40 border border-border-primary/15 p-1.5">
                <p className="text-[9px] font-mono text-text-muted uppercase">
                  ADX
                </p>
                <p className="text-xs font-bold font-mono text-text-primary">
                  {regime?.adx?.toFixed(1) ?? '—'}
                </p>
              </div>
              <div className="rounded bg-bg-secondary/40 border border-border-primary/15 p-1.5">
                <p className="text-[9px] font-mono text-text-muted uppercase">
                  BB Width
                </p>
                <p className="text-xs font-bold font-mono text-text-primary">
                  {regime?.bb_width?.toFixed(4) ?? '—'}
                </p>
              </div>
              <div className="rounded bg-bg-secondary/40 border border-border-primary/15 p-1.5">
                <p className="text-[9px] font-mono text-text-muted uppercase">
                  Strength
                </p>
                <p className="text-xs font-bold font-mono text-text-primary">
                  {regime?.regime_strength ?? '—'}%
                </p>
              </div>
            </div>

            {/* ── Mode footer ── */}
            {mode && (
              <div className="flex items-center gap-3 text-[9px] font-mono text-text-muted pt-1.5 border-t border-border-primary/20">
                <span>
                  Mode:{' '}
                  <span
                    className={cn(
                      'font-bold',
                      mode.current_mode === 'scalping'
                        ? 'text-neon-green'
                        : 'text-neon-purple',
                    )}
                  >
                    {mode.current_mode.toUpperCase()}
                  </span>
                </span>
                <span>
                  TF:{' '}
                  <span className="text-text-secondary">
                    {mode.config.operational_tf}
                  </span>
                </span>
                <span>
                  Session:{' '}
                  <span className="text-text-secondary">{mode.session}</span>
                </span>
              </div>
            )}
          </>
        ) : regime ? (
          <RuleBasedFallback data={regime} compact={compact} />
        ) : (
          <div className={`flex items-center justify-center ${compact ? 'py-4' : 'flex-1 min-h-[160px]'}`}>
            <div className="text-center">
              {loading ? (
                <>
                  <RefreshCw className="h-6 w-6 text-text-muted animate-spin mx-auto mb-2" />
                  <p className="text-[10px] font-mono text-text-muted">
                    Loading regime data…
                  </p>
                </>
              ) : error ? (
                <>
                  <Eye className="h-6 w-6 text-neon-red/50 mx-auto mb-2" />
                  <p className="text-[10px] font-mono text-text-muted">
                    Unable to fetch regime data
                  </p>
                  <button
                    onClick={fetchAll}
                    className="text-[9px] font-mono text-neon-purple hover:underline mt-1"
                  >
                    Retry
                  </button>
                </>
              ) : (
                <>
                  <Eye className="h-6 w-6 text-text-muted/30 mx-auto mb-2" />
                  <p className="text-[10px] font-mono text-text-muted">
                    Waiting for regime analysis…
                  </p>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </NeonCard>
  );
}
