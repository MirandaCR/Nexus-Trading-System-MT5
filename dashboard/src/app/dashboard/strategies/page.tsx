'use client';

import { useState, useMemo } from 'react';
import {
  BrainCircuit,
  ChevronDown,
  ChevronUp,
  TrendingUp,
  Shield,
  Zap,
  Activity,
  RefreshCw,
  Sparkles,
  AlertCircle,
  Clock,
  FlaskConical,
  Trophy,
  Target,
  BarChart3,
  ArrowUpRight,
  ArrowDownRight,
  Pause,
  Play,
  Eye,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useStrategies } from '@/hooks/useStrategies';
import type { StrategyDTO, ResearchRunDTO } from '@/lib/api-client';

type StrategyStatus = 'active' | 'backtested' | 'investigating' | 'rejected' | 'paused';
type SortKey = 'profit_factor' | 'win_rate' | 'sharpe' | 'max_dd_pct' | 'net_pnl_pct' | 'total_trades' | 'avg_rr';

const STATUS_META: Record<string, { label: string; color: string; icon: typeof Play }> = {
  active:        { label: 'ACTIVE',        color: 'text-neon-green bg-neon-green/10 border-neon-green/30',   icon: Zap },
  backtested:    { label: 'BACKTESTED',    color: 'text-neon-gold  bg-neon-gold/10  border-neon-gold/30',    icon: BarChart3 },
  investigating: { label: 'INVESTIGATING', color: 'text-neon-cyan  bg-neon-cyan/10  border-neon-cyan/30',    icon: Eye },
  rejected:      { label: 'REJECTED',      color: 'text-neon-red   bg-neon-red/10   border-neon-red/30',     icon: AlertCircle },
  paused:        { label: 'PAUSED',        color: 'text-text-muted bg-bg-tertiary   border-border-primary/20', icon: Pause },
};

function formatPct(v: number, mult100 = false): string {
  const val = mult100 ? v * 100 : v;
  return `${val >= 0 ? '+' : ''}${val.toFixed(1)}%`;
}

function KPICell({ label, value, good, sub }: { label: string; value: string; good: boolean; sub?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[9px] font-mono text-text-muted uppercase">{label}</span>
      <span className={cn('text-sm font-bold font-mono', good ? 'text-neon-green' : 'text-neon-red')}>{value}</span>
      {sub && <span className="text-[9px] text-text-muted truncate">{sub}</span>}
    </div>
  );
}

function MiniEquityCurve({ results }: { results: boolean[] }) {
  if (!results || results.length === 0) return null;
  const w = 100;
  const h = 28;
  let equity = 0;
  const pts = results.map((r, i) => {
    equity += r ? 1 : -1;
    return { x: (i / Math.max(results.length - 1, 1)) * w, y: h / 2 - (equity / Math.max(results.length * 0.3, 1)) * (h / 2 - 4) };
  });
  const d = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
  const finalUp = equity > 0;

  return (
    <svg width={w} height={h} className="opacity-80">
      <path d={d} fill="none" stroke={finalUp ? '#00ff88' : '#ff2255'} strokeWidth="1.5" />
      <line x1="0" y1={h / 2} x2={w} y2={h / 2} stroke="#333" strokeWidth="0.5" strokeDasharray="2,2" />
    </svg>
  );
}

