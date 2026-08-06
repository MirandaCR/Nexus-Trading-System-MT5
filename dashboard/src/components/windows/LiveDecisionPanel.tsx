'use client';

import { useEffect, useState } from 'react';
import { Brain, Shield, Activity, RefreshCw, AlertTriangle } from 'lucide-react';
import NeonCard from '../shared/NeonCard';
import { apiClient } from '@/lib/api-client';
import type { LiveDecisionDTO } from '@/lib/api-client';
import { useDashboardStore } from '@/stores/dashboardStore';
import { cn } from '@/lib/utils';

const POLL_MS = 8_000;

function verdictTone(v: string): { label: string; cls: string } {
  const u = v.toUpperCase();
  if (u === 'BUY') return { label: 'BUY', cls: 'text-neon-green bg-neon-green/15 border-neon-green/30' };
  if (u === 'SELL') return { label: 'SELL', cls: 'text-neon-red bg-neon-red/15 border-neon-red/30' };
  if (u === 'WATCH') return { label: 'WATCH', cls: 'text-neon-cyan bg-neon-cyan/15 border-neon-cyan/30' };
  return { label: 'NO TRADE', cls: 'text-text-muted bg-bg-tertiary border-border-primary/30' };
}

function riskTone(decision: string): string {
  const u = (decision || '').toUpperCase();
  if (u === 'APPROVE') return 'text-neon-green';
  if (u === 'MODIFY') return 'text-neon-gold';
  if (u === 'REJECT') return 'text-neon-red';
  return 'text-text-muted';
}

