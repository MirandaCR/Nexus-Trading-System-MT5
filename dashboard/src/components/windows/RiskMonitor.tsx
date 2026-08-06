'use client';

import React from 'react';
import NeonCard from '../shared/NeonCard';
import { useRisk } from '@/hooks/useRisk';
import { ShieldAlert, Check, X, RotateCcw } from 'lucide-react';
import { cn } from '@/lib/utils';

function MiniGauge({
  label,
  value,
  max,
  unit,
  thresholds,
}: {
  label: string;
  value: number;
  max: number;
  unit: string;
  thresholds?: { warn: number; danger: number };
}) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  const warn = thresholds?.warn ?? 60;
  const danger = thresholds?.danger ?? 80;

  let color: string;
  if (pct >= danger) color = '#ff3b3b';
  else if (pct >= warn) color = '#ffd600';
  else color = '#39ff14';

  const isCritical = pct >= 80;

  const SIZE = 80;
  const cx = SIZE / 2;
  const cy = SIZE / 2;
  const radius = 30;
  const strokeW = 5;

  const arcStart = -135;
  const arcSweep = 270;
  const valAngle = arcStart + (pct / 100) * arcSweep;

  const toXY = (deg: number, r: number) => {
    const rad = ((deg - 90) * Math.PI) / 180;
    return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
  };

  const arcPath = (startDeg: number, endDeg: number, r: number) => {
    const s = toXY(startDeg, r);
    const e = toXY(endDeg, r);
    const large = endDeg - startDeg > 180 ? 1 : 0;
    return `M ${s.x} ${s.y} A ${r} ${r} 0 ${large} 1 ${e.x} ${e.y}`;
  };

  const bgArc = arcPath(arcStart, arcStart + arcSweep, radius);
  const valArc = pct > 0.5 ? arcPath(arcStart, valAngle, radius) : '';

  const needlePt = toXY(valAngle, radius);

  return (
    <div className={`flex flex-col items-center ${isCritical ? 'animate-neon-pulse' : ''}`}>
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="w-20 h-20 sm:w-[76px] sm:h-[76px]">
        <path d={bgArc} fill="none" stroke="#1a1a2e" strokeWidth={strokeW} strokeLinecap="round" />
        {valArc && (
          <path
            d={valArc}
            fill="none"
            stroke={color}
            strokeWidth={strokeW}
            strokeLinecap="round"
            style={{ filter: `drop-shadow(0 0 3px ${color})` }}
          />
        )}
        {pct > 0.5 && (
          <circle
            cx={needlePt.x} cy={needlePt.y} r={2.5}
            fill={color}
            style={{ filter: `drop-shadow(0 0 3px ${color})` }}
          />
        )}
        <text x={cx} y={cy - 1} textAnchor="middle" dominantBaseline="central"
          fill={color} fontSize="12" fontWeight="bold" fontFamily="monospace">
          {typeof value === 'number' && value % 1 !== 0 ? value.toFixed(1) : value}
        </text>
        <text x={cx} y={cy + 11} textAnchor="middle" fill="#606078" fontSize="7" fontFamily="monospace">
          / {max}{unit}
        </text>
      </svg>
      <span className="text-[9px] text-text-secondary text-center mt-0.5 uppercase tracking-wider leading-tight">
        {label}
      </span>
    </div>
  );
}

