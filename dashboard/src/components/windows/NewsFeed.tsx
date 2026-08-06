'use client';

import { useEffect, useMemo, useState } from 'react';
import NeonCard from '../shared/NeonCard';
import { useDashboardStore } from '@/stores/dashboardStore';
import { apiClient } from '@/lib/api-client';

type Impact = 'HIGH' | 'MEDIUM' | 'LOW';
type Sentiment = 'bullish' | 'bearish' | 'neutral';

interface NewsItem {
  id: string;
  headline: string;
  source: string;
  timestamp: string;
  impact: Impact;
  sentiment: Sentiment;
  affected_currencies: string[];
  finbert_score: number;
}

interface CalendarEvent {
  id: string;
  event_name: string;
  currency: string;
  impact: Impact;
  forecast?: string;
  previous?: string;
  timestamp: string;
}

// ── Mock Data (fixed timestamps — no Date.now() at module scope) ───────────────

const MOCK_NEWS: NewsItem[] = [
  // Gold / XAU
  { id: 'n_xau1', headline: 'Gold Surges Past $3,300 as Safe-Haven Demand Spikes on US-China Tensions', source: 'Reuters', timestamp: '2026-05-06T22:30:00.000Z', impact: 'HIGH', sentiment: 'bullish', affected_currencies: ['XAU', 'USD'], finbert_score: 0.82 },
  { id: 'n_xau2', headline: 'Fed Officials Signal Higher-for-Longer Rates — Headwind for Gold', source: 'Bloomberg', timestamp: '2026-05-06T20:15:00.000Z', impact: 'HIGH', sentiment: 'bearish', affected_currencies: ['XAU', 'USD'], finbert_score: -0.68 },
  { id: 'n_xau3', headline: 'Central Banks Add 37 Tonnes to Gold Reserves in April — WGC', source: 'WGC', timestamp: '2026-05-06T18:00:00.000Z', impact: 'MEDIUM', sentiment: 'bullish', affected_currencies: ['XAU'], finbert_score: 0.55 },
  { id: 'n_xau4', headline: 'US Dollar Index Slides to 3-Month Low, Supporting Gold Prices', source: 'CNBC', timestamp: '2026-05-06T15:30:00.000Z', impact: 'MEDIUM', sentiment: 'bullish', affected_currencies: ['XAU', 'USD'], finbert_score: 0.48 },
  // Bitcoin / BTC
  { id: 'n_btc1', headline: 'Bitcoin Breaks $97K as ETF Inflows Hit Monthly Record', source: 'CoinDesk', timestamp: '2026-05-06T22:00:00.000Z', impact: 'HIGH', sentiment: 'bullish', affected_currencies: ['BTC', 'USD'], finbert_score: 0.88 },
  { id: 'n_btc2', headline: 'SEC Approves Two More Spot Bitcoin ETF Amendments', source: 'The Block', timestamp: '2026-05-06T19:45:00.000Z', impact: 'HIGH', sentiment: 'bullish', affected_currencies: ['BTC'], finbert_score: 0.75 },
  { id: 'n_btc3', headline: 'Bitcoin Miners Capitulate as Hash Rate Drops 8% Post-Halving', source: 'Decrypt', timestamp: '2026-05-06T17:00:00.000Z', impact: 'MEDIUM', sentiment: 'bearish', affected_currencies: ['BTC'], finbert_score: -0.42 },
  // Forex
  { id: 'n1', headline: 'ECB Holds Rates Steady, Signals Possible Cut in September', source: 'Reuters', timestamp: '2026-05-06T11:55:00.000Z', impact: 'HIGH', sentiment: 'bearish', affected_currencies: ['EUR'], finbert_score: -0.72 },
  { id: 'n2', headline: 'US Nonfarm Payrolls Beat Expectations at 275K', source: 'Bloomberg', timestamp: '2026-05-06T11:48:00.000Z', impact: 'HIGH', sentiment: 'bullish', affected_currencies: ['USD'], finbert_score: 0.85 },
  { id: 'n3', headline: 'BOJ Deputy Governor: No Rush to Raise Rates Further', source: 'Nikkei', timestamp: '2026-05-06T11:35:00.000Z', impact: 'MEDIUM', sentiment: 'bearish', affected_currencies: ['JPY'], finbert_score: -0.45 },
  { id: 'n4', headline: 'UK Manufacturing PMI Falls to 48.7, Below Forecast', source: 'Markit', timestamp: '2026-05-06T11:20:00.000Z', impact: 'MEDIUM', sentiment: 'bearish', affected_currencies: ['GBP'], finbert_score: -0.58 },
  { id: 'n5', headline: 'Swiss National Bank Unexpectedly Cuts Rate by 25bps', source: 'SNB', timestamp: '2026-05-06T10:48:00.000Z', impact: 'HIGH', sentiment: 'bearish', affected_currencies: ['CHF'], finbert_score: -0.68 },
  { id: 'n6', headline: 'Oil Prices Rise 2% on OPEC+ Output Cut Extension', source: 'CNBC', timestamp: '2026-05-06T10:30:00.000Z', impact: 'MEDIUM', sentiment: 'bullish', affected_currencies: ['CAD'], finbert_score: 0.55 },
  { id: 'n7', headline: 'Fed Minutes: Officials See Progress on Inflation but Want More Data', source: 'Fed', timestamp: '2026-05-06T10:00:00.000Z', impact: 'HIGH', sentiment: 'neutral', affected_currencies: ['USD'], finbert_score: 0.08 },
];

