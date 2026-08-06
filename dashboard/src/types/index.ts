export type { AgentId, AgentRole, AgentStatus, AgentMessage, AgentPipelineState } from './agents';
export type {
  Tick,
  OHLCV,
  Signal,
  SignalDirection,
  Position,
  Trade,
  Order,
  OrderType,
  OrderStatus,
  RiskState,
  CircuitBreakerLevel,
  SessionState,
  SessionMode,
  PerformanceMetrics,
  NewsItem,
  CalendarEvent,
  SystemHealth,
  ComponentHealth,
} from './trading';
export type {
  ApiResponse,
  PaginatedResponse,
  WSMessage,
  WSChannel,
  DashboardLayout,
  DashboardWindow,
} from './api';

// Additional types used across dashboard components

export type TradingMode = 'PAPER' | 'LIVE' | 'BACKTEST';
export type Timeframe = 'M1' | 'M5' | 'M15' | 'H1' | 'H4';
export type Verdict = 'BUY' | 'SELL' | 'NO_TRADE' | 'WATCH';
export type MessageType = 'analysis' | 'debate' | 'decision' | 'management' | 'execution';

export interface LayoutItem {
  i: string;
  x: number;
  y: number;
  w: number;
  h: number;
  minW?: number;
  minH?: number;
  maxW?: number;
  maxH?: number;
  static?: boolean;
}

export interface AccountInfo {
  balance: number;
  equity: number;
  margin: number;
  freeMargin: number;
  dailyPnL: number;
  openPositions: number;
}

export interface SRLevel {
  price: number;
  type: 'support' | 'resistance';
  strength: number;
}

export interface TradeMarker {
  time: number;
  position: 'aboveBar' | 'belowBar';
  color: string;
  shape: 'arrowUp' | 'arrowDown';
  text: string;
}

export interface AgentConsoleMessage {
  id: string;
  agent_id: string;
  agent_name: string;
  agent_color: string;
  message_type: MessageType;
  content: string;
  metadata: Record<string, unknown>;
  timestamp: string;
}

export type NeonVariant = 'success' | 'danger' | 'warning' | 'info' | 'neutral';
export type Impact = 'low' | 'medium' | 'high';
export type Sentiment = 'bullish' | 'bearish' | 'neutral';

export interface EquityPoint {
  timestamp: string;
  equity: number;
}

export interface DailyPL {
  date: string;
  pnl: number;
  trades?: number;
  winRate?: number;
}

export const AGENT_REGISTRY: Record<string, { name: string; color: string }> = {
  A1:  { name: 'Market Analyst',     color: '#00f5ff' },
  A2:  { name: 'News Sentinel',      color: '#ffd700' },
  A3:  { name: 'Intermarket',        color: '#ff6b35' },
  A4:  { name: 'Order Flow',         color: '#ff1493' },
  A5:  { name: 'Bull Researcher',    color: '#39ff14' },
  A6:  { name: 'Bear Researcher',    color: '#ff1744' },
  A7:  { name: 'Arbitrator',         color: '#bf40ff' },
  A8:  { name: 'Risk Manager',       color: '#ff4444' },
  A9:  { name: 'Trade Executor',     color: '#00ff88' },
  A10: { name: 'Position Manager',   color: '#4488ff' },
  A11: { name: 'Session Governor',   color: '#ffaa00' },
  A12: { name: 'Portfolio Strategist', color: '#aa44ff' },
};
