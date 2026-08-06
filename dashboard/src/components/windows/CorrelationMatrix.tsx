'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { GitCompare, AlertTriangle, RefreshCw } from 'lucide-react';
import { cn } from '@/lib/utils';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

interface CorrMatrix { [asset: string]: { [asset: string]: number | null } }

interface Divergence {
  pair: string;
  corr_7d: number;
  corr_30d: number;
  shift: number;
  severity: 'HIGH' | 'MEDIUM';
  description: string;
}

interface CorrData {
  assets: string[];
  matrices: { '7d': CorrMatrix; '30d': CorrMatrix };
  divergences: Divergence[];
  updated_at: string | null;
  error?: string;
}

const ASSET_COLORS: Record<string, string> = {
  XAUUSD: 'text-yellow-400',
  BTCUSD: 'text-orange-400',
  OIL:    'text-cyan-400',
  SPX:    'text-neon-green',
};

function corrColor(v: number | null): string {
  if (v === null) return 'bg-bg-tertiary/40 text-text-muted';
  if (v >= 0.7)  return 'bg-neon-green/30 text-neon-green';
  if (v >= 0.4)  return 'bg-neon-green/15 text-neon-green/80';
  if (v >= 0.15) return 'bg-neon-green/8 text-neon-green/60';
  if (v <= -0.7) return 'bg-neon-red/30 text-neon-red';
  if (v <= -0.4) return 'bg-neon-red/15 text-neon-red/80';
  if (v <= -0.15) return 'bg-neon-red/8 text-neon-red/60';
  return 'bg-bg-tertiary/30 text-text-muted';
}

function CorrCell({ val, isDiag }: { val: number | null; isDiag: boolean }) {
  if (isDiag) {
    return (
      <div className="flex items-center justify-center h-full w-full bg-bg-tertiary/20 rounded">
        <span className="text-[10px] font-mono text-text-muted">—</span>
      </div>
    );
  }
  return (
    <div className={cn('flex items-center justify-center h-full w-full rounded text-[10px] font-mono font-bold', corrColor(val))}>
      {val !== null ? (val >= 0 ? '+' : '') + val.toFixed(2) : '—'}
    </div>
  );
}

