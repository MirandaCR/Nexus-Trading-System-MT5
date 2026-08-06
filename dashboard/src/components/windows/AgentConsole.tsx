'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import NeonCard from '../shared/NeonCard';
import type { AgentConsoleMessage as AgentMessage, MessageType, Verdict } from '../../types';
import { AGENT_REGISTRY } from '../../types';
import { useWebSocket } from '@/hooks/useWebSocket';
import { gatewayWsUrl } from '@/lib/gateway-ws';

// ── Types ────────────────────────────────────────────────────────────────────

interface ConsoleEntry extends AgentMessage {
  is_cycle_start?: boolean;
  debate_stance?: 'bull' | 'bear';
  verdict?: Verdict;
  conviction?: number;
  bull_score?: number;
  bear_score?: number;
  full_reasoning?: string;
  factors?: { name: string; weight: number; score: number }[];
  risk_assessment?: Record<string, unknown>;
}

type FilterKey = 'all' | MessageType;

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'analysis', label: 'Analysis' },
  { key: 'debate', label: 'Debate' },
  { key: 'decision', label: 'Decision' },
  { key: 'management', label: 'Management' },
];


// ── Helpers ──────────────────────────────────────────────────────────────────

function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZone: 'UTC',
  });
}

function verdictStyle(v?: Verdict): string {
  if (v === 'BUY') return 'bg-neon-green/10 text-neon-green';
  if (v === 'SELL') return 'bg-neon-red/10 text-neon-red';
  return 'bg-bg-tertiary text-text-muted';
}

// ── WS event → ConsoleEntry mapping ──────────────────────────────────────────

interface WsAgentEvent {
  event_type?: string;
  agent_id?: string;
  agent_name?: string;
  symbol?: string;
  analysis?: string;
  reasoning?: string;
  verdict?: string;
  conviction?: number;
  confidence?: number;
  metadata?: Record<string, unknown>;
  timestamp?: string;
  correlation_id?: string;
}

function classifyAgent(agentId: string, eventType: string): {
  type: MessageType;
  stance?: 'bull' | 'bear';
  color: string;
} {
  if (eventType === 'agent_verdict' || agentId === 'A7') {
    return { type: 'decision', color: '#e8e8f0' };
  }
  if (agentId === 'A5') return { type: 'debate', stance: 'bull', color: '#00ff88' };
  if (agentId === 'A6') return { type: 'debate', stance: 'bear', color: '#ff2255' };
  if (['A8', 'A9', 'A10', 'A11', 'A12'].includes(agentId)) {
    return { type: 'management', color: '#ff4488' };
  }
  return { type: 'analysis', color: '#00d4ff' };
}

function mapWsEvent(ev: WsAgentEvent): ConsoleEntry | null {
  const agentId = (ev.agent_id || '').toUpperCase();
  if (!agentId) return null;

  const cls = classifyAgent(agentId, ev.event_type || '');
  const meta = (ev.metadata ?? {}) as Record<string, unknown>;
  const fromMeta = (meta as { agent_color?: string }).agent_color;
  const color =
    typeof fromMeta === 'string' && fromMeta.length > 0 ? fromMeta : cls.color;

  const content =
    ev.verdict
      ? `VERDICT: ${ev.verdict} ${ev.symbol ?? ''}${
          ev.reasoning ? ` — ${ev.reasoning}` : ''
        }`
      : ev.analysis ?? ev.reasoning ?? '';

  if (!content) return null;

  return {
    id: `${ev.correlation_id ?? ''}_${agentId}_${ev.timestamp ?? Date.now()}`,
    agent_id: agentId,
    agent_name:
      ev.agent_name ||
      (AGENT_REGISTRY[agentId as keyof typeof AGENT_REGISTRY] as { name?: string } | undefined)?.name ||
      agentId,
    agent_color: color,
    message_type: cls.type,
    debate_stance: cls.stance,
    verdict: (ev.verdict as Verdict | undefined) ?? undefined,
    conviction: ev.conviction ?? ev.confidence,
    content: String(content).slice(0, 400),
    full_reasoning: typeof ev.reasoning === 'string' && ev.reasoning.length > 200 ? ev.reasoning : undefined,
    metadata: meta,
    timestamp: ev.timestamp || new Date().toISOString(),
  };
}

