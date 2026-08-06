'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { Activity, AlertTriangle, Cpu, TrendingUp, TrendingDown, Minus, Zap } from 'lucide-react';
import { cn } from '@/lib/utils';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

interface RegimeData {
  regime: string;
  rule_regime: string;
  hmm_regime: string;
  hmm_confidence: number;
  hmm_state_probs: Record<string, number>;
  source: string;
  is_transition: boolean;
  previous_regime: string;
  volatility_regime: string;
  updated_at: string;
}

interface TransitionEvent {
  from: string;
  to: string;
  source: string;
  at: string;
}

const REGIME_CONFIG: Record<string, { label: string; color: string; bg: string; icon: React.ElementType; desc: string }> = {
  TRENDING_BULL:  { label: 'TRENDING ▲', color: 'text-neon-green',  bg: 'bg-neon-green/10  border-neon-green/30',  icon: TrendingUp,   desc: 'ML Momentum activo' },
  TRENDING_BEAR:  { label: 'TRENDING ▼', color: 'text-neon-red',    bg: 'bg-neon-red/10   border-neon-red/30',    icon: TrendingDown, desc: 'ML Momentum activo' },
  TRENDING:       { label: 'TRENDING',   color: 'text-neon-green',  bg: 'bg-neon-green/10  border-neon-green/30',  icon: TrendingUp,   desc: 'ML Trend activo' },
  RANGING:        { label: 'RANGING',    color: 'text-neon-gold',   bg: 'bg-yellow-500/10  border-yellow-500/30',  icon: Minus,        desc: 'Mean Reversion activo' },
  BREAKOUT:       { label: 'BREAKOUT',  color: 'text-neon-purple', bg: 'bg-neon-purple/10 border-neon-purple/30', icon: Zap,          desc: 'Breakout Engine activo' },
  HIGH_VOL:       { label: 'HIGH VOL',  color: 'text-orange-400',  bg: 'bg-orange-500/10  border-orange-500/30',  icon: AlertTriangle,desc: 'Umbral elevado' },
  UNKNOWN:        { label: 'UNKNOWN',   color: 'text-text-muted',  bg: 'bg-bg-tertiary    border-border-primary', icon: Activity,     desc: 'Sin datos' },
};

const VOL_COLOR: Record<string, string> = {
  LOW: 'text-neon-green', MEDIUM: 'text-neon-cyan', HIGH: 'text-yellow-400', EXTREME: 'text-neon-red',
};

