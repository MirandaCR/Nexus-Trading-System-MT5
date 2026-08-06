'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { Brain, TrendingUp, TrendingDown, Minus, Clock, Zap, AlertTriangle, RefreshCw } from 'lucide-react';
import { cn } from '@/lib/utils';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

interface GroupStats {
  trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  avg_pnl: number;
}

interface Streak { type: 'win' | 'loss' | 'none'; count: number }

interface Metrics {
  total_trades: number;
  total_wins: number;
  total_losses: number;
  overall_win_rate: number;
  avg_pnl_pips: number;
  recent_streak: Streak;
  by_session: Record<string, GroupStats>;
  by_direction: Record<string, GroupStats>;
  by_regime: Record<string, GroupStats>;
  by_source: Record<string, GroupStats>;
  by_hour: Record<string, GroupStats>;
}

interface Thresholds {
  quality_score_threshold: number;
  conviction_min: number;
  good_hours: string[];
  bad_hours: string[];
  hour_wr: Record<string, number>;
  overall_wr: number;
  total_trades: number;
  computed_at: string | null;
}

interface MemoryData {
  metrics: Metrics;
  thresholds: Thresholds;
  recent_trades: Record<string, unknown>[];
}

type ActiveTab = 'session' | 'direction' | 'regime' | 'hours' | 'thresholds';

// ── Helpers ───────────────────────────────────────────────────────────────────

function wrColor(wr: number) {
  if (wr >= 0.55) return 'text-neon-green';
  if (wr >= 0.40) return 'text-yellow-400';
  return 'text-neon-red';
}

function pnlColor(v: number) {
  return v > 0 ? 'text-neon-green' : v < 0 ? 'text-neon-red' : 'text-text-muted';
}

function BreakdownTable({ data, label }: { data: Record<string, GroupStats> | null | undefined; label: string }) {
  const rows = Object.entries(data ?? {}).sort((a, b) => b[1].trades - a[1].trades);
  if (!rows.length) return (
    <div className="text-[9px] font-mono text-text-muted text-center py-4">Sin datos</div>
  );
  return (
    <div className="flex flex-col gap-0.5">
      {rows.map(([key, s]) => (
        <div key={key} className="flex items-center gap-2 px-2 py-0.5 rounded hover:bg-bg-tertiary/20">
          <span className="text-[9px] font-mono text-text-muted w-20 shrink-0 truncate">{key}</span>
          <div className="flex-1 h-1 rounded-full bg-bg-tertiary/40 overflow-hidden">
            <div
              className={cn('h-full rounded-full', s.win_rate >= 0.5 ? 'bg-neon-green/60' : 'bg-neon-red/60')}
              style={{ width: `${Math.min(s.win_rate * 100, 100)}%` }}
            />
          </div>
          <span className={cn('text-[9px] font-mono font-bold w-9 text-right shrink-0', wrColor(s.win_rate))}>
            {(s.win_rate * 100).toFixed(0)}%
          </span>
          <span className={cn('text-[8px] font-mono w-12 text-right shrink-0', pnlColor(s.avg_pnl ?? 0))}>
            {(s.avg_pnl ?? 0) > 0 ? '+' : ''}{(s.avg_pnl ?? 0).toFixed(1)}p
          </span>
          <span className="text-[8px] font-mono text-text-muted w-7 text-right shrink-0">{s.trades}t</span>
        </div>
      ))}
    </div>
  );
}

