'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { BarChart2, Trophy } from 'lucide-react';
import { cn } from '@/lib/utils';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

interface RegimeStat {
  trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  avg_pnl: number;
}

interface IntelData {
  by_regime: Record<string, RegimeStat>;
  by_source: Record<string, RegimeStat>;
  by_session: Record<string, RegimeStat>;
  by_hour: Record<string, RegimeStat>;
  overall: {
    win_rate: number;
    total_trades: number;
    total_wins: number;
    total_losses: number;
    avg_pnl_pips: number;
    recent_streak: { type: string; count: number };
  };
}

const REGIME_COLOR: Record<string, string> = {
  TRENDING_BULL: 'text-neon-green', TRENDING_BEAR: 'text-neon-red',
  TRENDING: 'text-neon-green', RANGING: 'text-yellow-400',
  BREAKOUT: 'text-neon-purple', HIGH_VOL: 'text-orange-400',
};

export default function ModelsByRegime() {
  const [data, setData] = useState<IntelData | null>(null);
  const [activeView, setActiveView] = useState<'regime' | 'session' | 'source'>('regime');

  const fetch_ = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/intelligence/models-by-regime`);
      if (res.ok) setData(await res.json());
    } catch {}
  }, []);

  useEffect(() => { fetch_(); const t = setInterval(fetch_, 10000); return () => clearInterval(t); }, [fetch_]);

  const overall = data?.overall;
  const streak = overall?.recent_streak;
  const tableData = activeView === 'regime'
    ? data?.by_regime
    : activeView === 'session'
    ? data?.by_session
    : data?.by_source;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border-primary/50">
        <BarChart2 className="h-3.5 w-3.5 text-neon-purple" />
        <span className="text-[11px] font-mono font-bold text-text-secondary tracking-wider">MODEL PERFORMANCE</span>
        {streak && streak.count > 0 && (
          <div className={cn('ml-auto text-[9px] font-mono font-bold px-1.5 py-0.5 rounded',
            streak.type === 'win' ? 'bg-neon-green/10 text-neon-green' : 'bg-neon-red/10 text-neon-red'
          )}>
            {streak.type === 'win' ? '▲' : '▼'} {streak.count} streak
          </div>
        )}
      </div>

      {/* Overall summary */}
      {overall && overall.total_trades != null ? (
        <div className="grid grid-cols-4 gap-1 px-3 py-2 border-b border-border-primary/30">
          <MiniStat label="Win Rate" value={`${((overall.win_rate ?? 0) * 100).toFixed(1)}%`}
            color={(overall.win_rate ?? 0) >= 0.5 ? 'text-neon-green' : (overall.win_rate ?? 0) >= 0.4 ? 'text-yellow-400' : 'text-neon-red'} />
          <MiniStat label="Trades" value={String(overall.total_trades ?? 0)} color="text-neon-cyan" />
          <MiniStat label="Wins" value={String(overall.total_wins ?? 0)} color="text-neon-green" />
          <MiniStat label="Avg PnL" value={`${(overall.avg_pnl_pips ?? 0) >= 0 ? '+' : ''}${(overall.avg_pnl_pips ?? 0).toFixed(1)} p`}
            color={(overall.avg_pnl_pips ?? 0) >= 0 ? 'text-neon-green' : 'text-neon-red'} />
        </div>
      ) : null}

      {/* View toggle */}
      <div className="flex gap-1 px-3 py-1.5 border-b border-border-primary/30">
        {(['regime', 'session', 'source'] as const).map((v) => (
          <button key={v} onClick={() => setActiveView(v)}
            className={cn('px-2 py-0.5 rounded text-[9px] font-mono transition-all',
              activeView === v
                ? 'bg-neon-purple/20 text-neon-purple border border-neon-purple/30'
                : 'text-text-muted hover:text-text-secondary'
            )}>
            {v.toUpperCase()}
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="flex-1 overflow-y-auto px-3 py-2">
        {tableData && Object.keys(tableData).length > 0 ? (
          <table className="w-full text-[9px] font-mono">
            <thead>
              <tr className="text-text-muted border-b border-border-primary/30">
                <th className="text-left py-1 font-normal">Grupo</th>
                <th className="text-right py-1 font-normal">WR</th>
                <th className="text-right py-1 font-normal">N</th>
                <th className="text-right py-1 font-normal">Avg PnL</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(tableData)
                .sort((a, b) => b[1].win_rate - a[1].win_rate)
                .map(([label, stats]) => {
                  const wr = stats.win_rate;
                  const wrColor = wr >= 0.55 ? 'text-neon-green' : wr >= 0.45 ? 'text-yellow-400' : 'text-neon-red';
                  const pnlColor = stats.avg_pnl >= 0 ? 'text-neon-green' : 'text-neon-red';
                  const labelColor = REGIME_COLOR[label] || 'text-text-secondary';

                  return (
                    <tr key={label} className="border-b border-border-primary/10 hover:bg-bg-tertiary/20">
                      <td className={cn('py-1.5 font-bold', labelColor)}>{label}</td>
                      <td className={cn('text-right py-1.5 font-bold', wrColor)}>
                        {(wr * 100).toFixed(1)}%
                        <WRBar wr={wr} />
                      </td>
                      <td className="text-right py-1.5 text-text-muted">{stats.trades}</td>
                      <td className={cn('text-right py-1.5', pnlColor)}>
                        {stats.avg_pnl >= 0 ? '+' : ''}{stats.avg_pnl?.toFixed(1)}
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        ) : (
          <div className="text-[10px] font-mono text-text-muted text-center py-8">
            Sin datos suficientes (mínimo 5 trades)
          </div>
        )}
      </div>

      {/* Best regime highlight */}
      {data?.by_regime && Object.keys(data.by_regime).length > 0 && (
        <div className="px-3 pb-2">
          {(() => {
            const best = Object.entries(data.by_regime)
              .filter(([, s]) => s.trades >= 3)
              .sort((a, b) => b[1].win_rate - a[1].win_rate)[0];
            if (!best) return null;
            return (
              <div className="flex items-center gap-2 bg-neon-green/5 border border-neon-green/20 rounded px-2 py-1">
                <Trophy className="h-3 w-3 text-neon-green shrink-0" />
                <span className="text-[9px] font-mono text-text-muted">Mejor: </span>
                <span className="text-[9px] font-mono text-neon-green font-bold">{best[0]}</span>
                <span className="text-[9px] font-mono text-neon-green ml-auto">{(best[1].win_rate * 100).toFixed(0)}% WR</span>
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
}

function MiniStat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="flex flex-col gap-0.5 bg-bg-tertiary/30 rounded px-1.5 py-1">
      <span className="text-[7px] font-mono text-text-muted uppercase tracking-wider">{label}</span>
      <span className={cn('text-[10px] font-mono font-bold', color)}>{value}</span>
    </div>
  );
}

function WRBar({ wr }: { wr: number }) {
  const pct = Math.min(100, wr * 100);
  const color = wr >= 0.55 ? 'bg-neon-green' : wr >= 0.45 ? 'bg-yellow-500' : 'bg-neon-red';
  return (
    <div className="w-12 h-0.5 bg-bg-tertiary rounded-full overflow-hidden mt-0.5 ml-auto">
      <div className={cn('h-full rounded-full', color)} style={{ width: `${pct}%` }} />
    </div>
  );
}
