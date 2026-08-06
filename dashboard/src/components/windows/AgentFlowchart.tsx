'use client';

import { useEffect, useState, useMemo } from 'react';
import NeonCard from '../shared/NeonCard';

type AgentStatus = 'online' | 'processing' | 'idle' | 'offline' | 'error';

interface FlowAgent {
  id: string;
  name: string;
  shortName: string;
  color: string;
  status: AgentStatus;
  model: string;
  lastActivity?: string;
  layer: number;
  col: number;
}

const AGENTS: FlowAgent[] = [
  { id: 'A11', name: 'Session Governor',    shortName: 'SESSION',    color: '#ffaa00', status: 'idle',  model: 'deepseek-chat',     layer: 0, col: 0 },
  { id: 'A0',  name: 'Regime Analyst',      shortName: 'REGIME',     color: '#e040fb', status: 'idle',  model: 'deepseek-reasoner', layer: 1, col: 0 },
  { id: 'A1',  name: 'Market Analyst',      shortName: 'MARKET',     color: '#00f5ff', status: 'idle',  model: 'deepseek-reasoner', layer: 2, col: 0 },
  { id: 'A2',  name: 'News Sentinel',       shortName: 'NEWS',       color: '#ffd700', status: 'idle',  model: 'deepseek-chat',     layer: 2, col: 1 },
  { id: 'A3',  name: 'Intermarket',         shortName: 'INTER',      color: '#ff6b35', status: 'idle',  model: 'deepseek-chat',     layer: 2, col: 2 },
  { id: 'A4',  name: 'Order Flow',          shortName: 'FLOW',       color: '#ff1493', status: 'idle',  model: 'deepseek-chat',     layer: 2, col: 3 },
  { id: 'A5',  name: 'Bull Researcher',     shortName: 'BULL',       color: '#39ff14', status: 'idle',  model: 'deepseek-chat',     layer: 3, col: 0 },
  { id: 'A6',  name: 'Bear Researcher',     shortName: 'BEAR',       color: '#ff1744', status: 'idle',  model: 'deepseek-chat',     layer: 3, col: 1 },
  { id: 'A7',  name: 'Arbitrator',          shortName: 'JUDGE',      color: '#bf40ff', status: 'idle',  model: 'deepseek-reasoner', layer: 4, col: 0 },
  { id: 'A8',  name: 'Risk Manager',        shortName: 'RISK',       color: '#ff4444', status: 'idle',  model: 'deepseek-reasoner', layer: 5, col: 0 },
  { id: 'A9',  name: 'Trade Executor',      shortName: 'EXEC',       color: '#00ff88', status: 'idle',  model: 'deepseek-chat',     layer: 6, col: 0 },
  { id: 'A10', name: 'Position Manager',    shortName: 'POS MGR',    color: '#4488ff', status: 'idle',  model: 'deepseek-chat',     layer: 6, col: 1 },
  { id: 'A12', name: 'Portfolio Strategist', shortName: 'PORTFOLIO',  color: '#aa44ff', status: 'idle',  model: 'deepseek-reasoner', layer: 7, col: 0 },
];

interface FlowConnection {
  from: string;
  to: string;
  label?: string;
  animated?: boolean;
}

const CONNECTIONS: FlowConnection[] = [
  // A11 Session Governor → A0 Regime Analyst
  { from: 'A11', to: 'A0',  label: 'regime?',  animated: true },
  // A0 → Parallel Analysis (A1, A2, A3, A4)
  { from: 'A0',  to: 'A1',  label: 'analyze',   animated: true },
  { from: 'A0',  to: 'A2',  label: 'scan',      animated: true },
  { from: 'A0',  to: 'A3',  label: 'correlate', animated: true },
  { from: 'A0',  to: 'A4',  label: 'flow',      animated: true },
  // All analysts → Debate (A5, A6)
  { from: 'A1',  to: 'A5',  label: 'bull thesis', animated: true },
  { from: 'A1',  to: 'A6',  label: 'bear thesis', animated: true },
  { from: 'A2',  to: 'A5' },
  { from: 'A2',  to: 'A6' },
  { from: 'A3',  to: 'A5' },
  { from: 'A3',  to: 'A6' },
  { from: 'A4',  to: 'A5' },
  { from: 'A4',  to: 'A6' },
  // Debate → Arbitrator → Risk → Exec → Position → Portfolio
  { from: 'A5',  to: 'A7',  label: 'vote',    animated: true },
  { from: 'A6',  to: 'A7',  label: 'vote',    animated: true },
  { from: 'A7',  to: 'A8',  label: 'verdict', animated: true },
  { from: 'A8',  to: 'A9',  label: 'approved', animated: true },
  { from: 'A9',  to: 'A10', label: 'manage' },
  { from: 'A10', to: 'A12', label: 'review' },
];

