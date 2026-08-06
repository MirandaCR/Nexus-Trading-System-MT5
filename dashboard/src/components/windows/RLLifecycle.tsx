'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { Bot, TrendingUp, TrendingDown, Pause, Shield, Scissors, Activity } from 'lucide-react';
import { cn } from '@/lib/utils';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

interface LifecycleAction {
  ticket: number;
  symbol: string;
  side: string;
  action: string;
  color: string;
  probs: Record<string, number>;
  pnl_pips: number;
  bars_held: number;
  regime: string;
  confidence: number;
  entry_price: number;
  current_price: number;
  lot_size: number;
  model: string;
  evaluated_at: string;
}

const ACTION_CONFIG: Record<string, {
  label: string;
  desc: string;
  color: string;
  bgColor: string;
  borderColor: string;
  icon: React.ElementType;
}> = {
  HOLD: {
    label: 'HOLD',
    desc: 'Mantener posición',
    color: 'text-text-muted',
    bgColor: 'bg-bg-tertiary/40',
    borderColor: 'border-border-primary',
    icon: Pause,
  },
  CLOSE_NOW: {
    label: 'CERRAR YA',
    desc: 'Salir inmediatamente',
    color: 'text-neon-red',
    bgColor: 'bg-neon-red/5',
    borderColor: 'border-neon-red/30',
    icon: Activity,
  },
  MOVE_SL_BREAKEVEN: {
    label: 'BREAKEVEN SL',
    desc: 'Mover SL a entrada',
    color: 'text-yellow-400',
    bgColor: 'bg-yellow-400/5',
    borderColor: 'border-yellow-400/30',
    icon: Shield,
  },
  TAKE_PARTIAL_50PCT: {
    label: 'PARCIAL 50%',
    desc: 'Cerrar mitad de la posición',
    color: 'text-neon-green',
    bgColor: 'bg-neon-green/5',
    borderColor: 'border-neon-green/30',
    icon: Scissors,
  },
  TRAIL_SL_1ATR: {
    label: 'TRAIL 1×ATR',
    desc: 'Mover SL 1 ATR tras precio',
    color: 'text-neon-cyan',
    bgColor: 'bg-neon-cyan/5',
    borderColor: 'border-neon-cyan/30',
    icon: TrendingUp,
  },
};

