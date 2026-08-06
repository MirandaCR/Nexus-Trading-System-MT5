'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { Globe, TrendingUp, TrendingDown, Minus, AlertTriangle, Zap } from 'lucide-react';
import { cn } from '@/lib/utils';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

interface AssetData {
  level: number | null;
  change_5d: number | null;
  rsi_14?: number | null;
  trend_score?: number | null;
  source?: string;
}

interface CrossAssetData {
  dxy: AssetData;
  us10y: AssetData;
  gold_yield_regime: string;
  gold_dxy_divergence: number;
  cross_asset_signal_pts: number;
  updated_at: string | null;
  error?: string;
}

const REGIME_CONFIG: Record<string, { label: string; color: string; bg: string; desc: string }> = {
  risk_off:   { label: 'RISK OFF',   color: 'text-neon-green',  bg: 'bg-neon-green/10',  desc: 'Yields ↓ DXY ↑ → Oro alcista' },
  risk_on:    { label: 'RISK ON',    color: 'text-neon-red',    bg: 'bg-neon-red/10',    desc: 'Yields ↑ DXY ↑ → Oro bajista' },
  high_rates: { label: 'HIGH RATES', color: 'text-orange-400',  bg: 'bg-orange-400/10',  desc: 'Yields >5% → Viento en contra del oro' },
  neutral:    { label: 'NEUTRAL',    color: 'text-text-muted',  bg: 'bg-bg-tertiary/40', desc: 'Sin señal clara cross-asset' },
};

function TrendIcon({ value, className }: { value: number | null | undefined; className?: string }) {
  if (value == null) return <Minus className={cn('h-3 w-3 text-text-muted', className)} />;
  if (value > 0.05) return <TrendingUp className={cn('h-3 w-3 text-neon-green', className)} />;
  if (value < -0.05) return <TrendingDown className={cn('h-3 w-3 text-neon-red', className)} />;
  return <Minus className={cn('h-3 w-3 text-text-muted', className)} />;
}

function ChangeChip({ value, unit = '' }: { value: number | null | undefined; unit?: string }) {
  if (value == null) return <span className="text-text-muted">—</span>;
  const pos = value >= 0;
  return (
    <span className={cn('text-[10px] font-mono font-bold', pos ? 'text-neon-green' : 'text-neon-red')}>
      {pos ? '+' : ''}{value.toFixed(2)}{unit}
    </span>
  );
}

function RsiBar({ value }: { value: number | null | undefined }) {
  if (value == null) return null;
  const pct = Math.min(100, Math.max(0, value));
  const color = value > 70 ? '#ef4444' : value < 30 ? '#22c55e' : '#a855f7';
  return (
    <div className="flex items-center gap-1.5 w-full">
      <div className="text-[9px] font-mono text-text-muted w-6">RSI</div>
      <div className="flex-1 h-1 bg-bg-tertiary rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <div className="text-[9px] font-mono text-text-secondary w-6 text-right">{value.toFixed(0)}</div>
    </div>
  );
}

interface ShiftAlert {
  type: string;
  severity: 'HIGH' | 'MEDIUM';
  description: string;
  implication?: string;
  detected_at: string;
}