export default function LiveDecisionPanel() {
  const symbol = useDashboardStore((s) => s.symbol);
  const [decision, setDecision] = useState<LiveDecisionDTO | null>(null);
  const [loading, setLoading] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);

  const refresh = async () => {
    if (!symbol) return;
    setLoading(true);
    try {
      const result = await apiClient.getLiveDecision(symbol);
      setDecision(result);
      setLastUpdated(Date.now());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'unable to fetch decision');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  const handleEvaluate = async () => {
    if (!symbol) return;
    setEvaluating(true);
    try {
      await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000'}/api/agents/evaluate`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ symbol, timeframe: 'M15' }),
        },
      ).catch(() => null);
      await new Promise((r) => setTimeout(r, 1500));
      await refresh();
    } finally {
      setEvaluating(false);
    }
  };

  const verdict = decision ? verdictTone(decision.verdict) : verdictTone('NO_TRADE');
  const conviction = decision?.conviction ?? 0;
  const cBar = Math.max(0, Math.min(100, conviction <= 1 ? conviction * 100 : conviction));

  return (
    <NeonCard
      title={`Live Decision — ${symbol || '—'}`}
      glow="cyan"
      icon={<Brain className="h-4 w-4" />}
      className="h-full flex flex-col"
    >
      <div className="flex flex-col gap-3 flex-1 min-h-0">
        {/* Header row with verdict — always visible, does not scroll */}
        <div className="flex items-center gap-3 shrink-0">
          <span className={cn('inline-flex items-center gap-1 rounded px-2.5 py-1 text-xs font-mono font-bold border', verdict.cls)}>
            <Activity className="h-3 w-3" /> {verdict.label}
          </span>
          {decision && (
            <span className="text-[11px] text-text-muted font-mono">
              cycle {decision.cycle_id?.slice(0, 8) ?? ''}
            </span>
          )}
          <div className="flex-1" />
          <button
            onClick={refresh}
            disabled={loading}
            className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-mono text-text-muted hover:text-text-secondary border border-transparent hover:border-border-primary/30 disabled:opacity-50 transition-all"
          >
            <RefreshCw className={cn('h-3 w-3', loading && 'animate-spin')} />
            refresh
          </button>
          <button
            onClick={handleEvaluate}
            disabled={evaluating}
            className="px-2.5 py-1 rounded text-[10px] font-medium bg-neon-cyan/10 border border-neon-cyan/30 text-neon-cyan hover:bg-neon-cyan/20 disabled:opacity-50 transition-all"
          >
            {evaluating ? 'Evaluating…' : 'Evaluate now'}
          </button>
        </div>

        {/* Everything below scrolls internally so long reasoning text is
            never clipped — the header above stays pinned. */}
        <div className="flex flex-col gap-3 flex-1 min-h-0 overflow-y-auto pr-1">

        {/* Conviction */}
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[10px] font-mono text-text-muted uppercase">Conviction</span>
            <span className="text-xs font-mono text-text-primary">{cBar.toFixed(0)}%</span>
            {decision?.session && (
              <span className="ml-auto text-[10px] text-text-muted font-mono">
                Session: <span className="text-text-secondary">{decision.session}</span>
              </span>
            )}
          </div>
          <div className="h-1.5 rounded-full bg-bg-tertiary overflow-hidden">
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${cBar}%`,
                backgroundColor:
                  verdict.label === 'BUY'
                    ? '#00ff88'
                    : verdict.label === 'SELL'
                      ? '#ff2255'
                      : '#aa88ff',
              }}
            />
          </div>
        </div>

        {/* Regime tag (if available from decision cache) */}
        {decision?.regime_analysis && (
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-mono text-text-muted">Regime:</span>
            <span className={cn(
              'text-[10px] font-mono font-bold',
              decision.regime_analysis.regime === 'TRENDING' ? 'text-neon-cyan' :
              decision.regime_analysis.regime === 'RANGING' ? 'text-neon-gold' :
              decision.regime_analysis.regime === 'BREAKOUT' ? 'text-neon-purple' : 'text-text-muted'
            )}>
              {decision.regime_analysis.regime ?? '?'}
            </span>
            <span className="text-[10px] font-mono text-text-muted">
              | Approach: {decision.regime_analysis.suggested_approach?.replace(/_/g, ' ') ?? '?'}
            </span>
          </div>
        )}

        {/* Reasoning */}
        <div className="rounded-md bg-bg-secondary/40 border border-border-primary/15 p-2.5">
          <p className="text-[10px] font-mono text-text-muted uppercase mb-1">A7 Arbitrator reasoning</p>
          <p className="text-[11px] text-text-secondary leading-relaxed whitespace-pre-wrap">
            {decision?.reasoning?.trim() || 'No reasoning yet — trigger an evaluation.'}
          </p>
        </div>

        {/* Risk */}
        <div className="grid grid-cols-3 gap-2">
          <div className="rounded-md bg-bg-secondary/40 border border-border-primary/15 p-2.5">
            <p className="text-[10px] font-mono text-text-muted uppercase mb-1 flex items-center gap-1">
              <Shield className="h-3 w-3" /> Risk
            </p>
            <p className={cn('text-xs font-bold font-mono', riskTone(decision?.risk_decision || ''))}>
              {decision?.risk_decision || '—'}
            </p>
          </div>
          <div className="rounded-md bg-bg-secondary/40 border border-border-primary/15 p-2.5">
            <p className="text-[10px] font-mono text-text-muted uppercase mb-1">Lot size</p>
            <p className="text-xs font-bold font-mono text-text-primary">
              {decision?.position_size ? Number(decision.position_size).toFixed(2) : '—'}
            </p>
          </div>
          <div className="rounded-md bg-bg-secondary/40 border border-border-primary/15 p-2.5">
            <p className="text-[10px] font-mono text-text-muted uppercase mb-1">Updated</p>
            <p className="text-xs font-mono text-text-secondary">
              {lastUpdated ? new Date(lastUpdated).toLocaleTimeString() : '—'}
            </p>
          </div>
        </div>

        {decision?.risk_reasoning && (
          <div className="rounded-md bg-bg-secondary/40 border border-border-primary/15 p-2.5">
            <p className="text-[10px] font-mono text-text-muted uppercase mb-1">A8 Risk Manager note</p>
            <p className="text-[11px] text-text-secondary leading-relaxed whitespace-pre-wrap">
              {decision.risk_reasoning}
            </p>
          </div>
        )}

        {!decision && !loading && (
          <div className="flex items-center gap-2 text-[11px] text-text-muted">
            <AlertTriangle className="h-3 w-3 text-neon-gold" />
            {error
              ? error
              : 'No decision cached yet for this symbol. Click "Evaluate now" to run the agent pipeline.'}
          </div>
        )}
        </div>
      </div>
    </NeonCard>
  );
}
