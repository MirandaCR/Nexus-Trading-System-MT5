'use client';

import React, { useState } from 'react';
import {
  LineChart,
  TrendingUp,
  Newspaper,
  Terminal,
  BrainCircuit,
  Shield,
  Zap,
  Brain,
  BookOpen,
  FlaskConical,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import AgentFlowchart from '@/components/windows/AgentFlowchart';
import ActivePositions from '@/components/windows/ActivePositions';
import AgentConsole from '@/components/windows/AgentConsole';
import AutonomousMLPanel from '@/components/windows/AutonomousMLPanel';
import NewsFeed from '@/components/windows/NewsFeed';
import PLDashboard from '@/components/windows/PLDashboard';
import RegimeIndicator from '@/components/windows/RegimeIndicator';
import { RiskMonitor } from '@/components/windows/RiskMonitor';
import SessionClock from '@/components/windows/SessionClock';
import LiveChart from '@/components/windows/LiveChart';
import LiveDecisionPanel from '@/components/windows/LiveDecisionPanel';
import SystemDiagnostic from '@/components/windows/SystemDiagnostic';
import GatewayDiagnostic from '@/components/windows/GatewayDiagnostic';
import { SystemHealth } from '@/components/windows/SystemHealth';
import { AutoTradeToggle } from '@/components/trade-controls/AutoTradeToggle';
import { KillSwitch } from '@/components/trade-controls/KillSwitch';
import { TradeSettingsPanel } from '@/components/trade-controls/TradeSettingsPanel';
import SystemConsole from '@/components/windows/SystemConsole';
import RegimeLive from '@/components/windows/RegimeLive';
import MTFConfluence from '@/components/windows/MTFConfluence';
import SignalQualityGate from '@/components/windows/SignalQualityGate';
import ModelsByRegime from '@/components/windows/ModelsByRegime';
import GenAIBrief from '@/components/windows/GenAIBrief';
import TradeJournal from '@/components/windows/TradeJournal';
import RLLifecycle from '@/components/windows/RLLifecycle';
import CrossAsset from '@/components/windows/CrossAsset';
import CorrelationMatrix from '@/components/windows/CorrelationMatrix';
import BacktestPanel from '@/components/windows/BacktestPanel';
import TradeMemoryPanel from '@/components/windows/TradeMemoryPanel';
import ErgodicIntelligencePanel from '@/components/windows/ErgodicIntelligencePanel';

const SUB_TABS = [
  { id: 'overview',      label: 'Overview',      icon: LineChart },
  { id: 'positions',     label: 'Positions',     icon: TrendingUp },
  { id: 'risk',          label: 'Risk',          icon: Shield },
  { id: 'news',          label: 'News',          icon: Newspaper },
  { id: 'agents',        label: 'Agents',        icon: BrainCircuit },
  { id: 'intelligence',  label: 'Intelligence',  icon: Brain },
  { id: 'journal',       label: 'Journal',       icon: BookOpen },
  { id: 'backtest',      label: 'Backtest',      icon: FlaskConical },
  { id: 'console',       label: 'Console',       icon: Terminal },
] as const;

type SubTabId = (typeof SUB_TABS)[number]['id'];

export default function DashboardPage() {
  const [activeTab, setActiveTab] = useState<SubTabId>('overview');

  return (
    <div className="flex flex-col h-full">
      {/* Sub-tab bar with trade controls */}
      <div className="flex items-center gap-1 px-3 h-9 border-b border-border-primary/60 bg-bg-secondary/60 shrink-0 overflow-x-auto scrollbar-hide">
        {SUB_TABS.map((tab) => {
          const isActive = tab.id === activeTab;
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={cn(
                'flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-medium font-mono whitespace-nowrap transition-all',
                isActive
                  ? 'bg-neon-purple/10 text-neon-purple border border-neon-purple/20'
                  : 'text-text-muted hover:text-text-secondary hover:bg-bg-tertiary/30'
              )}
            >
              <Icon className="h-3 w-3" />
              {tab.label}
            </button>
          );
        })}

        <div className="flex-1" />

        {/* Inline trade controls — visible en todas las tabs */}
        <div className="flex items-center gap-2 shrink-0">
          <ScalpingModeToggle />
          <AutoTradeToggle compact />
          <KillSwitch compact />
        </div>
      </div>

      {/* Content — Keep all tabs mounted (display:none) so switching is instant */}
      <div className="flex-1 min-h-0 overflow-auto relative">
        <div style={{ display: activeTab === 'overview' ? 'block' : 'none' }}>
          <OverviewGrid />
        </div>
        <div style={{ display: activeTab === 'positions' ? 'block' : 'none' }} className="p-2 sm:p-3 h-full w-full max-w-[1600px] mx-auto">
          <ActivePositions />
        </div>
        <div
          style={{ display: activeTab === 'risk' ? 'grid' : 'none' }}
          className="p-2 sm:p-3 grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_minmax(280px,360px)] gap-2 sm:gap-3 w-full max-w-[1800px] mx-auto min-h-[calc(100vh-90px)] h-[calc(100vh-90px)]"
        >
          <div className="flex flex-col gap-2 min-h-0 overflow-hidden">
            <RiskMonitor />
            <div className="flex-1 min-h-[160px] overflow-hidden">
              <ActivePositions />
            </div>
          </div>
          <div className="flex flex-col gap-2 min-h-0 overflow-hidden">
            <TradeSettingsPanel />
            <SessionClock compact />
            <div className="flex-1 min-h-[140px] overflow-hidden">
              <SystemHealth compact className="h-full" />
            </div>
          </div>
        </div>
        <div style={{ display: activeTab === 'news' ? 'block' : 'none' }} className="p-2 sm:p-3 w-full max-w-[1200px] mx-auto">
          <NewsFeed />
        </div>
        <div style={{ display: activeTab === 'agents' ? 'flex' : 'none' }} className="p-2 sm:p-3 w-full max-w-[1600px] mx-auto min-h-[calc(100vh-90px)] h-[calc(100vh-90px)] flex flex-col">
          <AgentFlowchart />
        </div>
        <div style={{ display: activeTab === 'intelligence' ? 'block' : 'none' }} className="p-2 sm:p-3 w-full max-w-[1800px] mx-auto">
          <IntelligenceGrid />
        </div>
        <div style={{ display: activeTab === 'journal' ? 'flex' : 'none' }} className="p-2 sm:p-3 w-full max-w-[1200px] mx-auto min-h-[calc(100vh-90px)] h-[calc(100vh-90px)] flex flex-col">
          <div className="flex-1 min-h-0 overflow-hidden border border-border-primary rounded-lg bg-bg-secondary/40">
            <TradeJournal />
          </div>
        </div>
        <div style={{ display: activeTab === 'backtest' ? 'flex' : 'none' }} className="p-2 sm:p-3 w-full max-w-[1800px] mx-auto min-h-[calc(100vh-90px)] h-[calc(100vh-90px)] flex flex-col">
          <div className="flex-1 min-h-0 overflow-hidden border border-neon-purple/20 rounded-lg bg-bg-secondary/40">
            <BacktestPanel />
          </div>
        </div>
        <div style={{ display: activeTab === 'console' ? 'flex' : 'none' }} className="p-2 sm:p-3 w-full max-w-[1600px] mx-auto min-h-[calc(100vh-90px)] h-[calc(100vh-90px)] flex flex-col">
          <AgentConsole />
        </div>
      </div>
    </div>
  );
}

