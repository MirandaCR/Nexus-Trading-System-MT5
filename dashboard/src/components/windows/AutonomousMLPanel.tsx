'use client';

import React, { useEffect, useState } from 'react';
import { Bot, Power, PowerOff, TrendingUp, TrendingDown, RefreshCw, AlertCircle } from 'lucide-react';
import NeonCard from '../shared/NeonCard';
import { cn } from '@/lib/utils';

const POLL_MS = 8_000;
const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

interface AutonomousStats {
  running: boolean;
  trades_proposed: number;
  trades_approved: number;
  trades_rejected: number;
  last_cycle: string;
  last_signal: {
    symbol?: string;
    direction?: string;
    confidence?: number;
    sl_pips?: number;
    tp_pips?: number;
    session?: string;
    mode?: string;
    vol_regime?: string;
    timestamp?: string;
  };
  errors: number;
  mode: string;
  dynamic_tf?: {
    operational_tf?: string;
    confirmation_tfs?: string[];
    reason?: string;
    vol_regime?: string;
    auto_selected?: boolean;
  };
}

interface TraderStatus {
  ml: { model_type: string; model_name: string; is_fitted: boolean; performance: Record<string, number> };
  dl: { model_type: string; model_name: string; is_fitted: boolean; performance: Record<string, number> };
  rl: { model_type: string; model_name: string; is_fitted: boolean; performance: Record<string, number> };
  training_in_progress: Record<string, boolean>;
}

function directionBadge(dir: string) {
  if (dir === 'BUY') return { cls: 'text-neon-green bg-neon-green/10 border-neon-green/30', icon: <TrendingUp className="h-3 w-3" /> };
  if (dir === 'SELL') return { cls: 'text-neon-red bg-neon-red/10 border-neon-red/30', icon: <TrendingDown className="h-3 w-3" /> };
  return { cls: 'text-text-muted bg-bg-tertiary border-border-primary/30', icon: null };
}