const MOCK_CALENDAR: CalendarEvent[] = [
  { id: 'c_xau1', event_name: 'US CPI (MoM) — Gold key driver', currency: 'XAU', impact: 'HIGH', forecast: '0.3%', previous: '0.4%', timestamp: '2026-05-07T12:30:00.000Z' },
  { id: 'c_xau2', event_name: 'FOMC Meeting Minutes', currency: 'XAU', impact: 'HIGH', forecast: '-', previous: '-', timestamp: '2026-05-07T18:00:00.000Z' },
  { id: 'c_btc1', event_name: 'Bitcoin Options Expiry ($2.8B notional)', currency: 'BTC', impact: 'HIGH', forecast: '-', previous: '-', timestamp: '2026-05-07T08:00:00.000Z' },
  { id: 'c1', event_name: 'US CPI (MoM)', currency: 'USD', impact: 'HIGH', forecast: '0.3%', previous: '0.4%', timestamp: '2026-05-07T12:30:00.000Z' },
  { id: 'c2', event_name: 'ECB Press Conference', currency: 'EUR', impact: 'HIGH', forecast: '-', previous: '-', timestamp: '2026-05-07T12:45:00.000Z' },
  { id: 'c3', event_name: 'UK GDP (QoQ)', currency: 'GBP', impact: 'MEDIUM', forecast: '0.2%', previous: '0.1%', timestamp: '2026-05-07T14:00:00.000Z' },
  { id: 'c4', event_name: 'Japan Core CPI (YoY)', currency: 'JPY', impact: 'MEDIUM', forecast: '2.8%', previous: '2.7%', timestamp: '2026-05-07T23:30:00.000Z' },
  { id: 'c5', event_name: 'Canada Employment Change', currency: 'CAD', impact: 'MEDIUM', forecast: '25.0K', previous: '41.4K', timestamp: '2026-05-08T12:30:00.000Z' },
];

// ── Helpers ──────────────────────────────────────────────────────────────────