function ScalpingModeToggle() {
  const [mode, setMode] = useState<string>('scalping');
  const [loading, setLoading] = useState(false);

  const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

  React.useEffect(() => {
    fetch(`${API}/api/ml/traders/mode`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d?.current_mode) setMode(d.current_mode); })
      .catch(() => {});
  }, [API]);

  const toggle = async () => {
    const next = mode === 'scalping' ? 'swing' : 'scalping';
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/ml/traders/mode/switch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: next }),
      });
      if (res.ok) setMode(next);
    } catch {} finally { setLoading(false); }
  };

  return (
    <button
      onClick={toggle}
      disabled={loading}
      className={cn(
        'flex items-center gap-1.5 px-2.5 py-1 rounded text-[10px] font-mono font-bold border transition-all',
        mode === 'scalping'
          ? 'text-neon-green bg-neon-green/10 border-neon-green/30 hover:bg-neon-green/20'
          : 'text-neon-purple bg-neon-purple/10 border-neon-purple/30 hover:bg-neon-purple/20'
      )}
    >
      <Zap className="h-3 w-3" />
      {mode.toUpperCase()}
    </button>
  );
}

function IntelligenceGrid() {
  return (
    <div className="flex flex-col gap-2 sm:gap-3">
      {/* Top row: Regime + MTF + Signal Quality */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-2 sm:gap-3 min-h-[320px]">
        <div className="border border-border-primary rounded-lg bg-bg-secondary/40 overflow-hidden">
          <RegimeLive />
        </div>
        <div className="border border-border-primary rounded-lg bg-bg-secondary/40 overflow-hidden">
          <MTFConfluence />
        </div>
        <div className="border border-border-primary rounded-lg bg-bg-secondary/40 overflow-hidden">
          <SignalQualityGate />
        </div>
      </div>
      {/* Bottom row: Models by Regime + GenAI Brief + RL Lifecycle + Cross-Asset */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-2 sm:gap-3 min-h-[340px]">
        <div className="border border-border-primary rounded-lg bg-bg-secondary/40 overflow-hidden">
          <ModelsByRegime />
        </div>
        <div className="border border-border-primary rounded-lg bg-bg-secondary/40 overflow-hidden">
          <GenAIBrief />
        </div>
        <div className="border border-neon-purple/20 rounded-lg bg-neon-purple/5 overflow-hidden">
          <RLLifecycle />
        </div>
        <div className="border border-neon-purple/20 rounded-lg bg-neon-purple/5 overflow-hidden">
          <CrossAsset />
        </div>
      </div>
      {/* Trade Memory + Correlation row */}
      <div className="grid grid-cols-1 xl:grid-cols-[minmax(320px,420px)_minmax(0,1fr)] gap-2 sm:gap-3 min-h-[280px]">
        <div className="border border-neon-purple/20 rounded-lg bg-neon-purple/5 overflow-hidden">
          <TradeMemoryPanel />
        </div>
        <div className="border border-neon-purple/20 rounded-lg bg-neon-purple/5 overflow-hidden">
          <CorrelationMatrix />
        </div>
      </div>
      {/* Ergodic Intelligence row */}
      <div className="grid grid-cols-1 gap-2 sm:gap-3">
        <div className="border border-cyan-500/20 rounded-lg bg-cyan-500/5 overflow-hidden">
          <div className="flex items-center gap-2 px-3 pt-3 pb-1">
            <span className="text-xs font-medium text-cyan-400 uppercase tracking-widest">Inteligencia Ergódica Institucional</span>
            <span className="text-xs text-gray-600">Hurst · Kelly Peters · Lyapunov · Entropía</span>
          </div>
          <ErgodicIntelligencePanel />
        </div>
      </div>
    </div>
  );
}

function OverviewGrid() {
  return (
    <div className="p-2 sm:p-3 w-full max-w-[1800px] mx-auto flex flex-col gap-2 sm:gap-3">

      <SystemDiagnostic />
      <GatewayDiagnostic />

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_minmax(300px,380px)] gap-2 sm:gap-3 xl:items-stretch">
        <div className="min-h-[min(68vh,620px)] xl:min-h-0 h-[min(68vh,620px)] overflow-hidden">
          <LiveChart />
        </div>
        <div className="flex flex-col gap-2 xl:max-h-[min(68vh,620px)]">
          <SessionClock compact />
          <PLDashboard compact />
          <RegimeIndicator compact />
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-[minmax(300px,400px)_minmax(0,1fr)] gap-2 sm:gap-3">
        <div className="min-h-[260px] md:max-h-[460px] overflow-hidden">
          <LiveDecisionPanel />
        </div>
        <div className="min-h-[260px] md:max-h-[460px] overflow-hidden">
          <ActivePositions />
        </div>
      </div>

      <div className="min-h-[200px] overflow-hidden">
        <AutonomousMLPanel />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 sm:gap-3 items-stretch min-h-[340px] lg:min-h-[400px]">
        <div className="h-full min-h-[300px] overflow-hidden">
          <RiskMonitor className="h-full" />
        </div>
        <div className="h-full min-h-[300px] overflow-hidden">
          <SystemConsole className="h-full" />
        </div>
      </div>

    </div>
  );
}