export default function AutonomousMLPanel() {
  const [stats, setStats] = useState<AutonomousStats | null>(null);
  const [traders, setTraders] = useState<TraderStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [toggling, setToggling] = useState(false);

  const fetchAll = async () => {
    setLoading(true);
    try {
      const [autoRes, traderRes] = await Promise.all([
        fetch(`${API_BASE}/api/ml/traders/autonomous/status`).catch(() => null),
        fetch(`${API_BASE}/api/ml/traders/status`).catch(() => null),
      ]);
      if (autoRes?.ok) setStats(await autoRes.json());
      if (traderRes?.ok) setTraders(await traderRes.json());
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  };

  const togglePipeline = async () => {
    if (!stats) return;
    setToggling(true);
    try {
      const enable = !stats.running;
      await fetch(`${API_BASE}/api/ml/traders/autonomous/toggle?enable=${enable}`, { method: 'POST' });
      await new Promise((r) => setTimeout(r, 1000));
      await fetchAll();
    } catch {
      // silent
    } finally {
      setToggling(false);
    }
  };

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, POLL_MS);
    return () => clearInterval(id);
  }, []);

  const running = stats?.running ?? false;
  const sig = stats?.last_signal ?? {};
  const db = sig.direction ? directionBadge(sig.direction) : null;
  const approvalRate = (stats?.trades_proposed ?? 0) > 0
    ? ((stats?.trades_approved ?? 0) / stats!.trades_proposed * 100).toFixed(0)
    : '—';

  return (
    <NeonCard
      title="Autonomous ML Pipeline"
      icon={<Bot className="h-4 w-4" />}
      glow="green"
      className="h-full flex flex-col"
    >
      <div className="flex flex-col gap-2.5 flex-1">
        {/* Status + Toggle */}
        <div className="flex items-center gap-2">
          <span className={cn(
            'inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-mono font-bold border',
            running
              ? 'text-neon-green bg-neon-green/10 border-neon-green/30'
              : 'text-neon-red bg-neon-red/10 border-neon-red/30'
          )}>
            {running
              ? <><span className="h-1.5 w-1.5 rounded-full bg-neon-green animate-pulse" /> RUNNING</>
              : <><PowerOff className="h-3 w-3" /> STOPPED</>
            }
          </span>

          {stats?.mode && (
            <span className="text-[10px] font-mono text-text-muted">
              {stats.mode.toUpperCase()}
            </span>
          )}
          {stats?.dynamic_tf?.operational_tf && (
            <span className="text-[10px] font-mono text-neon-cyan border border-neon-cyan/25 rounded px-1.5 py-0.5" title={stats.dynamic_tf.reason}>
              TF {stats.dynamic_tf.operational_tf}
              {stats.dynamic_tf.auto_selected ? ' ⚡' : ''}
            </span>
          )}

          <div className="flex-1" />

          <button
            onClick={fetchAll}
            disabled={loading}
            className="text-[10px] text-text-muted hover:text-text-secondary"
          >
            <RefreshCw className={cn('h-3 w-3', loading && 'animate-spin')} />
          </button>

          <button
            onClick={togglePipeline}
            disabled={toggling}
            className={cn(
              'flex items-center gap-1 px-2 py-1 rounded text-[10px] font-mono font-medium border transition-all disabled:opacity-50',
              running
                ? 'text-neon-red border-neon-red/30 hover:bg-neon-red/10'
                : 'text-neon-green border-neon-green/30 hover:bg-neon-green/10'
            )}
          >
            {running ? <PowerOff className="h-3 w-3" /> : <Power className="h-3 w-3" />}
            {toggling ? '...' : running ? 'Stop' : 'Start'}
          </button>
        </div>

        {stats?.dynamic_tf?.reason && (
          <p className="text-[9px] font-mono text-text-muted leading-snug px-0.5">
            MTF: {(stats.dynamic_tf.confirmation_tfs ?? []).join(' + ') || '—'} · {stats.dynamic_tf.reason}
          </p>
        )}

        {/* Trade stats */}
        <div className="grid grid-cols-4 gap-1.5">
          <div className="rounded bg-bg-secondary/40 border border-border-primary/15 p-1.5 text-center">
            <p className="text-[9px] font-mono text-text-muted uppercase">Proposed</p>
            <p className="text-sm font-bold font-mono text-text-primary">{stats?.trades_proposed ?? 0}</p>
          </div>
          <div className="rounded bg-bg-secondary/40 border border-border-primary/15 p-1.5 text-center">
            <p className="text-[9px] font-mono text-neon-green uppercase">Approved</p>
            <p className="text-sm font-bold font-mono text-neon-green">{stats?.trades_approved ?? 0}</p>
          </div>
          <div className="rounded bg-bg-secondary/40 border border-border-primary/15 p-1.5 text-center">
            <p className="text-[9px] font-mono text-neon-red uppercase">Rejected</p>
            <p className="text-sm font-bold font-mono text-neon-red">{stats?.trades_rejected ?? 0}</p>
          </div>
          <div className="rounded bg-bg-secondary/40 border border-border-primary/15 p-1.5 text-center">
            <p className="text-[9px] font-mono text-text-muted uppercase">Rate</p>
            <p className="text-sm font-bold font-mono text-neon-cyan">{approvalRate}%</p>
          </div>
        </div>

        {/* Last Signal */}
        {sig.direction && (
          <div className="rounded bg-bg-secondary/40 border border-border-primary/15 p-2">
            <p className="text-[9px] font-mono text-text-muted uppercase mb-1.5">Last ML Signal</p>
            <div className="flex items-center gap-2 mb-1.5">
              <span className="text-[10px] font-mono text-text-secondary">{sig.symbol}</span>
              {db && (
                <span className={cn('inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-mono font-bold border', db.cls)}>
                  {db.icon} {sig.direction}
                </span>
              )}
              <span className="text-[10px] font-mono text-neon-cyan">
                conf={((sig.confidence ?? 0) * 100).toFixed(0)}%
              </span>
            </div>
            <div className="grid grid-cols-3 gap-1.5 text-[9px] font-mono text-text-muted">
              <span>SL: <span className="text-text-secondary">{sig.sl_pips?.toFixed(1)}</span></span>
              <span>TP: <span className="text-text-secondary">{sig.tp_pips?.toFixed(1)}</span></span>
              <span>Vol: <span className="text-text-secondary">{sig.vol_regime}</span></span>
              <span>Session: <span className="text-text-secondary">{sig.session}</span></span>
              <span>Mode: <span className="text-text-secondary">{sig.mode}</span></span>
              {sig.timestamp && (
                <span className="text-text-muted">{new Date(sig.timestamp).toLocaleTimeString()}</span>
              )}
            </div>
          </div>
        )}

        {/* Model status */}
        {traders && (
          <div className="grid grid-cols-3 gap-1.5">
            {(['ml', 'dl', 'rl'] as const).map((key) => {
              const t = traders[key];
              const training = traders.training_in_progress?.[key];
              const wr = t.performance?.win_rate;
              return (
                <div key={key} className="rounded bg-bg-secondary/40 border border-border-primary/15 p-1.5">
                  <div className="flex items-center gap-1 mb-0.5">
                    <span className={cn(
                      'h-1.5 w-1.5 rounded-full',
                      t.is_fitted ? 'bg-neon-green' : training ? 'bg-neon-gold animate-pulse' : 'bg-neon-red'
                    )} />
                    <span className="text-[10px] font-mono font-semibold text-text-primary uppercase">{key}</span>
                  </div>
                  <p className="text-[9px] font-mono text-text-muted truncate">{t.model_name}</p>
                  {wr !== undefined && (
                    <p className="text-[9px] font-mono text-neon-cyan">
                      WR: {(wr * 100).toFixed(0)}%
                    </p>
                  )}
                  {training && (
                    <p className="text-[9px] font-mono text-neon-gold animate-pulse">Training...</p>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Errors */}
        {(stats?.errors ?? 0) > 0 && (
          <div className="flex items-center gap-1.5 text-[10px] font-mono text-neon-gold">
            <AlertCircle className="h-3 w-3" />
            {stats!.errors} errors logged
          </div>
        )}

        {/* Last cycle */}
        {stats?.last_cycle && (
          <p className="text-[9px] font-mono text-text-muted text-right">
            Last cycle: {new Date(stats.last_cycle).toLocaleTimeString()}
          </p>
        )}
      </div>
    </NeonCard>
  );
}
