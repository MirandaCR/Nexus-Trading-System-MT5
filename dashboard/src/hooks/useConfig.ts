'use client';

import { useState, useCallback } from 'react';
import { apiClient } from '@/lib/api-client';
import { CONFIG_PANEL_STORAGE_KEY } from '@/lib/ui-settings';

export interface AgentConfig {
  id: string;
  name: string;
  type: string;
  enabled: boolean;
  model: 'cloud' | 'local' | 'off';
  color: string;
}

export interface RiskParams {
  riskPerTrade: number;
  dailyLossLimit: number;
  weeklyDDLimit: number;
  maxPositions: number;
  maxCorrelation: number;
  minRR: number;
  dailyTradeLimit: number;
  cooldownSec: number;
  minConviction: number;
}

export interface AssetConfig {
  symbol: string;
  broker: 'MT5' | 'IBKR' | 'CCXT';
  enabled: boolean;
  category: 'forex' | 'index' | 'crypto';
}

export interface BrokerConnection {
  name: 'MT5' | 'IBKR' | 'CCXT';
  status: 'connected' | 'disconnected' | 'connecting';
  exchange?: string;
}

export interface NexusConfig {
  tradingMode: 'PAPER' | 'LIVE';
  autoTrade: boolean;
  agents: AgentConfig[];
  risk: RiskParams;
  assets: AssetConfig[];
  brokers: BrokerConnection[];
}

const MOCK_CONFIG: NexusConfig = {
  tradingMode: 'PAPER',
  autoTrade: false,
  agents: [
    { id: 'orch-1', name: 'Orchestrator', type: 'orchestrator', enabled: true, model: 'cloud', color: '#bf5af2' },
    { id: 'ta-1', name: 'Technical Analyst', type: 'analyst', enabled: true, model: 'cloud', color: '#00e5ff' },
    { id: 'ta-2', name: 'Macro Analyst', type: 'analyst', enabled: true, model: 'cloud', color: '#00e5ff' },
    { id: 'risk-1', name: 'Risk Manager', type: 'risk', enabled: true, model: 'local', color: '#ff9500' },
    { id: 'exec-1', name: 'Executor', type: 'executor', enabled: true, model: 'local', color: '#39ff14' },
    { id: 'mon-1', name: 'Position Monitor', type: 'monitor', enabled: true, model: 'local', color: '#4d9fff' },
    { id: 'jrn-1', name: 'Journaler', type: 'journal', enabled: true, model: 'cloud', color: '#ffd600' },
    { id: 'news-1', name: 'News Analyst', type: 'news', enabled: true, model: 'cloud', color: '#ff2d78' },
    { id: 'strat-1', name: 'Strategy Selector', type: 'strategy', enabled: true, model: 'cloud', color: '#bf5af2' },
    { id: 'senti-1', name: 'Sentiment Analyzer', type: 'analyst', enabled: true, model: 'cloud', color: '#00e5ff' },
    { id: 'inter-1', name: 'Intermarket Agent', type: 'analyst', enabled: true, model: 'local', color: '#4d9fff' },
    { id: 'flow-1', name: 'Order Flow Agent', type: 'analyst', enabled: true, model: 'local', color: '#39ff14' },
  ],
  risk: {
    riskPerTrade: 2.0,
    dailyLossLimit: 12.0,
    weeklyDDLimit: 18.0,
    maxPositions: 8,
    maxCorrelation: 0.95,
    minRR: 1.0,
    dailyTradeLimit: 0,
    cooldownSec: 120,
    minConviction: 60,
  },
  assets: [
    { symbol: 'XAUUSD', broker: 'MT5', enabled: true, category: 'forex' },
    { symbol: 'BTCUSD', broker: 'MT5', enabled: true, category: 'crypto' },
    { symbol: 'EURUSD', broker: 'MT5', enabled: true, category: 'forex' },
    { symbol: 'GBPUSD', broker: 'MT5', enabled: false, category: 'forex' },
    { symbol: 'USDJPY', broker: 'MT5', enabled: false, category: 'forex' },
    { symbol: 'AUDUSD', broker: 'MT5', enabled: false, category: 'forex' },
    { symbol: 'USDCHF', broker: 'MT5', enabled: false, category: 'forex' },
    { symbol: 'USDCAD', broker: 'MT5', enabled: false, category: 'forex' },
    { symbol: 'SPX', broker: 'IBKR', enabled: true, category: 'index' },
    { symbol: 'NQ', broker: 'IBKR', enabled: true, category: 'index' },
    { symbol: 'DJI', broker: 'IBKR', enabled: false, category: 'index' },
    { symbol: 'BTC', broker: 'CCXT', enabled: false, category: 'crypto' },
    { symbol: 'ETH', broker: 'CCXT', enabled: false, category: 'crypto' },
    { symbol: 'SOL', broker: 'CCXT', enabled: false, category: 'crypto' },
  ],
  brokers: [
    { name: 'MT5', status: 'connected' },
    { name: 'IBKR', status: 'disconnected' },
    { name: 'CCXT', status: 'disconnected', exchange: 'Binance' },
  ],
};