export default function CrossAsset() {
  const [data, setData] = useState<CrossAssetData | null>(null);
  const [shifts, setShifts] = useState<ShiftAlert[]>([]);
  const [age, setAge] = useState(0);

  const fetch_ = useCallback(async () => {
    try {
      const [caRes, shiftRes] = await Promise.all([
        fetch(`${API}/api/intelligence/cross-asset`),
        fetch(`${API}/api/intelligence/shift-alerts?limit=3`),
      ]);
      if (caRes.ok) setData(await caRes.json());
      if (shiftRes.ok) {
        const shiftData = await shiftRes.json();
        setShifts(shiftData.alerts || []);
      }
    } catch {}
  }, []);

  useEffect(() => { fetch_(); const t = setInterval(fetch_, 10_000); return () => clearInterval(t); }, [fetch_]);

  useEffect(() => {
    if (!data?.updated_at) return;
    const u = () => setAge(Math.floor((Date.now() - new Date(data.updated_at!).getTime()) / 1000));
    u();
    const t = setInterval(u, 5_000);
    return () => clearInterval(t);
  }, [data?.updated_at]);

  const regime = data?.gold_yield_regime ?? 'neutral';
  const rCfg = REGIME_CONFIG[regime] ?? REGIME_CONFIG.neutral;
  const divg = data?.gold_dxy_divergence ?? 0;
  const pts = data?.cross_asset_signal_pts ?? 3;
  const ptsColor = pts >= 4 ? 'text-neon-green' : pts <= 1 ? 'text-neon-red' : 'text-yellow-400';

  return (
    <div className="flex flex-col h-full gap-0">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border-primary/50">
        <Globe className="h-3.5 w-3.5 text-neon-purple" />
        <span className="text-[11px] font-mono font-bold text-text-secondary tracking-wider">CROSS-ASSET CONTEXT</span>
        <span className="ml-auto text-[9px] font-mono text-text-muted">
          {data?.updated_at ? `${Math.floor(age / 60)}m${age % 60}s` : '—'}
        </span>
      </div>

      {/* Regime badge */}
      <div className={cn('mx-3 mt-2 mb-1 px-3 py-1.5 rounded-md flex items-center gap-2', rCfg.bg)}>
        <span className={cn('text-[11px] font-mono font-black tracking-wider', rCfg.color)}>{rCfg.label}</span>
        <span className="text-[9px] font-mono text-text-muted flex-1">{rCfg.desc}</span>
        <span className={cn('text-[11px] font-mono font-bold ml-auto', ptsColor)}>{pts}pts</span>
      </div>

      {/* DXY + US10Y */}
      <div className="flex flex-col gap-0 px-3 flex-1 overflow-y-auto pb-2">

        {/* DXY */}
        <div className="py-2 border-b border-border-primary/30">
          <div className="flex items-center gap-1.5 mb-1">
            <TrendIcon value={data?.dxy?.trend_score} />
            <span className="text-[11px] font-mono font-bold text-text-secondary">DXY</span>
            <span className="text-[9px] font-mono text-text-muted">Índice Dólar</span>
            <span className="ml-auto text-[13px] font-mono font-black text-text-primary">
              {data?.dxy?.level != null ? data.dxy.level.toFixed(2) : '—'}
            </span>
          </div>
          <div className="flex items-center gap-3 text-[9px] font-mono text-text-muted">
            <span>5d: <ChangeChip value={data?.dxy?.change_5d} unit="%" /></span>
            {data?.dxy?.source && (
              <span className="ml-auto opacity-60">{data.dxy.source}</span>
            )}
          </div>
          <div className="mt-1"><RsiBar value={data?.dxy?.rsi_14} /></div>
        </div>

        {/* US10Y */}
        <div className="py-2 border-b border-border-primary/30">
          <div className="flex items-center gap-1.5 mb-1">
            <TrendIcon value={data?.us10y?.change_5d} />
            <span className="text-[11px] font-mono font-bold text-text-secondary">US10Y</span>
            <span className="text-[9px] font-mono text-text-muted">Treasury 10Y</span>
            <span className="ml-auto text-[13px] font-mono font-black text-text-primary">
              {data?.us10y?.level != null ? data.us10y.level.toFixed(2) : '—'}
              <span className="text-[9px] text-text-muted">%</span>
            </span>
          </div>
          <div className="flex items-center gap-3 text-[9px] font-mono text-text-muted">
            <span>5d: <ChangeChip value={data?.us10y?.change_5d} unit="pp" /></span>
            {data?.us10y?.source && (
              <span className="ml-auto opacity-60">{data.us10y.source}</span>
            )}
          </div>
        </div>

        {/* Divergence + Signal points */}
        <div className="py-2">
          <div className="text-[9px] font-mono text-text-muted mb-1.5 tracking-wider">DIVERGENCIA ORO ↔ DXY</div>
          <div className={cn(
            'flex items-center gap-2 px-2 py-1.5 rounded text-[9px] font-mono',
            divg === 1 ? 'bg-neon-green/5' : divg === -1 ? 'bg-neon-red/5' : 'bg-bg-tertiary/30'
          )}>
            {divg === 1
              ? <><span className="text-neon-green font-bold">✓ NORMAL</span><span className="text-text-muted ml-1">correlación inversa activa</span></>
              : divg === -1
              ? <><AlertTriangle className="h-3 w-3 text-neon-red" /><span className="text-neon-red font-bold ml-1">DIVERGENCIA</span><span className="text-text-muted ml-1">cuidado — patrón atípico</span></>
              : <span className="text-text-muted">Sin datos suficientes de divergencia</span>
            }
          </div>

          <div className="flex items-center gap-2 mt-2">
            <span className="text-[9px] font-mono text-text-muted">Signal pts aportados:</span>
            <div className="flex gap-1">
              {[1,2,3,4,5].map(i => (
                <div key={i} className={cn(
                  'w-4 h-1.5 rounded-full',
                  i <= pts ? (pts >= 4 ? 'bg-neon-green' : pts >= 2 ? 'bg-yellow-500' : 'bg-neon-red') : 'bg-bg-tertiary'
                )} />
              ))}
            </div>
            <span className={cn('text-[10px] font-mono font-bold ml-auto', ptsColor)}>{pts}/5</span>
          </div>
        </div>

        {/* Shift alerts */}
        {shifts.length > 0 && (
          <div className="pt-1 border-t border-border-primary/30">
            <div className="text-[9px] font-mono text-text-muted mb-1 tracking-wider flex items-center gap-1">
              <AlertTriangle className="h-2.5 w-2.5 text-yellow-400" />
              SHIFT ALERTS
            </div>
            {shifts.map((s, i) => (
              <div key={i} className={cn(
                'flex flex-col gap-0.5 px-2 py-1 rounded mb-1 text-[9px] font-mono',
                s.severity === 'HIGH' ? 'bg-neon-red/10' : 'bg-yellow-400/10'
              )}>
                <span className={s.severity === 'HIGH' ? 'text-neon-red font-bold' : 'text-yellow-400 font-bold'}>
                  {s.type.replace('_', ' ')}
                </span>
                <span className="text-text-muted">{s.description}</span>
                {s.implication && <span className="text-text-muted opacity-70">{s.implication}</span>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
