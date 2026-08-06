'use client';

import React, { useEffect, useState, useCallback } from 'react';
import {
  Bot, TrendingUp, TrendingDown, Minus, Calendar, AlertTriangle,
  Shield, Radio, Zap, CheckCircle2, XCircle, Clock, RefreshCw,
} from 'lucide-react';
import { cn } from '@/lib/utils';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

interface SessionOutlook { LONDON?: string; NEW_YORK?: string }

interface BriefData {
  bias: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  summary?: string;
  reason?: string;
  key_drivers?: string[];
  risk_events?: string[];
  session_outlook?: SessionOutlook;
  dxy_signal?: 'HEADWIND' | 'TAILWIND' | 'NEUTRAL';
  yield_signal?: 'HEADWIND' | 'TAILWIND' | 'NEUTRAL';
  confidence?: number;
  // legacy fields
  events?: string[];
  pause_events?: string[];
  cross_asset_shifts?: string[];
  generated_at?: string;
  updated_at?: string;
  error?: string;
}

interface ServiceStatus {
  brief: 'ok' | 'running' | 'error' | 'unknown';
  blackout: boolean;
  briefLastRun: string | null;
}

const BIAS_CFG = {
  BULLISH: { color: 'text-neon-green', bg: 'bg-neon-green/10 border-neon-green/30', icon: TrendingUp,   label: 'ALCISTA' },
  BEARISH: { color: 'text-neon-red',   bg: 'bg-neon-red/10   border-neon-red/30',   icon: TrendingDown, label: 'BAJISTA' },
  NEUTRAL: { color: 'text-text-muted', bg: 'bg-bg-tertiary    border-border-primary',icon: Minus,        label: 'NEUTRAL'  },
};

const SIGNAL_CFG = {
  HEADWIND: { label: 'Viento en contra', color: 'text-neon-red' },
  TAILWIND: { label: 'Viento a favor',   color: 'text-neon-green' },
  NEUTRAL:  { label: 'Neutral',          color: 'text-text-muted' },
};

function ServicePill({ label, icon: Icon, ok, sub }: {
  label: string; icon: React.ElementType; ok: boolean; sub?: string;
}) {
  return (
    <div className={cn(
      'flex items-center gap-1 px-1.5 py-0.5 rounded text-[8px] font-mono',
      ok ? 'bg-neon-green/10 text-neon-green' : 'bg-neon-red/10 text-neon-red'
    )}>
      <Icon className="h-2.5 w-2.5 shrink-0" />
      <span className="font-bold">{label}</span>
      {sub && <span className="opacity-60 ml-0.5">{sub}</span>}
    </div>
  );
}

