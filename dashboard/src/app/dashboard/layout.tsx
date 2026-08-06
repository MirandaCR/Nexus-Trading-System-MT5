'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  Zap,
  Activity,
  Shield,
  LogOut,
  Bell,
  LayoutDashboard,
  History,
  BrainCircuit,
  Settings,
  Wallet,
  ChevronDown,
  AlertTriangle,
  X,
  Cpu,
  Trophy,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { apiClient } from '@/lib/api-client';
import { useDashboardStore } from '@/stores/dashboardStore';
import type { Timeframe, TradingMode } from '@/types';
import { NotificationBell } from '@/components/ui/NotificationBell';

function GatewaySettingsHydrator() {
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;

    let cancelled = false;
    (async () => {
      try {
        const [settingsRes, positionsRes] = await Promise.all([
          apiClient.getSettings(),
          apiClient.getPositionsSnapshot().catch(() => null),
        ]);
        if (cancelled) return;

        const d = settingsRes.data;
        const sym = d.default_symbol;
        if (typeof sym === 'string' && sym.length >= 4) {
          useDashboardStore.getState().setSymbol(sym.toUpperCase().replace(/\//g, ''));
        }
        const tf = d.default_timeframe;
        if (typeof tf === 'string') {
          useDashboardStore.getState().setTimeframe(tf as Timeframe);
        }
        const modeRaw = d.trading_mode;
        const modeStr = typeof modeRaw === 'string' ? modeRaw.toLowerCase() : '';
        if (modeStr === 'live') useDashboardStore.getState().setMode('LIVE');
        if (modeStr === 'paper') useDashboardStore.getState().setMode('PAPER');
        if (typeof d.auto_trade_enabled === 'boolean') {
          useDashboardStore.getState().setAutoTrade(d.auto_trade_enabled);
        }

        if (positionsRes?.data?.account) {
          const acc = positionsRes.data.account;
          useDashboardStore.getState().setAccount({
            balance: acc.balance ?? undefined,
            equity: acc.equity ?? undefined,
            margin: acc.margin ?? undefined,
            freeMargin: acc.free_margin ?? undefined,
          });
        }
      } catch {
        /* gateway offline — keep persisted state */
      }
    })();

    return () => { cancelled = true; };
  }, []);

  return null;
}

const NAV_ITEMS = [
  { href: '/dashboard',                       label: 'Trading',    icon: LayoutDashboard },
  { href: '/dashboard/strategies',             label: 'Strategies', icon: BrainCircuit },
  { href: '/dashboard/models',                 label: 'Models',     icon: Cpu },
  { href: '/dashboard/model-comparison',       label: 'Compare',    icon: Trophy },
  { href: '/dashboard/history',                label: 'History',    icon: History },
  { href: '/dashboard/settings',               label: 'Settings',   icon: Settings },
];

function LiveConfirmModal({
  onConfirm,
  onCancel,
}: {
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="w-full max-w-sm mx-4 rounded-xl border border-neon-red/40 bg-bg-card shadow-[0_0_40px_rgba(255,23,68,0.2)]">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-border-primary">
          <AlertTriangle className="h-4 w-4 text-neon-red shrink-0" />
          <span className="text-sm font-bold text-neon-red">Activar Trading en Vivo</span>
          <button onClick={onCancel} className="ml-auto text-text-muted hover:text-text-primary">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="px-4 py-4 space-y-3">
          <p className="text-xs text-text-secondary leading-relaxed">
            Vas a cambiar a <span className="text-neon-red font-semibold">modo LIVE</span>.
            Las órdenes de los agentes se ejecutarán con dinero real en MetaTrader 5.
          </p>
          <ul className="space-y-1 text-xs text-text-muted">
            <li className="flex items-center gap-1.5">
              <span className="h-1 w-1 rounded-full bg-neon-red shrink-0" />
              Asegúrate de que MT5 está conectado y autenticado
            </li>
            <li className="flex items-center gap-1.5">
              <span className="h-1 w-1 rounded-full bg-neon-red shrink-0" />
              El Kill Switch desactiva todo inmediatamente si es necesario
            </li>
          </ul>
        </div>
        <div className="flex gap-2 px-4 pb-4">
          <button
            onClick={onCancel}
            className="flex-1 px-3 py-2 rounded-lg text-xs font-medium bg-bg-secondary border border-border-primary text-text-secondary hover:text-text-primary transition-all"
          >
            Cancelar
          </button>
          <button
            onClick={onConfirm}
            className="flex-1 px-3 py-2 rounded-lg text-xs font-bold bg-neon-red/10 border border-neon-red/40 text-neon-red hover:bg-neon-red/20 transition-all"
          >
            Confirmar — ir a LIVE
          </button>
        </div>
      </div>
    </div>
  );
}

