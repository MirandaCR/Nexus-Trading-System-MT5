'use client';

import React, { useMemo } from 'react';
import NeonCard from '../shared/NeonCard';
import { useSystemHealth } from '@/hooks/useSystemHealth';
import { Activity, Clock } from 'lucide-react';
import { cn } from '@/lib/utils';

function StatusDot({ status }: { status: 'online' | 'offline' | 'degraded' }) {
  const colors = {
    online: { bg: '#39ff14', glow: 'rgba(57,255,20,0.5)' },
    degraded: { bg: '#ffd600', glow: 'rgba(255,214,0,0.5)' },
    offline: { bg: '#ff3b3b', glow: 'rgba(255,59,59,0.5)' },
  };
  const c = colors[status];
  return (
    <div
      className={cn('w-2 h-2 rounded-full shrink-0', status !== 'online' && 'animate-pulse-dot')}
      style={{ background: c.bg, boxShadow: `0 0 6px ${c.glow}` }}
    />
  );
}

function formatLatency(ms: number): { text: string; color: string } {
  if (ms <= 0) return { text: '—', color: '#606078' };
  if (ms < 100) return { text: `${ms}ms`, color: '#39ff14' };
  if (ms < 500) return { text: `${ms}ms`, color: '#ffd600' };
  return { text: `${ms}ms`, color: '#ff3b3b' };
}

function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m ${s}s`;
}

function ServiceCard({ svc, compact }: { svc: ReturnType<typeof useSystemHealth>['health']['services'][0]; compact?: boolean }) {
  const lat = formatLatency(svc.latencyP99);
  const shortName = svc.name.replace('nexus-', '');

  return (
    <div
      className={cn(
        'rounded-lg border transition-colors overflow-hidden',
        compact ? 'p-2' : 'p-2.5',
        svc.status === 'offline'
          ? 'bg-neon-red/5 border-neon-red/25'
          : svc.status === 'degraded'
            ? 'bg-neon-gold/5 border-neon-gold/20'
            : 'bg-bg-secondary border-border-secondary',
      )}
    >
      <div className="flex items-center gap-1.5 min-w-0">
        <StatusDot status={svc.status} />
        <span className="text-[10px] font-mono font-medium text-text-primary truncate flex-1">
          {shortName}
        </span>
        <span className="text-[9px] font-mono font-bold shrink-0" style={{ color: lat.color }}>
          {lat.text}
        </span>
      </div>
      {!compact && (svc.cpu > 0 || svc.ram > 0) && (
        <div className="mt-1.5 text-[8px] font-mono text-text-muted">
          CPU {svc.cpu}% · RAM {svc.ram}%
        </div>
      )}
      <div className="mt-1 text-[8px] font-mono text-text-muted uppercase">
        {svc.status === 'online' ? 'online' : svc.status === 'degraded' ? 'degraded' : 'offline'}
      </div>
    </div>
  );
}

export function SystemHealth({ compact = false, className = '' }: { compact?: boolean; className?: string }) {
  const { health, totalPipelineLatency } = useSystemHealth();
  const uptime = useMemo(() => formatUptime(health.uptimeSeconds), [health.uptimeSeconds]);
  const onlineCount = health.services.filter((s) => s.status === 'online').length;

  return (
    <NeonCard
      title="System Health"
      icon={<Activity size={compact ? 14 : 16} />}
      accentColor="blue"
      scrollable={compact}
      className={cn(compact ? 'h-full min-h-0 flex flex-col' : '', className)}
    >
      <div className={cn('flex flex-col', compact ? 'gap-2' : 'space-y-4')}>
        <div className={cn('grid gap-1.5', compact ? 'grid-cols-2' : 'grid-cols-2 sm:grid-cols-3')}>
          {health.services.map((svc) => (
            <ServiceCard key={svc.name} svc={svc} compact={compact} />
          ))}
        </div>

        {!compact && (
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[10px] text-text-muted uppercase tracking-wider">Pipeline Latency</span>
              <span className="text-xs font-mono font-bold text-neon-cyan">{totalPipelineLatency}ms</span>
            </div>
            <div className="flex h-5 rounded-lg overflow-hidden bg-bg-primary">
              {health.pipeline.map((seg) => {
                const widthPct = (seg.latency / totalPipelineLatency) * 100;
                return (
                  <div
                    key={seg.name}
                    className="h-full flex items-center justify-center text-[7px] font-mono font-bold text-bg-primary"
                    style={{ width: `${Math.max(widthPct, 4)}%`, background: seg.color, minWidth: '20px' }}
                    title={`${seg.name}: ${seg.latency}ms`}
                  >
                    {widthPct > 14 && seg.name}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <div className={cn('flex items-center justify-between text-[10px] font-mono', compact && 'pt-1 border-t border-border-primary/10')}>
          <div className="flex items-center gap-1.5">
            <Clock size={10} className="text-neon-purple shrink-0" />
            <span className="text-text-muted">Uptime</span>
            <span className="text-neon-purple font-bold">{uptime}</span>
          </div>
          <div>
            <span className="text-text-muted">Services </span>
            <span className={cn('font-bold', onlineCount === health.services.length ? 'text-neon-green' : 'text-neon-gold')}>
              {onlineCount}/{health.services.length}
            </span>
          </div>
        </div>
      </div>
    </NeonCard>
  );
}
