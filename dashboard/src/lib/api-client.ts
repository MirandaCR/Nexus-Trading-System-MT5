import type {
  ApiResponse,
  PaginatedResponse,
  LoginRequest,
  LoginResponse,
  TradeHistoryRequest,
  SettingsUpdateRequest,
  CommandRequest,
  CommandResponse,
} from '@/types/api';
import type {
  Position,
  Trade,
  Signal,
  Order,
  RiskState,
  SessionState,
  PerformanceMetrics,
  SystemHealth,
  ComponentHealth,
  ComponentHealthStatus,
  NewsItem,
  CalendarEvent,
  OHLCV,
} from '@/types/trading';
import type { AgentPipelineState } from '@/types/agents';

// ── Strategy Lab DTOs (mirrors nexus_agents/research/registry.py) ───────────
export interface StrategyDTO {
  id: string;
  name: string;
  short_name: string;
  type: string;
  description: string;
  status: 'active' | 'backtested' | 'investigating' | 'rejected' | 'paused';
  pairs: string[];
  timeframes: string[];
  agents: string[];
  entry_logic: string;
  exit_logic: string;
  color: string;
  win_rate: number;
  profit_factor: number;
  sharpe: number;
  max_dd_pct: number;
  total_trades: number;
  avg_rr: number;
  net_pnl_pct: number;
  recent_results: boolean[];
  promotion_reasons: string[];
  proposed_by: string;
  created_at: number;
  updated_at: number;
  mql5_signal_code?: string;
}

export interface ResearchRunDTO {
  ts: number;
  duration_ms: number;
  proposed: number;
  accepted: Array<{
    id: string;
    name: string;
    status: string;
    profit_factor: number;
    win_rate: number;
    sharpe: number;
    max_dd_pct: number;
    total_trades: number;
    promotion_reasons: string[];
  }>;
  rejected: Array<{
    id: string;
    name: string;
    status: string;
    profit_factor: number;
    win_rate: number;
    sharpe: number;
    max_dd_pct: number;
    total_trades: number;
    promotion_reasons: string[];
  }>;
}

export interface LiveDecisionDTO {
  cycle_id: string;
  verdict: string;
  conviction: number;
  reasoning: string;
  session: string;
  risk_decision: string;
  risk_reasoning: string;
  position_size: number;
  ml_signal?: Record<string, unknown>;
  regime_analysis?: {
    regime: string;
    regime_strength: number;
    suggested_approach: string;
    vol_regime: string;
    adx: number;
    bb_width: number;
    range_info?: Record<string, unknown>;
    breakout_signals?: Record<string, unknown>;
  };
  ts: string;
}

/** HTTP gateway (FastAPI). Market routes live under ``/api/v2/market``; everything else under ``/api``. */
const GATEWAY_ORIGIN = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

function nowIso(): string {
  return new Date().toISOString();
}

function wrap<T>(data: T): ApiResponse<T> {
  return { success: true, data, timestamp: nowIso() };
}

function parseGatewayHealthStatus(raw: unknown): ComponentHealthStatus {
  const s = String(raw ?? 'unknown').toLowerCase();
  if (s === 'healthy' || s === 'ok' || s === 'up') return 'healthy';
  if (s === 'degraded' || s === 'warning') return 'degraded';
  if (s === 'down' || s === 'unhealthy' || s === 'error') return 'down';
  return 'unknown';
}

function redisWireStatus(v: unknown): ComponentHealthStatus {
  if (v === true || v === 'connected') return 'healthy';
  if (v === false || v === 'disconnected') return 'down';
  return parseGatewayHealthStatus(v);
}

function duckWireStatus(v: unknown): ComponentHealthStatus {
  if (v === 'connected') return 'healthy';
  if (v === 'unavailable' || v === false) return 'down';
  if (v === true) return 'healthy';
  return 'unknown';
}

function mapGatewayHealthPayload(raw: Record<string, unknown>): SystemHealth {
  const ts = typeof raw.timestamp === 'string' ? raw.timestamp : nowIso();
  const overall = parseGatewayHealthStatus(raw.status);
  const components: ComponentHealth[] = [];

  if (raw.service !== undefined) {
    components.push({
      name: String(raw.service),
      status: overall,
      lastCheck: ts,
    });
  }

  if (raw.redis !== undefined) {
    components.push({
      name: 'redis',
      status: redisWireStatus(raw.redis),
      lastCheck: ts,
    });
  }

  if (raw.duckdb !== undefined) {
    components.push({
      name: 'duckdb',
      status: duckWireStatus(raw.duckdb),
      lastCheck: ts,
    });
  }

  return {
    status: overall,
    uptime: typeof raw.uptime === 'number' ? raw.uptime : 0,
    version: String(raw.version ?? '1.0.0'),
    components,
    cpuPercent: typeof raw.cpu_pct === 'number' ? raw.cpu_pct : undefined,
    memoryPercent: typeof raw.ram_pct === 'number' ? raw.ram_pct : undefined,
    timestamp: ts,
  };
}

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