function ModeToggle() {
  const mode = useDashboardStore((s) => s.mode);
  const setMode = useDashboardStore((s) => s.setMode);
  const [open, setOpen] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [syncing, setSyncing] = useState(false);

  const syncModeToBackend = useCallback(async (newMode: TradingMode) => {
    setSyncing(true);
    try {
      await apiClient.systemSwitchMode(newMode === 'LIVE' ? 'live' : 'paper');
    } catch {
      /* gateway offline — UI mode still applies locally */
    } finally {
      setSyncing(false);
    }
  }, []);

  const handleSelectPaper = () => {
    setMode('PAPER');
    setOpen(false);
    syncModeToBackend('PAPER');
  };

  const handleRequestLive = () => {
    setOpen(false);
    setShowConfirm(true);
  };

  const handleConfirmLive = () => {
    setShowConfirm(false);
    setMode('LIVE');
    syncModeToBackend('LIVE');
  };

  return (
    <>
      {showConfirm && (
        <LiveConfirmModal
          onConfirm={handleConfirmLive}
          onCancel={() => setShowConfirm(false)}
        />
      )}

      <div className="relative">
        <button
          onClick={() => setOpen(!open)}
          className={cn(
            'flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[10px] font-mono font-bold border transition-all',
            syncing && 'opacity-60 pointer-events-none',
            mode === 'LIVE'
              ? 'border-neon-red/40 text-neon-red bg-neon-red/10 hover:bg-neon-red/20'
              : 'border-neon-green/30 text-neon-green bg-neon-green/10 hover:bg-neon-green/20'
          )}
        >
          <Activity className={cn('h-3 w-3', mode === 'LIVE' && 'animate-pulse')} />
          {mode}
          <ChevronDown className="h-2.5 w-2.5 opacity-60" />
        </button>

        {open && (
          <>
            <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
            <div className="absolute right-0 top-full mt-1 w-44 rounded-lg border border-border-primary bg-bg-primary shadow-lg z-50 overflow-hidden">
              <button
                onClick={handleSelectPaper}
                className={cn(
                  'flex items-center gap-2 w-full px-3 py-2.5 text-xs font-medium transition-all border-b border-border-primary/20',
                  mode === 'PAPER' ? 'bg-neon-green/10 text-neon-green' : 'text-text-secondary hover:bg-bg-card-hover'
                )}
              >
                <span className="h-1.5 w-1.5 rounded-full bg-neon-green shrink-0" />
                Paper Trading
              </button>
              <button
                onClick={handleRequestLive}
                className={cn(
                  'flex items-center gap-2 w-full px-3 py-2.5 text-xs font-medium transition-all',
                  mode === 'LIVE' ? 'bg-neon-red/10 text-neon-red' : 'text-text-secondary hover:bg-bg-card-hover'
                )}
              >
                <span className="h-1.5 w-1.5 rounded-full bg-neon-red shrink-0 animate-pulse" />
                MT5 Live
              </button>
            </div>
          </>
        )}
      </div>
    </>
  );
}