// ── Component ────────────────────────────────────────────────────────────────

export default function AgentConsole() {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [filter, setFilter] = useState<FilterKey>('all');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [liveMessages, setLiveMessages] = useState<ConsoleEntry[]>([]);

  const { isConnected, lastMessage } = useWebSocket({
    url: gatewayWsUrl('/agents'),
    enabled: true,
  });

  useEffect(() => {
    if (!lastMessage || typeof lastMessage !== 'object') return;
    const ev = lastMessage as WsAgentEvent;
    const entry = mapWsEvent(ev);
    if (!entry) return;
    setLiveMessages((prev) => {
      if (prev.some((m) => m.id === entry.id)) return prev;
      return [...prev, entry].slice(-200);
    });
  }, [lastMessage]);

  const messages = useMemo<ConsoleEntry[]>(() => liveMessages, [liveMessages]);

  const filtered = filter === 'all' ? messages : messages.filter((m) => m.message_type === filter);

  // Auto-scroll to bottom
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [filtered.length]);

  return (
    <NeonCard
      title="Agent Console"
      glow="purple"
      noPadding
      icon={
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <polyline points="4 17 10 11 4 5" />
          <line x1="12" y1="19" x2="20" y2="19" />
        </svg>
      }
      className="h-full flex flex-col"
    >
      {/* Filter bar */}
      <div className="flex items-center gap-1 border-b border-border-primary/10 px-3 py-1.5 overflow-x-auto">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={`rounded px-2.5 py-1 text-[11px] font-mono font-semibold whitespace-nowrap transition-all ${
              f.key === filter
                ? 'bg-neon-purple/15 text-neon-purple'
                : 'text-text-muted hover:text-text-secondary hover:bg-bg-tertiary'
            }`}
          >
            {f.label}
          </button>
        ))}
        <span
          className={`ml-auto inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-mono ${
            isConnected
              ? 'text-neon-green bg-neon-green/10'
              : 'text-text-muted bg-bg-tertiary'
          }`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              isConnected ? 'bg-neon-green animate-pulse' : 'bg-text-muted'
            }`}
          />
          {isConnected ? (liveMessages.length === 0 ? 'live · idle' : 'live') : 'offline · mock'}
        </span>
        <span className="text-[10px] font-mono text-text-muted">{filtered.length} msgs</span>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto overscroll-contain px-3 py-2 space-y-1" style={{ background: '#0a0a0f' }}>
        {filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center gap-3 h-full text-center">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-neon-purple/30">
              <polyline points="4 17 10 11 4 5" /><line x1="12" y1="19" x2="20" y2="19" />
            </svg>
            <div>
              <p className="text-[11px] font-mono text-text-muted">Esperando actividad de agentes</p>
              <p className="text-[9px] font-mono text-text-muted/50 mt-1">
                {isConnected ? 'Conectado — los mensajes aparecen cuando el pipeline evalúa XAUUSD' : 'Sin conexión WebSocket — verificá que nexus-agents esté corriendo'}
              </p>
            </div>
          </div>
        )}
        {filtered.map((msg) => {
          const isExpanded = expandedId === msg.id;

          // Cycle separator
          if (msg.is_cycle_start) {
            return (
              <div key={msg.id} className="flex items-center gap-2 py-2 my-1">
                <div className="flex-1 h-px bg-gradient-to-r from-transparent via-neon-purple/40 to-transparent" />
                <span className="text-[10px] font-mono text-neon-purple/70 uppercase tracking-widest">{msg.content}</span>
                <div className="flex-1 h-px bg-gradient-to-r from-transparent via-neon-purple/40 to-transparent" />
              </div>
            );
          }

          // Debate: side-by-side layout
          if (msg.message_type === 'debate' && msg.debate_stance) {
            const isBull = msg.debate_stance === 'bull';
            const barColor = isBull ? '#00ff88' : '#ff2255';
            const conviction = msg.conviction ?? 0;

            return (
              <div
                key={msg.id}
                className={`rounded-lg border p-2.5 cursor-pointer transition-all ${
                  isBull ? 'border-neon-green/15 bg-neon-green/[0.03]' : 'border-neon-red/15 bg-neon-red/[0.03]'
                }`}
                onClick={() => setExpandedId(isExpanded ? null : msg.id)}
              >
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="h-2 w-2 rounded-full" style={{ backgroundColor: msg.agent_color }} />
                  <span className="text-[11px] font-semibold font-mono" style={{ color: msg.agent_color }}>{msg.agent_name}</span>
                  <span className="text-[10px] text-text-muted font-mono">{formatTime(msg.timestamp)}</span>
                  <span className={`ml-auto text-[10px] font-mono font-bold ${isBull ? 'text-neon-green' : 'text-neon-red'}`}>
                    {isBull ? '▲ BULL' : '▼ BEAR'} {(conviction * 100).toFixed(0)}%
                  </span>
                </div>
                <p className="text-xs font-mono text-text-secondary leading-relaxed">{msg.content}</p>
                {/* Conviction bar */}
                <div className="mt-2 h-1 w-full rounded-full bg-bg-tertiary overflow-hidden">
                  <div className="h-full rounded-full transition-all" style={{ width: `${conviction * 100}%`, backgroundColor: barColor }} />
                </div>
              </div>
            );
          }

          // Arbitrator verdict
          if (msg.message_type === 'decision' && msg.verdict) {
            return (
              <div
                key={msg.id}
                className={`rounded-lg border-2 p-3 cursor-pointer transition-all ${verdictStyle(msg.verdict)} border-current/20`}
                onClick={() => setExpandedId(isExpanded ? null : msg.id)}
              >
                <div className="flex items-center gap-2 mb-2">
                  <span className="h-2.5 w-2.5 rounded-full animate-pulse-dot" style={{ backgroundColor: msg.agent_color }} />
                  <span className="text-[11px] font-semibold font-mono" style={{ color: msg.agent_color }}>{msg.agent_name}</span>
                  <span className="text-[10px] text-text-muted font-mono">{formatTime(msg.timestamp)}</span>
                </div>
                <div className="flex items-center gap-3 mb-2">
                  <span className={`inline-block rounded px-3 py-1 text-sm font-mono font-bold ${verdictStyle(msg.verdict)}`}>
                    {msg.verdict}
                  </span>
                  {msg.conviction != null && (
                    <span className="text-xs font-mono text-text-secondary">
                      Conviction: <span className="text-text-primary font-bold">{(msg.conviction * 100).toFixed(0)}%</span>
                    </span>
                  )}
                  {msg.bull_score != null && msg.bear_score != null && (
                    <span className="text-[10px] font-mono text-text-muted">
                      Bull {msg.bull_score} vs Bear {msg.bear_score}
                    </span>
                  )}
                </div>
                <p className="text-xs font-mono text-text-secondary leading-relaxed">{msg.content}</p>
              </div>
            );
          }

          // Standard message
          return (
            <div
              key={msg.id}
              className="group rounded-lg border border-transparent px-2.5 py-2 cursor-pointer hover:border-border-primary/10 hover:bg-bg-secondary/30 transition-all"
              onClick={() => setExpandedId(isExpanded ? null : msg.id)}
            >
              <div className="flex items-center gap-2 mb-0.5">
                <span className="h-2 w-2 rounded-full shrink-0" style={{ backgroundColor: msg.agent_color }} />
                <span className="text-[11px] font-semibold font-mono" style={{ color: msg.agent_color }}>{msg.agent_name}</span>
                <span className="text-[10px] text-text-muted font-mono">{formatTime(msg.timestamp)}</span>
                <span className="ml-auto rounded bg-bg-tertiary px-1.5 py-0.5 text-[9px] font-mono text-text-muted uppercase">
                  {msg.message_type}
                </span>
              </div>
              <p className="text-xs font-mono text-text-secondary leading-relaxed pl-4">{msg.content}</p>
              {isExpanded && msg.full_reasoning && (
                <pre className="mt-2 ml-4 rounded bg-bg-primary/50 p-2 text-[11px] font-mono text-text-muted leading-relaxed whitespace-pre-wrap border border-border-primary/10 animate-fade-in">
                  {msg.full_reasoning}
                </pre>
              )}
            </div>
          );
        })}
      </div>
    </NeonCard>
  );
}
