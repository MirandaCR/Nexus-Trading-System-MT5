'use client';

import { cn } from '@/lib/utils';
import { useDashboardStore } from '@/stores/dashboardStore';
import { GlowText } from '@/components/shared/GlowText';
import { apiClient } from '@/lib/api-client';
import { ShieldAlert, ShieldCheck, X, AlertTriangle } from 'lucide-react';
import { useState, useCallback, useRef } from 'react';

// PIN stored as env var on dashboard side; falls back to default so it's not hardcoded in source
const KILL_PIN = process.env.NEXT_PUBLIC_KILL_PIN ?? '0000';

interface KillSwitchProps {
  compact?: boolean;
}

type DialogStep = 'confirm' | 'pin';

export function KillSwitch({ compact = false }: KillSwitchProps) {
  const { isKilled, activateKillSwitch, deactivateKillSwitch } = useDashboardStore();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [step, setStep] = useState<DialogStep>('confirm');
  const [pin, setPin] = useState('');
  const [pinError, setPinError] = useState(false);
  const [mode, setMode] = useState<'kill' | 'reactivate'>('kill');
  const inputRef = useRef<HTMLInputElement>(null);

  const openDialog = useCallback((action: 'kill' | 'reactivate') => {
    setMode(action);
    setStep('confirm');
    setPin('');
    setPinError(false);
    setDialogOpen(true);
  }, []);

  const handleAdvanceToPIN = useCallback(() => {
    setStep('pin');
    setTimeout(() => inputRef.current?.focus(), 80);
  }, []);

  const handleConfirmPIN = useCallback(() => {
    if (pin !== KILL_PIN) {
      setPinError(true);
      setPin('');
      return;
    }
    if (mode === 'kill') {
      activateKillSwitch();
      apiClient.systemKillSwitch(pin, 'manual kill-switch from dashboard').catch(() => undefined);
    } else {
      deactivateKillSwitch();
      apiClient.systemToggleAutoTrade(false).catch(() => undefined);
    }
    setDialogOpen(false);
    setPin('');
  }, [pin, mode, activateKillSwitch, deactivateKillSwitch]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter') handleConfirmPIN();
      if (e.key === 'Escape') setDialogOpen(false);
    },
    [handleConfirmPIN],
  );

  function KillDialog() {
    const isKillMode = mode === 'kill';
    return (
      <div className="fixed inset-0 z-[100] flex items-center justify-center bg-bg-overlay animate-fade-in">
        <div
          className={cn(
            'relative w-80 rounded-xl border bg-bg-card p-6 shadow-lg animate-slide-up',
            isKillMode ? 'border-neon-red/60 shadow-neon-red/20' : 'border-neon-green/60 shadow-neon-green/20',
          )}
        >
          <button
            onClick={() => setDialogOpen(false)}
            className="absolute top-3 right-3 text-text-muted hover:text-text-primary transition-colors"
          >
            <X className="h-4 w-4" />
          </button>

          <div className="flex flex-col items-center gap-3 mb-5">
            {isKillMode ? (
              <ShieldAlert className="h-10 w-10 text-neon-red animate-neon-pulse" />
            ) : (
              <ShieldCheck className="h-10 w-10 text-neon-green" />
            )}
            <h3 className="text-lg font-bold text-text-primary">
              {isKillMode ? 'EMERGENCY STOP' : 'Reactivate Trading'}
            </h3>
          </div>

          {/* Step 1 — Are you sure? */}
          {step === 'confirm' && (
            <>
              <div className={cn(
                'mb-4 rounded-lg px-3 py-2.5 text-xs leading-relaxed',
                isKillMode ? 'bg-neon-red/10 text-neon-red/90' : 'bg-neon-green/10 text-neon-green/90',
              )}>
                {isKillMode ? (
                  <>
                    <AlertTriangle className="inline h-3.5 w-3.5 mr-1 -mt-0.5" />
                    This will <strong>halt all autonomous trading</strong> immediately. Open positions will remain until manually closed.
                  </>
                ) : (
                  'This will re-enable the autonomous trading pipeline. Confirm you intend to resume.'
                )}
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => setDialogOpen(false)}
                  className="flex-1 rounded-lg py-2.5 text-sm font-medium border border-border-primary text-text-secondary hover:text-text-primary transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleAdvanceToPIN}
                  className={cn(
                    'flex-1 rounded-lg py-2.5 text-sm font-bold uppercase tracking-wider transition-all',
                    isKillMode
                      ? 'bg-neon-red/20 border border-neon-red text-neon-red hover:bg-neon-red/30'
                      : 'bg-neon-green/20 border border-neon-green text-neon-green hover:bg-neon-green/30',
                  )}
                >
                  {isKillMode ? 'Yes, Stop' : 'Reactivate'}
                </button>
              </div>
            </>
          )}

          {/* Step 2 — Enter PIN */}
          {step === 'pin' && (
            <>
              <p className="text-xs text-text-secondary text-center mb-4">
                Enter your 4-digit PIN to confirm.
              </p>
              <div className="mb-4">
                <input
                  ref={inputRef}
                  type="password"
                  maxLength={4}
                  value={pin}
                  onChange={(e) => { setPin(e.target.value); setPinError(false); }}
                  onKeyDown={handleKeyDown}
                  placeholder="• • • •"
                  className={cn(
                    'w-full rounded-lg bg-bg-input border px-4 py-3 text-center text-lg font-mono tracking-[0.5em]',
                    'focus:outline-none transition-colors',
                    pinError
                      ? 'border-neon-red text-neon-red'
                      : 'border-border-primary text-text-primary focus:border-neon-purple/60',
                  )}
                />
                {pinError && (
                  <p className="text-xs text-neon-red mt-1 text-center">Invalid PIN</p>
                )}
              </div>
              <button
                onClick={handleConfirmPIN}
                className={cn(
                  'w-full rounded-lg py-3 text-sm font-bold uppercase tracking-wider transition-all',
                  isKillMode
                    ? 'bg-neon-red/20 border border-neon-red text-neon-red hover:bg-neon-red/30 hover:shadow-neon-red'
                    : 'bg-neon-green/20 border border-neon-green text-neon-green hover:bg-neon-green/30 hover:shadow-neon-green',
                )}
              >
                {isKillMode ? 'CONFIRM KILL' : 'REACTIVATE'}
              </button>
            </>
          )}
        </div>
      </div>
    );
  }

  if (compact) {
    return (
      <>
        <button
          onClick={() => isKilled ? openDialog('reactivate') : openDialog('kill')}
          className={cn(
            'flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-bold uppercase transition-all',
            isKilled
              ? 'bg-neon-red/20 border border-neon-red text-neon-red shadow-neon-red'
              : 'bg-neon-red/5 border border-neon-red/40 text-neon-red hover:bg-neon-red/10 hover:shadow-neon-red animate-neon-pulse',
          )}
        >
          <ShieldAlert className="h-3.5 w-3.5" />
          {isKilled ? 'KILLED' : 'KILL'}
        </button>
        {dialogOpen && <KillDialog />}
      </>
    );
  }

  return (
    <>
      <div className="flex flex-col items-center gap-3">
        <button
          onClick={() => isKilled ? openDialog('reactivate') : openDialog('kill')}
          className={cn(
            'relative flex flex-col items-center justify-center gap-2 rounded-2xl px-8 py-6 transition-all',
            isKilled
              ? 'bg-neon-red/20 border-2 border-neon-red text-neon-red shadow-neon-red'
              : 'bg-neon-red/5 border-2 border-neon-red/40 text-neon-red hover:bg-neon-red/10 hover:shadow-neon-red animate-neon-pulse',
          )}
        >
          <ShieldAlert className="h-8 w-8" />
          <span className="text-sm font-bold uppercase tracking-widest">Emergency Stop</span>
        </button>
        {isKilled && (
          <GlowText color="red" size="xs" pulse>
            TRADING HALTED
          </GlowText>
        )}
      </div>
      {dialogOpen && <KillDialog />}
    </>
  );
}
