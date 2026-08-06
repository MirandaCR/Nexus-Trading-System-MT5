'use client';

import { cn } from '@/lib/utils';
import { useDashboardStore } from '@/stores/dashboardStore';
import { GlowText } from '@/components/shared/GlowText';
import { apiClient } from '@/lib/api-client';
import { Bot, BotOff } from 'lucide-react';
import { useState, useCallback, useRef, useEffect } from 'react';

interface AutoTradeToggleProps {
  compact?: boolean;
}

export function AutoTradeToggle({ compact = false }: AutoTradeToggleProps) {
  const { autoTradeEnabled, toggleAutoTrade, isKilled } = useDashboardStore();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);
  const [popupPos, setPopupPos] = useState({ top: 0, left: 0 });

  useEffect(() => {
    if (confirmOpen && btnRef.current) {
      const r = btnRef.current.getBoundingClientRect();
      setPopupPos({ top: r.bottom + 8, left: r.left + r.width / 2 });
    }
  }, [confirmOpen]);

  const handleToggle = useCallback(() => {
    if (isKilled) return;

    if (autoTradeEnabled) {
      toggleAutoTrade();
      apiClient.systemToggleAutoTrade(false).catch(() => {/* gateway offline */});
    } else {
      setConfirmOpen(true);
    }
  }, [autoTradeEnabled, toggleAutoTrade, isKilled]);

  const handleConfirm = useCallback(() => {
    const nextEnabled = !autoTradeEnabled;
    toggleAutoTrade();
    setConfirmOpen(false);
    apiClient.systemToggleAutoTrade(nextEnabled).catch(() => {/* gateway offline */});
  }, [autoTradeEnabled, toggleAutoTrade]);

  if (compact) {
    return (
      <>
        <button
          ref={btnRef}
          onClick={handleToggle}
          disabled={isKilled}
          className={cn(
            'flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-bold uppercase transition-all',
            autoTradeEnabled
              ? 'bg-neon-green/10 border border-neon-green/40 text-neon-green shadow-neon-green'
              : 'bg-bg-input border border-border-primary text-text-muted',
            isKilled && 'opacity-40 cursor-not-allowed'
          )}
        >
          {autoTradeEnabled ? <Bot className="h-3.5 w-3.5" /> : <BotOff className="h-3.5 w-3.5" />}
          {autoTradeEnabled ? 'Auto' : 'Off'}
        </button>

        {/* Fixed confirm — avoids overflow-x-auto clipping from nav bar */}
        {confirmOpen && (
          <div
            className="fixed z-[200] w-56 rounded-lg border border-border-primary bg-bg-card p-3 shadow-xl animate-fade-in"
            style={{ top: popupPos.top, left: popupPos.left, transform: 'translateX(-50%)' }}
          >
            <p className="text-xs text-text-secondary mb-3">¿Activar auto-trading?</p>
            <div className="flex gap-2">
              <button
                onClick={handleConfirm}
                className="flex-1 rounded-md bg-neon-green/20 border border-neon-green/40 px-3 py-1 text-xs font-bold text-neon-green hover:bg-neon-green/30 transition-colors"
              >
                Activar
              </button>
              <button
                onClick={() => setConfirmOpen(false)}
                className="flex-1 rounded-md bg-bg-tertiary border border-border-primary px-3 py-1 text-xs font-medium text-text-muted hover:text-text-secondary transition-colors"
              >
                Cancelar
              </button>
            </div>
          </div>
        )}
      </>
    );
  }

  return (
    <div className="flex flex-col items-center gap-3">
      {/* Large toggle */}
      <button
        onClick={handleToggle}
        disabled={isKilled}
        className={cn(
          'relative w-20 h-10 rounded-full transition-all duration-300',
          autoTradeEnabled
            ? 'bg-neon-green/20 shadow-neon-green'
            : 'bg-bg-tertiary',
          isKilled && 'opacity-40 cursor-not-allowed'
        )}
      >
        <span
          className={cn(
            'absolute top-1 left-1 h-8 w-8 rounded-full transition-all duration-300 flex items-center justify-center',
            autoTradeEnabled
              ? 'translate-x-10 bg-neon-green shadow-neon-green'
              : 'translate-x-0 bg-border-primary'
          )}
        >
          {autoTradeEnabled ? (
            <Bot className="h-4 w-4 text-bg-primary" />
          ) : (
            <BotOff className="h-4 w-4 text-text-muted" />
          )}
        </span>
      </button>

      <GlowText
        color={autoTradeEnabled ? 'green' : 'red'}
        size="sm"
        pulse={autoTradeEnabled}
      >
        {autoTradeEnabled ? 'AUTO TRADING ON' : 'AUTO TRADING OFF'}
      </GlowText>

      {/* Confirm dialog overlay */}
      {confirmOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg-overlay animate-fade-in">
          <div className="w-80 rounded-xl border border-border-primary bg-bg-card p-6 shadow-lg animate-slide-up">
            <h3 className="text-lg font-semibold text-text-primary mb-2">
              Enable Auto-Trading
            </h3>
            <p className="text-sm text-text-secondary mb-6">
              The AI agents will begin executing trades automatically. Are you sure?
            </p>
            <div className="flex gap-3">
              <button
                onClick={handleConfirm}
                className="flex-1 rounded-lg bg-neon-green/20 border border-neon-green/40 px-4 py-2 text-sm font-bold text-neon-green hover:bg-neon-green/30 hover:shadow-neon-green transition-all"
              >
                Enable
              </button>
              <button
                onClick={() => setConfirmOpen(false)}
                className="flex-1 rounded-lg bg-bg-tertiary border border-border-primary px-4 py-2 text-sm font-medium text-text-muted hover:text-text-secondary transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
