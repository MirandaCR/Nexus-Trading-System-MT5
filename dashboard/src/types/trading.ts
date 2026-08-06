export interface Tick {
  symbol: string;
  bid: number;
  ask: number;
  spread: number;
  timestamp: string;
  volume?: number;
}

export interface OHLCV {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  tickVolume?: number;
}

export type SignalDirection = 'buy' | 'sell';

export interface Signal {
  id: string;
  symbol: string;
  direction: SignalDirection;
  confidence: number;
  entryPrice: number;
  stopLoss: number;
  takeProfit: number;
  takeProfit2?: number;
  takeProfit3?: number;
  timestamp: string;
  timeframe: string;
  agentId: string;
  strategy: string;
  reasoning?: string;
  riskRewardRatio: number;
  status: 'pending' | 'approved' | 'rejected' | 'executed' | 'expired';
  indicators?: Record<string, number>;
}

export interface Position {
  ticket: number;
  symbol: string;
  direction: SignalDirection;
  volume: number;
  entryPrice: number;
  currentPrice: number;
  stopLoss: number;
  takeProfit: number;
  pnl: number;
  pnlPips: number;
  pnlPercent: number;
  swap: number;
  commission: number;
  openTime: string;
  magic: number;
  comment?: string;
  trailingActive?: boolean;
}

export interface Trade {
  id: string;
  ticket: number;
  symbol: string;
  direction: SignalDirection;
  volume: number;
  entryPrice: number;
  exitPrice: number;
  stopLoss: number;
  takeProfit: number;
  pnl: number;
  pips: number;
  pnlPercent: number;
  swap: number;
  commission: number;
  openTime: string;
  closeTime: string;
  duration: number;
  agentId: string;
  strategy: string;
  signalId?: string;
  comment?: string;
}

export type OrderType =
  | 'market'
  | 'limit'
  | 'stop'
  | 'stop_limit';

export type OrderStatus =
  | 'pending'
  | 'filled'
  | 'partially_filled'
  | 'cancelled'
  | 'rejected'
  | 'expired';

export interface Order {
  ticket: number;
  symbol: string;
  type: OrderType;
  direction: SignalDirection;
  volume: number;
  price: number;
  stopLoss: number;
  takeProfit: number;
  status: OrderStatus;
  filledVolume?: number;
  filledPrice?: number;
  createdAt: string;
  updatedAt: string;
  expiresAt?: string;
  comment?: string;
}

export type CircuitBreakerLevel = 'normal' | 'caution' | 'warning' | 'critical' | 'halted';

export interface RiskState {
  circuitBreaker: CircuitBreakerLevel;
  dailyPnl: number;
  dailyPnlPercent: number;
  maxDailyLoss: number;
  maxDailyLossPercent: number;
  currentDrawdown: number;
  maxDrawdown: number;
  openRisk: number;
  riskPerTrade: number;
  marginUsed: number;
  marginFree: number;
  marginLevel: number;
  openPositionsCount: number;
  maxOpenPositions: number;
  correlationRisk: number;
  newsRiskActive: boolean;
  lastUpdate: string;
}

export type SessionMode = 'paper' | 'live';

export interface SessionState {
  id: string;
  mode: SessionMode;
  startTime: string;
  uptime: number;
  symbol: string;
  timeframe: string;
  autoTrade: boolean;
  balance: number;
  equity: number;
  todayPnl: number;
  todayTrades: number;
  todayWins: number;
  todayLosses: number;
  isMarketOpen: boolean;
  serverTime: string;
  latencyMs: number;
}

export interface PerformanceMetrics {
  totalTrades: number;
  winRate: number;
  profitFactor: number;
  sharpeRatio: number;
  sortinoRatio: number;
  maxDrawdown: number;
  maxDrawdownPercent: number;
  averageWin: number;
  averageLoss: number;
  averageRR: number;
  expectancy: number;
  totalPnl: number;
  totalPnlPercent: number;
  bestTrade: number;
  worstTrade: number;
  averageDuration: number;
  longestWinStreak: number;
  longestLoseStreak: number;
  recoveryFactor: number;
  calmarRatio: number;
  equityCurve: { timestamp: string; equity: number }[];
}

export interface NewsItem {
  id: string;
  title: string;
  source: string;
  url?: string;
  summary?: string;
  sentiment: 'bullish' | 'bearish' | 'neutral';
  impact: 'low' | 'medium' | 'high';
  relatedSymbols: string[];
  publishedAt: string;
}

export interface CalendarEvent {
  id: string;
  title: string;
  country: string;
  currency: string;
  impact: 'low' | 'medium' | 'high';
  forecast?: string;
  previous?: string;
  actual?: string;
  scheduledAt: string;
}

export type ComponentHealthStatus = 'healthy' | 'degraded' | 'down' | 'unknown';

export interface ComponentHealth {
  name: string;
  status: ComponentHealthStatus;
  latencyMs?: number;
  message?: string;
  lastCheck: string;
}

export interface SystemHealth {
  status: ComponentHealthStatus;
  uptime: number;
  version: string;
  components: ComponentHealth[];
  cpuPercent?: number;
  memoryPercent?: number;
  timestamp: string;
}