function HourGrid({ byHour, goodHours, badHours }: {
  byHour: Record<string, GroupStats>;
  goodHours: string[];
  badHours: string[];
}) {
  const hours = Array.from({ length: 24 }, (_, i) => `H${i.toString().padStart(2, '0')}`);
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap gap-0.5">
        {hours.map((h) => {
          const s = byHour[h];
          const isGood = goodHours.includes(h);
          const isBad = badHours.includes(h);
          const wr = s?.win_rate ?? null;
          return (
            <div
              key={h}
              title={s ? `${h}: ${(s.win_rate * 100).toFixed(0)}% WR — ${s.trades} trades` : `${h}: sin datos`}
              className={cn(
                'w-7 h-7 rounded flex flex-col items-center justify-center cursor-default border',
                isGood ? 'border-neon-green/40 bg-neon-green/10' :
                isBad  ? 'border-neon-red/40 bg-neon-red/10' :
                s       ? 'border-border-primary/30 bg-bg-tertiary/20' :
                          'border-border-primary/10 bg-transparent',
              )}
            >
              <span className="text-[7px] font-mono text-text-muted leading-none">{h.slice(1)}</span>
              {wr !== null && (
                <span className={cn('text-[7px] font-mono font-bold leading-none', wrColor(wr))}>
                  {(wr * 100).toFixed(0)}
                </span>
              )}
            </div>
          );
        })}
      </div>
      <div className="flex items-center gap-3 text-[8px] font-mono text-text-muted mt-0.5">
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded border border-neon-green/40 bg-neon-green/10 inline-block" /> buenas (≥50%)</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded border border-neon-red/40 bg-neon-red/10 inline-block" /> malas (&lt;30%)</span>
      </div>
    </div>
  );
}

