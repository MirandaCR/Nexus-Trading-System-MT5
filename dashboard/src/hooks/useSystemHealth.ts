'use client';

import { useState, useEffect, useCallback, useRef } from 'react';

export interface ServiceHealth {
  name: string;
  status: 'online' | 'offline' | 'degraded';
  latencyP99: number;
  cpu: number;
  ram: number;
  lastHeartbeat: number;
}

export interface PipelineSegment {
  name: string;
  latency: number;
  color: string;
}

export interface SystemHealthState {
  services: ServiceHealth[];
  pipeline: PipelineSegment[];
  uptimeSeconds: number;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

const PROBE_ENDPOINTS: { name: string; url: string }[] = [
  { name: 'nexus-api',      url: '/health' },
  { name: 'nexus-data',     url: '/api/v2/market/ticks?symbol=XAUUSD&count=1&source=cache' },
  { name: 'nexus-agents',   url: '/api/agents/status' },
  { name: 'nexus-risk',     url: '/api/risk/status' },
  { name: 'nexus-executor', url: '/api/positions' },
  { name: 'nexus-ml',       url: '/api/ml/traders/status' },
];

const PIPELINE_SEGMENTS: PipelineSegment[] = [
  { name: 'Data',   latency: 0, color: '#00e5ff' },
  { name: 'ML',     latency: 0, color: '#bf5af2' },
  { name: 'Agents', latency: 0, color: '#ffd600' },
  { name: 'Risk',   latency: 0, color: '#ff9500' },
  { name: 'Exec',   latency: 0, color: '#39ff14' },
];

const PIPELINE_MAP: Record<string, string> = {
  'nexus-data': 'Data',
  'nexus-ml': 'ML',
  'nexus-agents': 'Agents',
  'nexus-risk': 'Risk',
  'nexus-executor': 'Exec',
};

function emptyHealth(): SystemHealthState {
  return {
    services: PROBE_ENDPOINTS.map((s) => ({
      name: s.name,
      status: 'offline' as const,
      latencyP99: 0,
      cpu: 0,
      ram: 0,
      lastHeartbeat: -1,
    })),
    pipeline: PIPELINE_SEGMENTS,
    uptimeSeconds: 0,
  };
}

async function probe(url: string): Promise<{ ok: boolean; status: number; latency: number; cpu?: number; ram?: number }> {
  const start = performance.now();
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 5000);
  try {
    const res = await fetch(`${API_BASE}${url}`, { signal: ctrl.signal });
    const latency = Math.round(performance.now() - start);
    let cpu = 0;
    let ram = 0;
    if (url === '/health' && res.ok) {
      try {
        const data = await res.json();
        if (typeof data.cpu_pct === 'number') cpu = data.cpu_pct;
        if (typeof data.ram_pct === 'number') ram = data.ram_pct;
      } catch { /* ignore */ }
    }
    return { ok: res.ok, status: res.status, latency, cpu, ram };
  } catch {
    return { ok: false, status: 0, latency: Math.round(performance.now() - start) };
  } finally {
    clearTimeout(timer);
  }
}

export function useSystemHealth() {
  const [health, setHealth] = useState<SystemHealthState>(emptyHealth);
  const startRef = useRef(Date.now());

  const refresh = useCallback(async () => {
    const results: ServiceHealth[] = [];

    await Promise.all(
      PROBE_ENDPOINTS.map(async (svc) => {
        const r = await probe(svc.url);
        let status: ServiceHealth['status'] = 'offline';
        if (r.ok) status = 'online';
        else if (r.status > 0 && r.status < 500) status = 'degraded';

        results.push({
          name: svc.name,
          status,
          latencyP99: r.latency,
          cpu: r.cpu ?? 0,
          ram: r.ram ?? 0,
          lastHeartbeat: status === 'online' ? 0 : -1,
        });
      }),
    );

    results.sort((a, b) => PROBE_ENDPOINTS.findIndex((p) => p.name === a.name) - PROBE_ENDPOINTS.findIndex((p) => p.name === b.name));

    const pipeline = PIPELINE_SEGMENTS.map((seg) => {
      const match = results.find((r) => PIPELINE_MAP[r.name] === seg.name);
      return { ...seg, latency: match?.latencyP99 ?? 0 };
    });

    setHealth({
      services: results,
      pipeline,
      uptimeSeconds: Math.floor((Date.now() - startRef.current) / 1000),
    });
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 15_000);
    return () => clearInterval(interval);
  }, [refresh]);

  const allHealthy = health.services.every((s) => s.status === 'online');
  const totalPipelineLatency = health.pipeline.reduce((s, p) => s + p.latency, 0) || 1;

  return { health, allHealthy, totalPipelineLatency };
}