function impactBadge(impact: Impact) {
  const styles: Record<Impact, string> = {
    HIGH: 'bg-neon-red/10 text-neon-red font-semibold',
    MEDIUM: 'bg-neon-gold/15 text-neon-gold',
    LOW: 'bg-bg-tertiary text-text-muted',
  };
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-[9px] font-mono font-bold ${styles[impact]}`}>
      {impact}
    </span>
  );
}

function sentimentBadge(sentiment: Sentiment) {
  const styles: Record<Sentiment, string> = {
    bullish: 'text-neon-green',
    bearish: 'text-neon-red',
    neutral: 'text-text-muted',
  };
  const icons: Record<Sentiment, string> = {
    bullish: '▲',
    bearish: '▼',
    neutral: '●',
  };
  return (
    <span className={`text-[10px] font-mono font-bold ${styles[sentiment]}`}>
      {icons[sentiment]} {sentiment.charAt(0).toUpperCase() + sentiment.slice(1)}
    </span>
  );
}

function finbertBar(score: number) {
  const normalized = (score + 1) / 2;
  const color = score > 0.2 ? '#00ff88' : score < -0.2 ? '#ff2255' : '#9898b0';
  return (
    <div className="h-1 w-full rounded-full bg-bg-tertiary overflow-hidden mt-1">
      <div className="h-full rounded-full transition-all" style={{ width: `${normalized * 100}%`, backgroundColor: color }} />
    </div>
  );
}

function timeAgo(iso: string, now: number | null): string {
  if (now === null) return '…';
  const diff = now - new Date(iso).getTime();
  const m = Math.floor(diff / 60_000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function formatCountdown(iso: string, now: number | null): string {
  if (now === null) return '…';
  const diff = new Date(iso).getTime() - now;
  if (diff <= 0) return 'NOW';
  const h = Math.floor(diff / 3_600_000);
  const m = Math.floor((diff % 3_600_000) / 60_000);
  const s = Math.floor((diff % 60_000) / 1000);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function isBlackoutZone(events: CalendarEvent[], now: number | null): boolean {
  if (now === null) return false;
  return events.some((e) => {
    const diff = new Date(e.timestamp).getTime() - now;
    return e.impact === 'HIGH' && diff > 0 && diff < 5 * 60_000;
  });
}

const CURRENCIES = ['ALL', 'XAU', 'BTC', 'USD', 'EUR', 'GBP', 'JPY', 'AUD', 'CAD', 'CHF', 'NZD'] as const;
type CurrencyFilter = (typeof CURRENCIES)[number];
type ImpactFilter = 'ALL' | Impact;

/** Map dashboard symbol to news currency filter */
function symbolToFilter(sym: string): CurrencyFilter {
  if (sym.startsWith('XAUUSD') || sym.startsWith('XAU')) return 'XAU';
  if (sym.startsWith('BTCUSD') || sym.startsWith('BTC')) return 'BTC';
  if (sym.startsWith('EUR')) return 'EUR';
  if (sym.startsWith('GBP')) return 'GBP';
  if (sym.startsWith('USDJPY') || sym.endsWith('JPY')) return 'JPY';
  if (sym.startsWith('AUD')) return 'AUD';
  if (sym.startsWith('CAD') || sym.endsWith('CAD')) return 'CAD';
  if (sym.startsWith('CHF') || sym.endsWith('CHF')) return 'CHF';
  return 'USD';
}

// ── Component ────────────────────────────────────────────────────────────────

export default function NewsFeed() {
  const selectedSymbol = useDashboardStore((s) => s.symbol);
  const [impactFilter, setImpactFilter] = useState<ImpactFilter>('ALL');
  const [currencyFilter, setCurrencyFilter] = useState<CurrencyFilter>('ALL');
  const [now, setNow] = useState<number | null>(null);
  const [liveNews, setLiveNews] = useState<NewsItem[] | null>(null);
  const [liveCalendar, setLiveCalendar] = useState<CalendarEvent[] | null>(null);

  useEffect(() => {
    setCurrencyFilter(symbolToFilter(selectedSymbol));
  }, [selectedSymbol]);

  useEffect(() => {
    const tick = () => setNow(Date.now());
    tick();
    const id = setInterval(tick, 10_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [newsRes, calRes] = await Promise.all([
          apiClient.getNews(),
          apiClient.getCalendar(),
        ]);
        if (cancelled) return;
        if (newsRes.data.length > 0) setLiveNews(newsRes.data as unknown as NewsItem[]);
        if (calRes.data.length > 0) setLiveCalendar(calRes.data as unknown as CalendarEvent[]);
      } catch { /* offline — keep mock */ }
    })();
    return () => { cancelled = true; };
  }, []);

  const newsSource = liveNews ?? MOCK_NEWS;
  const calendarSource = liveCalendar ?? MOCK_CALENDAR;

  const blackout = useMemo(() => isBlackoutZone(calendarSource, now), [calendarSource, now]);

  const filteredNews = useMemo(() => {
    return newsSource.filter((n) => {
      if (impactFilter !== 'ALL' && n.impact !== impactFilter) return false;
      if (currencyFilter !== 'ALL' && !n.affected_currencies.includes(currencyFilter)) return false;
      return true;
    });
  }, [newsSource, impactFilter, currencyFilter]);

  const filteredCalendar = useMemo(() => {
    return calendarSource.filter((e) => {
      if (impactFilter !== 'ALL' && e.impact !== impactFilter) return false;
      if (currencyFilter !== 'ALL' && e.currency !== currencyFilter) return false;
      return true;
    });
  }, [calendarSource, impactFilter, currencyFilter]);

  return (
    <NeonCard
      title={`News — ${selectedSymbol}`}
      glow="cyan"
      noPadding
      icon={
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M4 22h16a2 2 0 002-2V4a2 2 0 00-2-2H8a2 2 0 00-2 2v16a2 2 0 01-2 2zm0 0a2 2 0 01-2-2v-9c0-1.1.9-2 2-2h2" />
          <path d="M18 14h-8M15 18h-5M10 6h8v4h-8z" />
        </svg>
      }
      className="h-full flex flex-col"
    >
      {/* NEWS BLACKOUT BANNER */}
      {blackout && (
        <div className="relative overflow-hidden border-b-2 border-neon-red/50">
          <div className="bg-neon-red/10 px-4 py-2 flex items-center justify-center gap-2 animate-neon-pulse">
            <span className="h-2 w-2 rounded-full bg-neon-red animate-pulse" />
            <span className="text-xs font-mono font-bold text-neon-red uppercase tracking-widest">
              ⚠ NEWS BLACKOUT — High-Impact Event Imminent ⚠
            </span>
            <span className="h-2 w-2 rounded-full bg-neon-red animate-pulse" />
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="border-b border-border-primary/10 px-3 py-1.5 space-y-1">
        <div className="flex items-center gap-1 overflow-x-auto">
          <span className="text-[9px] font-mono text-text-muted mr-1 shrink-0">IMPACT</span>
          {(['ALL', 'HIGH', 'MEDIUM', 'LOW'] as ImpactFilter[]).map((f) => (
            <button
              key={f}
              onClick={() => setImpactFilter(f)}
              className={`rounded px-2 py-0.5 text-[10px] font-mono font-semibold transition-all ${
                f === impactFilter
                  ? 'bg-neon-purple/20 text-neon-purple'
                  : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              {f}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1 overflow-x-auto">
          <span className="text-[9px] font-mono text-text-muted mr-1 shrink-0">CCY</span>
          {CURRENCIES.map((c) => (
            <button
              key={c}
              onClick={() => setCurrencyFilter(c)}
              className={`rounded px-1.5 py-0.5 text-[10px] font-mono font-semibold transition-all ${
                c === currencyFilter
                  ? 'bg-neon-cyan/20 text-neon-cyan'
                  : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              {c}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto overscroll-contain">
        {/* News items */}
        <div className="px-3 py-2 space-y-2">
          {filteredNews.map((item) => (
            <div
              key={item.id}
              className="rounded-lg border border-border-primary/10 bg-bg-primary/30 p-3 hover:bg-bg-tertiary/20 transition-colors"
            >
              <div className="flex items-start justify-between gap-2 mb-1.5">
                <h4 className="text-xs font-semibold text-text-primary leading-snug flex-1">{item.headline}</h4>
                <div className="flex items-center gap-1.5 shrink-0">
                  {impactBadge(item.impact)}
                  {sentimentBadge(item.sentiment)}
                </div>
              </div>
              <div className="flex items-center gap-2 mb-1">
                <span className="text-[10px] font-mono text-text-muted">{item.source}</span>
                <span className="text-[10px] font-mono text-text-muted">•</span>
                <span className="text-[10px] font-mono text-text-muted">{timeAgo(item.timestamp, now)}</span>
              </div>
              <div className="flex items-center gap-1 mb-1">
                {item.affected_currencies.map((c) => (
                  <span key={c} className="rounded bg-neon-cyan/10 px-1.5 py-0.5 text-[9px] font-mono text-neon-cyan font-semibold">
                    {c}
                  </span>
                ))}
                <span className="ml-auto text-[9px] font-mono text-text-muted">
                  FinBERT: <span className={item.finbert_score > 0.2 ? 'text-neon-green' : item.finbert_score < -0.2 ? 'text-neon-red' : 'text-text-muted'}>
                    {item.finbert_score > 0 ? '+' : ''}{item.finbert_score.toFixed(2)}
                  </span>
                </span>
              </div>
              {finbertBar(item.finbert_score)}
            </div>
          ))}
          {filteredNews.length === 0 && (
            <div className="text-center py-6 text-xs font-mono text-text-muted">No news matching filters</div>
          )}
        </div>

        {/* Economic Calendar */}
        <div className="border-t border-border-primary/10 px-3 py-2">
          <h4 className="text-[10px] font-mono text-neon-purple uppercase tracking-widest mb-2">Economic Calendar</h4>
          <div className="space-y-1.5">
            {filteredCalendar.map((event) => {
              const diff = now === null ? 0 : new Date(event.timestamp).getTime() - now;
              const isImminent = now !== null && diff > 0 && diff < 15 * 60_000;

              return (
                <div
                  key={event.id}
                  className={`flex items-center gap-2 rounded px-2.5 py-2 ${
                    isImminent ? 'bg-neon-red/5 border border-neon-red/20' : 'bg-bg-primary/30'
                  }`}
                >
                  <div className="flex items-center gap-1.5 shrink-0">
                    {impactBadge(event.impact)}
                    <span className="rounded bg-neon-cyan/10 px-1 py-0.5 text-[9px] font-mono text-neon-cyan font-semibold">{event.currency}</span>
                  </div>
                  <span className="text-[11px] font-mono text-text-primary flex-1 truncate">{event.event_name}</span>
                  <div className="flex items-center gap-2 text-[10px] font-mono shrink-0">
                    {event.forecast && (
                      <span className="text-text-muted">F: <span className="text-text-secondary">{event.forecast}</span></span>
                    )}
                    {event.previous && (
                      <span className="text-text-muted">P: <span className="text-text-secondary">{event.previous}</span></span>
                    )}
                  </div>
                  <span className={`text-[10px] font-mono font-bold shrink-0 ${isImminent ? 'text-neon-red animate-neon-pulse' : 'text-neon-cyan'}`}>
                    {formatCountdown(event.timestamp, now)}
                  </span>
                </div>
              );
            })}
            {filteredCalendar.length === 0 && (
              <div className="text-center py-4 text-xs font-mono text-text-muted">No upcoming events</div>
            )}
          </div>
        </div>
      </div>
    </NeonCard>
  );
}
