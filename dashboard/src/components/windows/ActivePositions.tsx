'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import NeonCard from '../shared/NeonCard';
import { apiClient } from '@/lib/api-client';
import { useDashboardStore } from '@/stores/dashboardStore';
import { useWebSocket } from '@/hooks/useWebSocket';
import { gatewayWsUrl } from '@/lib/gateway-ws';

interface LocalPosition {
  id: string;
  symbol: string;
  side: string;
  entry_price: number;
  current_price: number;
  sl: number;
  tp1: number;
  tp2?: number;
  tp3?: number;
  size: number;
  pnl_pips: number;
  pnl_usd: number;
  duration: number;
  conviction: number;
  status: string;
  opened_at: string;
  reasoning?: string;
}

function mapGatewayToLocal(row: Record<string, unknown>): LocalPosition {
  const sym = String(row.symbol ?? '').trim();
  const sideRaw = String(row.side ?? 'BUY').toUpperCase();
  const isLong = !sideRaw.includes('SELL');
  const entry = Number(row.entry_price ?? 0);
  const cur = Number(row.current_price ?? 0);
  let pipMult = 10_000;
  if (sym.includes('JPY')) pipMult = 100;
  else if (sym.includes('XAU') || sym.includes('XAG')) pipMult = 10;
  else if (sym.includes('BTC') || sym.includes('ETH')) pipMult = 1;

  const pipDelta =
    pipMult === 1
      ? (isLong ? cur - entry : entry - cur)
      : (isLong ? cur - entry : entry - cur) * pipMult;

  return {
    id: String(row.id ?? ''),
    symbol: sym,
    side: isLong ? 'BUY' : 'SELL',
    entry_price: entry,
    current_price: cur,
    sl: Number(row.sl ?? 0),
    tp1: Number(row.tp1 ?? row.tp ?? 0),
    tp2: row.tp2 != null ? Number(row.tp2) : undefined,
    tp3: row.tp3 != null ? Number(row.tp3) : undefined,
    size: Number(row.size ?? 0),
    pnl_pips: Number.isFinite(Number(row.pnl_pips)) ? Number(row.pnl_pips) : +pipDelta.toFixed(1),
    pnl_usd: Number(row.pnl_usd ?? 0),
    duration: Number(row.duration ?? 0),
    conviction: Number(row.conviction ?? 0) > 1 ? Number(row.conviction) / 100 : Number(row.conviction ?? 0.65),
    status: String(row.status ?? 'open'),
    opened_at: typeof row.opened_at === 'string' ? row.opened_at : '',
    reasoning: typeof row.reasoning === 'string' ? row.reasoning : undefined,
  };
}

