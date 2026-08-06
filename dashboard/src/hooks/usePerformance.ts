'use client';

import { useState, useEffect } from 'react';
import { useWebSocket } from './useWebSocket';

export interface PerformanceMetrics {
  equity: number;
  todayPnl: number;
  weekPnl: number;
  monthPnl: number;
  totalReturn: number;
  sharpeRatio: number;
  maxDrawdown: number;
  winRate: number;
  profitFactor: number;
  avgRR: number;
  totalTrades: number;
  equityCurve: { date: string; equity: number }[];
  dailyPnl: { date: string; pnl: number }[];
}

const MOCK_PERFORMANCE: PerformanceMetrics = {
  equity: 52_840,
  todayPnl: 425,
  weekPnl: 1_680,
  monthPnl: 4_210,
  totalReturn: 5.68,
  sharpeRatio: 2.15,
  maxDrawdown: -3.2,
  winRate: 68.5,
  profitFactor: 2.4,
  avgRR: 2.35,
  totalTrades: 156,
  equityCurve: Array.from({ length: 30 }, (_, i) => ({
    date: `2026-03-${String(15 + i).padStart(2, '0')}`,
    equity: 50000 + Math.round((i * 100 + Math.sin(i * 0.5) * 300) * 100) / 100,
  })),
  dailyPnl: Array.from({ length: 20 }, (_, i) => ({
    date: `2026-03-${String(25 + i).padStart(2, '0')}`,
    pnl: Math.round((Math.sin(i * 1.73) * 220 + 80) * 100) / 100,
  })),
};

export function usePerformance() {
  const [metrics, setMetrics] = useState<PerformanceMetrics>(MOCK_PERFORMANCE);

  const { isConnected, lastMessage } = useWebSocket({
    url: 'ws://127.0.0.1:8000/ws/performance',
    enabled: false,
  });

  useEffect(() => {
    if (lastMessage && typeof lastMessage === 'object' && 'metrics' in (lastMessage as Record<string, unknown>)) {
      setMetrics((lastMessage as { metrics: PerformanceMetrics }).metrics);
    }
  }, [lastMessage]);

  return { metrics, isConnected };
}
