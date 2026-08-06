import type { Layout } from 'react-grid-layout';
import type { AgentId } from './agents';

export interface ApiResponse<T = unknown> {
  success: boolean;
  data: T;
  message?: string;
  timestamp: string;
}

export interface ApiError {
  success: false;
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
  timestamp: string;
}

export interface PaginatedResponse<T> {
  success: boolean;
  data: T[];
  pagination: {
    page: number;
    pageSize: number;
    total: number;
    totalPages: number;
    hasNext: boolean;
    hasPrevious: boolean;
  };
  timestamp: string;
}

export type WSChannel =
  | 'ticks'
  | 'candles'
  | 'positions'
  | 'orders'
  | 'signals'
  | 'agents'
  | 'risk'
  | 'session'
  | 'health'
  | 'news'
  | 'logs'
  | 'notifications';

export interface WSMessage<T = unknown> {
  channel: WSChannel;
  event: string;
  data: T;
  timestamp: string;
  sequence?: number;
}

export interface WSSubscription {
  channel: WSChannel;
  params?: Record<string, string | number | boolean>;
}

export interface DashboardWindow {
  id: string;
  title: string;
  visible: boolean;
  locked: boolean;
}

export interface DashboardLayout {
  id: string;
  name: string;
  layouts: {
    lg: Layout[];
    md: Layout[];
    sm?: Layout[];
    xs?: Layout[];
  };
  windows: DashboardWindow[];
  createdAt: string;
  updatedAt: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  token: string;
  expiresAt: string;
  user: {
    id: string;
    username: string;
    role: string;
  };
}

export interface TradeHistoryRequest {
  page?: number;
  pageSize?: number;
  symbol?: string;
  startDate?: string;
  endDate?: string;
  direction?: 'buy' | 'sell';
  agentId?: AgentId;
}

export interface SettingsUpdateRequest {
  section: string;
  values: Record<string, unknown>;
}

export interface CommandRequest {
  command: 'start_session' | 'stop_session' | 'pause' | 'resume' | 'close_all' | 'emergency_stop';
  params?: Record<string, unknown>;
}

export interface CommandResponse {
  success: boolean;
  command: string;
  message: string;
  executedAt: string;
}