const STATUS_CONFIG: Record<AgentStatus, { glow: string; bg: string; border: string; text: string; dot: string }> = {
  online:     { glow: '0 0 8px rgba(0,255,136,0.5)',  bg: 'rgba(0,255,136,0.08)',  border: 'rgba(0,255,136,0.4)',  text: '#00ff88', dot: '#00ff88' },
  processing: { glow: '0 0 12px rgba(191,64,255,0.6)', bg: 'rgba(191,64,255,0.10)', border: 'rgba(191,64,255,0.5)', text: '#bf40ff', dot: '#bf40ff' },
  idle:       { glow: '0 0 6px rgba(255,215,0,0.3)',   bg: 'rgba(255,215,0,0.06)',  border: 'rgba(255,215,0,0.3)',  text: '#ffd700', dot: '#ffd700' },
  offline:    { glow: 'none',                           bg: 'rgba(90,90,120,0.05)',  border: 'rgba(90,90,120,0.15)', text: '#5a5a78', dot: '#5a5a78' },
  error:      { glow: '0 0 12px rgba(255,23,68,0.5)',  bg: 'rgba(255,23,68,0.08)',  border: 'rgba(255,23,68,0.4)',  text: '#ff1744', dot: '#ff1744' },
};

const NODE_W = 130;
const NODE_H = 62;
const LAYER_GAP = 90;
const COL_GAP = 150;
const PAD_X = 40;
const PAD_Y = 30;

function getNodePos(agent: FlowAgent, layerColCounts: number[]): { x: number; y: number } {
  const colCount = layerColCounts[agent.layer];
  const totalWidth = colCount * NODE_W + (colCount - 1) * (COL_GAP - NODE_W);
  const startX = PAD_X + (800 - 2 * PAD_X - totalWidth) / 2;
  const x = startX + agent.col * COL_GAP;
  const y = PAD_Y + agent.layer * LAYER_GAP;
  return { x, y };
}

interface PipelineMeta {
  service_online: boolean;
  active_cycles: number;
  total_cycles_completed: number;
  ready_count: number;
  total_agents: number;
  pipeline_state: 'offline' | 'ready' | 'evaluating' | 'error';
}

const PIPELINE_LABELS: Record<PipelineMeta['pipeline_state'], { label: string; color: string }> = {
  offline: { label: 'SERVICIO OFFLINE', color: '#5a5a78' },
  ready: { label: 'LISTO — ESPERANDO SEÑAL', color: '#ffd700' },
  evaluating: { label: 'EVALUANDO MERCADO', color: '#bf40ff' },
  error: { label: 'ERROR EN PIPELINE', color: '#ff1744' },
};