function BalanceDisplay() {
  const balance = useDashboardStore((s) => s.account.balance);
  const equity = useDashboardStore((s) => s.account.equity);
  const dailyPnL = useDashboardStore((s) => s.account.dailyPnL);

  const fmt = (n: number) =>
    n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });

  return (
    <div className="hidden md:flex items-center gap-2 lg:gap-3 px-2 lg:px-3 py-1 rounded-md bg-bg-card border border-border-primary/30">
      <div className="flex items-center gap-1.5">
        <Wallet className="h-3 w-3 text-neon-gold" />
        <span className="text-[10px] text-text-muted font-mono">BAL</span>
        <span className="text-[11px] font-mono font-bold text-neon-gold">{fmt(balance)}</span>
      </div>
      {dailyPnL !== 0 && (
        <>
          <div className="h-3 w-px bg-border-primary" />
          <span className={cn(
            'text-[11px] font-mono font-bold',
            dailyPnL >= 0 ? 'text-neon-green' : 'text-neon-red'
          )}>
            {dailyPnL >= 0 ? '+' : ''}{fmt(dailyPnL)}
          </span>
        </>
      )}
      {equity !== balance && (
        <>
          <div className="h-3 w-px bg-border-primary" />
          <div className="flex items-center gap-1">
            <span className="text-[10px] text-text-muted font-mono">EQ</span>
            <span className="text-[11px] font-mono text-text-secondary">{fmt(equity)}</span>
          </div>
        </>
      )}
    </div>
  );
}

function LiveModeBanner() {
  const mode = useDashboardStore((s) => s.mode);
  const isKilled = useDashboardStore((s) => s.isKilled);

  if (mode !== 'LIVE' || isKilled) return null;

  return (
    <div className="h-0.5 w-full bg-gradient-to-r from-transparent via-neon-red/60 to-transparent animate-pulse shrink-0" />
  );
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isKilled = useDashboardStore((s) => s.isKilled);

  return (
    <div className="flex flex-col h-screen overflow-hidden">
      <GatewaySettingsHydrator />

      {/* Kill switch banner */}
      {isKilled && (
        <div className="flex items-center justify-center gap-2 h-6 bg-neon-red/15 border-b border-neon-red/30 shrink-0">
          <Shield className="h-3 w-3 text-neon-red" />
          <span className="text-[10px] font-mono font-bold text-neon-red uppercase tracking-widest">
            KILL SWITCH ACTIVO — TRADING PAUSADO
          </span>
        </div>
      )}

      {/* Top Nav Bar */}
      <header className="flex items-center gap-1.5 sm:gap-2 px-2 sm:px-4 h-10 border-b border-border-primary bg-bg-secondary/90 backdrop-blur-sm shrink-0 z-20">
        {/* Brand */}
        <div className="flex items-center gap-2 mr-3 shrink-0">
          <Zap className="h-4 w-4 text-neon-green" />
          <span className="text-xs font-black tracking-[0.2em] gradient-text hidden sm:inline select-none">
            NEXUS
          </span>
          <span className="hidden sm:inline px-1.5 py-0.5 rounded text-[8px] font-mono font-bold bg-yellow-400/10 border border-yellow-400/30 text-yellow-400 tracking-wider select-none">
            DEMO
          </span>
        </div>

        <div className="h-4 w-px bg-border-primary shrink-0" />

        {/* Nav links */}
        <nav className="flex items-center gap-0.5 flex-1 overflow-x-auto scrollbar-hide">
          {NAV_ITEMS.map((item) => {
            const isActive =
              item.href === '/dashboard'
                ? pathname === '/dashboard'
                : pathname.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  'flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-medium whitespace-nowrap transition-all relative',
                  isActive
                    ? 'bg-neon-purple/15 text-neon-purple'
                    : 'text-text-muted hover:text-text-secondary hover:bg-bg-tertiary/50'
                )}
              >
                <Icon className="h-3 w-3" />
                <span className="hidden sm:inline">{item.label}</span>
                {isActive && (
                  <span className="absolute bottom-0 left-1 right-1 h-px rounded-full bg-neon-purple/60" />
                )}
              </Link>
            );
          })}
        </nav>

        {/* Right controls */}
        <div className="flex items-center gap-1.5 shrink-0">
          <BalanceDisplay />

          <div className="h-4 w-px bg-border-primary" />

          <ModeToggle />

          <div className="h-4 w-px bg-border-primary" />

          <NotificationBell />
          <button className="p-1.5 rounded text-text-muted hover:text-neon-red hover:bg-bg-card transition-colors">
            <LogOut className="h-3.5 w-3.5" />
          </button>
        </div>
      </header>

      {/* Live mode indicator line */}
      <LiveModeBanner />

      <main className="flex-1 min-h-0 overflow-auto">{children}</main>
    </div>
  );
}
