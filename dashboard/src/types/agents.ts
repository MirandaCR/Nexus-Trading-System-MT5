export type AgentId =
  | 'orchestrator'
  | 'analyst'
  | 'risk_manager'
  | 'executor'
  | 'monitor'
  | 'journal'
  | 'news'
  | 'strategy_generator';

export type AgentRole =
  | 'orchestrator'
  | 'market_analyst'
  | 'risk_manager'
  | 'trade_executor'
  | 'position_monitor'
  | 'trade_journalist'
  | 'news_analyst'
  | 'strategy_generator';

export interface AgentStatus {
  id: AgentId;
  role: AgentRole;
  name: string;
  status: 'active' | 'idle' | 'processing' | 'error' | 'disabled';
  lastActivity: string;
  messagesProcessed: number;
  currentTask?: string;
  confidence?: number;
  uptime: number;
  errorCount: number;
}

export interface AgentMessage {
  id: string;
  timestamp: string;
  fromAgent: AgentId;
  toAgent: AgentId | 'broadcast';
  type: AgentMessageType;
  priority: 'low' | 'normal' | 'high' | 'critical';
  payload: Record<string, unknown>;
  correlationId?: string;
}

export type AgentMessageType =
  | 'analysis_request'
  | 'analysis_result'
  | 'signal'
  | 'risk_check_request'
  | 'risk_check_result'
  | 'execute_order'
  | 'order_result'
  | 'position_update'
  | 'alert'
  | 'heartbeat'
  | 'config_update';

export interface AgentPipelineState {
  agents: AgentStatus[];
  messageQueue: AgentMessage[];
  totalMessages: number;
  activePipelines: number;
  lastCycleMs: number;
}

export interface AgentDecision {
  agentId: AgentId;
  decision: 'buy' | 'sell' | 'hold' | 'close';
  confidence: number;
  reasoning: string;
  timestamp: string;
  factors: {
    name: string;
    weight: number;
    score: number;
  }[];
}

export interface AgentVote {
  signal_id: string;
  votes: {
    agentId: AgentId;
    vote: 'approve' | 'reject' | 'abstain';
    confidence: number;
    comment?: string;
  }[];
  result: 'approved' | 'rejected' | 'pending';
  requiredApprovals: number;
  timestamp: string;
}
