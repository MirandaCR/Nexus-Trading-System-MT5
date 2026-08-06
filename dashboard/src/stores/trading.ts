import { create } from 'zustand';
import type { SessionMode } from '@/types/trading';

interface TradingState {
  autoTrade: boolean;
  mode: SessionMode;
  selectedSymbol: string;
  selectedTimeframe: string;
  isSessionActive: boolean;

  setAutoTrade: (enabled: boolean) => void;
  setMode: (mode: SessionMode) => void;
  setSelectedSymbol: (symbol: string) => void;
  setSelectedTimeframe: (tf: string) => void;
  setSessionActive: (active: boolean) => void;
  toggleAutoTrade: () => void;
}

export const useTradingStore = create<TradingState>()((set) => ({
  autoTrade: false,
  mode: 'paper',
  selectedSymbol: process.env.NEXT_PUBLIC_DEFAULT_SYMBOL || 'XAUUSD',
  selectedTimeframe: process.env.NEXT_PUBLIC_DEFAULT_TIMEFRAME || 'H1',
  isSessionActive: false,

  setAutoTrade: (enabled) => set({ autoTrade: enabled }),

  setMode: (mode) => set({ mode }),

  setSelectedSymbol: (symbol) => set({ selectedSymbol: symbol }),

  setSelectedTimeframe: (tf) => set({ selectedTimeframe: tf }),

  setSessionActive: (active) => set({ isSessionActive: active }),

  toggleAutoTrade: () =>
    set((state) => ({ autoTrade: !state.autoTrade })),
}));
