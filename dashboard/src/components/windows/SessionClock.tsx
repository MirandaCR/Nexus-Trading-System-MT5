'use client';

import { useEffect, useState, useMemo } from 'react';
import NeonCard from '../shared/NeonCard';

interface LocalSessionState {
  name: string;
  mode: string;
  active_agents: string[];
  max_trades: number;
  eligible_pairs: string[];
  volatility_mode: string;
  size_multiplier: number;
}

interface SessionDef {
  name: string;
  startHour: number;
  endHour: number;
  color: string;
  glow: string;
}

const SESSIONS: SessionDef[] = [
  { name: 'Sydney',   startHour: 22, endHour: 6,  color: '#4488ff', glow: 'rgba(68,136,255,0.3)' },
  { name: 'Tokyo',    startHour: 0,  endHour: 9,  color: '#00f5ff', glow: 'rgba(0,245,255,0.3)' },
  { name: 'London',   startHour: 7,  endHour: 16, color: '#39ff14', glow: 'rgba(57,255,20,0.3)' },
  { name: 'New York', startHour: 12, endHour: 21, color: '#bf40ff', glow: 'rgba(191,64,255,0.3)' },
];

const OVERLAP_ZONES = [
  { name: 'Sydney–Tokyo', startHour: 0, endHour: 6, color: '#ffd700' },
  { name: 'London–NY',    startHour: 12, endHour: 16, color: '#ffd700' },
];

function deriveSessionState(sessionName: string): LocalSessionState {
  const name = sessionName.toUpperCase();
  const baseAgents = ['A1', 'A2', 'A3', 'A5', 'A6', 'A7', 'A8', 'A9'];
  const defaults: Record<string, Partial<LocalSessionState>> = {
    'SYDNEY':   { max_trades: 2, size_multiplier: 0.6, eligible_pairs: ['XAU/USD', 'AUD/USD'] },
    'TOKYO':    { max_trades: 3, size_multiplier: 0.7, eligible_pairs: ['USD/JPY', 'XAU/USD', 'AUD/USD'] },
    'LONDON':   { max_trades: 5, size_multiplier: 1.0, eligible_pairs: ['EUR/USD', 'GBP/USD', 'XAU/USD', 'USD/CHF'] },
    'NEW YORK': { max_trades: 5, size_multiplier: 1.0, eligible_pairs: ['EUR/USD', 'XAU/USD', 'BTC/USD', 'USD/CAD'] },
    'LONDON/NY OVERLAP': { max_trades: 6, size_multiplier: 1.2, eligible_pairs: ['EUR/USD', 'GBP/USD', 'XAU/USD', 'BTC/USD'] },
  };
  const d = defaults[name] ?? { max_trades: 1, size_multiplier: 0.5, eligible_pairs: [] };
  return {
    name,
    mode: 'paper',
    active_agents: baseAgents,
    max_trades: d.max_trades ?? 1,
    eligible_pairs: d.eligible_pairs ?? [],
    volatility_mode: 'normal',
    size_multiplier: d.size_multiplier ?? 0.5,
  };
}

function getActiveSessions(utcHour: number): SessionDef[] {
  return SESSIONS.filter((s) => {
    if (s.startHour < s.endHour) return utcHour >= s.startHour && utcHour < s.endHour;
    return utcHour >= s.startHour || utcHour < s.endHour;
  });
}

function getNextSessionChange(now: Date): { name: string; seconds: number } {
  const utcH = now.getUTCHours();
  const utcM = now.getUTCMinutes();
  const utcS = now.getUTCSeconds();
  const currentMins = utcH * 60 + utcM;

  const boundaries = SESSIONS.flatMap((s) => [
    { hour: s.startHour, label: `${s.name} Open` },
    { hour: s.endHour, label: `${s.name} Close` },
  ]).sort((a, b) => a.hour - b.hour);

  for (const b of boundaries) {
    const bMins = b.hour * 60;
    if (bMins > currentMins) {
      return { name: b.label, seconds: (bMins - currentMins) * 60 - utcS };
    }
  }
  const first = boundaries[0];
  return { name: first.label, seconds: ((24 * 60 - currentMins) + first.hour * 60) * 60 - utcS };
}

function formatCountdown(totalSeconds: number): string {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

// ── Ring Clock SVG helpers ───────────────────────────────────────────────────

const CX = 100;
const CY = 100;
const RING_R = 80;
const RING_W = 14;
const INNER_R = RING_R - RING_W / 2 - 6;

function hourToAngle(hour: number): number {
  return (hour / 24) * 360 - 90;
}

function polarToXY(angleDeg: number, radius: number): { x: number; y: number } {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: CX + radius * Math.cos(rad), y: CY + radius * Math.sin(rad) };
}