export default function AgentFlowchart() {
  const [agents, setAgents] = useState<FlowAgent[]>(AGENTS);
  const [hoveredAgent, setHoveredAgent] = useState<string | null>(null);
  const [meta, setMeta] = useState<PipelineMeta | null>(null);

  useEffect(() => {
    const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';
    let cancelled = false;

    async function fetchStatuses() {
      try {
        const res = await fetch(`${API_BASE}/api/agents/status`, {
          signal: AbortSignal.timeout(9000),
        });
        if (!res.ok || cancelled) return;
        const payload = await res.json();
        const rows: { agent_id?: string; id?: string; status?: string; last_active?: string }[] =
          Array.isArray(payload) ? payload : (payload?.agents ?? []);
        if (!Array.isArray(rows) || cancelled) return;

        if (!Array.isArray(payload) && payload?.meta) {
          setMeta(payload.meta as PipelineMeta);
        }

        const statusMap: Record<string, { status: AgentStatus; lastActive?: string }> = {};
        for (const a of rows) {
          const agentId = a.agent_id || a.id;
          if (agentId) {
            const raw = (a.status || 'offline').toLowerCase();
            statusMap[agentId] = {
              status: (['online', 'processing', 'idle', 'error'].includes(raw)
                ? raw
                : 'offline') as AgentStatus,
              lastActive: a.last_active as string | undefined,
            };
          }
        }

        setAgents((prev) =>
          prev.map((a) => ({
            ...a,
            status: statusMap[a.id]?.status ?? 'offline',
            lastActivity: statusMap[a.id]?.lastActive,
          }))
        );
      } catch {
        setMeta(null);
        setAgents((prev) => prev.map((a) => ({ ...a, status: 'offline' as AgentStatus })));
      }
    }

    fetchStatuses();
    const interval = setInterval(fetchStatuses, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const layerColCounts = useMemo(() => {
    const counts: number[] = [];
    agents.forEach((a) => {
      counts[a.layer] = Math.max(counts[a.layer] ?? 0, a.col + 1);
    });
    return counts;
  }, [agents]);

  const nodePositions = useMemo(() => {
    const map: Record<string, { x: number; y: number }> = {};
    agents.forEach((a) => {
      map[a.id] = getNodePos(a, layerColCounts);
    });
    return map;
  }, [agents, layerColCounts]);

  const maxLayer = Math.max(...agents.map((a) => a.layer));
  const svgHeight = PAD_Y * 2 + maxLayer * LAYER_GAP + NODE_H;
  const svgWidth = 800;

  const activeCount = meta?.ready_count ?? agents.filter(
    (a) => a.status === 'online' || a.status === 'processing' || a.status === 'idle',
  ).length;
  const hasProcessing = meta?.pipeline_state === 'evaluating' || agents.some((a) => a.status === 'processing');
  const pipelineInfo = meta ? PIPELINE_LABELS[meta.pipeline_state] : null;

  return (
    <NeonCard
      title="Agent Pipeline"
      glow="purple"
      noPadding
      icon={
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="5" r="3" />
          <line x1="12" y1="8" x2="12" y2="14" />
          <circle cx="6" cy="19" r="3" />
          <circle cx="18" cy="19" r="3" />
          <line x1="12" y1="14" x2="6" y2="16" />
          <line x1="12" y1="14" x2="18" y2="16" />
        </svg>
      }
      headerRight={
        <div className="flex items-center gap-2 flex-wrap justify-end">
          {pipelineInfo && (
            <span
              className="text-[9px] font-mono font-bold px-1.5 py-0.5 rounded border"
              style={{ color: pipelineInfo.color, borderColor: `${pipelineInfo.color}40`, backgroundColor: `${pipelineInfo.color}12` }}
            >
              {pipelineInfo.label}
            </span>
          )}
          <span className="text-[10px] font-mono" style={{ color: activeCount > 0 ? '#00ff88' : '#5a5a78' }}>
            {activeCount}/{agents.length} listos
          </span>
          {meta && (
            <span className="text-[9px] font-mono text-text-muted hidden sm:inline">
              ciclos={meta.total_cycles_completed}
            </span>
          )}
          <span
            className="h-2 w-2 rounded-full animate-pulse-dot"
            style={{ backgroundColor: hasProcessing ? '#bf40ff' : activeCount > 0 ? '#00ff88' : '#5a5a78' }}
          />
        </div>
      }
      className="h-full flex flex-col min-h-0"
    >
      {meta && (
        <div className="px-3 py-2 border-b border-border-primary/10 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] font-mono shrink-0">
          <span className={meta.service_online ? 'text-neon-green' : 'text-neon-red'}>
            ● Servicio {meta.service_online ? 'ONLINE' : 'OFFLINE'}
          </span>
          <span className="text-text-muted">
            Pipeline: <span style={{ color: pipelineInfo?.color }}>{pipelineInfo?.label ?? '—'}</span>
          </span>
          <span className="text-text-muted">
            Ciclos activos: <span className="text-neon-purple">{meta.active_cycles}</span>
          </span>
          <span className="text-text-muted">
            Completados: <span className="text-text-secondary">{meta.total_cycles_completed}</span>
          </span>
        </div>
      )}
      <div className="flex-1 overflow-x-auto overflow-y-auto p-2 min-h-0">
        <svg
          viewBox={`0 0 ${svgWidth} ${svgHeight}`}
          className="w-full min-w-[640px] h-auto"
          style={{ minHeight: Math.min(svgHeight, 520) }}
          preserveAspectRatio="xMidYMin meet"
        >
          <defs>
            <marker id="arrow" viewBox="0 0 10 6" refX="10" refY="3" markerWidth="8" markerHeight="6" orient="auto">
              <path d="M0 0 L10 3 L0 6 Z" fill="rgba(152,152,176,0.4)" />
            </marker>
            <marker id="arrow-active" viewBox="0 0 10 6" refX="10" refY="3" markerWidth="8" markerHeight="6" orient="auto">
              <path d="M0 0 L10 3 L0 6 Z" fill="rgba(191,64,255,0.6)" />
            </marker>
            <filter id="glow-processing">
              <feGaussianBlur stdDeviation="3" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          {/* ── Connection Lines ──────────────────────────────── */}
          {CONNECTIONS.map((conn, i) => {
            const fromPos = nodePositions[conn.from];
            const toPos = nodePositions[conn.to];
            if (!fromPos || !toPos) return null;

            const fromAgent = agents.find((a) => a.id === conn.from);
            const toAgent = agents.find((a) => a.id === conn.to);
            const isActive = fromAgent?.status === 'processing' || toAgent?.status === 'processing';

            const x1 = fromPos.x + NODE_W / 2;
            const y1 = fromPos.y + NODE_H;
            const x2 = toPos.x + NODE_W / 2;
            const y2 = toPos.y;
            const midY = (y1 + y2) / 2;

            return (
              <g key={`conn-${i}`}>
                <path
                  d={`M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`}
                  fill="none"
                  stroke={isActive ? 'rgba(191,64,255,0.35)' : 'rgba(152,152,176,0.15)'}
                  strokeWidth={isActive ? 2 : 1}
                  strokeDasharray={conn.animated && isActive ? '6 4' : undefined}
                  markerEnd={isActive ? 'url(#arrow-active)' : 'url(#arrow)'}
                >
                  {conn.animated && isActive && (
                    <animate attributeName="stroke-dashoffset" from="20" to="0" dur="1.5s" repeatCount="indefinite" />
                  )}
                </path>
                {conn.label && isActive && (
                  <text
                    x={(x1 + x2) / 2}
                    y={midY - 4}
                    textAnchor="middle"
                    className="text-[8px] font-mono"
                    fill="rgba(191,64,255,0.5)"
                  >
                    {conn.label}
                  </text>
                )}
              </g>
            );
          })}

          {/* ── Agent Nodes ───────────────────────────────────── */}
          {agents.map((agent) => {
            const pos = nodePositions[agent.id];
            if (!pos) return null;

            const cfg = STATUS_CONFIG[agent.status];
            const isHovered = hoveredAgent === agent.id;
            const isProcessing = agent.status === 'processing';

            return (
              <g
                key={agent.id}
                transform={`translate(${pos.x}, ${pos.y})`}
                onMouseEnter={() => setHoveredAgent(agent.id)}
                onMouseLeave={() => setHoveredAgent(null)}
                className="cursor-pointer"
                style={{ filter: isProcessing ? 'url(#glow-processing)' : undefined }}
              >
                {/* Node background */}
                <rect
                  x={0}
                  y={0}
                  width={NODE_W}
                  height={NODE_H}
                  rx={8}
                  fill={isHovered ? 'rgba(26,26,46,0.95)' : cfg.bg}
                  stroke={agent.color}
                  strokeWidth={isHovered ? 2 : 1}
                  strokeOpacity={agent.status === 'offline' ? 0.2 : isHovered ? 1 : 0.5}
                  style={{ filter: isHovered ? `drop-shadow(${cfg.glow})` : undefined }}
                />

                {/* Status dot */}
                <circle
                  cx={12}
                  cy={12}
                  r={4}
                  fill={cfg.dot}
                  opacity={agent.status === 'offline' ? 0.3 : 1}
                >
                  {(agent.status === 'online' || agent.status === 'processing') && (
                    <animate attributeName="opacity" values="1;0.4;1" dur="2s" repeatCount="indefinite" />
                  )}
                </circle>

                {/* Agent ID */}
                <text x={22} y={15} className="text-[9px] font-mono font-bold" fill={agent.color} opacity={agent.status === 'offline' ? 0.4 : 1}>
                  {agent.id}
                </text>

                {/* Agent short name */}
                <text x={NODE_W / 2} y={32} textAnchor="middle" className="text-[10px] font-mono font-semibold" fill={cfg.text}>
                  {agent.shortName}
                </text>

                {/* Model tag */}
                <text x={NODE_W / 2} y={48} textAnchor="middle" className="text-[7px] font-mono" fill="rgba(152,152,176,0.5)">
                  {agent.model}
                </text>

                {/* Status badge */}
                <rect
                  x={NODE_W - 45}
                  y={4}
                  width={40}
                  height={14}
                  rx={3}
                  fill={cfg.bg}
                  stroke={cfg.border}
                  strokeWidth={0.5}
                />
                <text x={NODE_W - 25} y={14} textAnchor="middle" className="text-[7px] font-mono font-bold" fill={cfg.text}>
                  {agent.status === 'processing' ? 'PROC' : agent.status === 'idle' ? 'READY' : agent.status.toUpperCase()}
                </text>

                {/* Hover tooltip */}
                {isHovered && (
                  <g>
                    <rect x={-10} y={NODE_H + 4} width={NODE_W + 20} height={44} rx={6} fill="rgba(10,10,15,0.95)" stroke={agent.color} strokeWidth={0.5} strokeOpacity={0.4} />
                    <text x={NODE_W / 2} y={NODE_H + 17} textAnchor="middle" className="text-[9px] font-mono" fill={agent.color}>{agent.name}</text>
                    <text x={NODE_W / 2} y={NODE_H + 29} textAnchor="middle" className="text-[8px] font-mono" fill="rgba(152,152,176,0.6)">via DeepSeek API</text>
                    <text x={NODE_W / 2} y={NODE_H + 40} textAnchor="middle" className="text-[7px] font-mono" fill={cfg.dot}>
                      {agent.lastActivity ? `last: ${new Date(agent.lastActivity).toLocaleTimeString()}` : 'no activity'}
                    </text>
                  </g>
                )}
              </g>
            );
          })}

          {/* ── Layer Labels ──────────────────────────────────── */}
          {[
            { layer: 0, label: 'ORCHESTRATION' },
            { layer: 1, label: 'REGIME' },
            { layer: 2, label: 'ANALYSIS' },
            { layer: 3, label: 'DEBATE' },
            { layer: 4, label: 'DECISION' },
            { layer: 5, label: 'RISK CHECK' },
            { layer: 6, label: 'EXECUTION' },
            { layer: 7, label: 'PORTFOLIO' },
          ].map(({ layer, label }) => (
            <text
              key={label}
              x={8}
              y={PAD_Y + layer * LAYER_GAP + NODE_H / 2 + 3}
              className="text-[8px] font-mono font-bold uppercase"
              fill="rgba(152,152,176,0.2)"
              textAnchor="start"
              transform={`rotate(-90, 8, ${PAD_Y + layer * LAYER_GAP + NODE_H / 2})`}
            >
              {label}
            </text>
          ))}
        </svg>
      </div>

      {/* ── Status Legend ──────────────────────────────────────── */}
      <div className="border-t border-border-primary/10 px-3 py-2 flex items-center gap-4 flex-wrap">
        {(['online', 'processing', 'idle', 'offline'] as AgentStatus[]).map((st) => (
          <div key={st} className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: STATUS_CONFIG[st].dot, opacity: st === 'offline' ? 0.3 : 1 }} />
            <span className="text-[9px] font-mono uppercase" style={{ color: STATUS_CONFIG[st].text }}>{st === 'idle' ? 'ready' : st}</span>
          </div>
        ))}
        <span className="ml-auto text-[9px] font-mono text-neon-purple">Powered by DeepSeek</span>
      </div>
    </NeonCard>
  );
}
