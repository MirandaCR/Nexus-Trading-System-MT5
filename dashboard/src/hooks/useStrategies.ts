'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { apiClient } from '@/lib/api-client';
import type {
  StrategyDTO,
  ResearchRunDTO,
} from '@/lib/api-client';

interface StrategiesState {
  strategies: StrategyDTO[];
  rules: Record<string, number> | null;
  log: ResearchRunDTO[];
  loading: boolean;
  error: string | null;
  lastUpdated: number | null;
  source: 'live' | 'unavailable';
}

const POLL_MS = 30_000;

export function useStrategies(autoRefreshMs: number = POLL_MS) {
  const [state, setState] = useState<StrategiesState>({
    strategies: [],
    rules: null,
    log: [],
    loading: true,
    error: null,
    lastUpdated: null,
    source: 'unavailable',
  });
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const [list, log] = await Promise.all([
        apiClient.listStrategies(),
        apiClient.getResearchLog(15).catch(() => ({ runs: [], count: 0 })),
      ]);
      if (!mounted.current) return;
      setState({
        strategies: list.items ?? [],
        rules: list.rules ?? null,
        log: log.runs ?? [],
        loading: false,
        error: null,
        lastUpdated: Date.now(),
        source: 'live',
      });
    } catch (err) {
      if (!mounted.current) return;
      setState((prev) => ({
        ...prev,
        loading: false,
        error: err instanceof Error ? err.message : 'Could not load strategies',
        source: 'unavailable',
      }));
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    refresh();
    const id = setInterval(refresh, autoRefreshMs);
    return () => {
      mounted.current = false;
      clearInterval(id);
    };
  }, [refresh, autoRefreshMs]);

  const triggerResearch = useCallback(async (n = 2) => {
    const result = await apiClient.triggerResearch(n);
    await refresh();
    return result;
  }, [refresh]);

  const reevaluate = useCallback(async (id: string) => {
    const result = await apiClient.reevaluateStrategy(id);
    await refresh();
    return result;
  }, [refresh]);

  const setStatus = useCallback(async (id: string, status: string) => {
    const result = await apiClient.setStrategyStatus(id, status);
    await refresh();
    return result;
  }, [refresh]);

  const exportMQL5 = useCallback(async (id: string) => {
    return apiClient.exportStrategyMQL5(id);
  }, []);

  return {
    ...state,
    refresh,
    triggerResearch,
    reevaluate,
    setStatus,
    exportMQL5,
  };
}