function ThresholdsView({ t }: { t: Thresholds }) {
  if (!t.computed_at) return (
    <div className="text-[9px] font-mono text-text-muted text-center py-4">Sin thresholds calculados</div>
  );
  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-2 gap-1.5">
        <div className="bg-bg-tertiary/30 rounded px-2 py-1.5">
          <div className="text-[8px] font-mono text-text-muted">Signal Quality Min</div>
          <div className="text-sm font-mono font-black text-neon-purple">{t.quality_score_threshold}</div>
        </div>
        <div className="bg-bg-tertiary/30 rounded px-2 py-1.5">
          <div className="text-[8px] font-mono text-text-muted">Conviction Min</div>
          <div className="text-sm font-mono font-black text-neon-purple">{(t.conviction_min * 100).toFixed(0)}%</div>
        </div>
      </div>
      {t.good_hours.length > 0 && (
        <div className="bg-neon-green/5 border border-neon-green/20 rounded px-2 py-1.5">
          <div className="text-[8px] font-mono text-neon-green mb-0.5">HORAS BUENAS (UTC)</div>
          <div className="text-[9px] font-mono text-text-secondary">{t.good_hours.join(' · ')}</div>
        </div>
      )}
      {t.bad_hours.length > 0 && (
        <div className="bg-neon-red/5 border border-neon-red/20 rounded px-2 py-1.5">
          <div className="text-[8px] font-mono text-neon-red mb-0.5">HORAS MALAS (UTC)</div>
          <div className="text-[9px] font-mono text-text-secondary">{t.bad_hours.join(' · ')}</div>
        </div>
      )}
      {t.computed_at && (
        <div className="text-[8px] font-mono text-text-muted">
          Calculado: {new Date(t.computed_at).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' })}
        </div>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function TradeMemoryPanel() {
  const [data, setData] = useState<MemoryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<ActiveTab>('session');

  const fetch_ = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/intelligence/trade-memory`);
      if (res.ok) setData(await res.json());
    } catch {} finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetch_();
    const t = setInterval(fetch_, 30_000);
    return () => clearInterval(t);
  }, [fetch_]);

  const m = data?.metrics;
  const th = data?.thresholds;
  const streak = m?.recent_streak;
  const noData = !m || m.total_trades === 0;

  const TABS: { id: ActiveTab; label: string }[] = [
    { id: 'session',    label: 'Sesión' },
    { id: 'direction',  label: 'Dirección' },
    { id: 'regime',     label: 'Régimen' },
    { id: 'hours',      label: 'Horas UTC' },
    { id: 'thresholds', label: 'Thresholds' },
  ];

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border-primary/50 shrink-0">
        <Brain className="h-3.5 w-3.5 text-neon-purple" />
        <span className="text-[11px] font-mono font-bold text-text-secondary tracking-wider">TRADE MEMORY</span>
        <span className="text-[8px] font-mono text-text-muted">{m ? `${m.total_trades} trades` : '...'}</span>
        <button onClick={fetch_} className="ml-auto p-1 rounded text-text-muted hover:text-neon-purple hover:bg-neon-purple/10 transition-colors">
          <RefreshCw className={cn('h-3 w-3', loading && 'animate-spin')} />
        </button>
      </div>

      {/* Summary strip */}
      {m && !noData && (
        <div className="flex items-center gap-3 px-3 py-1.5 border-b border-border-primary/30 shrink-0 flex-wrap">
          {/* Overall WR */}
          <div className="flex items-center gap-1">
            <span className="text-[8px] font-mono text-text-muted">WR</span>
            <span className={cn('text-[11px] font-mono font-black', wrColor(m.overall_win_rate))}>
              {(m.overall_win_rate * 100).toFixed(1)}%
            </span>
          </div>
          <div className="h-3 w-px bg-border-primary/40" />
          {/* Avg PnL */}
          <div className="flex items-center gap-1">
            <span className="text-[8px] font-mono text-text-muted">Avg</span>
            <span className={cn('text-[11px] font-mono font-black', pnlColor(m.avg_pnl_pips ?? 0))}>
              {(m.avg_pnl_pips ?? 0) > 0 ? '+' : ''}{(m.avg_pnl_pips ?? 0).toFixed(1)}p
            </span>
          </div>
          <div className="h-3 w-px bg-border-primary/40" />
          {/* W/L */}
          <div className="flex items-center gap-1 text-[9px] font-mono">
            <span className="text-neon-green">{m.total_wins}W</span>
            <span className="text-text-muted">/</span>
            <span className="text-neon-red">{m.total_losses}L</span>
          </div>
          {/* Streak */}
          {streak && streak.type !== 'none' && streak.count > 0 && (
            <>
              <div className="h-3 w-px bg-border-primary/40" />
              <div className={cn(
                'flex items-center gap-1 px-1.5 py-0.5 rounded text-[8px] font-mono font-bold',
                streak.type === 'win' ? 'bg-neon-green/10 text-neon-green' : 'bg-neon-red/10 text-neon-red'
              )}>
                {streak.type === 'win' ? <TrendingUp className="h-2.5 w-2.5" /> : <TrendingDown className="h-2.5 w-2.5" />}
                {streak.count} {streak.type === 'win' ? 'ganando' : 'perdiendo'}
              </div>
            </>
          )}
        </div>
      )}

      {/* Tabs */}
      <div className="flex items-center gap-0.5 px-2 py-1 border-b border-border-primary/30 shrink-0 overflow-x-auto scrollbar-hide">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              'px-2 py-0.5 rounded text-[9px] font-mono whitespace-nowrap transition-all',
              activeTab === tab.id
                ? 'bg-neon-purple/10 text-neon-purple border border-neon-purple/20'
                : 'text-text-muted hover:text-text-secondary'
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-2 py-2">
        {loading && (
          <div className="flex items-center justify-center gap-2 py-8 text-text-muted">
            <Brain className="h-4 w-4 animate-pulse text-neon-purple" />
            <span className="text-[10px] font-mono">Cargando memoria...</span>
          </div>
        )}

        {!loading && noData && (
          <div className="flex flex-col items-center justify-center gap-2 py-8 text-text-muted">
            <Brain className="h-8 w-8 opacity-20" />
            <span className="text-[10px] font-mono">Sin trades registrados en memoria</span>
            <span className="text-[9px] font-mono opacity-60">El sistema aprende tras cada cierre</span>
          </div>
        )}

        {!loading && m && !noData && (
          <>
            {activeTab === 'session' && (
              <BreakdownTable data={m.by_session} label="session" />
            )}
            {activeTab === 'direction' && (
              <BreakdownTable data={m.by_direction} label="direction" />
            )}
            {activeTab === 'regime' && (
              <BreakdownTable data={m.by_regime} label="regime" />
            )}
            {activeTab === 'hours' && (
              <HourGrid
                byHour={m.by_hour}
                goodHours={th?.good_hours ?? []}
                badHours={th?.bad_hours ?? []}
              />
            )}
            {activeTab === 'thresholds' && th && (
              <ThresholdsView t={th} />
            )}
          </>
        )}
      </div>
    </div>
  );
}