function decimalsForSymbol(sym: string, price: number): number {
  if (sym.includes('JPY')) return 3;
  if (sym.includes('BTC') || sym.includes('ETH')) return 2;
  if (sym.includes('XAU') || sym.includes('XAG') || price >= 50) return 2;
  return 5;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function pnlColor(v: number): string {
  if (v > 0) return 'text-neon-green';
  if (v < 0) return 'text-neon-red';
  return 'text-text-muted';
}

function pnlGlow(v: number): string {
  if (v > 0) return '';
  if (v < 0) return '';
  return '';
}

function sideBadge(side: string) {
  const isLong = side === 'BUY';
  return (
    <span
      className={`inline-block rounded px-2 py-0.5 text-[10px] font-mono font-bold uppercase ${
        isLong
          ? 'bg-neon-green/10 text-neon-green'
          : 'bg-neon-red/10 text-neon-red'
      }`}
    >
      {isLong ? 'LONG' : 'SHORT'}
    </span>
  );
}

function convictionBar(c: number) {
  const color = c >= 0.7 ? '#00ff88' : c >= 0.5 ? '#ffcc00' : '#ff2255';
  return (
    <div className="flex items-center gap-1.5">
      <div className="h-1 w-12 rounded-full bg-bg-tertiary overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${c * 100}%`, backgroundColor: color }} />
      </div>
      <span className="text-[10px] font-mono" style={{ color }}>{(c * 100).toFixed(0)}%</span>
    </div>
  );
}

// ── Component ────────────────────────────────────────────────────────────────

export default function ActivePositions() {
  const [positions, setPositions] = useState<LocalPosition[]>([]);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [closingId, setClosingId] = useState<string | null>(null);
  const [liveConnected, setLiveConnected] = useState(false);
  const [dataSource, setDataSource] = useState('cache');
  const [accountBroker, setAccountBroker] = useState<string | null>(null);
  const [nexusCount, setNexusCount] = useState(0);
  const [externalCount, setExternalCount] = useState(0);
  const setAccount = useDashboardStore((s) => s.setAccount);

  const refresh = useCallback(async () => {
    try {
      const res = await apiClient.getPositionsSnapshot();
      const mapped = (res.data.positions as Record<string, unknown>[]).map(mapGatewayToLocal).filter((p) => p.id);
      setPositions(mapped);
      setLiveConnected(Boolean(res.data.live_connected));
      setDataSource(String(res.data.source ?? 'unknown'));
      setNexusCount(Number(res.data.nexus_count ?? 0));
      setExternalCount(Number(res.data.external_count ?? 0));
      const account = res.data.account;
      if (account) {
        setAccountBroker(typeof account.broker === 'string' ? account.broker.toUpperCase() : null);
        setAccount({
          balance: Number(account.balance ?? 0),
          equity: Number(account.equity ?? 0),
          margin: Number(account.margin ?? 0),
          freeMargin: Number(account.free_margin ?? 0),
          openPositions: mapped.length,
        });
      } else {
        setAccountBroker(null);
        setAccount({ openPositions: mapped.length });
      }
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load positions');
    }
  }, [setAccount]);

  // WebSocket-driven refresh: nexus-api's position_broadcaster pushes an
  // event on every price/PnL update and on close, so we refetch the full
  // snapshot immediately instead of waiting for the next poll tick. The
  // 45s interval below is only a safety net for a dropped/reconnecting
  // socket, not the primary freshness mechanism.
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;
  const wsDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const { isConnected: wsConnected } = useWebSocket({
    url: gatewayWsUrl('/positions'),
    onMessage: () => {
      // Multiple position updates often arrive back-to-back (one per open
      // position) — coalesce into a single refetch.
      if (wsDebounceRef.current) clearTimeout(wsDebounceRef.current);
      wsDebounceRef.current = setTimeout(() => refreshRef.current(), 250);
    },
  });

  useEffect(() => {
    refresh();
    const pollMs = wsConnected ? 45_000 : 8_000;
    const id = setInterval(refresh, pollMs);
    return () => clearInterval(id);
  }, [refresh, wsConnected]);

  const onClose = async (pos: LocalPosition, e: React.MouseEvent) => {
    e.stopPropagation();
    setClosingId(pos.id);
    try {
      await apiClient.closePosition(pos.id);
      await refresh();
    } catch {
      setError(`Could not close ${pos.symbol} (#${pos.id})`);
    } finally {
      setClosingId(null);
    }
  };

  const totalPnl = positions.reduce((sum, p) => sum + p.pnl_usd, 0);

  return (
    <NeonCard
      title="Active Positions"
      glow="green"
      noPadding
      icon={
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 2v20M2 12h20" />
        </svg>
      }
      headerRight={
        <div className="flex items-center gap-2 flex-wrap justify-end">
          <span className="text-[10px] font-mono text-neon-green" title="Posiciones con comment nexus — cuentan para risk">
            {nexusCount} nexus
          </span>
          {externalCount > 0 && (
            <span className="text-[10px] font-mono text-text-muted" title="Posiciones externas — ignoradas por risk">
              {externalCount} ext
            </span>
          )}
          <span className="text-xs font-mono text-text-muted">{positions.length} total</span>
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] font-mono ${
              liveConnected ? 'bg-neon-green/15 text-neon-green' : 'bg-neon-red/15 text-neon-red'
            }`}
            title={`source=${dataSource}`}
          >
            {liveConnected ? `LIVE${accountBroker ? `:${accountBroker}` : ''}` : 'OFFLINE'}
          </span>
        </div>
      }
      className="h-full flex flex-col"
    >
      {error && (
        <div className="px-3 py-2 text-[10px] font-mono text-neon-red border-b border-neon-red/20 bg-neon-red/5">
          {error}
        </div>
      )}
      {/* Table */}
      <div className="flex-1 overflow-auto scrollbar-hide">
        <table className="w-full text-[10px] font-mono border-collapse table-fixed">
          <colgroup>
            <col className="w-[72px]" />
            <col className="w-[52px]" />
            <col className="w-[78px]" />
            <col className="w-[78px]" />
            <col className="w-[62px]" />
            <col className="w-[72px]" />
            <col className="w-[72px]" />
            <col className="w-[72px]" />
            <col className="w-[50px]" />
            <col className="w-[70px]" />
            <col className="w-[90px]" />
          </colgroup>
          <thead>
            <tr className="border-b border-border-primary/15 text-text-muted text-[9px] uppercase tracking-wider">
              <th className="sticky top-0 z-10 bg-bg-card px-2 py-1.5 text-left font-semibold">Symbol</th>
              <th className="sticky top-0 z-10 bg-bg-card px-1 py-1.5 text-left font-semibold">Side</th>
              <th className="sticky top-0 z-10 bg-bg-card px-1 py-1.5 text-right font-semibold">Entry</th>
              <th className="sticky top-0 z-10 bg-bg-card px-1 py-1.5 text-right font-semibold">Current</th>
              <th className="sticky top-0 z-10 bg-bg-card px-1 py-1.5 text-right font-semibold">Pips</th>
              <th className="sticky top-0 z-10 bg-bg-card px-1 py-1.5 text-right font-semibold">P&L</th>
              <th className="sticky top-0 z-10 bg-bg-card px-1 py-1.5 text-right font-semibold">SL</th>
              <th className="sticky top-0 z-10 bg-bg-card px-1 py-1.5 text-right font-semibold">TP</th>
              <th className="sticky top-0 z-10 bg-bg-card px-1 py-1.5 text-right font-semibold">Time</th>
              <th className="sticky top-0 z-10 bg-bg-card px-1 py-1.5 text-center font-semibold">Conv</th>
              <th className="sticky top-0 z-10 bg-bg-card px-1 py-1.5 text-center font-semibold">Actions</th>
            </tr>
          </thead>
          <tbody>
            {positions.length === 0 && (
              <tr>
                <td colSpan={11} className="px-3 py-6 text-center text-[10px] text-text-muted">
                  No open positions. Live MT5 positions appear here when the executor is connected.
                </td>
              </tr>
            )}
            {positions.map((pos) => {
              const isProfit = pos.pnl_usd >= 0;
              const isExpanded = expandedId === pos.id;
              const tps = [pos.tp1, pos.tp2, pos.tp3].filter((v): v is number => v != null && v !== 0);
              const dec = decimalsForSymbol(pos.symbol, pos.entry_price);

              return (
                <React.Fragment key={pos.id}>
                  <tr
                    className={`border-b border-border-primary/5 cursor-pointer transition-colors hover:bg-bg-tertiary/30 ${isProfit ? 'bg-neon-green/[0.02]' : 'bg-neon-red/[0.02]'}`}
                    onClick={() => setExpandedId(isExpanded ? null : pos.id)}
                  >
                    <td className="px-2 py-1.5 text-text-primary font-semibold truncate">{pos.symbol}</td>
                    <td className="px-1 py-1.5">{sideBadge(pos.side)}</td>
                    <td className="px-1 py-1.5 text-right text-text-secondary tabular-nums">{pos.entry_price.toFixed(dec)}</td>
                    <td className="px-1 py-1.5 text-right text-text-primary tabular-nums">{pos.current_price.toFixed(dec)}</td>
                    <td className={`px-1 py-1.5 text-right font-bold tabular-nums ${pnlColor(pos.pnl_pips)}`}>
                      {pos.pnl_pips > 0 ? '+' : ''}{pos.pnl_pips.toFixed(1)}
                    </td>
                    <td className={`px-1 py-1.5 text-right font-bold tabular-nums ${pnlColor(pos.pnl_usd)}`}>
                      {pos.pnl_usd > 0 ? '+' : ''}${pos.pnl_usd.toFixed(2)}
                    </td>
                    <td className="px-1 py-1.5 text-right text-neon-red/70 tabular-nums">{pos.sl > 0 ? pos.sl.toFixed(dec) : '—'}</td>
                    <td className="px-1 py-1.5 text-right text-neon-cyan/70 tabular-nums truncate">
                      {tps.length === 0 ? '—' : tps[0].toFixed(dec)}
                    </td>
                    <td className="px-1 py-1.5 text-right text-text-muted">{formatDuration(pos.duration)}</td>
                    <td className="px-1 py-1.5">{convictionBar(pos.conviction)}</td>
                    <td className="px-1 py-1.5">
                      <div className="flex items-center gap-1 justify-center">
                        <button
                          type="button"
                          className="rounded bg-neon-red/15 px-1.5 py-0.5 text-[9px] font-bold text-neon-red hover:bg-neon-red/25 transition-colors disabled:opacity-40"
                          disabled={closingId === pos.id}
                          onClick={(e) => onClose(pos, e)}
                        >
                          {closingId === pos.id ? '…' : 'Close'}
                        </button>
                        <button
                          type="button"
                          className="rounded bg-neon-purple/15 px-1.5 py-0.5 text-[9px] font-bold text-neon-purple hover:bg-neon-purple/25 transition-colors"
                          onClick={(e) => { e.stopPropagation(); }}
                        >
                          Mod
                        </button>
                      </div>
                    </td>
                  </tr>
                  {isExpanded && pos.reasoning && (
                    <tr>
                      <td colSpan={11} className="border-b border-border-primary/10 bg-bg-primary/40 px-3 py-2 animate-fade-in">
                        <p className="text-[9px] font-mono text-neon-purple/70 uppercase tracking-wider mb-1">Agent Reasoning</p>
                        <p className="text-[10px] font-mono text-text-secondary leading-relaxed whitespace-pre-wrap">{pos.reasoning}</p>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="border-t border-border-primary/10 px-4 py-2.5 flex items-center justify-between">
        <span className="text-[11px] font-mono text-text-muted">Unrealized P&L</span>
        <span className={`text-sm font-mono font-bold ${pnlColor(totalPnl)} ${pnlGlow(totalPnl)}`}>
          {totalPnl > 0 ? '+' : ''}${totalPnl.toFixed(2)}
        </span>
      </div>
    </NeonCard>
  );
}
