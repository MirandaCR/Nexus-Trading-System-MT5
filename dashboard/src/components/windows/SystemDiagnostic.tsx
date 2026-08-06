'use client';

import { useEffect, useState } from 'react';
import { apiClient } from '@/lib/api-client';
import { cn } from '@/lib/utils';
import { AlertTriangle, CheckCircle2, XCircle, RefreshCw } from 'lucide-react';

interface ServiceStatus {
  name: string;
  ok: boolean;
  detail?: string;
}

export default function SystemDiagnostic() {
  const [services, setServices] = useState<ServiceStatus[]>([]);
  const [checking, setChecking] = useState(false);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [expanded, setExpanded] = useState(false);

  const check = async () => {
    setChecking(true);
    const results: ServiceStatus[] = [];

    // Check nexus-api (gateway)
    try {
      const health = await apiClient.getHealth();
      const h = health.data;
      results.push({ name: 'nexus-api (gateway)', ok: h.status === 'healthy', detail: h.status });

      const redis = h.components.find((c) => c.name === 'redis');
      results.push({
        name: 'Redis',
        ok: redis?.status === 'healthy',
        detail: redis?.status ?? 'not reported',
      });

      const duckdb = h.components.find((c) => c.name === 'duckdb');
      results.push({
        name: 'DuckDB',
        ok: duckdb?.status === 'healthy',
        detail: duckdb?.status ?? 'not reported',
      });
    } catch (e) {
      results.push({ name: 'nexus-api (gateway)', ok: false, detail: 'unreachable' });
      results.push({ name: 'Redis', ok: false, detail: 'gateway offline' });
      results.push({ name: 'DuckDB', ok: false, detail: 'gateway offline' });
    }

    // Check nexus-data via candles (502 = nexus-data down)
    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000'}/api/v2/market/candles?symbol=XAUUSD&timeframe=M15&count=1`,
      );
      if (res.status === 502) {
        results.push({ name: 'nexus-data (MT5 feed)', ok: false, detail: '502 — service crashed' });
      } else if (res.ok) {
        results.push({ name: 'nexus-data (MT5 feed)', ok: true, detail: 'ok' });
      } else {
        results.push({ name: 'nexus-data (MT5 feed)', ok: false, detail: `HTTP ${res.status}` });
      }
    } catch {
      results.push({ name: 'nexus-data (MT5 feed)', ok: false, detail: 'unreachable' });
    }

    // Check MT5 connection via positions
    try {
      const pos = await apiClient.getPositionsSnapshot();
      const live = pos.data.live_connected;
      results.push({
        name: 'MetaTrader 5',
        ok: Boolean(live),
        detail: live ? 'connected' : 'disconnected — check MT5_LOGIN in .env',
      });
    } catch {
      results.push({ name: 'MetaTrader 5', ok: false, detail: 'positions endpoint failed' });
    }

    // Check ML models (traders pipeline)
    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000'}/api/ml/traders/status`,
      );
      if (res.ok) {
        const data = await res.json();
        const mlFitted = data?.ml?.is_fitted;
        results.push({
          name: 'ML Models',
          ok: Boolean(mlFitted),
          detail: mlFitted ? `trained (${data?.ml?.model_name ?? 'ensemble'})` : 'not trained yet',
        });
      } else {
        results.push({ name: 'ML Models', ok: false, detail: `HTTP ${res.status}` });
      }
    } catch {
      results.push({ name: 'ML Models', ok: false, detail: 'unreachable' });
    }

    // Check agents service
    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000'}/api/agents/status`,
      );
      if (res.ok) {
        const payload = await res.json();
        const rows = Array.isArray(payload) ? payload : (payload?.agents ?? []);
        const meta = Array.isArray(payload) ? null : payload?.meta;
        const ready = rows.filter((a: { status: string }) => a.status !== 'offline').length;
        const pipeline = meta?.pipeline_state ?? (ready > 0 ? 'ready' : 'offline');
        results.push({
          name: 'Agent Pipeline',
          ok: meta?.service_online ?? ready > 0,
          detail: `${ready}/${rows.length} listos · ${pipeline}`,
        });
      } else {
        results.push({ name: 'Agent Pipeline', ok: false, detail: `HTTP ${res.status}` });
      }
    } catch {
      results.push({ name: 'Agent Pipeline', ok: false, detail: 'unreachable' });
    }

    setServices(results);
    setLastChecked(new Date());
    setChecking(false);
  };

  useEffect(() => {
    check();
    const id = window.setInterval(check, 30_000);
    return () => window.clearInterval(id);
  }, []);

  const allOk = services.length > 0 && services.every((s) => s.ok);
  const anyDown = services.some((s) => !s.ok);
  const downCount = services.filter((s) => !s.ok).length;

  if (allOk && !expanded) return null; // Hide when everything is healthy

  return (
    <div
      className={cn(
        'rounded-lg border px-4 py-2.5 mb-3 transition-all',
        anyDown
          ? 'bg-neon-red/5 border-neon-red/30'
          : 'bg-neon-green/5 border-neon-green/30',
      )}
    >
      <div className="flex items-center gap-3">
        {anyDown ? (
          <AlertTriangle className="h-4 w-4 text-neon-red shrink-0" />
        ) : (
          <CheckCircle2 className="h-4 w-4 text-neon-green shrink-0" />
        )}

        <span className={cn('text-xs font-semibold flex-1', anyDown ? 'text-neon-red' : 'text-neon-green')}>
          {anyDown
            ? `${downCount} service${downCount > 1 ? 's' : ''} offline — trading paused`
            : 'All systems operational'}
        </span>

        {lastChecked && (
          <span className="text-[10px] font-mono text-text-muted hidden sm:inline">
            {lastChecked.toLocaleTimeString()}
          </span>
        )}

        <button
          onClick={check}
          disabled={checking}
          className="p-1 rounded text-text-muted hover:text-text-primary transition-colors"
        >
          <RefreshCw className={cn('h-3.5 w-3.5', checking && 'animate-spin')} />
        </button>

        <button
          onClick={() => setExpanded(!expanded)}
          className="text-[10px] font-mono text-text-muted hover:text-text-secondary transition-colors"
        >
          {expanded ? 'hide' : 'details'}
        </button>
      </div>

      {expanded && services.length > 0 && (
        <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5">
          {services.map((svc) => (
            <div
              key={svc.name}
              className={cn(
                'flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs',
                svc.ok ? 'bg-neon-green/5' : 'bg-neon-red/8',
              )}
            >
              {svc.ok ? (
                <CheckCircle2 className="h-3.5 w-3.5 text-neon-green shrink-0" />
              ) : (
                <XCircle className="h-3.5 w-3.5 text-neon-red shrink-0" />
              )}
              <span className={cn('font-medium', svc.ok ? 'text-text-secondary' : 'text-neon-red')}>
                {svc.name}
              </span>
              {svc.detail && (
                <span className={cn('ml-auto text-[10px] font-mono shrink-0', svc.ok ? 'text-text-muted' : 'text-neon-red/70')}>
                  {svc.detail}
                </span>
              )}
            </div>
          ))}

          {services.some((s) => s.name === 'nexus-data (MT5 feed)' && !s.ok) && (
            <div className="col-span-full mt-1 text-[10px] font-mono text-neon-gold/80 leading-relaxed">
              nexus-data crash fix applied (MT5 COM single-thread). Restart <code>python run.py</code> to reload.
            </div>
          )}
          {services.some((s) => s.name === 'MetaTrader 5' && !s.ok) && (
            <div className="col-span-full mt-1 text-[10px] font-mono text-neon-gold/80 leading-relaxed">
              Open MetaTrader 5 terminal and confirm MT5_LOGIN / MT5_SERVER in .env match your broker.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