class ApiClient {
  constructor(private readonly origin: string) {}

  private getToken(): string | null {
    if (typeof window === 'undefined') return null;
    return localStorage.getItem('nexus-token');
  }

  private async fetchPath(path: string, options: RequestInit = {}): Promise<Response> {
    const token = this.getToken();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...((options.headers as Record<string, string>) ?? {}),
    };

    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const res = await fetch(`${this.origin}${path}`, {
      ...options,
      headers,
    });

    if (res.status === 401) {
      if (typeof window !== 'undefined') {
        localStorage.removeItem('nexus-token');
        window.location.href = '/login';
      }
      throw new Error('Unauthorized');
    }

    return res;
  }

  private async parseOk<T>(res: Response): Promise<T> {
    if (!res.ok) {
      const errorBody = await res.json().catch(() => ({}));
      throw new ApiError(
        res.status,
        (errorBody as { detail?: string }).detail ||
          (errorBody as { error?: { message?: string } }).error?.message ||
          res.statusText,
        (errorBody as { error?: { code?: string } }).error?.code,
      );
    }
    return res.json() as Promise<T>;
  }

  // ── Auth (optional — gateway may not expose auth yet) ───────────────
  login(credentials: LoginRequest) {
    return this.postJson<ApiResponse<LoginResponse>>('/api/v2/auth/login', credentials);
  }

  logout() {
    return this.postJson<ApiResponse>('/api/v2/auth/logout');
  }

  // ── Session (stub paths — adapt when gateway adds routes) ────────────
  getSession() {
    return this.getJson<ApiResponse<SessionState>>('/api/v2/session');
  }

  sendCommand(cmd: CommandRequest) {
    return this.postJson<ApiResponse<CommandResponse>>('/api/v2/session/command', cmd);
  }

  // ── Market (v2) ──────────────────────────────────────────────────────
  getCandles(symbol: string, timeframe: string, count = 500) {
    const q = new URLSearchParams({
      symbol,
      timeframe,
      count: String(count),
    });
    return this.getJson<ApiResponse<OHLCV[]>>(`/api/v2/market/candles?${q}`);
  }

  getSymbols() {
    return this.getJson<ApiResponse<string[]>>('/api/v2/market/symbols');
  }

  getTicks(symbol: string, count = 50, source: 'cache' | 'db' = 'cache') {
    const q = new URLSearchParams({ symbol, count: String(count), source });
    return this.getJson<
      ApiResponse<
        {
          symbol: string;
          bid: number;
          ask: number;
          spread: number;
          timestamp: string;
          volume?: number;
        }[]
      >
    >(`/api/v2/market/ticks?${q}`);
  }

  // ── Positions ────────────────────────────────────────────────────────
  async getPositionsSnapshot(): Promise<
    ApiResponse<{
      positions: Record<string, unknown>[];
      count: number;
      nexus_count?: number;
      external_count?: number;
      account?: {
        balance?: number;
        equity?: number;
        margin?: number;
        free_margin?: number;
        currency?: string;
        broker?: string;
      };
      live_connected?: boolean;
      source?: string;
    }>
  > {
    const res = await this.fetchPath('/api/positions');
    const raw = await this.parseOk<{
      positions: Record<string, unknown>[];
      count: number;
      nexus_count?: number;
      external_count?: number;
      account?: {
        balance?: number;
        equity?: number;
        margin?: number;
        free_margin?: number;
        currency?: string;
        broker?: string;
      };
      live_connected?: boolean;
      source?: string;
    }>(res);
    return wrap(raw);
  }

  async getPositions(): Promise<ApiResponse<Record<string, unknown>[]>> {
    const snapshot = await this.getPositionsSnapshot();
    const raw = snapshot.data;
    return wrap(raw.positions ?? []);
  }

  async closePosition(positionId: string | number): Promise<ApiResponse<{ status?: string }>> {
    const res = await this.fetchPath(`/api/positions/${positionId}/close`, {
      method: 'POST',
    });
    const body = await this.parseOk<{ status?: string }>(res);
    return wrap(body);
  }

  async closeAllPositions(): Promise<ApiResponse<{ ok?: boolean }>> {
    const { data } = await this.getPositions();
    await Promise.all(
      data
        .map((p) => String((p as { id?: unknown }).id ?? '').trim())
        .filter(Boolean)
        .map((id) => this.closePosition(id).catch(() => null)),
    );
    return wrap({ ok: true });
  }

  // ── Orders / Signals (not on gateway yet) ─────────────────────────────
  getOrders(): Promise<ApiResponse<Order[]>> {
    return Promise.resolve(wrap<Order[]>([]));
  }

  cancelOrder(_ticket: number): Promise<ApiResponse> {
    return Promise.resolve(wrap(null));
  }

  getSignals(): Promise<ApiResponse<Signal[]>> {
    return Promise.resolve(wrap<Signal[]>([]));
  }

  approveSignal(_signalId: string): Promise<ApiResponse> {
    return Promise.resolve(wrap(null));
  }

  rejectSignal(_signalId: string): Promise<ApiResponse> {
    return Promise.resolve(wrap(null));
  }

  // ── Trades ───────────────────────────────────────────────────────────
  async getTradeHistory(params: TradeHistoryRequest = {}): Promise<PaginatedResponse<Trade>> {
    const query = new URLSearchParams();
    if (params.page !== undefined) query.set('page', String(params.page));
    if (params.pageSize !== undefined) query.set('page_size', String(params.pageSize));
    if (params.symbol !== undefined) query.set('symbol', params.symbol);
    if (params.startDate !== undefined) query.set('since', params.startDate);
    const res = await this.fetchPath(`/api/trades?${query}`);
    const raw = await this.parseOk<{
      trades: Trade[];
      total: number;
      page: number;
      page_size: number;
    }>(res);
    const pageSize = raw.page_size ?? 50;
    const page = raw.page ?? 1;
    const total = raw.total ?? 0;
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    return {
      success: true,
      data: raw.trades ?? [],
      pagination: {
        page,
        pageSize,
        total,
        totalPages,
        hasNext: page < totalPages,
        hasPrevious: page > 1,
      },
      timestamp: nowIso(),
    };
  }

  // ── Agents ───────────────────────────────────────────────────────────
  async getAgentPipeline(): Promise<ApiResponse<AgentPipelineState>> {
    await this.fetchPath('/api/agents/status').catch(() => null);
    const pipeline: AgentPipelineState = {
      agents: [],
      messageQueue: [],
      totalMessages: 0,
      activePipelines: 0,
      lastCycleMs: 0,
    };
    return wrap(pipeline);
  }

  // ── Strategies ───────────────────────────────────────────────────────
  async listStrategies(status?: string): Promise<{
    items: StrategyDTO[];
    count: number;
    rules?: Record<string, number>;
  }> {
    const q = status ? `?status=${encodeURIComponent(status)}` : '';
    const res = await this.fetchPath(`/api/strategies${q}`);
    return this.parseOk(res);
  }

  async getStrategy(id: string): Promise<StrategyDTO> {
    const res = await this.fetchPath(`/api/strategies/${id}`);
    return this.parseOk(res);
  }

  async triggerResearch(n = 2): Promise<{
    proposed: number;
    accepted: unknown[];
    rejected: unknown[];
    duration_ms: number;
  }> {
    const res = await this.fetchPath('/api/strategies/research', {
      method: 'POST',
      body: JSON.stringify({ n }),
    });
    return this.parseOk(res);
  }

  async reevaluateStrategy(id: string): Promise<StrategyDTO> {
    const res = await this.fetchPath(`/api/strategies/${id}/reevaluate`, {
      method: 'POST',
    });
    return this.parseOk(res);
  }

  async setStrategyStatus(id: string, status: string): Promise<StrategyDTO> {
    const res = await this.fetchPath(`/api/strategies/${id}/status`, {
      method: 'PUT',
      body: JSON.stringify({ status }),
    });
    return this.parseOk(res);
  }

  async getResearchLog(limit = 25): Promise<{
    runs: ResearchRunDTO[];
    count: number;
  }> {
    const res = await this.fetchPath(`/api/strategies/research/log?limit=${limit}`);
    return this.parseOk(res);
  }

  async exportStrategyMQL5(id: string): Promise<{
    id: string;
    filename: string;
    mql5: string;
  }> {
    const res = await this.fetchPath(`/api/strategies/${id}/mql5`);
    return this.parseOk(res);
  }

  async getLiveDecision(symbol: string): Promise<LiveDecisionDTO | null> {
    try {
      const res = await this.fetchPath(`/api/agents/decision/${symbol.toUpperCase()}`);
      if (res.status === 404) return null;
      return this.parseOk<LiveDecisionDTO>(res);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }
  }

  // ── Risk ─────────────────────────────────────────────────────────────
  async getRiskState(): Promise<ApiResponse<RiskState>> {
    const res = await this.fetchPath('/api/risk/status');
    const data = await this.parseOk<RiskState>(res);
    return wrap(data);
  }

  // ── Performance ──────────────────────────────────────────────────────
  /** Gateway returns snake_case fields aligned with ``PerformanceMetrics`` model. */
  async getPerformanceSummary(): Promise<{
    total_pnl?: number;
    win_rate?: number;
    profit_factor?: number;
    sharpe_ratio?: number;
    max_drawdown?: number;
    total_trades?: number;
    avg_win?: number;
    avg_loss?: number;
  }> {
    const res = await this.fetchPath('/api/performance');
    return this.parseOk(res);
  }

  async getEquityCurvePoints(limit = 200): Promise<
    Array<{ closed_at: string; pnl_usd: number; cumulative_pnl: number }>
  > {
    const res = await this.fetchPath(`/api/performance/equity-curve?limit=${limit}`);
    return this.parseOk(res);
  }

  async getDailyPnL(): Promise<
    Array<{ date: string; pnl: number; trades: number; win_rate: number }>
  > {
    const res = await this.fetchPath('/api/performance/daily');
    return this.parseOk(res);
  }

  async getPerformance(period?: string): Promise<ApiResponse<PerformanceMetrics>> {
    const q = period ? `?period=${encodeURIComponent(period)}` : '';
    const res = await this.fetchPath(`/api/performance${q}`);
    const data = await this.parseOk<PerformanceMetrics>(res);
    return wrap(data);
  }

  // ── News ─────────────────────────────────────────────────────────────
  async getNews(): Promise<ApiResponse<NewsItem[]>> {
    const res = await this.fetchPath('/api/news');
    const data = await this.parseOk<NewsItem[]>(res);
    return wrap(data);
  }

  async getCalendar(): Promise<ApiResponse<CalendarEvent[]>> {
    const res = await this.fetchPath('/api/news/calendar');
    const data = await this.parseOk<CalendarEvent[]>(res);
    return wrap(data);
  }

  // ── System ───────────────────────────────────────────────────────────
  async getHealth(): Promise<ApiResponse<SystemHealth>> {
    const res = await this.fetchPath('/health');
    const raw = await this.parseOk<Record<string, unknown>>(res);
    return wrap(mapGatewayHealthPayload(raw));
  }

  // ── Settings ─────────────────────────────────────────────────────────
  async getSettings(): Promise<ApiResponse<Record<string, unknown>>> {
    const res = await this.fetchPath('/api/config/settings');
    const data = await this.parseOk<Record<string, unknown>>(res);
    return wrap(data);
  }

  /**
   * Merge into gateway ``settings.yaml`` schema (flat keys). Use ``patchUiSettings`` for UI-only prefs.
   */
  async updateSettings(update: SettingsUpdateRequest): Promise<ApiResponse<Record<string, unknown>>> {
    const merged =
      update.section === 'general' || update.section === ''
        ? update.values
        : { [`${update.section}`]: update.values };
    return this.putGatewaySettings(merged as Record<string, unknown>);
  }

  async putGatewaySettings(settings: Record<string, unknown>): Promise<ApiResponse<Record<string, unknown>>> {
    const res = await this.fetchPath('/api/config/settings', {
      method: 'PUT',
      body: JSON.stringify({ settings }),
    });
    const data = await this.parseOk<Record<string, unknown>>(res);
    return wrap(data);
  }

  async putRiskRules(rules: Record<string, unknown>): Promise<ApiResponse<Record<string, unknown>>> {
    const res = await this.fetchPath('/api/config/risk', {
      method: 'PUT',
      body: JSON.stringify({ rules }),
    });
    const data = await this.parseOk<Record<string, unknown>>(res);
    return wrap(data);
  }

  async systemKillSwitch(pin: string, reason = 'manual kill-switch'): Promise<ApiResponse<Record<string, unknown>>> {
    const res = await this.fetchPath('/api/system/kill-switch', {
      method: 'POST',
      body: JSON.stringify({ pin, reason }),
    });
    const data = await this.parseOk<Record<string, unknown>>(res);
    return wrap(data);
  }

  async systemToggleAutoTrade(enabled: boolean): Promise<ApiResponse<Record<string, unknown>>> {
    const res = await this.fetchPath('/api/system/toggle-auto-trade', {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    });
    const data = await this.parseOk<Record<string, unknown>>(res);
    return wrap(data);
  }

  async systemSwitchMode(mode: 'paper' | 'live', pin?: string): Promise<ApiResponse<Record<string, unknown>>> {
    const res = await this.fetchPath('/api/system/mode', {
      method: 'POST',
      body: JSON.stringify({ mode, pin }),
    });
    const data = await this.parseOk<Record<string, unknown>>(res);
    return wrap(data);
  }

  private async getJson<T>(path: string): Promise<T> {
    const res = await this.fetchPath(path);
    return this.parseOk<T>(res);
  }

  private async postJson<T>(path: string, body?: unknown): Promise<T> {
    const res = await this.fetchPath(path, {
      method: 'POST',
      body: body ? JSON.stringify(body) : undefined,
    });
    return this.parseOk<T>(res);
  }
}

export const apiClient = new ApiClient(GATEWAY_ORIGIN);
