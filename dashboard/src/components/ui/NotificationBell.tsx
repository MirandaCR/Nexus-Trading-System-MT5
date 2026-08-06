'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Bell, BellRing, Wifi, WifiOff, X, AlertTriangle, Bot, GitCompare, Zap, Shield } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useNotifications, type SystemAlert, ALERT_META } from '@/hooks/useNotifications';

const TYPE_ICON: Record<string, React.ElementType> = {
  BLACKOUT_START:  AlertTriangle,
  BLACKOUT_END:    Shield,
  BRIEF_READY:     Bot,
  CORR_DIVERGENCE: GitCompare,
  SIGNAL_HIGH:     Zap,
  CIRCUIT_BREAKER: AlertTriangle,
};

const SEV_BG: Record<string, string> = {
  HIGH:   'bg-neon-red/10 border-neon-red/20',
  MEDIUM: 'bg-yellow-400/10 border-yellow-400/20',
  INFO:   'bg-bg-tertiary/40 border-border-primary/30',
};

function timeAgo(ts: string): string {
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (diff < 60) return `${diff}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  return `${Math.floor(diff / 3600)}h`;
}

function AlertRow({ alert, isNew }: { alert: SystemAlert; isNew: boolean }) {
  const meta = ALERT_META[alert.type] ?? { label: alert.type, color: 'text-text-muted' };
  const Icon = TYPE_ICON[alert.type] ?? Bell;
  return (
    <div className={cn(
      'flex items-start gap-2 px-3 py-2 border-b border-border-primary/20 last:border-0 transition-colors',
      isNew && 'bg-neon-purple/5',
    )}>
      <div className={cn(
        'flex items-center justify-center w-5 h-5 rounded shrink-0 mt-0.5',
        SEV_BG[alert.severity] ?? SEV_BG.INFO,
      )}>
        <Icon className={cn('h-2.5 w-2.5', meta.color)} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className={cn('text-[9px] font-mono font-bold', meta.color)}>{meta.label}</span>
          <span className="text-[8px] font-mono text-text-muted ml-auto shrink-0">{timeAgo(alert.ts)}</span>
        </div>
        <p className="text-[9px] font-mono text-text-secondary leading-relaxed mt-0.5 line-clamp-2">
          {alert.message}
        </p>
      </div>
    </div>
  );
}

export function NotificationBell() {
  const { alerts, unread, connected, markAllRead } = useNotifications();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const handleOpen = () => {
    setOpen(o => !o);
    if (!open && unread > 0) markAllRead();
  };

  const hasHigh = alerts.slice(0, 5).some(a => a.severity === 'HIGH');
  const lastReadTs = typeof window !== 'undefined'
    ? (localStorage.getItem('nexus:notif:last_read') || '')
    : '';

  return (
    <div ref={ref} className="relative">
      <button
        onClick={handleOpen}
        className={cn(
          'relative p-1.5 rounded transition-colors',
          open
            ? 'text-neon-purple bg-neon-purple/10'
            : 'text-text-muted hover:text-text-primary hover:bg-bg-card'
        )}
        title="Notificaciones"
      >
        {unread > 0
          ? <BellRing className="h-3.5 w-3.5 animate-pulse" />
          : <Bell className="h-3.5 w-3.5" />
        }
        {unread > 0 && (
          <span className={cn(
            'absolute top-0.5 right-0.5 min-w-[14px] h-[14px] flex items-center justify-center rounded-full text-[8px] font-mono font-black',
            hasHigh ? 'bg-neon-red text-white' : 'bg-neon-purple text-white',
          )}>
            {unread > 9 ? '9+' : unread}
          </span>
        )}
        {unread === 0 && (
          <span className={cn(
            'absolute top-1 right-1 h-1.5 w-1.5 rounded-full',
            connected ? 'bg-neon-green' : 'bg-neon-red',
          )} />
        )}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full mt-1 w-72 rounded-lg border border-border-primary bg-bg-primary shadow-xl z-50 overflow-hidden">
            {/* Header */}
            <div className="flex items-center gap-2 px-3 py-2 border-b border-border-primary bg-bg-secondary/80">
              <Bell className="h-3 w-3 text-neon-purple" />
              <span className="text-[10px] font-mono font-bold text-text-secondary tracking-wider">ALERTAS</span>
              <div className="flex items-center gap-1 ml-auto">
                {connected
                  ? <><Wifi className="h-2.5 w-2.5 text-neon-green" /><span className="text-[8px] font-mono text-neon-green">live</span></>
                  : <><WifiOff className="h-2.5 w-2.5 text-neon-red" /><span className="text-[8px] font-mono text-neon-red">offline</span></>
                }
              </div>
              <button
                onClick={() => setOpen(false)}
                className="ml-1 text-text-muted hover:text-text-primary transition-colors"
              >
                <X className="h-3 w-3" />
              </button>
            </div>

            {/* Alert list */}
            <div className="max-h-80 overflow-y-auto">
              {alerts.length === 0 ? (
                <div className="flex flex-col items-center gap-2 py-8 text-text-muted">
                  <Bell className="h-6 w-6 opacity-20" />
                  <span className="text-[10px] font-mono">Sin alertas recientes</span>
                </div>
              ) : (
                alerts.slice(0, 20).map((a, i) => (
                  <AlertRow key={`${a.ts}-${i}`} alert={a} isNew={a.ts > lastReadTs} />
                ))
              )}
            </div>

            {/* Footer */}
            {alerts.length > 0 && (
              <div className="px-3 py-1.5 border-t border-border-primary/50 bg-bg-secondary/40">
                <span className="text-[8px] font-mono text-text-muted">
                  {alerts.length} alerta{alerts.length !== 1 ? 's' : ''} · SSE {connected ? 'conectado' : 'reconectando…'}
                </span>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
