'use client';

import React, { useEffect, useRef, useState } from 'react';
import { Terminal } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface ConsoleLog {
  ts: string;
  level: 'info' | 'warn' | 'error' | 'trade';
  source: string;
  msg: string;
}

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

const levelColor: Record<string, string> = {
  info: 'text-neon-green',
  warn: 'text-neon-gold',
  error: 'text-neon-red',
  trade: 'text-neon-cyan',
};

function pushLog(
  setter: React.Dispatch<React.SetStateAction<ConsoleLog[]>>,
  entry: Omit<ConsoleLog, 'ts'>,
) {
  const ts = new Date().toLocaleTimeString();
  setter((prev) => [...prev.slice(-80), { ...entry, ts }]);
}

async function fetchJsonOnce(url: string, timeoutMs: number): Promise<{ ok: boolean; status: number; data: unknown }> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    const data = await res.json().catch(() => null);
    return { ok: res.ok, status: res.status, data };
  } catch {
    return { ok: false, status: 0, data: null };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Local dev runs 8+ Python services + Redis + MT5 + Docker on one machine —
 * brief OS scheduling hiccups cause occasional single-request timeouts that
 * are not real outages. One silent retry absorbs those without spamming the
 * console with false-alarm ERROR lines; a failure that survives the retry is
 * still logged (real outages are not hidden).
 */
async function fetchJson(url: string, timeoutMs = 9000): Promise<{ ok: boolean; status: number; data: unknown }> {
  const first = await fetchJsonOnce(url, timeoutMs);
  if (first.ok) return first;
  await new Promise((r) => setTimeout(r, 900));
  return fetchJsonOnce(url, timeoutMs);
}

export default function SystemConsole({ className = '' }: { className?: string }) {
  const [logs, setLogs] = useState<ConsoleLog[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    let inFlight = false;

    async function poll() {
      // Retries can make a single cycle run long under a real outage —
      // skip overlapping runs instead of piling up concurrent fetches.
      if (!alive || inFlight) return;
      inFlight = true;
      try {
        await runCycle();
      } finally {
        inFlight = false;
      }
    }

    async function runCycle() {
      // ── Risk settings + counters ─────────────────────────────────────
      const riskSettings = await fetchJson(`${API}/api/risk/settings`, 12_000);
      if (riskSettings.ok && riskSettings.data) {
        const d = riskSettings.data as Record<string, unknown>;
        const settings = (d.settings ?? {}) as Record<string, unknown>;
        const count = Number(d.daily_trade_count ?? 0);
        const max = Number(settings.max_daily_trades ?? 5);
        const paused = Boolean(d.trades_paused);
        const lossPct = Number(d.daily_loss_pct ?? 0);

        if (paused) {
          pushLog(setLogs, { level: 'warn', source: 'nexus-risk', msg: 'TRADING PAUSED manually — POST /api/risk/pause?paused=false to resume' });
        }
        if (max > 0 && count >= max) {
          pushLog(setLogs, {
            level: 'warn',
            source: 'nexus-risk',
            msg: `BLOCKED: daily trade limit ${count}/${max} — increase max_daily_trades or POST /api/risk/reset-day`,
          });
        } else {
          pushLog(setLogs, {
            level: 'info',
            source: 'nexus-risk',
            msg: `trades_today=${count}/${max} daily_loss=${lossPct.toFixed(1)}% max_pos=${settings.max_open_positions ?? '?'}`,
          });
        }
      } else {
        pushLog(setLogs, { level: 'error', source: 'nexus-risk', msg: `settings unreachable HTTP ${riskSettings.status || 'timeout'}` });
      }

      // ── Risk live state ──────────────────────────────────────────────
      const riskStatus = await fetchJson(`${API}/api/risk/status`);
      if (riskStatus.ok && riskStatus.data) {
        const d = riskStatus.data as Record<string, unknown>;
        const st = (d.state ?? d) as Record<string, unknown>;
        pushLog(setLogs, {
          level: 'info',
          source: 'nexus-risk',
          msg: `balance=$${Number(st.balance ?? 0).toFixed(0)} equity=$${Number(st.equity ?? 0).toFixed(0)} nexus_pos=${st.open_positions_count ?? 0} ext=${st.external_positions_count ?? 0}`,
        });
      }

      // ── Agents pipeline ──────────────────────────────────────────────
      const agents = await fetchJson(`${API}/api/agents/status`);
      if (agents.ok && agents.data) {
        const d = agents.data as Record<string, unknown>;
        const rows = Array.isArray(d) ? d : (d.agents as unknown[] ?? []);
        const m = Array.isArray(d) ? null : (d.meta as Record<string, unknown> | null);
        const online = rows.filter((a) => (a as { status?: string }).status !== 'offline').length;
        if (m) {
          pushLog(setLogs, {
            level: 'info',
            source: 'nexus-agents',
            msg: `pipeline=${m.pipeline_state} ready=${m.ready_count}/${m.total_agents} cycles=${m.total_cycles_completed} service=${m.service_online ? 'up' : 'down'}`,
          });
        } else {
          pushLog(setLogs, { level: 'info', source: 'nexus-agents', msg: `${online}/${rows.length} agents active` });
        }
      }

      // ── Latest decision (quick-ish, still short so it doesn't stall the poll) ──
      const decision = await fetchJson(`${API}/api/agents/decision/XAUUSD`, 6000);
      if (decision.ok && decision.data) {
        const d = decision.data as Record<string, unknown>;
        const verdict = String(d.verdict ?? d.final_verdict ?? '—');
        const risk = String(d.risk_decision ?? '—');
        const conv = Number(d.verdict_conviction ?? d.conviction ?? 0);
        const reason = String(d.risk_reasoning ?? d.verdict_reasoning ?? '').slice(0, 120);
        const level = verdict === 'NO_TRADE' || risk === 'REJECT' ? 'warn' : 'trade';
        pushLog(setLogs, {
          level,
          source: 'decision',
          msg: `${verdict} conv=${conv}% risk=${risk}${reason ? ` — ${reason}` : ''}`,
        });
      }

      // ── Autonomous ML ────────────────────────────────────────────────
      const autoMl = await fetchJson(`${API}/api/ml/traders/autonomous/status`);
      if (autoMl.ok && autoMl.data) {
        const d = autoMl.data as Record<string, unknown>;
        const lastSig = (d.last_signal ?? {}) as Record<string, unknown>;
        const proposed = Number(d.trades_proposed ?? 0);
        const approved = Number(d.trades_approved ?? 0);
        const rejected = Number(d.trades_rejected ?? 0);
        pushLog(setLogs, {
          level: rejected > approved ? 'warn' : 'info',
          source: 'nexus-ml',
          msg: `autonomous=${d.running ? 'RUNNING' : 'STOPPED'} mode=${d.mode ?? '?'} proposed=${proposed} approved=${approved} rejected=${rejected} errors=${d.errors ?? 0}`,
        });
        if (lastSig.direction) {
          pushLog(setLogs, {
            level: 'trade',
            source: 'nexus-ml',
            msg: `last_signal ${lastSig.symbol ?? 'XAUUSD'} ${lastSig.direction} conf=${Number(lastSig.confidence ?? 0).toFixed(2)} source=${lastSig.source ?? '?'}`,
          });
        }
      }

      // ── Executor / positions ─────────────────────────────────────────
      const positions = await fetchJson(`${API}/api/positions`);
      if (positions.ok && positions.data) {
        const d = positions.data as Record<string, unknown>;
        pushLog(setLogs, {
          level: 'info',
          source: 'nexus-executor',
          msg: `mt5=${d.live_connected ? 'connected' : 'disconnected'} positions=${d.count ?? 0} nexus=${d.nexus_count ?? 0} external=${d.external_count ?? 0}`,
        });
      }

      // ── Data feed ────────────────────────────────────────────────────
      const ticks = await fetchJson(`${API}/api/v2/market/ticks?symbol=XAUUSD&count=1&source=cache`);
      if (ticks.ok && ticks.data != null) {
        const wrap = ticks.data as Record<string, unknown>;
        const arr = (wrap.data ?? wrap) as unknown[];
        const tick = Array.isArray(arr) && arr.length ? (arr[0] as Record<string, unknown>) : null;
        pushLog(setLogs, {
          level: 'info',
          source: 'nexus-data',
          msg: tick
            ? `XAUUSD bid=${tick.bid ?? '?'} ask=${tick.ask ?? '?'}`
            : 'ticks ok (empty cache)',
        });
      } else {
        pushLog(setLogs, { level: 'warn', source: 'nexus-data', msg: `HTTP ${ticks.status || 'timeout'} — check nexus-data service` });
      }

      // ── Auto-trade / kill switch ─────────────────────────────────────
      const mode = await fetchJson(`${API}/api/system/mode`);
      if (mode.ok && mode.data) {
        const d = mode.data as Record<string, unknown>;
        pushLog(setLogs, {
          level: d.auto_trade ? 'info' : 'warn',
          source: 'system',
          msg: `mode=${d.mode ?? '?'} auto_trade=${d.auto_trade ?? '?'}`,
        });
      }
    }

    // Give services ~8s to finish startup before first poll
    const delay = setTimeout(() => {
      if (!alive) return;
      poll();
    }, 8_000);
    const id = setInterval(poll, 12_000);
    return () => { alive = false; clearTimeout(delay); clearInterval(id); };
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [logs]);

  return (
    <div className={cn('rounded-xl border border-border-primary/20 bg-bg-primary flex flex-col overflow-hidden h-full min-h-0', className)}>
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border-primary/10 bg-bg-secondary/40 shrink-0">
        <Terminal className="h-3.5 w-3.5 text-neon-cyan" />
        <span className="text-[11px] font-mono font-semibold text-text-secondary">System Console</span>
        <span className="h-1.5 w-1.5 rounded-full bg-neon-green animate-pulse" />
        <span className="text-[10px] text-text-muted">{logs.length} events</span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => setLogs([])}
          className="text-[10px] text-text-muted hover:text-text-secondary font-mono"
        >
          clear
        </button>
      </div>
      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto px-3 py-1.5 font-mono text-[10px] leading-relaxed scrollbar-hide">
        {logs.length === 0 && (
          <div className="text-text-muted text-center py-6">Collecting pipeline logs…</div>
        )}
        {logs.map((log, i) => (
          <div key={`${log.ts}-${i}`} className="flex gap-2 py-0.5 border-b border-border-primary/5 last:border-0">
            <span className="text-text-muted shrink-0 w-[72px]">{log.ts}</span>
            <span className={cn('shrink-0 uppercase w-[42px] font-bold', levelColor[log.level] || 'text-text-muted')}>
              {log.level}
            </span>
            <span className="text-neon-purple shrink-0 w-[100px] truncate">{log.source}</span>
            <span className="text-text-secondary break-words min-w-0">{log.msg}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