function TiltBadge({ level }: { level: 'NORMAL' | 'WARNING' | 'TILT' }) {
  const config = {
    NORMAL:  { color: '#39ff14', glow: 'rgba(57,255,20,0.5)',  bg: 'rgba(57,255,20,0.08)' },
    WARNING: { color: '#ffd600', glow: 'rgba(255,214,0,0.5)',  bg: 'rgba(255,214,0,0.08)' },
    TILT:    { color: '#ff3b3b', glow: 'rgba(255,59,59,0.5)',  bg: 'rgba(255,59,59,0.08)' },
  }[level];

  return (
    <div
      className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-md border transition-all ${
        level === 'TILT' ? 'animate-neon-pulse' : ''
      }`}
      style={{
        backgroundColor: config.bg,
        borderColor: config.color + '40',
      }}
    >
      <span
        className="h-2 w-2 rounded-full animate-pulse-dot"
        style={{
          backgroundColor: config.color,
          boxShadow: `0 0 6px ${config.glow}`,
        }}
      />
      <span
        className="text-[10px] font-bold font-mono tracking-wider"
        style={{ color: config.color, textShadow: `0 0 4px ${config.glow}` }}
      >
        {level}
      </span>
    </div>
  );
}

export function RiskMonitor({ className = '' }: { className?: string }) {
  const { risk, fullRisk, anyViolation, isConnected } = useRisk();
  const [tradeLimit, setTradeLimit] = React.useState<{ count: number; max: number; paused: boolean } | null>(null);
  const [resetting, setResetting] = React.useState(false);
  const [resetOk, setResetOk] = React.useState(false);

  const loadTradeLimit = React.useCallback(() => {
    // Relative URL — proxied same-origin to the gateway by next.config.js,
    // immune to CORS and stale NEXT_PUBLIC_API_URL bakes.
    return fetch(`/api/risk/settings`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d) return;
        setTradeLimit({
          count: Number(d.daily_trade_count ?? 0),
          max: Number(d.settings?.max_daily_trades ?? 5),
          paused: Boolean(d.trades_paused),
        });
      })
      .catch(() => {});
  }, []);

  React.useEffect(() => {
    loadTradeLimit();
    const id = setInterval(loadTradeLimit, 15_000);
    return () => clearInterval(id);
  }, [loadTradeLimit]);

  const resetDay = async () => {
    setResetting(true);
    setResetOk(false);
    try {
      const res = await fetch(`/api/risk/reset-day`, { method: 'POST' });
      if (!res.ok) throw new Error('reset failed');
      await loadTradeLimit();
      setResetOk(true);
      setTimeout(() => setResetOk(false), 3000);
    } catch {
      /* ignore — user can retry from Risk tab */
    } finally {
      setResetting(false);
    }
  };

  const limitHit = tradeLimit && tradeLimit.max > 0 && tradeLimit.count >= tradeLimit.max;

  // Unified "system will not trade" detection: any tripped circuit breaker
  // (daily loss, weekly DD, anti-tilt…), the daily trade cap, or a manual
  // pause. One banner, one reset button — the user must never have to infer
  // from silence that trading is halted.
  const violatedBreakers = fullRisk.circuitBreakers.filter((cb) => cb.status === 'violated');
  const isBlocked = violatedBreakers.length > 0 || Boolean(limitHit) || Boolean(tradeLimit?.paused);
  const blockReasons: string[] = [
    ...violatedBreakers.map((cb) => cb.detail ? `${cb.name}: ${cb.detail}` : cb.name),
    ...(limitHit ? [`Límite diario alcanzado (${tradeLimit!.count}/${tradeLimit!.max})`] : []),
    ...(tradeLimit?.paused ? ['Pausa manual activa'] : []),
  ];

  return (
    <NeonCard
      title="Risk Monitor"
      icon={<ShieldAlert size={14} />}
      accentColor="orange"
      pulseBorder={anyViolation || Boolean(limitHit)}
      borderColor={limitHit ? 'border-neon-gold/50' : anyViolation ? 'border-neon-red/60' : undefined}
      className={className}
      headerRight={
        <div className="flex items-center gap-2">
          <TiltBadge level={risk.tiltLevel} />
          {!isConnected && (
            <span className="text-[9px] font-mono text-neon-gold/60 uppercase tracking-wider">offline</span>
          )}
        </div>
      }
    >
      <div className="space-y-3 h-full flex flex-col overflow-y-auto min-h-0">
        {isBlocked && (
          <div
            className={cn(
              'rounded-md border px-2.5 py-2 flex items-start justify-between gap-2',
              violatedBreakers.length > 0
                ? 'border-neon-red/50 bg-neon-red/10'
                : 'border-neon-gold/40 bg-neon-gold/10',
            )}
          >
            <div className="min-w-0">
              <span
                className={cn(
                  'block text-[10px] font-mono font-bold uppercase tracking-wider',
                  violatedBreakers.length > 0 ? 'text-neon-red' : 'text-neon-gold',
                )}
              >
                🛑 Sistema bloqueado — no operará
              </span>
              <ul className="mt-1 space-y-0.5">
                {blockReasons.map((r) => (
                  <li key={r} className="text-[10px] font-mono text-text-secondary leading-snug break-words">
                    • {r}
                  </li>
                ))}
              </ul>
            </div>
            <button
              type="button"
              onClick={resetDay}
              disabled={resetting}
              title="Re-basea el día: limpia pérdida diaria, contadores, breakers, anti-tilt, bloqueos direccionales y pausa ergódica"
              className={cn(
                'shrink-0 flex items-center gap-1 rounded px-2 py-1 text-[10px] font-bold font-mono border transition-all',
                resetOk
                  ? 'bg-neon-green/15 border-neon-green/40 text-neon-green'
                  : violatedBreakers.length > 0
                    ? 'bg-neon-red/20 border-neon-red/50 text-neon-red hover:bg-neon-red/30'
                    : 'bg-neon-gold/20 border-neon-gold/50 text-neon-gold hover:bg-neon-gold/30',
              )}
            >
              <RotateCcw className={cn('h-3 w-3', resetting && 'animate-spin')} />
              {resetOk ? 'OK' : resetting ? '…' : 'Reset Todo'}
            </button>
          </div>
        )}
        {/* Session info — compact row */}
        <div className="flex items-center justify-between flex-wrap gap-x-3 gap-y-1 text-[10px] font-mono">
          <div className="flex items-center gap-1.5">
            <span className="text-text-muted">Session</span>
            <span className="text-neon-cyan font-semibold">{fullRisk.currentSession}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-text-muted">Nexus pos</span>
            <span className="text-neon-green font-semibold">{fullRisk.positionCount}</span>
            <span className="text-text-muted">/</span>
            <span className="text-text-secondary font-semibold">{fullRisk.positionMax}</span>
          </div>
          {fullRisk.consecutiveLosses > 0 && (
            <div className="flex items-center gap-1">
              <span className="text-neon-gold/80">
                {fullRisk.consecutiveLosses} consec. losses
                {fullRisk.consecutiveLosses >= 6 ? ' — 50% size' : ''}
              </span>
            </div>
          )}
        </div>

        {/* Gauges — responsive: 2×2 on mobile, single row on sm+ */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 justify-items-center">
          <MiniGauge label="Daily Loss" value={risk.dailyLossUsed} max={risk.dailyLossMax} unit="%" />
          <MiniGauge label="Weekly DD" value={risk.weeklyDDUsed} max={risk.weeklyDDMax} unit="%" />
          <MiniGauge
            label={`Positions`}
            value={risk.positionCount}
            max={risk.positionMax}
            unit=""
          />
          <MiniGauge label="Correlation" value={risk.correlationExposure} max={risk.correlationMax} unit="" />
        </div>

        {/* Circuit Breakers — wrapping grid */}
        <div>
          <div className="text-[10px] text-text-muted uppercase tracking-wider mb-1.5">Circuit Breakers</div>
          <div className="flex flex-wrap gap-1">
            {risk.circuitBreakers.map((cb) => (
              <div
                key={cb.name}
                className={`flex items-center gap-1 px-2 py-1 rounded text-[10px] font-mono transition-colors ${
                  cb.status === 'violated'
                    ? 'bg-neon-red/10 border border-neon-red/30 text-neon-red'
                    : 'bg-bg-secondary text-text-secondary'
                }`}
              >
                {cb.status === 'ok' ? (
                  <Check size={10} className="text-neon-green shrink-0" />
                ) : (
                  <X size={10} className="text-neon-red shrink-0" />
                )}
                <span className="whitespace-nowrap">{cb.name}</span>
                {cb.detail && (
                  <span className="text-[9px] text-neon-red/70 ml-1 shrink-0">{cb.detail}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </NeonCard>
  );
}
