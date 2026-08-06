'use client';

import { useState, useCallback, useEffect } from 'react';
import { gatewayWsUrl } from '@/lib/gateway-ws';
import { useWebSocket } from './useWebSocket';

export interface Position {
  id: string;
  symbol: string;
  side: 'LONG' | 'SHORT';
  entry: number;
  current: number;
  sl: number;
  tp: number;
  size: number;
  pnl: number;
  pnlPercent: number;
  riskReward: number;
  openTime: string;
  session: string;
  conviction: number;
  agentId: string;
}

const MOCK_POSITIONS: Position[] = [
  { id: 'pos-1', symbol: 'EUR/USD', side: 'LONG', entry: 1.0845, current: 1.0872, sl: 1.0815, tp: 1.0920, size: 0.5, pnl: 135, pnlPercent: 0.25, riskReward: 2.5, openTime: '2026-04-13T08:30:00Z', session: 'London', conviction: 8, agentId: 'analyst-1' },
  { id: 'pos-2', symbol: 'GBP/USD', side: 'SHORT', entry: 1.2650, current: 1.2618, sl: 1.2690, tp: 1.2550, size: 0.3, pnl: 96, pnlPercent: 0.25, riskReward: 2.5, openTime: '2026-04-13T09:15:00Z', session: 'London', conviction: 7, agentId: 'analyst-2' },
  { id: 'pos-3', symbol: 'USD/JPY', side: 'LONG', entry: 151.85, current: 151.62, sl: 151.50, tp: 152.50, size: 0.4, pnl: -92, pnlPercent: -0.15, riskReward: 1.86, openTime: '2026-04-13T03:00:00Z', session: 'Tokyo', conviction: 6, agentId: 'analyst-1' },
];

export function usePositions() {
  const [positions, setPositions] = useState<Position[]>(MOCK_POSITIONS);

  const { isConnected, lastMessage } = useWebSocket({
    url: gatewayWsUrl('/positions'),
    enabled: true,
  });

  useEffect(() => {
    if (lastMessage && typeof lastMessage === 'object' && 'positions' in (lastMessage as Record<string, unknown>)) {
      setPositions((lastMessage as { positions: Position[] }).positions);
    }
  }, [lastMessage]);

  const closePosition = useCallback((id: string) => {
    setPositions((prev) => prev.filter((p) => p.id !== id));
  }, []);

  const totalPnl = positions.reduce((sum, p) => sum + p.pnl, 0);
  const openCount = positions.length;

  return {
    positions,
    totalPnl,
    openCount,
    closePosition,
    isConnected,
  };
}
