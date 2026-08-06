'use client';

import { useEffect, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import {
  ShieldCheck, ShieldAlert, AlertTriangle, RefreshCw,
  TrendingUp, TrendingDown, Minus, Zap,
} from 'lucide-react';

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:9000';

interface TiltState {
  status: 'NORMAL' | 'WARNING' | 'TILT';
  consecutive_losses: number;
  session_losses: number;
  session_wins: number;
  cooldown_remaining_min: number;
}

interface RiskState {
  balance: number;
  equity: number;
  daily_loss_pct: number;
  open_positions: number;
  tilt_status: string;
  circuit_breakers: Array<{ name: string; triggered: boolean; reason?: string }>;
}

interface SessionPerf {
  session: string;
  trades: number;
  win_rate: number;
  net_pnl: number;
  kelly: number;
}

interface GatewayData {
  risk?: RiskState;
  tilt?: TiltState;
  session_perf?: SessionPerf[];
  min_rr?: number;
  tokyo_conviction?: number;
  last_violations?: Array<{ rule: string; message: string }>;
}

function KellyBar({ kelly }: { kelly: number }) {
  const pct = Math.min(Math.abs(kelly) * 200, 100);
  const negative = kelly < 0;
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-1.5 rounded-full bg-bg-input overflow-hidden">
        <div
          className={cn('h-full rounded-full transition-all', negative ? 'bg-neon-red' : 'bg-neon-green')}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className={cn('text-xs font-mono tabular-nums', negative ? 'text-neon-red' : 'text-neon-green')}>
        {kelly >= 0 ? '+' : ''}{(kelly * 100).toFixed(1)}%
      </span>
    </div>
  );
}

function SessionRow({ s }: { s: SessionPerf }) {
  const isNeg = s.kelly < 0;
  const isTokyo = s.session === 'TOKYO';
  return (
    <div className={cn(
      'flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs',
      isTokyo && isNeg ? 'bg-neon-red/8 border border-neon-red/20' : 'bg-bg-input/40',
    )}>
      <span className={cn('w-20 font-semibold', isTokyo ? 'text-neon-gold' : 'text-text-secondary')}>
        {s.session}
      </span>
      <span className="w-10 font-mono tabular-nums text-text-muted">{s.trades}T</span>
      <span className={cn('w-10 font-mono tabular-nums', s.win_rate >= 0.5 ? 'text-neon-green' : 'text-neon-red')}>
        {(s.win_rate * 100).toFixed(0)}%
      </span>
      <KellyBar kelly={s.kelly} />
      {isTokyo && isNeg && (
        <AlertTriangle className="h-3 w-3 text-neon-red shrink-0" />
      )}
    </div>
  );
}

