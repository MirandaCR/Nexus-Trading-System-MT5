'use client';

import { useState } from 'react';
import {
  BarChart3,
  TrendingUp,
  TrendingDown,
  Percent,
  DollarSign,
  Target,
  Timer,
  Activity,
} from 'lucide-react';
import { cn } from '@/lib/utils';

type AnalyticsTab = 'overview' | 'agents' | 'symbols' | 'drawdown';

const TABS: { value: AnalyticsTab; label: string }[] = [
  { value: 'overview', label: 'Overview' },
  { value: 'agents', label: 'By Agent' },
  { value: 'symbols', label: 'By Symbol' },
  { value: 'drawdown', label: 'Drawdown' },
];

interface MetricCard {
  label: string;
  value: string;
  change?: string;
  positive?: boolean;
  icon: React.ElementType;
  color: string;
}

const OVERVIEW_METRICS: MetricCard[] = [
  { label: 'Total P&L', value: '$0.00', change: '+0%', positive: true, icon: DollarSign, color: 'var(--neon-green)' },
  { label: 'Win Rate', value: '0%', icon: Target, color: 'var(--neon-cyan)' },
  { label: 'Profit Factor', value: '0.00', icon: TrendingUp, color: 'var(--neon-purple)' },
  { label: 'Sharpe Ratio', value: '0.00', icon: Activity, color: 'var(--neon-gold)' },
  { label: 'Max Drawdown', value: '0%', icon: TrendingDown, color: 'var(--neon-red)' },
  { label: 'Avg Trade Duration', value: '—', icon: Timer, color: 'var(--neon-blue)' },
  { label: 'Expectancy', value: '$0.00', icon: Percent, color: 'var(--neon-orange)' },
  { label: 'Recovery Factor', value: '0.00', icon: BarChart3, color: 'var(--neon-pink)' },
];

export default function AnalyticsPage() {
  const [activeTab, setActiveTab] = useState<AnalyticsTab>('overview');

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <BarChart3 className="h-6 w-6 text-neon-cyan" />
          <h1 className="text-xl font-bold text-text-primary">Analytics</h1>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 bg-bg-card rounded-lg p-1 border border-border-primary w-fit">
        {TABS.map((tab) => (
          <button
            key={tab.value}
            onClick={() => setActiveTab(tab.value)}
            className={cn(
              'px-4 py-2 text-sm font-medium rounded-md transition-all',
              activeTab === tab.value
                ? 'bg-neon-purple/15 text-neon-purple'
                : 'text-text-muted hover:text-text-secondary'
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Overview Metrics Grid */}
      {activeTab === 'overview' && (
        <>
          <div className="grid grid-cols-4 gap-4">
            {OVERVIEW_METRICS.map((metric) => {
              const Icon = metric.icon;
              return (
                <div key={metric.label} className="neon-card p-4 group">
                  <div className="flex items-center justify-between mb-3">
                    <Icon
                      className="h-5 w-5 opacity-70 group-hover:opacity-100 transition-opacity"
                      style={{ color: metric.color }}
                    />
                    {metric.change && (
                      <span
                        className={cn(
                          'text-xs font-mono px-1.5 py-0.5 rounded',
                          metric.positive
                            ? 'bg-neon-green/10 text-neon-green'
                            : 'bg-neon-red/10 text-neon-red'
                        )}
                      >
                        {metric.change}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-text-muted mb-1">{metric.label}</p>
                  <p className="text-xl font-bold font-mono text-text-primary">
                    {metric.value}
                  </p>
                </div>
              );
            })}
          </div>

          {/* Equity Curve Placeholder */}
          <div className="neon-card p-6">
            <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wider mb-4">
              Equity Curve
            </h3>
            <div className="h-64 flex items-center justify-center border border-dashed border-border-primary rounded-lg">
              <span className="text-text-muted text-sm font-mono">
                Equity curve will render here with Recharts
              </span>
            </div>
          </div>

          {/* Distribution Charts */}
          <div className="grid grid-cols-2 gap-4">
            <div className="neon-card p-6">
              <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wider mb-4">
                P&L Distribution
              </h3>
              <div className="h-48 flex items-center justify-center border border-dashed border-border-primary rounded-lg">
                <span className="text-text-muted text-xs font-mono">
                  Histogram — awaiting data
                </span>
              </div>
            </div>
            <div className="neon-card p-6">
              <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wider mb-4">
                Win/Loss by Hour
              </h3>
              <div className="h-48 flex items-center justify-center border border-dashed border-border-primary rounded-lg">
                <span className="text-text-muted text-xs font-mono">
                  Heatmap — awaiting data
                </span>
              </div>
            </div>
          </div>
        </>
      )}

      {activeTab === 'agents' && (
        <div className="neon-card p-6">
          <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wider mb-4">
            Agent Performance Comparison
          </h3>
          <div className="h-96 flex items-center justify-center border border-dashed border-border-primary rounded-lg">
            <span className="text-text-muted text-sm font-mono">
              Agent breakdown — awaiting trading data
            </span>
          </div>
        </div>
      )}

      {activeTab === 'symbols' && (
        <div className="neon-card p-6">
          <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wider mb-4">
            Symbol Performance
          </h3>
          <div className="h-96 flex items-center justify-center border border-dashed border-border-primary rounded-lg">
            <span className="text-text-muted text-sm font-mono">
              Per-symbol analytics — awaiting trading data
            </span>
          </div>
        </div>
      )}

      {activeTab === 'drawdown' && (
        <div className="neon-card p-6">
          <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wider mb-4">
            Drawdown Analysis
          </h3>
          <div className="h-96 flex items-center justify-center border border-dashed border-border-primary rounded-lg">
            <span className="text-text-muted text-sm font-mono">
              Drawdown waterfall — awaiting trading data
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
