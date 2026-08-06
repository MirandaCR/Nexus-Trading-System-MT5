'use client';

import { useEffect, useRef, useState, useCallback } from 'react';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

export type AlertSeverity = 'HIGH' | 'MEDIUM' | 'INFO';

export interface SystemAlert {
  type: string;
  message: string;
  severity: AlertSeverity;
  ts: string;
  // optional per-type extra fields
  bias?: string;
  confidence?: number;
  pair?: string;
  corr_7d?: number;
  corr_30d?: number;
  shift?: number;
  events?: { title: string; event_time: string }[];
  ends_at?: string | null;
}

// Notification type → label + icon char
export const ALERT_META: Record<string, { label: string; color: string }> = {
  BLACKOUT_START:  { label: 'Blackout inicio',  color: 'text-neon-red' },
  BLACKOUT_END:    { label: 'Blackout fin',      color: 'text-neon-green' },
  BRIEF_READY:     { label: 'Brief generado',    color: 'text-yellow-400' },
  CORR_DIVERGENCE: { label: 'Divergencia corr.', color: 'text-orange-400' },
  SIGNAL_HIGH:     { label: 'Señal alta calidad',color: 'text-neon-purple' },
  CIRCUIT_BREAKER: { label: 'Circuit breaker',   color: 'text-neon-red' },
};

const STORAGE_PREFS_KEY = 'nexus:notif:prefs';
const STORAGE_READ_KEY  = 'nexus:notif:last_read';

export interface NotifPrefs {
  browser:    boolean;
  blackout:   boolean;
  brief:      boolean;
  divergence: boolean;
}

function loadPrefs(): NotifPrefs {
  if (typeof window === 'undefined') return { browser: true, blackout: true, brief: true, divergence: true };
  try {
    const raw = localStorage.getItem(STORAGE_PREFS_KEY);
    if (raw) return { browser: true, blackout: true, brief: true, divergence: true, ...JSON.parse(raw) };
  } catch {}
  return { browser: true, blackout: true, brief: true, divergence: true };
}

function shouldNotify(alert: SystemAlert, prefs: NotifPrefs): boolean {
  if (alert.type === 'BLACKOUT_START' || alert.type === 'BLACKOUT_END') return prefs.blackout;
  if (alert.type === 'BRIEF_READY') return prefs.brief;
  if (alert.type === 'CORR_DIVERGENCE') return prefs.divergence;
  return true;
}

export function useNotifications() {
  const [alerts, setAlerts] = useState<SystemAlert[]>([]);
  const [unread, setUnread] = useState(0);
  const [connected, setConnected] = useState(false);
  const [prefs, setPrefs] = useState<NotifPrefs>(loadPrefs);
  const esRef = useRef<EventSource | null>(null);
  const lastReadRef = useRef<string>(
    typeof window !== 'undefined' ? (localStorage.getItem(STORAGE_READ_KEY) || '') : ''
  );

  const addAlert = useCallback((alert: SystemAlert) => {
    setAlerts(prev => {
      const next = [alert, ...prev].slice(0, 50);
      // Count unread: alerts newer than lastRead
      const unreadCount = next.filter(a => a.ts > lastReadRef.current).length;
      setUnread(unreadCount);
      return next;
    });

    // Browser notification
    if (prefs.browser && shouldNotify(alert, prefs) && typeof window !== 'undefined') {
      if (Notification.permission === 'granted') {
        const meta = ALERT_META[alert.type];
        new Notification(`Nexus — ${meta?.label ?? alert.type}`, {
          body: alert.message,
          icon: '/favicon.ico',
          tag: alert.type,
          silent: alert.severity === 'INFO',
        });
      }
    }
  }, [prefs]);

  // Request browser notification permission on mount
  useEffect(() => {
    if (typeof window !== 'undefined' && 'Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission().catch(() => {});
    }
  }, []);

  // SSE connection
  useEffect(() => {
    const connect = () => {
      const es = new EventSource(`${API}/api/notifications/stream?prefetch=15`);
      esRef.current = es;

      es.onopen = () => setConnected(true);

      es.onmessage = (event) => {
        try {
          const alert: SystemAlert = JSON.parse(event.data);
          if (alert.type && alert.message) addAlert(alert);
        } catch {}
      };

      es.onerror = () => {
        setConnected(false);
        es.close();
        // Reconnect after 5s
        setTimeout(connect, 5000);
      };
    };

    connect();
    return () => {
      esRef.current?.close();
      esRef.current = null;
    };
  }, [addAlert]);

  const markAllRead = useCallback(() => {
    const now = new Date().toISOString();
    lastReadRef.current = now;
    setUnread(0);
    if (typeof window !== 'undefined') localStorage.setItem(STORAGE_READ_KEY, now);
  }, []);

  const updatePrefs = useCallback((next: Partial<NotifPrefs>) => {
    setPrefs(prev => {
      const merged = { ...prev, ...next };
      if (typeof window !== 'undefined') localStorage.setItem(STORAGE_PREFS_KEY, JSON.stringify(merged));
      return merged;
    });
  }, []);

  return { alerts, unread, connected, prefs, updatePrefs, markAllRead };
}