export default function GatewayDiagnostic() {
  const [data, setData] = useState<GatewayData>({});
  const [loading, setLoading] = useState(false);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [expanded, setExpanded] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // Risk status (tilt + breakers)
      const riskRes = await fetch(`${API}/api/risk/status`);
      if (riskRes.ok) {
        const rd = await riskRes.json();
        setData(prev => ({
          ...prev,
          risk: rd.state ?? rd,
          tilt: rd.tilt,
        }));
      }

      // Trade memory metrics for session Kelly
      const memRes = await fetch(`${API}/api/trade-memory/metrics`);
      if (memRes.ok) {
        const md = await memRes.json();
        const bySession: SessionPerf[] = [];
        const sessions = md?.by_session ?? md?.metrics?.by_session ?? {};
        for (const [sess, stats] of Object.entries(sessions as Record<string, Record<string, number>>)) {
          const n = Number(stats?.total_trades ?? 0);
          if (n < 3) continue;
          const wr = Number(stats?.win_rate ?? 0);
          const avgW = Number(stats?.avg_win_pips ?? 0);
          const avgL = Math.abs(Number(stats?.avg_loss_pips ?? 0));
          const kelly = avgL > 0 ? wr - (1 - wr) / (avgW / avgL) : 0;
          bySession.push({
            session: sess,
            trades: n,
            win_rate: wr,
            net_pnl: Number(stats?.net_pnl_usd ?? 0),
            kelly,
          });
        }
        setData(prev => ({ ...prev, session_perf: bySession }));
      }

      // Risk settings for min_rr + tokyo conviction
      const cfgRes = await fetch(`${API}/api/settings`);
      if (cfgRes.ok) {
        const cfg = await cfgRes.json();
        setData(prev => ({
          ...prev,
          min_rr: Number(cfg?.min_rr_ratio ?? 1.5),
          tokyo_conviction: Number(cfg?.tokyo_min_conviction ?? 0.85),
        }));
      }

      setLastUpdate(new Date());
    } catch {
      // gateway offline — data stays stale
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, 30_000);
    return () => window.clearInterval(id);
  }, [load]);

  const tilt = data.tilt;
  const risk = data.risk;
  const tiltStatus = tilt?.status ?? risk?.tilt_status ?? 'NORMAL';
  const consecutiveLosses = tilt?.consecutive_losses ?? 0;
  const cooldown = tilt?.cooldown_remaining_min ?? 0;
  const activeBreakers = (risk?.circuit_breakers ?? []).filter(b => b.triggered);

  const anyIssue = tiltStatus !== 'NORMAL' || activeBreakers.length > 0 ||
    (data.session_perf ?? []).some(s => s.kelly < 0);

  const summary = anyIssue
    ? `${activeBreakers.length > 0 ? `${activeBreakers.length} breaker${activeBreakers.length > 1 ? 's' : ''} active` : 'warnings present'}`
    : 'Gateway nominal';

  return (
    <div className={cn(
      'rounded-xl border bg-bg-card transition-all',
      tiltStatus === 'TILT'
        ? 'border-neon-red/50 shadow-sm shadow-neon-red/10'
        : tiltStatus === 'WARNING' || anyIssue
        ? 'border-neon-gold/40'
        : 'border-border-primary',
    )}>
      {/* Header */}
      <div className="flex items-center gap-2.5 px-4 py-3 border-b border-border-primary">
        <Zap className={cn(
          'h-4 w-4 shrink-0',
          tiltStatus === 'TILT' ? 'text-neon-red' :
          tiltStatus === 'WARNING' ? 'text-neon-gold' : 'text-neon-purple',
        )} />
        <span className="text-xs font-bold uppercase tracking-wider text-text-secondary flex-1">
          Risk Gateway
        </span>
        <span className={cn(
          'text-[10px] font-mono',
          tiltStatus === 'TILT' ? 'text-neon-red' :
          tiltStatus === 'WARNING' ? 'text-neon-gold' : 'text-text-muted',
        )}>
          {summary}
        </span>
        {lastUpdate && (
          <span className="text-[10px] font-mono text-text-muted hidden sm:inline">
            {lastUpdate.toLocaleTimeString()}
          </span>
        )}
        <button onClick={load} disabled={loading} className="p-1 rounded text-text-muted hover:text-text-primary transition-colors">
          <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
        </button>
        <button
          onClick={() => setExpanded(e => !e)}
          className="text-[10px] font-mono text-text-muted hover:text-text-secondary transition-colors"
        >
          {expanded ? 'collapse' : 'expand'}
        </button>
      </div>

      {/* Compact row — always visible */}
      <div className="flex flex-wrap gap-x-5 gap-y-2 px-4 py-2.5 text-xs">
        {/* Anti-tilt */}
        <div className="flex items-center gap-1.5">
          {tiltStatus === 'TILT' ? (
            <ShieldAlert className="h-3.5 w-3.5 text-neon-red" />
          ) : tiltStatus === 'WARNING' ? (
            <AlertTriangle className="h-3.5 w-3.5 text-neon-gold" />
          ) : (
            <ShieldCheck className="h-3.5 w-3.5 text-neon-green" />
          )}
          <span className={cn(
            'font-semibold',
            tiltStatus === 'TILT' ? 'text-neon-red' :
            tiltStatus === 'WARNING' ? 'text-neon-gold' : 'text-text-secondary',
          )}>
            Tilt: {tiltStatus}
          </span>
          {consecutiveLosses > 0 && (
            <span className="font-mono text-text-muted">({consecutiveLosses}L streak)</span>
          )}
          {cooldown > 0 && (
            <span className="font-mono text-neon-red">{cooldown.toFixed(0)}min CD</span>
          )}
        </div>

        {/* Min RR */}
        <div className="flex items-center gap-1.5">
          <TrendingUp className="h-3.5 w-3.5 text-text-muted" />
          <span className="text-text-muted">Min R:R</span>
          <span className="font-mono font-semibold text-neon-blue">
            {data.min_rr?.toFixed(1) ?? '1.5'}:1
          </span>
        </div>

        {/* TOKYO conviction */}
        <div className="flex items-center gap-1.5">
          <Minus className="h-3.5 w-3.5 text-neon-gold" />
          <span className="text-text-muted">Tokyo gate</span>
          <span className="font-mono font-semibold text-neon-gold">
            {((data.tokyo_conviction ?? 0.85) * 100).toFixed(0)}%
          </span>
        </div>

        {/* Active circuit breakers */}
        {activeBreakers.length > 0 && (
          <div className="flex items-center gap-1.5">
            <AlertTriangle className="h-3.5 w-3.5 text-neon-red" />
            <span className="text-neon-red font-semibold">
              {activeBreakers.map(b => b.name).join(', ')}
            </span>
          </div>
        )}
      </div>

      {/* Expanded — session Kelly table */}
      {expanded && (data.session_perf ?? []).length > 0 && (
        <div className="px-4 pb-4 space-y-1 border-t border-border-primary pt-3">
          <p className="text-[10px] font-mono uppercase text-text-muted mb-2">Session Kelly Fractions</p>
          {[...(data.session_perf ?? [])]
            .sort((a, b) => b.kelly - a.kelly)
            .map(s => <SessionRow key={s.session} s={s} />)
          }
          <p className="text-[10px] font-mono text-text-muted mt-2 leading-relaxed">
            Kelly &lt; 0 = negative EV (system loses money on average in that session).
            TOKYO gate raised to 85% conviction to compensate.
          </p>
        </div>
      )}

      {/* Expanded — active violations */}
      {expanded && activeBreakers.length > 0 && (
        <div className="px-4 pb-4 border-t border-border-primary pt-3 space-y-1">
          <p className="text-[10px] font-mono uppercase text-text-muted mb-2">Active Breakers</p>
          {activeBreakers.map(b => (
            <div key={b.name} className="flex items-start gap-2 text-xs px-2.5 py-1.5 rounded-lg bg-neon-red/8">
              <AlertTriangle className="h-3.5 w-3.5 text-neon-red shrink-0 mt-0.5" />
              <div>
                <span className="font-semibold text-neon-red">{b.name}</span>
                {b.reason && <span className="ml-2 text-neon-red/70 font-mono text-[10px]">{b.reason}</span>}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Tilt persist indicator */}
      {expanded && (
        <div className="px-4 pb-3 border-t border-border-primary pt-2">
          <div className="flex items-center gap-2 text-[10px] font-mono text-text-muted">
            <div className={cn(
              'h-1.5 w-1.5 rounded-full',
              tilt ? 'bg-neon-green' : 'bg-neon-red',
            )} />
            Anti-tilt state: {tilt ? 'persisted via Redis' : 'no data from backend'}
            {tilt && (
              <span className="ml-2">
                · {tilt.session_wins}W / {tilt.session_losses}L this session
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