function loadStoredConfig(): NexusConfig {
  if (typeof window === 'undefined') return MOCK_CONFIG;
  try {
    const raw = localStorage.getItem(CONFIG_PANEL_STORAGE_KEY);
    if (!raw) return MOCK_CONFIG;
    const parsed = JSON.parse(raw) as Partial<NexusConfig>;
    return {
      ...MOCK_CONFIG,
      ...parsed,
      agents: parsed.agents ?? MOCK_CONFIG.agents,
      assets: parsed.assets ?? MOCK_CONFIG.assets,
      risk: { ...MOCK_CONFIG.risk, ...parsed.risk },
      brokers: parsed.brokers ?? MOCK_CONFIG.brokers,
    };
  } catch {
    return MOCK_CONFIG;
  }
}

export function useConfig() {
  const [config, setConfig] = useState<NexusConfig>(() => loadStoredConfig());
  const [isDirty, setIsDirty] = useState(false);

  const updateConfig = useCallback(<K extends keyof NexusConfig>(key: K, value: NexusConfig[K]) => {
    setConfig((prev) => ({ ...prev, [key]: value }));
    setIsDirty(true);
  }, []);

  const toggleAgent = useCallback((agentId: string) => {
    setConfig((prev) => ({
      ...prev,
      agents: prev.agents.map((a) => (a.id === agentId ? { ...a, enabled: !a.enabled } : a)),
    }));
    setIsDirty(true);
  }, []);

  const setAgentModel = useCallback((agentId: string, model: 'cloud' | 'local' | 'off') => {
    setConfig((prev) => ({
      ...prev,
      agents: prev.agents.map((a) => (a.id === agentId ? { ...a, model } : a)),
    }));
    setIsDirty(true);
  }, []);

  const toggleAsset = useCallback((symbol: string) => {
    setConfig((prev) => ({
      ...prev,
      assets: prev.assets.map((a) => (a.symbol === symbol ? { ...a, enabled: !a.enabled } : a)),
    }));
    setIsDirty(true);
  }, []);

  const updateRisk = useCallback(<K extends keyof RiskParams>(key: K, value: RiskParams[K]) => {
    setConfig((prev) => ({ ...prev, risk: { ...prev.risk, [key]: value } }));
    setIsDirty(true);
  }, []);

  const toggleBroker = useCallback((name: string) => {
    setConfig((prev) => ({
      ...prev,
      brokers: prev.brokers.map((b) =>
        b.name === name
          ? { ...b, status: b.status === 'connected' ? 'disconnected' : 'connecting' }
          : b
      ),
    }));
    setIsDirty(true);
  }, []);

  const save = useCallback(async () => {
    localStorage.setItem(CONFIG_PANEL_STORAGE_KEY, JSON.stringify(config));
    try {
      const sym =
        config.assets.find((a) => a.enabled)?.symbol?.replace(/\//g, '').replace('_', '') ?? 'XAUUSD';
      await apiClient.putGatewaySettings({ default_symbol: sym });
    } catch {
      /* gateway offline */
    }
    try {
      await apiClient.putRiskRules({
        max_risk_per_trade_pct: config.risk.riskPerTrade,
        daily_trade_limit: config.risk.dailyTradeLimit,
        cooldown_after_loss_sec: config.risk.cooldownSec,
        min_conviction: config.risk.minConviction,
      });
    } catch {
      /* risk service offline */
    }
    setIsDirty(false);
  }, [config]);

  return {
    config,
    isDirty,
    updateConfig,
    toggleAgent,
    setAgentModel,
    toggleAsset,
    updateRisk,
    toggleBroker,
    save,
  };
}