export default function GenAIBrief() {
  const [data, setData] = useState<BriefData | null>(null);
  const [svc, setSvc] = useState<ServiceStatus>({ brief: 'unknown', blackout: false, briefLastRun: null });
  const [age, setAge] = useState<string>('');
  const [triggering, setTriggering] = useState(false);

  const fetchBrief = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/intelligence/genai-brief`);
      if (res.ok) setData(await res.json());
    } catch {}
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const [briefRes, blackoutRes] = await Promise.all([
        fetch(`${API}/api/intelligence/brief-status`),
        fetch(`${API}/api/intelligence/news-blackout`),
      ]);
      const briefSt = briefRes.ok ? await briefRes.json() : {};
      const blackout = blackoutRes.ok ? await blackoutRes.json() : {};
      setSvc({
        brief: briefSt.status ?? 'unknown',
        blackout: !!blackout.active,
        briefLastRun: briefSt.last_run ?? null,
      });
    } catch {}
  }, []);

  useEffect(() => {
    fetchBrief(); fetchStatus();
    const t1 = setInterval(fetchBrief, 30_000);
    const t2 = setInterval(fetchStatus, 15_000);
    return () => { clearInterval(t1); clearInterval(t2); };
  }, [fetchBrief, fetchStatus]);

  useEffect(() => {
    const ts = data?.generated_at || data?.updated_at;
    if (!ts) return;
    const u = () => {
      const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 60000);
      setAge(diff < 1 ? 'ahora' : diff < 60 ? `${diff}m` : `${Math.floor(diff / 60)}h`);
    };
    u();
    const t = setInterval(u, 30_000);
    return () => clearInterval(t);
  }, [data?.generated_at, data?.updated_at]);

  const triggerBrief = async () => {
    setTriggering(true);
    try {
      await fetch(`${API}/api/intelligence/brief/trigger?symbol=XAUUSD`, { method: 'POST' });
      await new Promise((r) => setTimeout(r, 3000));
      await Promise.all([fetchBrief(), fetchStatus()]);
    } catch {} finally {
      setTriggering(false);
    }
  };

  const bias = (data?.bias || 'NEUTRAL') as keyof typeof BIAS_CFG;
  const cfg = BIAS_CFG[bias] || BIAS_CFG.NEUTRAL;
  const Icon = cfg.icon;
  const dxyS = SIGNAL_CFG[data?.dxy_signal || 'NEUTRAL'];
  const yldS = SIGNAL_CFG[data?.yield_signal || 'NEUTRAL'];

  const noData = !data?.summary && !data?.reason && !data?.key_drivers?.length;

  return (
    <div className="flex flex-col h-full">
      {/* Header with service status */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border-primary/50">
        <Bot className="h-3.5 w-3.5 text-yellow-400" />
        <span className="text-[11px] font-mono font-bold text-text-secondary tracking-wider">GENAI BRIEF</span>
        <div className="ml-auto flex items-center gap-1">
          <ServicePill label="Brief" icon={Zap}   ok={svc.brief === 'ok'} />
          <ServicePill label="Sentinel" icon={Radio} ok={!svc.blackout} sub={svc.blackout ? 'BLACKOUT' : ''} />
          {age && <span className="text-[8px] font-mono text-text-muted ml-1">{age}</span>}
          <button
            onClick={triggerBrief}
            disabled={triggering}
            title="Generar brief ahora"
            className="p-1 rounded text-text-muted hover:text-neon-purple hover:bg-neon-purple/10 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={cn('h-3 w-3', triggering && 'animate-spin')} />
          </button>
        </div>
      </div>

      {/* Blackout banner */}
      {svc.blackout && (
        <div className="mx-3 mt-1.5 flex items-center gap-2 px-2 py-1 rounded bg-neon-red/10 border border-neon-red/30 text-[9px] font-mono text-neon-red">
          <AlertTriangle className="h-3 w-3 shrink-0" />
          <span className="font-bold">BLACKOUT ACTIVO</span>
          <span className="text-text-muted ml-1">Evento de alto impacto — trading pausado</span>
        </div>
      )}

      {/* Bias badge */}
      <div className={cn('mx-3 mt-2 flex items-center gap-3 px-3 py-2 rounded border', cfg.bg)}>
        <Icon className={cn('h-5 w-5', cfg.color)} />
        <div className="flex-1">
          <div className={cn('text-sm font-mono font-black tracking-widest', cfg.color)}>{cfg.label}</div>
          <div className="text-[9px] font-mono text-text-muted">
            {new Date().toLocaleDateString('es', { weekday: 'short', day: '2-digit', month: 'short' })}
          </div>
        </div>
        {data?.confidence != null && (
          <div className="text-right">
            <div className={cn('text-[13px] font-mono font-black', cfg.color)}>{data.confidence}%</div>
            <div className="text-[8px] font-mono text-text-muted">confianza</div>
          </div>
        )}
      </div>

      {/* DXY + Yield signals */}
      {(data?.dxy_signal || data?.yield_signal) && (
        <div className="flex gap-2 px-3 mt-1.5">
          <div className="flex-1 bg-bg-tertiary/30 rounded px-2 py-1 text-[9px] font-mono">
            <span className="text-text-muted">DXY </span>
            <span className={dxyS.color}>{dxyS.label}</span>
          </div>
          <div className="flex-1 bg-bg-tertiary/30 rounded px-2 py-1 text-[9px] font-mono">
            <span className="text-text-muted">Yields </span>
            <span className={yldS.color}>{yldS.label}</span>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-3 pb-2">
        {/* Summary */}
        {(data?.summary || data?.reason) && (
          <div className="mt-2">
            <p className="text-[10px] font-mono text-text-secondary leading-relaxed">
              {data.summary || data.reason}
            </p>
          </div>
        )}

        {/* Key drivers */}
        {data?.key_drivers && data.key_drivers.length > 0 && (
          <div className="mt-2">
            <div className="text-[9px] font-mono text-text-muted mb-1 tracking-wider">DRIVERS CLAVE</div>
            <div className="flex flex-col gap-0.5">
              {data.key_drivers.map((d, i) => (
                <div key={i} className="text-[9px] font-mono text-text-secondary flex items-start gap-1.5">
                  <span className="w-1 h-1 rounded-full bg-neon-purple mt-1 shrink-0" />
                  {d}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Risk events */}
        {data?.risk_events && data.risk_events.length > 0 && (
          <div className="mt-2">
            <div className="text-[9px] font-mono text-text-muted mb-1 tracking-wider flex items-center gap-1">
              <AlertTriangle className="h-2.5 w-2.5 text-yellow-400" />
              RIESGOS
            </div>
            <div className="flex flex-col gap-0.5">
              {data.risk_events.map((ev, i) => (
                <div key={i} className="text-[9px] font-mono text-yellow-400/80 flex items-start gap-1.5">
                  <span className="w-1 h-1 rounded-full bg-yellow-400 mt-1 shrink-0" />
                  {ev}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Session outlook */}
        {data?.session_outlook && (
          <div className="mt-2">
            <div className="text-[9px] font-mono text-text-muted mb-1 tracking-wider flex items-center gap-1">
              <Clock className="h-2.5 w-2.5" />
              SESIONES
            </div>
            {data.session_outlook.LONDON && (
              <div className="text-[9px] font-mono mb-0.5">
                <span className="text-neon-purple font-bold">LON </span>
                <span className="text-text-secondary">{data.session_outlook.LONDON}</span>
              </div>
            )}
            {data.session_outlook.NEW_YORK && (
              <div className="text-[9px] font-mono">
                <span className="text-neon-green font-bold">NY  </span>
                <span className="text-text-secondary">{data.session_outlook.NEW_YORK}</span>
              </div>
            )}
          </div>
        )}

        {/* Legacy: pause events */}
        {data?.pause_events && data.pause_events.length > 0 && (
          <div className="mt-2">
            <div className="text-[9px] font-mono text-yellow-400/80 flex flex-col gap-0.5">
              {data.pause_events.map((ev, i) => (
                <div key={i} className="flex items-center gap-1.5">
                  <AlertTriangle className="h-2.5 w-2.5 shrink-0" />{ev}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* No data */}
        {noData && (
          <div className="flex flex-col items-center justify-center gap-2 text-text-muted mt-6">
            <Bot className="h-8 w-8 opacity-20" />
            <span className="text-[10px] font-mono">Brief se genera a las 06:00 UTC</span>
            {svc.briefLastRun && (
              <span className="text-[9px] font-mono opacity-60">Último: {new Date(svc.briefLastRun).toLocaleTimeString('es')}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
