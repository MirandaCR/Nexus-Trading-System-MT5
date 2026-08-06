'use client';

import { cn } from '@/lib/utils';
import { useDashboardStore } from '@/stores/dashboardStore';
import { NeonBadge } from '@/components/shared/NeonBadge';
import { FlaskConical, Radio, Rewind, Lock, X } from 'lucide-react';
import { useState, useCallback, useRef } from 'react';
import type { TradingMode, NeonVariant } from '@/types';

const LIVE_PIN = '0000';

interface ModeConfig {
  mode: TradingMode;
  label: string;
  icon: React.ElementType;
  variant: NeonVariant;
  description: string;
  requiresPin: boolean;
}

const MODES: ModeConfig[] = [
  {
    mode: 'PAPER',
    label: 'Paper',
    icon: FlaskConical,
    variant: 'warning',
    description: 'Simulated trading with virtual funds',
    requiresPin: false,
  },
  {
    mode: 'LIVE',
    label: 'Live',
    icon: Radio,
    variant: 'danger',
    description: 'Real trading with real funds — requires PIN',
    requiresPin: true,
  },
  {
    mode: 'BACKTEST',
    label: 'Backtest',
    icon: Rewind,
    variant: 'info',
    description: 'Run strategies on historical data',
    requiresPin: false,
  },
];

export function ModeSelector() {
  const { mode, setMode } = useDashboardStore();
  const [open, setOpen] = useState(false);
  const [pinDialogOpen, setPinDialogOpen] = useState(false);
  const [pin, setPin] = useState('');
  const [pinError, setPinError] = useState(false);
  const [pendingMode, setPendingMode] = useState<TradingMode | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSelect = useCallback(
    (config: ModeConfig) => {
      if (config.mode === mode) {
        setOpen(false);
        return;
      }

      if (config.requiresPin) {
        setPendingMode(config.mode);
        setPin('');
        setPinError(false);
        setPinDialogOpen(true);
        setOpen(false);
        setTimeout(() => inputRef.current?.focus(), 100);
      } else {
        setMode(config.mode);
        setOpen(false);
      }
    },
    [mode, setMode]
  );

  const handlePinConfirm = useCallback(() => {
    if (pin !== LIVE_PIN) {
      setPinError(true);
      setPin('');
      return;
    }
    if (pendingMode) {
      setMode(pendingMode);
    }
    setPinDialogOpen(false);
    setPendingMode(null);
  }, [pin, pendingMode, setMode]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter') handlePinConfirm();
      if (e.key === 'Escape') setPinDialogOpen(false);
    },
    [handlePinConfirm]
  );

  const currentConfig = MODES.find((m) => m.mode === mode)!;
  const CurrentIcon = currentConfig.icon;

  return (
    <>
      <div className="relative">
        <button
          onClick={() => setOpen(!open)}
          className={cn(
            'flex items-center gap-2 rounded-lg px-3 py-2',
            'bg-bg-input border border-border-primary',
            'hover:border-neon-purple/40 transition-colors'
          )}
        >
          <CurrentIcon className="h-4 w-4 text-text-muted" />
          <NeonBadge
            label={currentConfig.label}
            variant={currentConfig.variant}
            pulse={mode === 'LIVE'}
          />
        </button>

        {open && (
          <div className="absolute top-full left-0 mt-1 z-50 w-64 rounded-lg border border-border-primary bg-bg-card shadow-lg animate-fade-in">
            {MODES.map((config) => {
              const Icon = config.icon;
              const isActive = config.mode === mode;

              return (
                <button
                  key={config.mode}
                  onClick={() => handleSelect(config)}
                  className={cn(
                    'flex items-start gap-3 w-full px-3 py-3 text-left transition-colors',
                    isActive
                      ? 'bg-neon-purple/10'
                      : 'hover:bg-bg-card-hover',
                    config.mode !== MODES[MODES.length - 1].mode && 'border-b border-border-secondary'
                  )}
                >
                  <Icon
                    className={cn(
                      'h-4 w-4 mt-0.5 shrink-0',
                      isActive ? 'text-neon-purple' : 'text-text-muted'
                    )}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <NeonBadge label={config.label} variant={config.variant} size="sm" />
                      {config.requiresPin && (
                        <Lock className="h-3 w-3 text-text-muted" />
                      )}
                    </div>
                    <p className="text-[10px] text-text-muted mt-1 leading-tight">
                      {config.description}
                    </p>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* PIN dialog for LIVE mode */}
      {pinDialogOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-bg-overlay animate-fade-in">
          <div className="relative w-80 rounded-xl border border-neon-red/60 bg-bg-card p-6 shadow-lg shadow-neon-red/20 animate-slide-up">
            <button
              onClick={() => setPinDialogOpen(false)}
              className="absolute top-3 right-3 text-text-muted hover:text-text-primary transition-colors"
            >
              <X className="h-4 w-4" />
            </button>

            <div className="flex flex-col items-center gap-3 mb-4">
              <Lock className="h-8 w-8 text-neon-red" />
              <h3 className="text-lg font-bold text-text-primary">
                Switch to LIVE Mode
              </h3>
              <p className="text-xs text-text-secondary text-center">
                You are about to switch to live trading with real funds. Enter your PIN to confirm.
              </p>
            </div>

            <div className="mb-4">
              <input
                ref={inputRef}
                type="password"
                maxLength={4}
                value={pin}
                onChange={(e) => {
                  setPin(e.target.value);
                  setPinError(false);
                }}
                onKeyDown={handleKeyDown}
                placeholder="Enter PIN"
                className={cn(
                  'w-full rounded-lg bg-bg-input border px-4 py-3 text-center text-lg font-mono tracking-[0.5em]',
                  'focus:outline-none transition-colors',
                  pinError
                    ? 'border-neon-red text-neon-red'
                    : 'border-border-primary text-text-primary focus:border-neon-purple/60'
                )}
              />
              {pinError && (
                <p className="text-xs text-neon-red mt-1 text-center">Invalid PIN</p>
              )}
            </div>

            <button
              onClick={handlePinConfirm}
              className="w-full rounded-lg bg-neon-red/20 border border-neon-red py-3 text-sm font-bold uppercase tracking-wider text-neon-red hover:bg-neon-red/30 hover:shadow-neon-red transition-all"
            >
              Confirm LIVE Mode
            </button>
          </div>
        </div>
      )}
    </>
  );
}