export default function RLLifecycle() {
  const [actions, setActions] = useState<LifecycleAction[]>([]);
  const [age, setAge] = useState(0);
  const [lastEval, setLastEval] = useState<string | null>(null);

  const fetch_ = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/intelligence/lifecycle-actions`);
      if (res.ok) {
        const d = await res.json();
        const list = d.actions || [];
        setActions(list);
        if (list.length > 0) setLastEval(list[0].evaluated_at);
      }
    } catch {}
  }, []);

  useEffect(() => {
    fetch_();
    const t = setInterval(fetch_, 5000);
    return () => clearInterval(t);
  }, [fetch_]);

  useEffect(() => {
    if (!lastEval) return;
    const u = () => setAge(Math.floor((Date.now() - new Date(lastEval).getTime()) / 1000));
    u();
    const t = setInterval(u, 1000);
    return () => clearInterval(t);
  }, [lastEval]);

  const hasActions = actions.length > 0;
  const nonHold = actions.filter((a) => a.action !== 'HOLD');

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border-primary/50">
        <Bot className="h-3.5 w-3.5 text-neon-purple" />
        <span className="text-[11px] font-mono font-bold text-text-secondary tracking-wider">RL LIFECYCLE MANAGER</span>
        <span className="ml-1 text-[8px] font-mono text-text-muted px-1.5 py-0.5 rounded border border-border-primary/50">
          PPO pos-manager
        </span>
        {lastEval && (
          <span className="ml-auto text-[9px] font-mono text-text-muted">{age}s ago</span>
        )}
      </div>

      {/* Summary bar */}
      {hasActions && (
        <div className="flex gap-3 px-3 py-1.5 border-b border-border-primary/30">
          <div className="text-[9px] font-mono text-text-muted">{actions.length} posiciones activas</div>
          {nonHold.length > 0 && (
            <div className="text-[9px] font-mono text-neon-purple font-bold">
              ⚡ {nonHold.length} acción{nonHold.length > 1 ? 'es' : ''} recomendada{nonHold.length > 1 ? 's' : ''}
            </div>
          )}
        </div>
      )}

      {/* Position cards */}
      <div className="flex-1 overflow-y-auto px-3 py-2 flex flex-col gap-2">
        {!hasActions ? (
          <div className="flex flex-col items-center justify-center gap-2 h-full text-text-muted">
            <Bot className="h-8 w-8 opacity-20" />
            <span className="text-[10px] font-mono">Sin posiciones abiertas</span>
            <span className="text-[9px] font-mono text-text-muted/60">El RL lifecycle manager evaluará cada posición cada 30s</span>
          </div>
        ) : (
          actions.map((pos) => (
            <PositionCard key={pos.ticket} pos={pos} />
          ))
        )}
      </div>
    </div>
  );
}

function PositionCard({ pos }: { pos: LifecycleAction }) {
  const cfg = ACTION_CONFIG[pos.action] || ACTION_CONFIG.HOLD;
  const Icon = cfg.icon;
  const isWin = pos.pnl_pips > 0;
  const pnlColor = isWin ? 'text-neon-green' : pos.pnl_pips < 0 ? 'text-neon-red' : 'text-text-muted';
  const sortedProbs = Object.entries(pos.probs || {}).sort((a, b) => b[1] - a[1]);

  return (
    <div className={cn('border rounded-lg p-2.5', cfg.borderColor, cfg.bgColor)}>
      {/* Top row */}
      <div className="flex items-center gap-2 mb-2">
        {/* Direction icon */}
        {pos.side === 'BUY'
          ? <TrendingUp className="h-3 w-3 text-neon-green shrink-0" />
          : <TrendingDown className="h-3 w-3 text-neon-red shrink-0" />
        }
        <span className="text-[10px] font-mono font-bold text-text-primary">{pos.symbol}</span>
        <span className={cn('text-[9px] font-mono font-bold', pos.side === 'BUY' ? 'text-neon-green' : 'text-neon-red')}>
          {pos.side}
        </span>
        <span className="text-[8px] font-mono text-text-muted">#{pos.ticket}</span>

        {/* Action badge */}
        <div className={cn('flex items-center gap-1 ml-auto px-2 py-0.5 rounded border', cfg.borderColor, cfg.bgColor)}>
          <Icon className={cn('h-2.5 w-2.5', cfg.color)} />
          <span className={cn('text-[9px] font-mono font-bold', cfg.color)}>{cfg.label}</span>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-4 gap-1 mb-2">
        <MiniStat label="PnL" value={`${pos.pnl_pips >= 0 ? '+' : ''}${pos.pnl_pips}p`} color={pnlColor} />
        <MiniStat label="Bars" value={String(pos.bars_held)} color="text-text-secondary" />
        <MiniStat label="Conf" value={`${(pos.confidence * 100).toFixed(0)}%`}
          color={pos.confidence >= 0.7 ? 'text-neon-green' : pos.confidence >= 0.55 ? 'text-yellow-400' : 'text-text-muted'} />
        <MiniStat label="Modelo" value={pos.model === 'ppo' ? 'PPO' : 'RULE'} color="text-neon-purple" />
      </div>

      {/* Action description */}
      <div className="text-[8px] font-mono text-text-muted mb-1.5">{cfg.desc} · {pos.regime}</div>

      {/* Probability bars for top 3 actions */}
      <div className="flex flex-col gap-0.5">
        {sortedProbs.slice(0, 3).map(([action, prob]) => {
          const aCfg = ACTION_CONFIG[action] || ACTION_CONFIG.HOLD;
          return (
            <div key={action} className="flex items-center gap-1.5">
              <span className="text-[7px] font-mono text-text-muted w-20 truncate">{aCfg.label}</span>
              <div className="flex-1 h-1 bg-bg-tertiary rounded-full overflow-hidden">
                <div
                  className={cn('h-full rounded-full', aCfg.color.replace('text-', 'bg-'))}
                  style={{ width: `${prob * 100}%` }}
                />
              </div>
              <span className={cn('text-[7px] font-mono w-7 text-right', aCfg.color)}>
                {(prob * 100).toFixed(0)}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function MiniStat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="flex flex-col gap-0.5 bg-bg-secondary/30 rounded px-1.5 py-1">
      <span className="text-[7px] font-mono text-text-muted uppercase tracking-wider">{label}</span>
      <span className={cn('text-[9px] font-mono font-bold leading-none', color)}>{value}</span>
    </div>
  );
}