function StrategyRow({
  strategy,
  rank,
  selected,
  onSelect,
  busy,
  onReevaluate,
  onPromote,
}: {
  strategy: StrategyDTO;
  rank: number;
  selected: boolean;
  onSelect: () => void;
  busy?: boolean;
  onReevaluate?: () => void;
  onPromote?: () => void;
}) {
  const status = STATUS_META[strategy.status] || STATUS_META.investigating;
  const wr = strategy.win_rate > 1 ? strategy.win_rate : strategy.win_rate * 100;
  const isTop3 = rank <= 3;

  return (
    <div
      className={cn(
        'rounded-lg border bg-bg-primary/60 backdrop-blur-sm transition-all cursor-pointer hover:bg-bg-secondary/40',
        selected ? 'ring-1 ring-neon-purple/40 border-neon-purple/30' : 'border-border-primary/15',
      )}
      onClick={onSelect}
    >
      <div className="grid grid-cols-[32px_1fr_80px_repeat(6,56px)_72px] items-center gap-1.5 px-2 py-2 min-w-[700px]">
        {/* Rank */}
        <div className={cn(
          'flex items-center justify-center h-7 w-7 rounded-full text-[10px] font-bold font-mono',
          isTop3 ? 'bg-neon-gold/15 text-neon-gold border border-neon-gold/30' : 'bg-bg-tertiary text-text-muted',
        )}>
          {isTop3 ? <Trophy className="h-3 w-3" /> : `#${rank}`}
        </div>

        {/* Name + status */}
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-text-primary truncate">{strategy.name}</span>
            <span className={cn('text-[8px] font-mono px-1.5 py-0.5 rounded border shrink-0', status.color)}>
              {status.label}
            </span>
          </div>
          <div className="flex items-center gap-1.5 mt-0.5">
            <span className="text-[9px] text-text-muted">{strategy.type}</span>
            <span className="text-[9px] text-neon-purple font-mono">{(strategy.pairs || []).join(', ')}</span>
          </div>
        </div>

        {/* Mini equity curve */}
        <div className="flex justify-center">
          <MiniEquityCurve results={strategy.recent_results || []} />
        </div>

        {/* KPIs */}
        <KPICell label="Win Rate" value={`${wr.toFixed(1)}%`} good={wr >= 55} />
        <KPICell label="P.Factor" value={(strategy.profit_factor || 0).toFixed(2)} good={(strategy.profit_factor || 0) >= 1.5} />
        <KPICell label="Sharpe" value={(strategy.sharpe || 0).toFixed(2)} good={(strategy.sharpe || 0) >= 1.0} />
        <KPICell label="Max DD" value={`${(strategy.max_dd_pct || 0).toFixed(1)}%`} good={(strategy.max_dd_pct || 0) <= 10} />
        <KPICell label="Avg RR" value={(strategy.avg_rr || 0).toFixed(2)} good={(strategy.avg_rr || 0) >= 1.5} />
        <KPICell label="Net PnL" value={formatPct(strategy.net_pnl_pct || 0)} good={(strategy.net_pnl_pct || 0) > 0} />

        {/* Actions */}
        <div className="flex items-center gap-1 justify-end">
          {onReevaluate && (
            <button
              onClick={(e) => { e.stopPropagation(); onReevaluate(); }}
              disabled={busy}
              className="p-1.5 rounded text-text-muted hover:text-neon-cyan hover:bg-neon-cyan/10 disabled:opacity-40 transition-all"
              title="Re-evaluate"
            >
              <RefreshCw className={cn('h-3.5 w-3.5', busy && 'animate-spin')} />
            </button>
          )}
          {onPromote && (
            <button
              onClick={(e) => { e.stopPropagation(); onPromote(); }}
              disabled={busy}
              className={cn(
                'p-1.5 rounded transition-all disabled:opacity-40',
                strategy.status === 'active'
                  ? 'text-neon-red hover:bg-neon-red/10'
                  : 'text-neon-green hover:bg-neon-green/10',
              )}
              title={strategy.status === 'active' ? 'Pause' : 'Activate'}
            >
              {strategy.status === 'active' ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
            </button>
          )}
        </div>
      </div>

      {/* Expanded detail */}
      {selected && (
        <div className="px-4 pb-4 border-t border-border-primary/10 pt-3 space-y-3">
          <p className="text-xs text-text-secondary leading-relaxed">{strategy.description}</p>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <p className="text-[9px] text-text-muted font-semibold uppercase tracking-wider mb-1">Entry Logic</p>
              <p className="text-[11px] text-text-secondary font-mono leading-relaxed">{strategy.entry_logic || '—'}</p>
            </div>
            <div>
              <p className="text-[9px] text-text-muted font-semibold uppercase tracking-wider mb-1">Exit Logic</p>
              <p className="text-[11px] text-text-secondary font-mono leading-relaxed">{strategy.exit_logic || '—'}</p>
            </div>
          </div>

          <div className="grid grid-cols-4 gap-3">
            <div className="bg-bg-secondary/60 rounded-lg px-3 py-2">
              <p className="text-[9px] text-text-muted mb-0.5">Total Trades</p>
              <p className="text-sm font-bold font-mono text-text-primary">{strategy.total_trades || 0}</p>
            </div>
            <div className="bg-bg-secondary/60 rounded-lg px-3 py-2">
              <p className="text-[9px] text-text-muted mb-0.5">Timeframes</p>
              <p className="text-[11px] font-mono text-neon-cyan">{(strategy.timeframes || []).join(', ') || '—'}</p>
            </div>
            <div className="bg-bg-secondary/60 rounded-lg px-3 py-2">
              <p className="text-[9px] text-text-muted mb-0.5">Agents</p>
              <p className="text-[11px] font-mono text-neon-purple">{(strategy.agents || []).join(', ') || '—'}</p>
            </div>
            <div className="bg-bg-secondary/60 rounded-lg px-3 py-2">
              <p className="text-[9px] text-text-muted mb-0.5">Proposed By</p>
              <p className="text-[11px] font-mono text-neon-gold">{strategy.proposed_by || 'A12'}</p>
            </div>
          </div>

          {strategy.promotion_reasons && strategy.promotion_reasons.length > 0 && (
            <div>
              <p className="text-[9px] text-text-muted font-semibold uppercase tracking-wider mb-1">
                {strategy.status === 'rejected' ? 'Rejection Reasons' : 'Promotion Reasons'}
              </p>
              <div className="flex flex-wrap gap-1">
                {strategy.promotion_reasons.map((r, i) => (
                  <span key={i} className={cn(
                    'text-[9px] font-mono px-1.5 py-0.5 rounded border',
                    strategy.status === 'rejected' ? 'text-neon-red border-neon-red/20 bg-neon-red/5' : 'text-neon-green border-neon-green/20 bg-neon-green/5',
                  )}>
                    {r}
                  </span>
                ))}
              </div>
            </div>
          )}

          <div className="text-[9px] text-text-muted font-mono">
            Created: {strategy.created_at ? new Date(strategy.created_at * 1000).toLocaleString() : '—'}
            {' · '}
            Updated: {strategy.updated_at ? new Date(strategy.updated_at * 1000).toLocaleString() : '—'}
          </div>
        </div>
      )}
    </div>
  );
}

function ResearchLogEntry({ run }: { run: ResearchRunDTO }) {
  const [expanded, setExpanded] = useState(false);
  const accepted = run.accepted ?? [];
  const rejected = run.rejected ?? [];

  return (
    <div className="rounded-lg border border-border-primary/10 bg-bg-primary/40">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-3 py-2 text-left"
      >
        <Clock className="h-3 w-3 text-text-muted shrink-0" />
        <span className="text-[11px] font-mono text-text-muted">
          {run.ts ? new Date(run.ts * 1000).toLocaleString() : '—'}
        </span>
        <span className="text-[11px] font-mono text-neon-green">{accepted.length} accepted</span>
        <span className="text-[11px] text-text-muted">·</span>
        <span className="text-[11px] font-mono text-neon-red">{rejected.length} rejected</span>
        <span className="text-[11px] text-text-muted">·</span>
        <span className="text-[11px] font-mono text-text-muted">{run.duration_ms ?? 0}ms</span>
        <div className="flex-1" />
        {expanded ? <ChevronUp className="h-3 w-3 text-text-muted" /> : <ChevronDown className="h-3 w-3 text-text-muted" />}
      </button>

      {expanded && (accepted.length > 0 || rejected.length > 0) && (
        <div className="px-3 pb-3 space-y-2 border-t border-border-primary/10 pt-2">
          {accepted.map((s, i) => (
            <div key={i} className="flex items-center gap-3 text-[10px] font-mono">
              <ArrowUpRight className="h-3 w-3 text-neon-green shrink-0" />
              <span className="text-text-primary truncate flex-1">{s.name}</span>
              <span className="text-neon-green">PF={s.profit_factor?.toFixed(2)}</span>
              <span className="text-text-muted">WR={(s.win_rate > 1 ? s.win_rate : s.win_rate * 100).toFixed(0)}%</span>
              <span className="text-neon-cyan">S={s.sharpe?.toFixed(2)}</span>
            </div>
          ))}
          {rejected.map((s, i) => (
            <div key={i} className="flex items-center gap-3 text-[10px] font-mono">
              <ArrowDownRight className="h-3 w-3 text-neon-red shrink-0" />
              <span className="text-text-muted truncate flex-1">{s.name}</span>
              <span className="text-neon-red">PF={s.profit_factor?.toFixed(2)}</span>
              <span className="text-text-muted">WR={(s.win_rate > 1 ? s.win_rate : s.win_rate * 100).toFixed(0)}%</span>
              {s.promotion_reasons?.map((r: string, ri: number) => (
                <span key={ri} className="text-neon-red/60">{r}</span>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function StrategiesPage() {
  const {
    strategies,
    log,
    rules,
    loading,
    error,
    lastUpdated,
    source,
    triggerResearch,
    reevaluate,
    setStatus,
    refresh,
  } = useStrategies(15_000);

  const [sortKey, setSortKey] = useState<SortKey>('profit_factor');
  const [sortAsc, setSortAsc] = useState(false);
  const [filterStatus, setFilterStatus] = useState<StrategyStatus | 'all'>('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);

  const sorted = useMemo(() => {
    const base = filterStatus === 'all' ? strategies : strategies.filter((s) => s.status === filterStatus);
    return [...base].sort((a, b) => {
      const va = (a as unknown as Record<string, number>)[sortKey] ?? 0;
      const vb = (b as unknown as Record<string, number>)[sortKey] ?? 0;
      return sortAsc ? va - vb : vb - va;
    });
  }, [strategies, sortKey, sortAsc, filterStatus]);

  const handleSort = (key: SortKey) => {
    if (key === sortKey) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
  };

  const handleResearch = async () => {
    setBusyAction('research');
    try { await triggerResearch(2); } finally { setBusyAction(null); }
  };

  const handleReevaluate = async (id: string) => {
    setBusyAction(id);
    try { await reevaluate(id); } finally { setBusyAction(null); }
  };

  const handlePromote = async (id: string, current: string) => {
    const next = current === 'active' ? 'paused' : 'active';
    setBusyAction(id);
    try { await setStatus(id, next); } finally { setBusyAction(null); }
  };

  const counts = useMemo(() => {
    const c = { total: strategies.length, active: 0, investigating: 0, backtested: 0, rejected: 0 };
    for (const s of strategies) {
      if (s.status === 'active') c.active++;
      else if (s.status === 'investigating') c.investigating++;
      else if (s.status === 'backtested') c.backtested++;
      else if (s.status === 'rejected') c.rejected++;
    }
    return c;
  }, [strategies]);

  const bestPF = useMemo(() => {
    if (strategies.length === 0) return null;
    return strategies.reduce((a, b) => (a.profit_factor || 0) > (b.profit_factor || 0) ? a : b);
  }, [strategies]);

  return (
    <div className="p-2 sm:p-4 space-y-3 sm:space-y-4 animate-fade-in w-full max-w-[1800px] mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3">
        <FlaskConical className="h-5 w-5 text-neon-purple" />
        <div className="flex-1">
          <h1 className="text-lg font-bold text-text-primary">Strategy Research Lab</h1>
          <p className="text-[11px] text-text-muted">
            Live research pipeline · AI proposes → backtest → evaluate → promote
          </p>
        </div>
        <div className="flex items-center gap-2">
          {source === 'unavailable' && (
            <span className="flex items-center gap-1 text-[10px] text-neon-red">
              <AlertCircle className="h-3 w-3" /> offline
            </span>
          )}
          <button
            onClick={refresh}
            className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-mono text-text-muted hover:text-text-secondary border border-border-primary/20 hover:border-border-primary/40 transition-all"
          >
            <RefreshCw className="h-3 w-3" /> refresh
          </button>
          <button
            onClick={handleResearch}
            disabled={busyAction === 'research'}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-[11px] font-semibold bg-neon-purple/10 border border-neon-purple/30 text-neon-purple hover:bg-neon-purple/20 transition-all disabled:opacity-50"
          >
            {busyAction === 'research' ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
            {busyAction === 'research' ? 'Researching...' : 'Run Research Cycle'}
          </button>
        </div>
      </div>

      {/* Summary row */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {[
          { label: 'Total Strategies', value: counts.total, color: 'text-neon-cyan', icon: Activity },
          { label: 'Active (Live)', value: counts.active, color: 'text-neon-green', icon: Zap },
          { label: 'Investigating', value: counts.investigating, color: 'text-neon-gold', icon: Eye },
          { label: 'Best P.Factor', value: bestPF ? (bestPF.profit_factor || 0).toFixed(2) : '—', color: 'text-neon-purple', icon: TrendingUp },
          { label: 'Research Runs', value: log.length, color: 'text-text-secondary', icon: FlaskConical },
        ].map((item) => {
          const Icon = item.icon;
          return (
            <div key={item.label} className="neon-card px-3 py-2.5 flex items-center gap-3">
              <Icon className={cn('h-4 w-4 shrink-0', item.color)} />
              <div>
                <p className="text-[9px] text-text-muted uppercase">{item.label}</p>
                <p className={cn('text-lg font-bold font-mono', item.color)}>{item.value}</p>
              </div>
            </div>
          );
        })}
      </div>

      {/* Filters & sorting */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-0.5 rounded-lg border border-border-primary/20 bg-bg-secondary p-0.5">
          {(['all', 'active', 'backtested', 'investigating', 'rejected'] as const).map((s) => (
            <button
              key={s}
              onClick={() => setFilterStatus(s)}
              className={cn(
                'px-2 py-1 rounded text-[10px] font-mono font-semibold transition-all capitalize',
                filterStatus === s ? 'bg-neon-purple/20 text-neon-purple' : 'text-text-muted hover:text-text-secondary',
              )}
            >
              {s === 'all' ? `All (${counts.total})` : `${s} (${counts[s as keyof typeof counts] ?? 0})`}
            </button>
          ))}
        </div>

        <div className="flex-1" />

        <span className="text-[9px] text-text-muted font-mono uppercase">Sort:</span>
        {([
          { key: 'profit_factor', label: 'P.Factor' },
          { key: 'win_rate',      label: 'Win Rate' },
          { key: 'sharpe',        label: 'Sharpe' },
          { key: 'max_dd_pct',    label: 'Max DD' },
          { key: 'net_pnl_pct',   label: 'PnL' },
          { key: 'total_trades',  label: 'Trades' },
        ] as { key: SortKey; label: string }[]).map(({ key, label }) => (
          <button
            key={key}
            onClick={() => handleSort(key)}
            className={cn(
              'px-1.5 py-0.5 rounded text-[9px] font-mono transition-all',
              sortKey === key
                ? 'bg-neon-cyan/10 text-neon-cyan border border-neon-cyan/20'
                : 'text-text-muted hover:text-text-secondary border border-transparent',
            )}
          >
            {label} {sortKey === key && (sortAsc ? '↑' : '↓')}
          </button>
        ))}
      </div>

      {/* Promotion thresholds */}
      {rules && (
        <div className="flex flex-wrap items-center gap-3 px-3 py-2 rounded-lg border border-border-primary/15 bg-bg-secondary/30">
          <Shield className="h-3.5 w-3.5 text-neon-gold shrink-0" />
          <span className="text-[10px] text-text-muted">Promotion thresholds:</span>
          {[
            { label: 'PF', value: `≥${rules.min_profit_factor?.toFixed(1)}` },
            { label: 'Win', value: `≥${((rules.min_win_rate ?? 0) * 100).toFixed(0)}%` },
            { label: 'Sharpe', value: `≥${rules.min_sharpe?.toFixed(1)}` },
            { label: 'MaxDD', value: `≤${rules.max_drawdown_pct?.toFixed(0)}%` },
            { label: 'Trades', value: `≥${rules.min_trades}` },
            { label: 'NetPnL', value: `≥${rules.min_net_pnl_pct?.toFixed(0)}%` },
          ].map((t) => (
            <span key={t.label} className="text-[10px] font-mono text-neon-gold/80">{t.label} {t.value}</span>
          ))}
        </div>
      )}

      {/* Strategy list — table header */}
      <div className="grid grid-cols-[40px_1fr_100px_repeat(6,70px)_90px] items-center gap-2 px-3 py-1.5 text-[8px] font-mono text-text-muted uppercase tracking-wider">
        <span>#</span>
        <span>Strategy</span>
        <span className="text-center">Equity</span>
        <span>Win Rate</span>
        <span>P.Factor</span>
        <span>Sharpe</span>
        <span>Max DD</span>
        <span>Avg RR</span>
        <span>Net PnL</span>
        <span className="text-right">Actions</span>
      </div>

      {/* Loading / empty states */}
      {loading && strategies.length === 0 && (
        <div className="text-center text-xs text-text-muted py-12 neon-card">
          <RefreshCw className="h-5 w-5 animate-spin mx-auto mb-2 text-neon-purple" />
          Loading strategies from registry...
        </div>
      )}
      {!loading && strategies.length === 0 && (
        <div className="text-center py-12 neon-card space-y-3">
          <FlaskConical className="h-8 w-8 mx-auto text-neon-purple/40" />
          <p className="text-sm text-text-muted">No strategies researched yet</p>
          <p className="text-xs text-text-muted">Click "Run Research Cycle" to have the AI propose and backtest strategies</p>
          <button
            onClick={handleResearch}
            disabled={busyAction === 'research'}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold bg-neon-purple/10 border border-neon-purple/30 text-neon-purple hover:bg-neon-purple/20 disabled:opacity-50"
          >
            <Sparkles className="h-4 w-4" /> Run Research Cycle
          </button>
        </div>
      )}

      {/* Strategy rows */}
      <div className="space-y-1.5 overflow-x-auto scrollbar-hide">
        {sorted.map((strategy, i) => (
          <StrategyRow
            key={strategy.id}
            strategy={strategy}
            rank={i + 1}
            selected={selectedId === strategy.id}
            onSelect={() => setSelectedId(selectedId === strategy.id ? null : strategy.id)}
            busy={busyAction === strategy.id}
            onReevaluate={() => handleReevaluate(strategy.id)}
            onPromote={() => handlePromote(strategy.id, strategy.status)}
          />
        ))}
      </div>

      {/* Research log */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <BrainCircuit className="h-4 w-4 text-neon-purple" />
          <span className="text-sm font-semibold text-text-primary">Research History</span>
          <span className="text-[10px] text-text-muted font-mono">{log.length} runs</span>
        </div>

        {log.length === 0 && (
          <div className="neon-card py-6 text-center text-xs text-text-muted">
            No research runs recorded yet. Trigger a cycle above.
          </div>
        )}

        <div className="space-y-1 max-h-[400px] overflow-y-auto">
          {log.map((run, i) => (
            <ResearchLogEntry key={i} run={run} />
          ))}
        </div>
      </div>

      {/* Last update footer */}
      {lastUpdated && (
        <div className="text-center text-[10px] text-text-muted font-mono py-2">
          Last updated: {new Date(lastUpdated).toLocaleString()} · auto-refresh every 15s
        </div>
      )}
    </div>
  );
}