function describeArc(startHour: number, endHour: number, radius: number): string {
  let duration = endHour - startHour;
  if (duration <= 0) duration += 24;

  const startAngle = hourToAngle(startHour);
  const endAngle = startAngle + (duration / 24) * 360;
  const start = polarToXY(startAngle, radius);
  const end = polarToXY(endAngle, radius);
  const largeArc = duration > 12 ? 1 : 0;

  // Round to a fixed precision so the path string is byte-identical between the
  // SSR (Node) and browser JS engines. Math.cos/sin can differ in their last
  // ULP across engines, which otherwise triggers a React hydration mismatch
  // warning on every session arc.
  const r3 = (v: number) => Math.round(v * 1000) / 1000;

  return `M ${r3(start.x)} ${r3(start.y)} A ${radius} ${radius} 0 ${largeArc} 1 ${r3(end.x)} ${r3(end.y)}`;
}

// ── Component ────────────────────────────────────────────────────────────────

export default function SessionClock({ compact = false, className = '' }: { compact?: boolean; className?: string }) {
  const [now, setNow] = useState<Date | null>(null);

  useEffect(() => {
    const tick = () => setNow(new Date());
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  const ready = now !== null;
  const utcHour = ready ? now.getUTCHours() : 0;
  const utcMinute = ready ? now.getUTCMinutes() : 0;
  const utcSecond = ready ? now.getUTCSeconds() : 0;
  const fractionalHour = utcHour + utcMinute / 60 + utcSecond / 3600;
  const needleAngle = hourToAngle(fractionalHour);

  const activeSessions = useMemo(
    () => (ready ? getActiveSessions(utcHour) : []),
    [ready, utcHour],
  );
  const primarySession = activeSessions[0] ?? null;
  const nextChange = useMemo(
    () => (ready && now ? getNextSessionChange(now) : { name: '—', seconds: 0 }),
    [ready, now],
  );
  const sessionState = useMemo(
    () => deriveSessionState(primarySession?.name ?? 'OFF_HOURS'),
    [primarySession?.name],
  );

  const utcString = ready
    ? now.toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
        timeZone: 'UTC',
      })
    : '--:--:--';

  const needleTip = polarToXY(needleAngle, RING_R + 2);
  const needleBase = polarToXY(needleAngle, INNER_R + 8);

  return (
    <NeonCard
      title="Session Clock"
      glow="cyan"
      noPadding
      icon={
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="10" />
          <polyline points="12 6 12 12 16 14" />
        </svg>
      }
      headerRight={
        <span className="text-xs font-mono text-neon-cyan" suppressHydrationWarning>
          {utcString} UTC
        </span>
      }
      className={`${compact ? '' : 'h-full'} flex flex-col overflow-hidden ${className}`}
    >
      <div className={`px-2 ${compact ? 'py-1.5' : 'px-3 pt-2 pb-3'} flex flex-col items-center gap-1.5 overflow-hidden`}>
        {/* Ring Clock */}
        <div className={`w-full relative ${compact ? 'max-w-[140px] aspect-square' : 'max-w-[200px] aspect-square'}`}>
          <svg viewBox="0 0 200 200" className="w-full h-full">
            {/* Track background */}
            <circle
              cx={CX} cy={CY} r={RING_R}
              fill="none" stroke="#1a1a2e" strokeWidth={RING_W}
            />

            {/* Session arcs */}
            {SESSIONS.map((s) => (
              <path
                key={s.name}
                d={describeArc(s.startHour, s.endHour, RING_R)}
                fill="none"
                stroke={s.color}
                strokeWidth={RING_W}
                strokeLinecap="butt"
                opacity={0.35}
              />
            ))}

            {/* Active session arcs — brighter */}
            {activeSessions.map((s) => (
              <path
                key={`active-${s.name}`}
                d={describeArc(s.startHour, s.endHour, RING_R)}
                fill="none"
                stroke={s.color}
                strokeWidth={RING_W}
                strokeLinecap="butt"
                opacity={0.85}
                style={{ filter: `drop-shadow(0 0 4px ${s.glow})` }}
              />
            ))}

            {/* Overlap highlight arcs */}
            {OVERLAP_ZONES.map((o) => (
              <path
                key={o.name}
                d={describeArc(o.startHour, o.endHour, RING_R)}
                fill="none"
                stroke={o.color}
                strokeWidth={RING_W + 2}
                strokeLinecap="butt"
                opacity={0.15}
              />
            ))}

            {/* Hour tick marks — every 3 hours */}
            {[0, 3, 6, 9, 12, 15, 18, 21].map((h) => {
              const angle = hourToAngle(h);
              const outer = polarToXY(angle, RING_R + RING_W / 2 + 2);
              const inner = polarToXY(angle, RING_R - RING_W / 2 - 2);
              return (
                <line
                  key={h}
                  x1={inner.x} y1={inner.y}
                  x2={outer.x} y2={outer.y}
                  stroke="#606078"
                  strokeWidth={1}
                />
              );
            })}

            {/* Hour labels inside ring */}
            {[0, 6, 12, 18].map((h) => {
              const pos = polarToXY(hourToAngle(h), INNER_R - 2);
              return (
                <text
                  key={h}
                  x={pos.x} y={pos.y}
                  textAnchor="middle" dominantBaseline="central"
                  fill="#606078" fontSize="9" fontFamily="monospace"
                >
                  {h.toString().padStart(2, '0')}
                </text>
              );
            })}

            {/* Current-time needle */}
            {ready && (
              <>
                <line
                  x1={needleBase.x} y1={needleBase.y}
                  x2={needleTip.x} y2={needleTip.y}
                  stroke="#ffffff"
                  strokeWidth={2}
                  strokeLinecap="round"
                  style={{ filter: 'drop-shadow(0 0 3px rgba(255,255,255,0.8))' }}
                />
                <circle
                  cx={needleTip.x} cy={needleTip.y} r={3}
                  fill="#ffffff"
                  style={{ filter: 'drop-shadow(0 0 4px rgba(255,255,255,0.9))' }}
                />
                <circle cx={CX} cy={CY} r={3} fill="#ffffff" opacity={0.6} />
              </>
            )}

            {/* Center: active session name */}
            {!ready ? (
              <text x={CX} y={CY} textAnchor="middle" dominantBaseline="central"
                fill="#606078" fontSize="14" fontFamily="monospace" fontWeight="bold">
                …
              </text>
            ) : primarySession ? (
              <>
                <text x={CX} y={CY - 8} textAnchor="middle" dominantBaseline="central"
                  fill={primarySession.color} fontSize="13" fontFamily="monospace" fontWeight="bold"
                  style={{ filter: `drop-shadow(0 0 6px ${primarySession.glow})` }}>
                  {primarySession.name.toUpperCase()}
                </text>
                <text x={CX} y={CY + 8} textAnchor="middle" dominantBaseline="central"
                  fill="#a0a0b8" fontSize="8" fontFamily="monospace">
                  {utcString} UTC
                </text>
                {activeSessions.length > 1 && (
                  <text x={CX} y={CY + 20} textAnchor="middle" dominantBaseline="central"
                    fill="#ffd700" fontSize="7" fontFamily="monospace">
                    + {activeSessions.slice(1).map((s) => s.name).join(', ')}
                  </text>
                )}
              </>
            ) : (
              <text x={CX} y={CY} textAnchor="middle" dominantBaseline="central"
                fill="#606078" fontSize="13" fontFamily="monospace" fontWeight="bold">
                OFF HOURS
              </text>
            )}
          </svg>
        </div>

        <div className="text-center w-full">
          <div className={`font-mono text-text-muted ${compact ? 'text-[9px]' : 'text-[10px]'}`}>
            Next: <span className="text-neon-cyan">{nextChange.name}</span>
          </div>
          <div className={`font-mono font-bold text-text-primary tabular-nums ${compact ? 'text-sm' : 'text-lg'}`} suppressHydrationWarning>
            {ready ? formatCountdown(nextChange.seconds) : '--:--:--'}
          </div>
        </div>

        {!compact && (
        <div className="flex items-center justify-center gap-2 flex-wrap">
          {SESSIONS.map((s) => {
            const isActive = activeSessions.some((a) => a.name === s.name);
            return (
              <div key={s.name} className="flex items-center gap-1">
                <span
                  className="h-2 w-2 rounded-sm shrink-0"
                  style={{
                    backgroundColor: s.color,
                    opacity: isActive ? 1 : 0.4,
                    boxShadow: isActive ? `0 0 6px ${s.glow}` : 'none',
                  }}
                />
                <span className={`text-[9px] font-mono ${isActive ? 'text-text-secondary' : 'text-text-muted'}`}>
                  {s.name}
                </span>
              </div>
            );
          })}
          <div className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-sm shrink-0" style={{ backgroundColor: '#ffd700', opacity: 0.5 }} />
            <span className="text-[9px] font-mono text-text-muted">Overlap</span>
          </div>
        </div>
        )}

        {!compact && (
        <div className="w-full border-t border-border-primary/10 pt-2 grid grid-cols-2 gap-x-3 gap-y-1.5">
          <div className="flex justify-between items-center">
            <span className="text-[10px] font-mono text-text-muted">Agents</span>
            <span className="text-[11px] font-mono text-neon-cyan font-semibold">{sessionState.active_agents.length}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-[10px] font-mono text-text-muted">Max Trades</span>
            <span className="text-[11px] font-mono text-neon-purple font-semibold">{sessionState.max_trades}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-[10px] font-mono text-text-muted">Size ×</span>
            <span className="text-[11px] font-mono text-neon-gold font-semibold">{sessionState.size_multiplier}x</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-[10px] font-mono text-text-muted">Volatility</span>
            <span className={`text-[11px] font-mono font-semibold ${
              sessionState.volatility_mode === 'high' ? 'text-neon-red' : sessionState.volatility_mode === 'low' ? 'text-neon-blue' : 'text-neon-green'
            }`}>
              {sessionState.volatility_mode.toUpperCase()}
            </span>
          </div>
          <div className="col-span-2">
            <span className="text-[10px] font-mono text-text-muted block mb-0.5">Pairs</span>
            <div className="flex flex-wrap gap-1">
              {sessionState.eligible_pairs.map((pair) => (
                <span key={pair} className="rounded bg-bg-tertiary px-1.5 py-0.5 text-[9px] font-mono text-text-secondary">
                  {pair}
                </span>
              ))}
            </div>
          </div>
        </div>
        )}
      </div>
    </NeonCard>
  );
}