export default function CorrelationMatrix() {
  const [data, setData] = useState<CorrData | null>(null);
  const [window, setWindow] = useState<'7d' | '30d'>('7d');
  const [age, setAge] = useState('');
  const [loading, setLoading] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/intelligence/correlation`);
      if (res.ok) setData(await res.json());
    } catch {}
  }, []);

  useEffect(() => {
    fetchData();
    const t = setInterval(fetchData, 30_000);
    return () => clearInterval(t);
  }, [fetchData]);

  useEffect(() => {
    if (!data?.updated_at) return;
    const update = () => {
      const diff = Math.floor((Date.now() - new Date(data.updated_at!).getTime()) / 60000);
      setAge(diff < 1 ? 'ahora' : diff < 60 ? `${diff}m` : `${Math.floor(diff / 60)}h`);
    };
    update();
    const t = setInterval(update, 30_000);
    return () => clearInterval(t);
  }, [data?.updated_at]);

  const handleRefresh = async () => {
    setLoading(true);
    await fetchData();
    setLoading(false);
  };

  const assets = data?.assets ?? ['XAUUSD', 'BTCUSD', 'OIL', 'SPX'];
  const matrix = data?.matrices?.[window] ?? {};
  const divs   = data?.divergences ?? [];

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border-primary/50 shrink-0">
        <GitCompare className="h-3.5 w-3.5 text-neon-purple" />
        <span className="text-[11px] font-mono font-bold text-text-secondary tracking-wider">
          CORRELACIÓN MULTI-ACTIVO
        </span>

        {/* Window toggle */}
        <div className="ml-3 flex gap-0.5">
          {(['7d', '30d'] as const).map(w => (
            <button
              key={w}
              onClick={() => setWindow(w)}
              className={cn(
                'px-2 py-0.5 rounded text-[9px] font-mono font-bold transition-all',
                window === w
                  ? 'bg-neon-purple/20 text-neon-purple border border-neon-purple/30'
                  : 'text-text-muted hover:text-text-secondary'
              )}
            >
              {w}
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-2">
          {age && <span className="text-[9px] font-mono text-text-muted">{age}</span>}
          <button
            onClick={handleRefresh}
            className="text-text-muted hover:text-neon-purple transition-colors"
          >
            <RefreshCw className={cn('h-3 w-3', loading && 'animate-spin')} />
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-auto px-3 py-2 flex flex-col gap-3">
        {/* Correlation heatmap */}
        <div className="flex gap-2">
          {/* Row labels column */}
          <div className="flex flex-col gap-1 shrink-0">
            <div className="h-6" /> {/* spacer for column headers */}
            {assets.map(a => (
              <div key={a} className="flex items-center h-7">
                <span className={cn('text-[10px] font-mono font-bold w-14 text-right pr-2', ASSET_COLORS[a] ?? 'text-text-secondary')}>
                  {a}
                </span>
              </div>
            ))}
          </div>

          {/* Grid */}
          <div className="flex-1 min-w-0">
            {/* Column headers */}
            <div className="flex gap-1 mb-1">
              {assets.map(a => (
                <div key={a} className="flex-1 flex items-center justify-center h-6">
                  <span className={cn('text-[9px] font-mono font-bold', ASSET_COLORS[a] ?? 'text-text-secondary')}>
                    {a}
                  </span>
                </div>
              ))}
            </div>

            {/* Matrix rows */}
            <div className="flex flex-col gap-1">
              {assets.map(rowAsset => (
                <div key={rowAsset} className="flex gap-1">
                  {assets.map(colAsset => {
                    const isDiag = rowAsset === colAsset;
                    const val = isDiag ? null : (matrix[rowAsset]?.[colAsset] ?? null);
                    return (
                      <div key={colAsset} className="flex-1 h-7 min-w-0">
                        <CorrCell val={val} isDiag={isDiag} />
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Legend */}
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-[8px] font-mono text-text-muted">Escala Pearson:</span>
          {[
            { label: '≥+0.7 fuerte+', cls: 'bg-neon-green/30 text-neon-green' },
            { label: '+0.4/+0.7 mod+', cls: 'bg-neon-green/15 text-neon-green/80' },
            { label: '±0.15 neutro', cls: 'bg-bg-tertiary/30 text-text-muted' },
            { label: '-0.4/-0.7 mod−', cls: 'bg-neon-red/15 text-neon-red/80' },
            { label: '≤-0.7 fuerte−', cls: 'bg-neon-red/30 text-neon-red' },
          ].map(({ label, cls }) => (
            <div key={label} className={cn('px-1.5 py-0.5 rounded text-[8px] font-mono', cls)}>
              {label}
            </div>
          ))}
        </div>

        {/* Divergences */}
        {divs.length > 0 && (
          <div className="border-t border-border-primary/30 pt-2">
            <div className="flex items-center gap-1 mb-1.5 text-[9px] font-mono text-text-muted tracking-wider">
              <AlertTriangle className="h-2.5 w-2.5 text-yellow-400" />
              DIVERGENCIAS DETECTADAS (7d vs 30d)
            </div>
            <div className="flex flex-col gap-1">
              {divs.slice(0, 4).map((d, i) => (
                <div
                  key={i}
                  className={cn(
                    'flex items-center gap-2 px-2 py-1 rounded text-[9px] font-mono',
                    d.severity === 'HIGH' ? 'bg-neon-red/8 border border-neon-red/20' : 'bg-yellow-400/8 border border-yellow-400/20'
                  )}
                >
                  <span className={cn('font-bold shrink-0 w-20', d.severity === 'HIGH' ? 'text-neon-red' : 'text-yellow-400')}>
                    {d.pair}
                  </span>
                  <span className="text-text-muted flex-1">{d.description}</span>
                  <span className={cn('shrink-0 font-bold', d.severity === 'HIGH' ? 'text-neon-red' : 'text-yellow-400')}>
                    Δ{d.shift.toFixed(2)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* No data state */}
        {!data?.updated_at && (
          <div className="flex flex-col items-center justify-center gap-2 text-text-muted mt-4">
            <GitCompare className="h-8 w-8 opacity-20" />
            <span className="text-[10px] font-mono">Calculando correlaciones…</span>
            <span className="text-[9px] font-mono opacity-60">Primera ejecución tarda ~30s</span>
          </div>
        )}
      </div>
    </div>
  );
}
