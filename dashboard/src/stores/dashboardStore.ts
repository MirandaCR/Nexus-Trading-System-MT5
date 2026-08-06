import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { TradingMode, Timeframe, LayoutItem, AccountInfo } from '@/types';

interface DashboardState {
  /* Trading state */
  symbol: string;
  timeframe: Timeframe;
  mode: TradingMode;
  autoTradeEnabled: boolean;
  isKilled: boolean;

  /* Account */
  account: AccountInfo;

  /* Layout */
  gridLayout: LayoutItem[];
  sidebarCollapsed: boolean;

  /* Actions */
  setSymbol: (symbol: string) => void;
  setTimeframe: (tf: Timeframe) => void;
  setMode: (mode: TradingMode) => void;
  toggleAutoTrade: () => void;
  setAutoTrade: (enabled: boolean) => void;
  activateKillSwitch: () => void;
  deactivateKillSwitch: () => void;
  setAccount: (account: Partial<AccountInfo>) => void;
  setGridLayout: (layout: LayoutItem[]) => void;
  toggleSidebar: () => void;
}

const DEFAULT_LAYOUT: LayoutItem[] = [
  { i: 'live-chart', x: 0, y: 0, w: 7, h: 6, minW: 4, minH: 3 },
  { i: 'agent-console', x: 7, y: 0, w: 5, h: 6, minW: 3, minH: 3 },
  { i: 'positions', x: 0, y: 6, w: 4, h: 4, minW: 3, minH: 2 },
  { i: 'order-book', x: 4, y: 6, w: 4, h: 4, minW: 3, minH: 2 },
  { i: 'risk-gauge', x: 8, y: 6, w: 4, h: 4, minW: 2, minH: 2 },
  { i: 'news-feed', x: 0, y: 10, w: 3, h: 4, minW: 2, minH: 2 },
  { i: 'trade-journal', x: 3, y: 10, w: 3, h: 4, minW: 2, minH: 2 },
  { i: 'performance', x: 6, y: 10, w: 3, h: 4, minW: 2, minH: 2 },
  { i: 'market-depth', x: 9, y: 10, w: 3, h: 4, minW: 2, minH: 2 },
  { i: 'strategy-config', x: 0, y: 14, w: 4, h: 3, minW: 2, minH: 2 },
  { i: 'alerts', x: 4, y: 14, w: 4, h: 3, minW: 2, minH: 2 },
  { i: 'system-status', x: 8, y: 14, w: 4, h: 3, minW: 2, minH: 2 },
];

export const useDashboardStore = create<DashboardState>()(
  persist(
    (set) => ({
      symbol: 'XAUUSD',
      timeframe: 'M15',
      mode: 'PAPER',
      autoTradeEnabled: true,
      isKilled: false,

      account: {
        balance: 0,
        equity: 0,
        margin: 0,
        freeMargin: 0,
        dailyPnL: 0,
        openPositions: 0,
      },

      gridLayout: DEFAULT_LAYOUT,
      sidebarCollapsed: false,

      setSymbol: (symbol) => set({ symbol }),
      setTimeframe: (timeframe) => set({ timeframe }),
      setMode: (mode) => set({ mode }),
      toggleAutoTrade: () =>
        set((s) => ({ autoTradeEnabled: !s.autoTradeEnabled })),
      setAutoTrade: (enabled) => set({ autoTradeEnabled: enabled }),
      activateKillSwitch: () =>
        set({ isKilled: true, autoTradeEnabled: false }),
      deactivateKillSwitch: () => set({ isKilled: false }),
      setAccount: (account) =>
        set((s) => ({ account: { ...s.account, ...account } })),
      setGridLayout: (gridLayout) => set({ gridLayout }),
      toggleSidebar: () =>
        set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
    }),
    {
      name: 'nexus-dashboard',
      version: 3,
      partialize: (state) => ({
        symbol: state.symbol,
        timeframe: state.timeframe,
        mode: state.mode,
        autoTradeEnabled: state.autoTradeEnabled,
        gridLayout: state.gridLayout,
        sidebarCollapsed: state.sidebarCollapsed,
      }),
      migrate: (persisted: unknown) => {
        const state = persisted as unknown as Partial<DashboardState>;
        // Ensure symbol is always one of our valid symbols
        if (state?.symbol && !['XAUUSD', 'BTCUSD'].includes(state.symbol)) {
          state.symbol = 'XAUUSD';
        }
        return state as DashboardState;
      },
    }
  )
);
