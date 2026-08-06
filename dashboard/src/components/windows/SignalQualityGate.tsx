'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { ShieldCheck, ShieldX, CheckCircle2, XCircle, AlertCircle } from 'lucide-react';
import { cn } from '@/lib/utils';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

interface FilterItem {
  ok: boolean;
  pts: number;
  detail: string;
}

interface QualityData {
  score: number;
  max_score: number;
  threshold: number;
  passes: boolean;
  direction: string;
  regime: string;
  session: string;
  computed_at: string;
  breakdown: Record<string, FilterItem>;
}

const FILTER_LABELS: Record<string, string> = {
  spread:         'Spread',
  htf_confluence: 'HTF Confluencia',
  news_safe:      'Sin Noticias',
  entry_timing:   'Timing entrada',
  regime_wr:      'WR Régimen',
  hour_quality:   'Hora WR',
  hmm_confidence: 'Confianza HMM',
  macro_bias:     'Bias Macro (GenAI)',
  cross_asset:    'Cross-Asset (DXY/US10Y)',
  tick_imbalance: 'Imbalance Ticks',
};

const FILTER_MAX: Record<string, number> = {
  spread: 15, htf_confluence: 20, news_safe: 15, entry_timing: 15,
  regime_wr: 10, hour_quality: 10, hmm_confidence: 10, macro_bias: 5,
  cross_asset: 5, tick_imbalance: 5,
};

export default function SignalQualityGate() {
  const [data, setData] = useState<QualityData | null>(null);
  const [age, setAge] = useState(0);

  const fetch_ = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/intelligence/quality`);
      if (res.ok) setData(await res.json());
    } catch {}
  }, []);

  useEffect(() => { fetch_(); const t = setInterval(fetch_, 3000); return () => clearInterval(t); }, [fetch_]);

  useEffect(() => {
    if (!data?.computed_at) return;
    const u = () => setAge(Math.floor((Date.now() - new Date(data.computed_at).getTime()) / 1000));
    u();
    const t = setInterval(u, 1000);
    return () => clearInterval(t);
  }, [data?.computed_at]);

  const score = data?.score ?? 0;
  const threshold = data?.threshold ?? 60;
  const passes = data?.passes ?? false;
  const pct = Math.min(100, (score / 100) * 100);
  const thresholdPct = (threshold / 100) * 100;

  const scoreColor = passes
    ? 'text-neon-green'
    : score >= threshold * 0.85
    ? 'text-yellow-400'
    : 'text-neon-red';

  const barColor = passes ? 'bg-neon-green' : score >= threshold * 0.85 ? 'bg-yellow-500' : 'bg-neon-red';

  return (
    <div className="flex flex-col h-full gap-0">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border-primary/50">
        {passes
          ? <ShieldCheck className="h-3.5 w-3.5 text-neon-green" />
          : <ShieldX className="h-3.5 w-3.5 text-neon-red" />
        }
        <span className="text-[11px] font-mono font-bold text-text-secondary tracking-wider">SIGNAL QUALITY</span>
        <span className="ml-auto text-[9px] font-mono text-text-muted">{age}s ago</span>
      </div>

      {/* Score ring */}
      <div className="flex items-center gap-4 px-3 py-3">
        <div className="relative flex items-center justify-center w-16 h-16 shrink-0">
          <svg className="absolute inset-0 w-full h-full -rotate-90" viewBox="0 0 64 64">
            <circle cx="32" cy="32" r="26" fill="none" stroke="rgba(255,255,255,0.05)" strokeWidth="8" />
            {/* threshold marker */}
            <circle cx="32" cy="32" r="26" fill="none" stroke="rgba(255,255,255,0.15)" strokeWidth="2"
              strokeDasharray={`${(thresholdPct / 100) * 163.4} 163.4`} strokeLinecap="round" />
            <circle cx="32" cy="32" r="26" fill="none"
              stroke={passes ? '#22c55e' : score >= threshold * 0.85 ? '#eab308' : '#ef4444'}
              strokeWidth="8"
              strokeDasharray={`${(pct / 100) * 163.4} 163.4`}
              strokeLinecap="round"
              className="transition-all duration-700"
            />
          </svg>
          <div className="text-center z-10">
            <div className={cn('text-lg font-mono font-black leading-none', scoreColor)}>{score}</div>
            <div className="text-[8px] font-mono text-text-muted">/{data?.max_score ?? 100}</div>
          </div>
        </div>

        <div className="flex flex-col gap-1.5 flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className={cn('text-[13px] font-mono font-black', scoreColor)}>
              {passes ? '✓ PASA' : '✗ BLOQUEADA'}
            </span>
          </div>
          <div className="text-[9px] font-mono text-text-muted">Umbral dinámico: {threshold}/100</div>
          {data?.direction && (
            <div className="text-[9px] font-mono">
              <span className="text-text-muted">Dir: </span>
              <span className={data.direction === 'BUY' ? 'text-neon-green font-bold' : 'text-neon-red font-bold'}>{data.direction}</span>
              <span className="text-text-muted ml-2">· {data.regime}</span>
            </div>
          )}
          {data?.session && (
            <div className="text-[9px] font-mono text-text-muted">Sesión: {data.session}</div>
          )}
        </div>
      </div>

      {/* Filter checklist */}
      <div className="flex-1 overflow-y-auto px-3 pb-2">
        <div className="text-[9px] font-mono text-text-muted mb-1.5 tracking-wider">FILTROS</div>
        <div className="flex flex-col gap-1">
          {Object.entries(FILTER_LABELS).map(([key, label]) => {
            const item = data?.breakdown[key];
            const maxPts = FILTER_MAX[key] || 10;
            const pts = item?.pts ?? 0;
            const ok = item?.ok ?? false;
            const detail = item?.detail ?? 'pendiente';

            return (
              <div key={key} className={cn(
                'flex items-center gap-2 px-2 py-1 rounded text-[9px] font-mono',
                ok ? 'bg-neon-green/5' : item ? 'bg-neon-red/5' : 'bg-bg-tertiary/30'
              )}>
                {ok
                  ? <CheckCircle2 className="h-3 w-3 text-neon-green shrink-0" />
                  : item
                  ? <XCircle className="h-3 w-3 text-neon-red shrink-0" />
                  : <AlertCircle className="h-3 w-3 text-text-muted shrink-0" />
                }
                <span className="text-text-secondary flex-1 truncate">{label}</span>
                <span className={ok ? 'text-neon-green' : 'text-text-muted'}>
                  {pts}/{maxPts}
                </span>
              </div>
            );
          })}
        </div>

        {data?.breakdown && Object.keys(data.breakdown).length > 0 && (
          <div className="mt-2">
            <div className="text-[9px] font-mono text-text-muted mb-1 tracking-wider">DETALLE FILTROS</div>
            {Object.entries(data.breakdown)
              .filter(([, v]) => !v.ok)
              .map(([key, v]) => (
                <div key={key} className="text-[9px] font-mono text-neon-red/70 mb-0.5">
                  ✗ {FILTER_LABELS[key] || key}: {v.detail}
                </div>
              ))}
          </div>
        )}
      </div>
    </div>
  );
}