export default function RegimeLive({ compact = false }: { compact?: boolean }) {
  const [data, setData] = useState<RegimeData | null>(null);
  const [history, setHistory] = useState<TransitionEvent[]>([]);
  const [elapsed, setElapsed] = useState(0);

  const fetchData = useCallback(async () => {
    try {
      const [regRes, histRes] = await Promise.all([
        fetch(`${API}/api/regime/current`),
        fetch(`${API}/api/regime/history?limit=8`),
      ]);
      if (regRes.ok) setData(await regRes.json());
      if (histRes.ok) {
        const d = await histRes.json();
        setHistory(d.transitions || []);
      }
    } catch {}
  }, []);

  useEffect(() => { fetchData(); const t = setInterval(fetchData, 3000); return () => clearInterval(t); }, [fetchData]);

  // Elapsed seconds since last update.
  useEffect(() => {
    if (!data?.updated_at) return;
    const update = () => setElapsed(Math.floor((Date.now() - new Date(data.updated_at).getTime()) / 1000));
    update();
    const t = setInterval(update, 1000);
    return () => clearInterval(t);
  }, [data?.updated_at]);

  const regime = data?.regime || 'UNKNOWN';
  const cfg = REGIME_CONFIG[regime] || REGIME_CONFIG['UNKNOWN'];
  const Icon = cfg.icon;

  if (compact) {
    return (
      <div className={cn('flex items-center gap-2 px-2.5 py-1.5 rounded border text-[10px] font-mono', cfg.bg)}>
        <Icon className={cn('h-3 w-3', cfg.color)} />
        <span className={cn('font-bold', cfg.color)}>{cfg.label}</span>
        {data?.is_transition && <span className="text-yellow-400 animate-pulse">TRANS</span>}
        <span className="text-text-muted ml-auto">{data?.hmm_confidence ? `${(data.hmm_confidence * 100).toFixed(0)}%` : '--'}</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 h-full">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border-primary/50">
        <Cpu className="h-3.5 w-3.5 text-neon-purple" />
        <span className="text-[11px] font-mono font-bold text-text-secondary tracking-wider">REGIME MONITOR</span>
        <span className="ml-auto text-[10px] font-mono text-text-muted">{elapsed}s ago</span>
      </div>

      {/* Current regime */}
      <div className={cn('mx-3 rounded-lg border p-4 flex items-center gap-4', cfg.bg, data?.is_transition && 'animate-pulse')}>
        <Icon className={cn('h-8 w-8', cfg.color)} />
        <div className="flex-1 min-w-0">
          <div className={cn('text-lg font-mono font-black tracking-widest', cfg.color)}>{cfg.label}</div>
          <div className="text-[11px] text-text-muted font-mono">{cfg.desc}</div>
        </div>
        {data?.is_transition && (
          <div className="flex flex-col items-end gap-0.5">
            <span className="text-yellow-400 text-[9px] font-mono font-bold animate-pulse">TRANSICIÓN</span>
            <span className="text-text-muted text-[9px] font-mono">← {data.previous_regime}</span>
          </div>
        )}
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-2 px-3">
        <Stat label="HMM conf" value={data ? `${(data.hmm_confidence * 100).toFixed(0)}%` : '--'} color={data && data.hmm_confidence >= 0.65 ? 'text-neon-green' : 'text-yellow-400'} />
        <Stat label="Fuente" value={data?.source === 'hmm' ? 'HMM' : 'Rules'} color="text-neon-cyan" />
        <Stat label="Vol" value={data?.volatility_regime || '--'} color={VOL_COLOR[data?.volatility_regime || ''] || 'text-text-muted'} />
      </div>

      {/* HMM state probabilities */}
      {data?.hmm_state_probs && Object.keys(data.hmm_state_probs).length > 0 && (
        <div className="px-3">
          <div className="text-[9px] font-mono text-text-muted mb-1.5 tracking-wider">PROBABILIDADES HMM</div>
          <div className="flex flex-col gap-1">
            {Object.entries(data.hmm_state_probs)
              .sort((a, b) => b[1] - a[1])
              .map(([state, prob]) => (
                <div key={state} className="flex items-center gap-2">
                  <span className="text-[9px] font-mono text-text-muted w-28 truncate">{state}</span>
                  <div className="flex-1 h-1 bg-bg-tertiary rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full bg-neon-purple/60 transition-all duration-500"
                      style={{ width: `${(prob * 100).toFixed(0)}%` }}
                    />
                  </div>
                  <span className="text-[9px] font-mono text-text-secondary w-8 text-right">{(prob * 100).toFixed(0)}%</span>
                </div>
              ))}
          </div>
        </div>
      )}

      {/* Transition history */}
      {history.length > 0 && (
        <div className="px-3 flex-1 min-h-0 overflow-hidden">
          <div className="text-[9px] font-mono text-text-muted mb-1.5 tracking-wider">ÚLTIMAS TRANSICIONES</div>
          <div className="flex flex-col gap-1 overflow-y-auto max-h-[140px]">
            {history.map((t, i) => (
              <div key={i} className="flex items-center gap-2 text-[9px] font-mono">
                <span className="text-text-muted w-16 shrink-0">{new Date(t.at).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' })}</span>
                <span className="text-neon-red/70 truncate max-w-[70px]">{t.from}</span>
                <span className="text-text-muted">→</span>
                <span className="text-neon-green/70 truncate max-w-[70px]">{t.to}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="flex flex-col gap-0.5 bg-bg-tertiary/40 rounded px-2 py-1.5">
      <span className="text-[8px] font-mono text-text-muted tracking-wider uppercase">{label}</span>
      <span className={cn('text-[11px] font-mono font-bold', color)}>{value}</span>
    </div>
  );
}
