'use client';

import { useState, useEffect } from 'react';
import { gatewayWsUrl } from '@/lib/gateway-ws';
import { useWebSocket } from './useWebSocket';

export interface AgentStatus {
  id: string;
  name: string;
  type: 'orchestrator' | 'analyst' | 'risk' | 'executor' | 'monitor' | 'journal' | 'news' | 'strategy';
  status: 'idle' | 'thinking' | 'acting' | 'error' | 'disabled';
  model: 'cloud' | 'local' | 'off';
  lastAction: string;
  lastActionTime: string;
  reasoning?: string;
  confidence?: number;
  tokensUsed?: number;
}

const MOCK_AGENTS: AgentStatus[] = [
  { id: 'orch-1', name: 'Orchestrator', type: 'orchestrator', status: 'thinking', model: 'cloud', lastAction: 'Evaluating EUR/USD setup', lastActionTime: '2s ago', confidence: 0.85, tokensUsed: 1240 },
  { id: 'ta-1', name: 'Technical Analyst', type: 'analyst', status: 'acting', model: 'cloud', lastAction: 'Multi-TF analysis GBP/USD', lastActionTime: '5s ago', confidence: 0.78, tokensUsed: 890 },
  { id: 'ta-2', name: 'Macro Analyst', type: 'analyst', status: 'idle', model: 'cloud', lastAction: 'DXY correlation check', lastActionTime: '30s ago', confidence: 0.72, tokensUsed: 650 },
  { id: 'risk-1', name: 'Risk Manager', type: 'risk', status: 'idle', model: 'local', lastAction: 'Portfolio exposure check', lastActionTime: '10s ago', confidence: 0.92, tokensUsed: 320 },
  { id: 'exec-1', name: 'Executor', type: 'executor', status: 'idle', model: 'local', lastAction: 'Filled EUR/USD LONG', lastActionTime: '2m ago', tokensUsed: 180 },
  { id: 'mon-1', name: 'Position Monitor', type: 'monitor', status: 'acting', model: 'local', lastAction: 'Trailing SL EUR/USD', lastActionTime: '1s ago', tokensUsed: 240 },
  { id: 'jrn-1', name: 'Journaler', type: 'journal', status: 'idle', model: 'cloud', lastAction: 'Logged GBP/USD entry', lastActionTime: '5m ago', tokensUsed: 560 },
  { id: 'news-1', name: 'News Analyst', type: 'news', status: 'thinking', model: 'cloud', lastAction: 'Scanning Fed minutes', lastActionTime: '15s ago', confidence: 0.65, tokensUsed: 2100 },
  { id: 'strat-1', name: 'Strategy Selector', type: 'strategy', status: 'idle', model: 'cloud', lastAction: 'Selected breakout mode', lastActionTime: '1m ago', confidence: 0.80, tokensUsed: 780 },
  { id: 'senti-1', name: 'Sentiment Analyzer', type: 'analyst', status: 'idle', model: 'cloud', lastAction: 'COT data parsed', lastActionTime: '15m ago', confidence: 0.70, tokensUsed: 450 },
  { id: 'inter-1', name: 'Intermarket Agent', type: 'analyst', status: 'acting', model: 'local', lastAction: 'Cross-asset correlation', lastActionTime: '3s ago', confidence: 0.88, tokensUsed: 380 },
  { id: 'flow-1', name: 'Order Flow Agent', type: 'analyst', status: 'idle', model: 'local', lastAction: 'Volume delta spike', lastActionTime: '8s ago', confidence: 0.75, tokensUsed: 290 },
];

export function useAgents() {
  const [agents, setAgents] = useState<AgentStatus[]>(MOCK_AGENTS);
  const [reasoningStream, setReasoningStream] = useState<string>('');

  const { isConnected, lastMessage } = useWebSocket({
    url: gatewayWsUrl('/agents'),
    enabled: true,
  });

  useEffect(() => {
    if (lastMessage && typeof lastMessage === 'object') {
      const msg = lastMessage as Record<string, unknown>;
      if ('agents' in msg) setAgents(msg.agents as AgentStatus[]);
      if ('reasoning' in msg) setReasoningStream(msg.reasoning as string);
    }
  }, [lastMessage]);

  const activeCount = agents.filter((a) => a.status === 'thinking' || a.status === 'acting').length;
  const errorCount = agents.filter((a) => a.status === 'error').length;

  return { agents, activeCount, errorCount, reasoningStream, isConnected };
}
