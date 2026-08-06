import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import type { AgentId } from '@/types';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const currencyFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const compactCurrencyFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  notation: 'compact',
  maximumFractionDigits: 1,
});

export function formatCurrency(value: number, compact = false): string {
  return compact
    ? compactCurrencyFormatter.format(value)
    : currencyFormatter.format(value);
}

export function formatPips(pips: number): string {
  const sign = pips >= 0 ? '+' : '';
  return `${sign}${pips.toFixed(1)} pips`;
}

export function formatPercent(value: number, decimals = 2): string {
  const sign = value >= 0 ? '+' : '';
  return `${sign}${value.toFixed(decimals)}%`;
}

export function formatPrice(price: number, digits = 5): string {
  return price.toFixed(digits);
}

export function formatVolume(volume: number): string {
  return volume.toFixed(2);
}

const AGENT_COLORS: Record<AgentId, string> = {
  orchestrator: 'var(--agent-orchestrator)',
  analyst: 'var(--agent-analyst)',
  risk_manager: 'var(--agent-risk)',
  executor: 'var(--agent-executor)',
  monitor: 'var(--agent-monitor)',
  journal: 'var(--agent-journal)',
  news: 'var(--agent-news)',
  strategy_generator: 'var(--agent-strategy)',
};

export function getAgentColor(agentId: string): string {
  return AGENT_COLORS[agentId as AgentId] ?? 'var(--text-muted)';
}

export function formatPnL(value: number): string {
  const sign = value >= 0 ? '+' : '';
  return `${sign}${currencyFormatter.format(value)}`;
}

export function getPnlColor(pnl: number): string {
  if (pnl > 0) return 'var(--neon-green)';
  if (pnl < 0) return 'var(--neon-red)';
  return 'var(--text-secondary)';
}

export function getPnlClass(pnl: number): string {
  if (pnl > 0) return 'text-neon-green';
  if (pnl < 0) return 'text-neon-red';
  return 'text-text-secondary';
}

export function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

export function formatTimeShort(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

export function timeAgo(iso: string): string {
  const seconds = Math.floor(
    (Date.now() - new Date(iso).getTime()) / 1000
  );

  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;

  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function getSessionName(timestamp: string): string {
  const date = new Date(timestamp);
  const hour = date.getUTCHours();

  if (hour >= 0 && hour < 8) return 'Asian';
  if (hour >= 7 && hour < 16) return 'London';
  if (hour >= 13 && hour < 22) return 'New York';
  return 'Off-Hours';
}

export function formatDuration(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

export function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}
