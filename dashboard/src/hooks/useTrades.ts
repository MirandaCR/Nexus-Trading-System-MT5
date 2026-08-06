'use client';

import { useState, useMemo, useCallback, useEffect, useRef } from 'react';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

export interface Trade {
  id: string;
  date: string;
  symbol: string;
  side: 'LONG' | 'SHORT';
  entry: number;
  exit: number;
  pnlPips: number;
  pnlDollar: number;
  rr: number;
  duration: string;
  session: string;
  conviction: number;
  strategy: string;
  reasoning?: string;
}

export interface TradeFilters {
  dateRange: { start: string; end: string };
  symbol: string;
  result: 'all' | 'win' | 'loss';
  session: string;
}

function formatDuration(seconds: number): string {
  if (!seconds || seconds <= 0) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function apiRowToTrade(row: Record<string, unknown>): Trade {
  const side = String(row.side ?? 'BUY').toUpperCase();
  const closedAt = row.closed_at ? String(row.closed_at) : row.opened_at ? String(row.opened_at) : '';
  return {
    id: String(row.id ?? ''),
    date: closedAt ? closedAt.slice(0, 10) : '',
    symbol: String(row.symbol ?? ''),
    side: side.includes('SELL') ? 'SHORT' : 'LONG',
    entry: Number(row.entry_price ?? 0),
    exit: Number(row.exit_price ?? 0),
    pnlPips: Number(row.pnl_pips ?? 0),
    pnlDollar: Number(row.pnl_usd ?? 0),
    rr: Number(row.rr_ratio ?? 0),
    duration: formatDuration(Number(row.duration_s ?? 0)),
    session: String(row.session ?? ''),
    conviction: Number(row.conviction ?? 0),
    strategy: String(row.strategy ?? ''),
    reasoning: row.comment ? String(row.comment) : undefined,
  };
}

export function useTrades() {
  const [allTrades, setAllTrades] = useState<Trade[]>([]);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(10);
  const [sortField, setSortField] = useState<keyof Trade>('date');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [filters, setFilters] = useState<TradeFilters>({
    dateRange: { start: '', end: '' },
    symbol: 'all',
    result: 'all',
    session: 'all',
  });
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const mounted = useRef(true);

  // Fetch trades from gateway API
  useEffect(() => {
    mounted.current = true;
    let id: ReturnType<typeof setInterval>;

    async function fetchTrades() {
      try {
        const res = await fetch(`${API}/api/trades?page=1&page_size=200`);
        if (!res.ok || !mounted.current) return;
        const data = await res.json();
        const rows: Record<string, unknown>[] = data.trades ?? [];
        if (!mounted.current) return;
        setAllTrades(rows.map(apiRowToTrade));
      } catch {
        // silent — API may not be available
      }
    }

    fetchTrades();
    id = setInterval(fetchTrades, 30_000);
    return () => {
      mounted.current = false;
      clearInterval(id);
    };
  }, []);

  const filteredTrades = useMemo(() => {
    let result = [...allTrades];

    if (filters.symbol !== 'all') result = result.filter((t) => t.symbol === filters.symbol);
    if (filters.result === 'win') result = result.filter((t) => t.pnlDollar > 0);
    if (filters.result === 'loss') result = result.filter((t) => t.pnlDollar < 0);
    if (filters.session !== 'all') result = result.filter((t) => t.session === filters.session);
    if (filters.dateRange.start) result = result.filter((t) => t.date >= filters.dateRange.start);
    if (filters.dateRange.end) result = result.filter((t) => t.date <= filters.dateRange.end);

    result.sort((a, b) => {
      const av = a[sortField];
      const bv = b[sortField];
      if (typeof av === 'number' && typeof bv === 'number') return sortDir === 'asc' ? av - bv : bv - av;
      return sortDir === 'asc' ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
    });

    return result;
  }, [allTrades, filters, sortField, sortDir]);

  const totalPages = Math.ceil(filteredTrades.length / perPage);
  const paginatedTrades = filteredTrades.slice((page - 1) * perPage, page * perPage);

  const stats = useMemo(() => {
    const wins = filteredTrades.filter((t) => t.pnlDollar > 0);
    const losses = filteredTrades.filter((t) => t.pnlDollar < 0);
    return {
      total: filteredTrades.length,
      winRate: filteredTrades.length > 0 ? (wins.length / filteredTrades.length) * 100 : 0,
      avgWin: wins.length > 0 ? wins.reduce((s, t) => s + t.pnlDollar, 0) / wins.length : 0,
      avgLoss: losses.length > 0 ? losses.reduce((s, t) => s + t.pnlDollar, 0) / losses.length : 0,
      best: filteredTrades.length > 0 ? Math.max(...filteredTrades.map((t) => t.pnlDollar)) : 0,
      worst: filteredTrades.length > 0 ? Math.min(...filteredTrades.map((t) => t.pnlDollar)) : 0,
    };
  }, [filteredTrades]);

  const toggleSort = useCallback((field: keyof Trade) => {
    setSortField((prev) => {
      if (prev === field) {
        setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
        return prev;
      }
      setSortDir('desc');
      return field;
    });
  }, []);

  const exportCSV = useCallback(() => {
    const headers = ['Date', 'Symbol', 'Side', 'Entry', 'Exit', 'P&L Pips', 'P&L $', 'R:R', 'Duration', 'Session', 'Conviction', 'Strategy'];
    const rows = filteredTrades.map((t) => [t.date, t.symbol, t.side, t.entry, t.exit, t.pnlPips, t.pnlDollar, t.rr, t.duration, t.session, t.conviction, t.strategy].join(','));
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'trade_history.csv';
    a.click();
    URL.revokeObjectURL(url);
  }, [filteredTrades]);

  return {
    trades: paginatedTrades,
    allTrades: filteredTrades,
    page,
    setPage,
    perPage,
    setPerPage,
    totalPages,
    sortField,
    sortDir,
    toggleSort,
    filters,
    setFilters,
    stats,
    expandedId,
    setExpandedId,
    exportCSV,
  };
}
